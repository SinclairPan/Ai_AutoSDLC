from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import BinaryIO

import pytest
from tests.unit.stage_review.optimization.test_snapshots import (
    _binding,
    _promotion_evidence,
    _promotion_package,
    _promotion_policy,
    _register_promotion,
    _service,
    _snapshot,
)

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization import (
    snapshot_trusted_files,
    snapshot_windows_io,
)
from ai_sdlc.core.stage_review.optimization.promotion import (
    AutoPromotionDecision,
    AutoPromotionGate,
)
from ai_sdlc.core.stage_review.optimization.snapshot_trust_anchor import (
    SnapshotControlTrustAnchor,
)
from ai_sdlc.core.stage_review.resource_storage_bundles import (
    ResourceStorageBundleLedger,
)


def test_barrier_serializes_promotion_revocation_and_sixteen_bindings(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("baseline", is_baseline=True)
    active = _snapshot(
        "active",
        parent_snapshot_digest=baseline.snapshot_digest,
        stable_fallback_digest=baseline.snapshot_digest,
    )
    challenger = _snapshot(
        "challenger",
        parent_snapshot_digest=active.snapshot_digest,
        stable_fallback_digest=active.snapshot_digest,
    )
    service = _service(tmp_path, baseline)
    service.register_snapshot(active)
    active_package = _promotion_package(
        active,
        _decision(
            baseline.snapshot_digest,
            active.snapshot_digest,
            active.candidate_digest,
            "activate",
            shadow_result_digest=active.shadow_result_digest,
            evaluation_report_digests=active.evaluation_report_digests,
        ),
    )
    active_authorization = _register_promotion(service, active_package)
    service._promote_committed_package(
        active.snapshot_digest,
        promotion_package_digest=active_package.package_digest,
        promotion_authorization_digest=active_authorization,
        operation_id="operation.activate",
    )
    service.mark_stable(active.snapshot_digest, operation_id="operation.stable")
    service.register_snapshot(challenger)
    challenger_package = _promotion_package(
        challenger,
        _decision(
            active.snapshot_digest,
            challenger.snapshot_digest,
            challenger.candidate_digest,
            "concurrent",
            shadow_result_digest=challenger.shadow_result_digest,
            evaluation_report_digests=challenger.evaluation_report_digests,
        ),
    )
    challenger_authorization = _register_promotion(service, challenger_package)
    token = service.resolve_snapshot()
    barrier = threading.Barrier(18)

    def bind(index: int) -> object:
        barrier.wait()
        try:
            return service.bind_session(
                _binding(f"session.concurrent-{index}", token), token
            )
        except SharedStateIntegrityError as exc:
            assert _is_expected_contention_result(str(exc))
            return str(exc)

    def promote() -> object:
        barrier.wait()
        try:
            return service._promote_committed_package(
                challenger.snapshot_digest,
                promotion_package_digest=challenger_package.package_digest,
                promotion_authorization_digest=challenger_authorization,
                operation_id="operation.concurrent-promotion",
            )
        except SharedStateIntegrityError as exc:
            assert _is_expected_contention_result(str(exc))
            return str(exc)

    def revoke() -> object:
        barrier.wait()
        try:
            return service.revoke_and_rollback(
                active.snapshot_digest,
                reason="concurrent-safety-test",
                operation_id="operation.concurrent-revocation",
            )
        except SharedStateIntegrityError as exc:
            assert _is_expected_contention_result(str(exc))
            return str(exc)

    with ThreadPoolExecutor(max_workers=18) as pool:
        futures = [pool.submit(bind, index) for index in range(16)]
        futures.extend((pool.submit(promote), pool.submit(revoke)))
        results = tuple(future.result(timeout=5) for future in futures)

    assert len(results) == 18
    if isinstance(results[-1], str):
        # safety_pending 是有界竞争的公开结果；竞争结束后必须用同一操作身份重试。
        service.revoke_and_rollback(
            active.snapshot_digest,
            reason="concurrent-safety-test",
            operation_id="operation.concurrent-revocation",
        )
    final = service.resolve_snapshot()
    events = service.events()
    assert active.snapshot_digest in final.revoked_snapshot_digests
    assert tuple(event.sequence for event in events) == tuple(range(1, len(events) + 1))
    assert all(
        event.previous_event_digest == events[index - 1].event_digest
        for index, event in enumerate(events[1:], start=1)
    )
    revocation_sequence = next(
        event.sequence
        for event in events
        if event.event_kind == "revocation"
        and event.revoked_snapshot_digest == active.snapshot_digest
    )
    assert not any(
        event.event_kind == "session_binding"
        and event.sequence > revocation_sequence
        and event.target_snapshot_digest == active.snapshot_digest
        for event in events
    )
    bundle_events = ResourceStorageBundleLedger(service.resources._store).events()
    reserved = {
        event.bundle_id for event in bundle_events if event.event_kind == "reserved"
    }
    released = {
        event.bundle_id for event in bundle_events if event.event_kind == "released"
    }
    assert reserved == released


def test_trust_anchor_creation_waits_for_concurrent_key_writer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    writer_opened = threading.Event()
    release_writer = threading.Event()
    if os.name == "nt":
        original_write_handle = snapshot_windows_io._write_handle

        def delayed_write_handle(handle: int, payload: bytes) -> None:
            if len(payload) == 32 and not writer_opened.is_set():
                writer_opened.set()
                assert release_writer.wait(timeout=2)
            original_write_handle(handle, payload)

        monkeypatch.setattr(
            snapshot_windows_io,
            "_write_handle",
            delayed_write_handle,
        )
    else:
        original_fdopen = snapshot_trusted_files.os.fdopen

        class _DelayedKeyWriter:
            def __init__(self, handle: BinaryIO) -> None:
                self.handle = handle

            def __enter__(self) -> BinaryIO:
                writer_opened.set()
                assert release_writer.wait(timeout=2)
                return self.handle

            def __exit__(self, *args: object) -> None:
                self.handle.close()

        def delayed_fdopen(descriptor: int, mode: str) -> _DelayedKeyWriter:
            return _DelayedKeyWriter(original_fdopen(descriptor, mode))

        monkeypatch.setattr(snapshot_trusted_files.os, "fdopen", delayed_fdopen)
    release_timer = threading.Timer(0.05, release_writer.set)
    release_timer.start()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                SnapshotControlTrustAnchor,
                tmp_path,
                project_id="project.shared",
            )
            assert writer_opened.wait(timeout=2)
            second = pool.submit(
                SnapshotControlTrustAnchor,
                tmp_path,
                project_id="project.shared",
            )
            anchors = (first.result(timeout=2), second.result(timeout=2))
    finally:
        release_writer.set()
        release_timer.cancel()

    assert anchors[0]._key == anchors[1]._key


def test_trust_anchor_recovers_key_publish_crash_before_first_event(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    key_path = (
        root
        / ".ai-sdlc"
        / "state"
        / "trusted"
        / "projects"
        / "project.shared"
        / "snapshot-control"
        / "event-anchor.key"
    )
    if os.name == "nt":
        snapshot_trusted_files._prepare_trusted_directory(key_path.parent)
        with snapshot_windows_io._open_registered_directory(
            key_path.parent
        ) as directory_handle:
            handle = snapshot_windows_io._open_windows_relative(
                directory_handle,
                key_path.name,
                desired_access=0x40000000,
                creation_disposition=1,
            )
            snapshot_windows_io._close_windows_handle(handle)
    else:
        script = (
            "import os, pathlib, sys; "
            "path = pathlib.Path(sys.argv[1]); "
            "path.parent.mkdir(parents=True, exist_ok=True, mode=0o700); "
            "os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600); "
            "os._exit(0)"
        )
        subprocess.run([sys.executable, "-c", script, str(key_path)], check=True)
    assert key_path.stat().st_size == 0
    trusted = root / ".ai-sdlc" / "state" / "trusted"
    if os.name != "nt":
        for directory in (
            trusted,
            trusted / "projects",
            trusted / "projects" / "project.shared",
            key_path.parent,
        ):
            directory.chmod(0o700)

    anchor = SnapshotControlTrustAnchor(root, project_id="project.shared")

    assert len(anchor._key) == 32
    assert key_path.stat().st_size > 0


def test_trust_epoch_recovers_first_commit_before_project_anchor_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    anchor = SnapshotControlTrustAnchor(root, project_id="project.shared")
    commitment = {
        "event_key_digest": "sha256:first-event-key",
        "legacy_sequence": 0,
        "legacy_digest": "",
        "head_sequence": 0,
        "head_digest": "",
    }

    def crash_before_anchor_publish(epoch: dict[str, object]) -> None:
        raise RuntimeError("simulated crash before project anchor publish")

    monkeypatch.setattr(
        anchor.epoch_store,
        "_commit_anchor",
        crash_before_anchor_publish,
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        anchor.epoch_store._reconcile(commitment)

    reopened = SnapshotControlTrustAnchor(root, project_id="project.shared")
    reopened.epoch_store._reconcile(commitment)

    assert reopened.epoch_store._exists()
    assert reopened.epoch_store._read()["generation"] == 1


def test_trust_anchor_rejects_parent_symlink_outside_trusted_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    trusted = root / ".ai-sdlc" / "state" / "trusted"
    trusted.parent.mkdir(parents=True)
    if sys.platform == "win32":
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(trusted), str(external)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert created.returncode == 0, created.stderr
    else:
        trusted.symlink_to(external, target_is_directory=True)

    with pytest.raises(SharedStateIntegrityError, match="trusted.*unsafe"):
        SnapshotControlTrustAnchor(root, project_id="project.shared")


def test_trust_anchor_rejects_or_removes_unsafe_intermediate_permissions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    anchor = SnapshotControlTrustAnchor(root, project_id="project.shared")
    projects = anchor.root.parent.parent
    if sys.platform == "win32":
        granted = subprocess.run(
            ["icacls", str(projects), "/grant", "*S-1-1-0:(F)"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert granted.returncode == 0, granted.stderr
        SnapshotControlTrustAnchor(root, project_id="project.shared")
        verified = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$ErrorActionPreference='Stop'; "
                "$acl=[System.IO.Directory]::GetAccessControl("
                "$env:AI_SDLC_TEST_ACL_PATH); "
                "$rules=$acl.GetAccessRules($true,$true,"
                "[System.Security.Principal.SecurityIdentifier]); "
                "$ids=$rules | ForEach-Object { $_.IdentityReference.Value }; "
                "if ($ids -contains 'S-1-1-0') { exit 1 }",
            ],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "AI_SDLC_TEST_ACL_PATH": str(projects)},
        )
        assert verified.returncode == 0, verified.stderr
    else:
        projects.chmod(0o777)
        with pytest.raises(SharedStateIntegrityError, match="permissions.*unsafe"):
            SnapshotControlTrustAnchor(root, project_id="project.shared")


def _mock_windows_directory_handles(
    target: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle_paths = {40: Path(target.absolute().anchor)}
    next_handle = 40

    def open_relative_directory(
        parent_handle: int,
        name: str,
        *,
        create: bool,
        harden: bool,
    ) -> int:
        nonlocal next_handle
        del create, harden
        next_handle += 1
        handle_paths[next_handle] = handle_paths[parent_handle] / name
        return next_handle

    monkeypatch.setattr(
        snapshot_trusted_files,
        "_open_windows_path",
        lambda *_args, **_kwargs: 40,
    )
    monkeypatch.setattr(
        snapshot_trusted_files,
        "_open_windows_relative_directory",
        open_relative_directory,
    )
    monkeypatch.setattr(
        snapshot_trusted_files,
        "_windows_handle_metadata",
        lambda handle: ((1, hash(handle_paths[handle])), 0x10),
    )
    monkeypatch.setattr(
        snapshot_trusted_files,
        "_harden_windows_handle_acl",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        snapshot_trusted_files,
        "_close_windows_handle",
        lambda _: None,
    )


def test_windows_traversal_rejects_intermediate_directory_identity_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / ".ai-sdlc" / "state" / "trusted" / "projects" / "project.shared"

    def changing_identity(path: Path) -> tuple[int, int]:
        identity = hash(path) + (1 if path.name == "projects" else 0)
        return (1, identity)

    _mock_windows_directory_handles(target, monkeypatch)
    monkeypatch.setattr(
        snapshot_trusted_files,
        "_windows_path_identity",
        changing_identity,
        raising=False,
    )

    with pytest.raises(SharedStateIntegrityError, match="identity.*changed"):
        snapshot_trusted_files._prepare_windows_directory(target)


def test_registered_windows_directory_rejects_post_init_identity_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / ".ai-sdlc" / "state" / "trusted" / "projects" / "project.shared"
    changed = False

    def observed_identity(path: Path) -> tuple[int, int]:
        suffix = 99 if changed and path.name == "projects" else 1
        return (1, hash(path) + suffix - 1)

    _mock_windows_directory_handles(target, monkeypatch)
    monkeypatch.setattr(
        snapshot_trusted_files,
        "_windows_path_identity",
        observed_identity,
    )
    monkeypatch.setattr(
        snapshot_windows_io,
        "_windows_path_identity",
        observed_identity,
    )
    snapshot_trusted_files._prepare_windows_directory(target)
    changed = True

    with pytest.raises(SharedStateIntegrityError, match="identity.*changed"):
        snapshot_windows_io._verify_registered_directory(target)


if os.name != "nt":

    def test_trust_anchor_rejects_starting_directory_identity_change(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        external = tmp_path / "external"
        external.mkdir()
        backup = tmp_path / "project-original"
        original_boundary = snapshot_trusted_files._trusted_boundary
        swapped = False

        def swap_before_open(path: Path) -> Path:
            nonlocal swapped
            boundary = original_boundary(path)
            if not swapped:
                root.rename(backup)
                root.symlink_to(external, target_is_directory=True)
                swapped = True
            return boundary

        monkeypatch.setattr(
            snapshot_trusted_files,
            "_trusted_boundary",
            swap_before_open,
        )

        with pytest.raises(SharedStateIntegrityError, match="trusted.*unsafe"):
            SnapshotControlTrustAnchor(root, project_id="project.shared")


if os.name == "nt":

    def _set_windows_owner_to_administrators(path: Path) -> None:
        target_kind = "directory" if path.is_dir() else "file"
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$ErrorActionPreference='Stop'; "
                "$path=$env:AI_SDLC_TEST_ACL_PATH; "
                "$kind=$env:AI_SDLC_TEST_ACL_KIND; "
                "$acl=if($kind -eq 'directory'){"
                "[System.IO.Directory]::GetAccessControl($path)"
                "}else{[System.IO.File]::GetAccessControl($path)}; "
                "$owner=[System.Security.Principal.NTAccount]::new("
                "'BUILTIN','Administrators'); "
                "$acl.SetOwner($owner); "
                "if($kind -eq 'directory'){"
                "[System.IO.Directory]::SetAccessControl($path,$acl)"
                "}else{[System.IO.File]::SetAccessControl($path,$acl)}",
            ],
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "AI_SDLC_TEST_ACL_PATH": str(path),
                "AI_SDLC_TEST_ACL_KIND": target_kind,
            },
        )
        assert completed.returncode == 0, completed.stderr

    def test_trust_anchor_rejects_post_init_intermediate_junction(
        tmp_path: Path,
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        anchor = SnapshotControlTrustAnchor(root, project_id="project.shared")
        projects = anchor.root.parent.parent
        original_projects = projects.with_name("projects-original")
        external = tmp_path / "external"
        external.mkdir()
        projects.rename(original_projects)
        linked = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(projects), str(external)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert linked.returncode == 0, linked.stderr

        with pytest.raises(SharedStateIntegrityError, match="trusted.*unsafe"):
            SnapshotControlTrustAnchor(root, project_id="project.shared")

    @pytest.mark.parametrize("target_kind", ("directory", "file"))
    def test_trust_anchor_rejects_foreign_windows_owner(
        tmp_path: Path,
        target_kind: str,
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        anchor = SnapshotControlTrustAnchor(root, project_id="project.shared")
        target = (
            anchor.root.parent.parent
            if target_kind == "directory"
            else anchor.epoch_store.root / "snapshot-project-anchor.key"
        )
        _set_windows_owner_to_administrators(target)

        with pytest.raises(SharedStateIntegrityError, match="ACL.*unsafe"):
            SnapshotControlTrustAnchor(root, project_id="project.shared")

    def test_existing_anchor_rejects_all_file_operations_after_parent_junction(
        tmp_path: Path,
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        anchor = SnapshotControlTrustAnchor(root, project_id="project.shared")
        anchor.epoch_store._reconcile(
            {
                "event_key_digest": "sha256:test",
                "legacy_sequence": 0,
                "legacy_digest": "",
                "head_sequence": 0,
                "head_digest": "",
            }
        )
        projects = anchor.root.parent.parent
        original_projects = projects.with_name("projects-original")
        external = tmp_path / "external"
        shutil.copytree(projects, external)
        projects.rename(original_projects)
        linked = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(projects), str(external)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert linked.returncode == 0, linked.stderr

        def acquire_lock() -> None:
            with snapshot_trusted_files._secure_file_lock(
                anchor.root,
                ".existing-anchor.lock",
                timeout_seconds=0.1,
            ):
                pass

        operations = (
            lambda: snapshot_trusted_files._read_secure_file(
                anchor.epoch_store.root,
                "snapshot-control-epoch.json",
            ),
            lambda: snapshot_trusted_files._create_secure_file(
                anchor.root,
                "created.json",
                b"created",
            ),
            lambda: snapshot_trusted_files._replace_secure_file(
                anchor.root,
                "replaced.json",
                b"replaced",
            ),
            lambda: snapshot_trusted_files._unlink_secure_file(
                anchor.epoch_store.root,
                "snapshot-control-epoch.json",
            ),
            acquire_lock,
        )
        for operation in operations:
            with pytest.raises(SharedStateIntegrityError, match="identity.*changed"):
                operation()


def test_trust_anchor_does_not_rotate_incomplete_key_after_head_commit(
    tmp_path: Path,
) -> None:
    baseline = _snapshot("committed-head", is_baseline=True)
    service = _service(tmp_path, baseline)
    token = service.resolve_snapshot()
    service.bind_session(_binding("session.committed", token), token)
    key_path = service.store.trust_anchor.key_path
    assert service.store.trust_anchor.head_path.is_file()
    key_path.write_bytes(b"")

    with pytest.raises(SharedStateIntegrityError, match="key is invalid"):
        _service(tmp_path, baseline)


def _decision(
    baseline_digest: str,
    challenger_digest: str,
    candidate_digest: str,
    suffix: str,
    *,
    shadow_result_digest: str,
    evaluation_report_digests: tuple[str, ...],
) -> AutoPromotionDecision:
    return AutoPromotionGate(_promotion_policy()).evaluate(
        _promotion_evidence(
            baseline_digest=baseline_digest,
            challenger_digest=challenger_digest,
            candidate_digest=candidate_digest,
            shadow_result_digest=shadow_result_digest,
            evaluation_report_digests=evaluation_report_digests,
        ),
        decision_id=f"decision.{suffix}",
    )


def _is_expected_contention_result(message: str) -> bool:
    return any(
        code in message
        for code in (
            "snapshot_control_busy",
            "snapshot_control_safety_pending",
            "snapshot selection is stale",
        )
    )
