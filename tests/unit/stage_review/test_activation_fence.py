from __future__ import annotations

import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import ai_sdlc.core.stage_review.activation_fence as activation_fence
import ai_sdlc.core.stage_review.artifacts as stage_review_artifacts


def _complete_lease_while_process_stays_alive(
    root: str,
    project_id: str,
    lease_kind: str,
    completed,
    release_process,
) -> None:
    original_unlink = activation_fence._unlink_with_retry
    activation_fence._unlink_with_retry = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        PermissionError(13, "simulated cleanup sharing violation")
    )
    try:
        lease = (
            activation_fence.activation_safety_read_lease(Path(root), project_id)
            if lease_kind == "read"
            else activation_fence.activation_safety_mutation_fence(
                Path(root),
                project_id,
            )
        )
        with lease:
            pass
        completed.set()
        release_process.wait(timeout=30)
    finally:
        activation_fence._unlink_with_retry = original_unlink


def _hold_lease_until_released(
    root: str,
    project_id: str,
    lease_kind: str,
    ready,
    release_process,
) -> None:
    lease = (
        activation_fence.activation_safety_read_lease(Path(root), project_id)
        if lease_kind == "read"
        else activation_fence.activation_safety_mutation_fence(
            Path(root),
            project_id,
        )
    )
    with lease:
        ready.set()
        release_process.wait(timeout=30)


@pytest.mark.parametrize("lease_kind", ["read", "mutation"])
@pytest.mark.parametrize("body_fails", [False, True])
def test_completed_live_worker_lease_is_reclaimable_after_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lease_kind: str,
    body_fails: bool,
) -> None:
    project_id = "project.activation-fence-cleanup"
    original_unlink = activation_fence._unlink_with_retry

    def fail_cleanup(path: Path, *, missing_ok: bool) -> bool:
        raise PermissionError(path)

    monkeypatch.setattr(activation_fence, "_unlink_with_retry", fail_cleanup)

    def complete_lease() -> None:
        lease = (
            activation_fence.activation_safety_read_lease(tmp_path, project_id)
            if lease_kind == "read"
            else activation_fence.activation_safety_mutation_fence(
                tmp_path,
                project_id,
            )
        )
        with lease:
            if body_fails:
                raise ValueError("primary failure")

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(complete_lease)
        if body_fails:
            with pytest.raises(ValueError, match="primary failure") as caught:
                future.result(timeout=5)
            assert any(
                "activation safety marker cleanup was deferred" in note
                for note in getattr(caught.value, "__notes__", ())
            )
        else:
            assert future.result(timeout=5) is None
        monkeypatch.setattr(
            activation_fence,
            "_unlink_with_retry",
            original_unlink,
        )
        fence_root = activation_fence._fence_root(tmp_path, project_id)
        marker = (
            next((fence_root / "readers").glob("*.json"))
            if lease_kind == "read"
            else fence_root / "writer-intent.lock"
        )

        assert marker.is_file()
        assert activation_fence._clear_stale_owner(marker) is True
        assert marker.exists() is False


@pytest.mark.parametrize("lease_kind", ["read", "mutation"])
def test_completed_live_process_lease_is_reclaimable_after_cleanup_failure(
    tmp_path: Path,
    lease_kind: str,
) -> None:
    project_id = "project.activation-fence-cross-process-cleanup"
    context = multiprocessing.get_context("spawn")
    completed = context.Event()
    release_process = context.Event()
    process = context.Process(
        target=_complete_lease_while_process_stays_alive,
        args=(
            str(tmp_path),
            project_id,
            lease_kind,
            completed,
            release_process,
        ),
    )
    process.start()
    try:
        assert completed.wait(timeout=10)
        assert process.is_alive()
        fence_root = activation_fence._fence_root(tmp_path, project_id)
        marker = (
            next((fence_root / "readers").glob("*.json"))
            if lease_kind == "read"
            else fence_root / "writer-intent.lock"
        )

        assert activation_fence._clear_stale_owner(marker) is True
        assert marker.exists() is False
    finally:
        release_process.set()
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert process.exitcode == 0


@pytest.mark.parametrize("lease_kind", ["read", "mutation"])
def test_marker_indexes_deferred_owner_lock_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lease_kind: str,
) -> None:
    project_id = "project.activation-fence-owner-lock-retry"
    original_unlink = stage_review_artifacts._unlink_with_retry

    def fail_owner_lock_cleanup(path: Path, *, missing_ok: bool) -> bool:
        if path.parent.name == "owner-locks":
            raise PermissionError(path)
        return original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(
        stage_review_artifacts,
        "_unlink_with_retry",
        fail_owner_lock_cleanup,
    )
    lease_count = 5 if lease_kind == "read" else 1
    for _ in range(lease_count):
        lease = (
            activation_fence.activation_safety_read_lease(tmp_path, project_id)
            if lease_kind == "read"
            else activation_fence.activation_safety_mutation_fence(
                tmp_path,
                project_id,
            )
        )
        with lease:
            pass

    fence_root = activation_fence._fence_root(tmp_path, project_id)
    owner_root = fence_root / "owner-locks"
    markers = (
        tuple((fence_root / "readers").glob("*.json"))
        if lease_kind == "read"
        else (fence_root / "writer-intent.lock",)
    )
    assert all(marker.is_file() for marker in markers)
    assert len(tuple(owner_root.glob("*.lock"))) == lease_count

    monkeypatch.setattr(
        stage_review_artifacts,
        "_unlink_with_retry",
        original_unlink,
    )
    recovery_lease = (
        activation_fence.activation_safety_read_lease(tmp_path, project_id)
        if lease_kind == "read"
        else activation_fence.activation_safety_mutation_fence(
            tmp_path,
            project_id,
        )
    )
    with recovery_lease:
        pass

    assert tuple(owner_root.glob("*.lock")) == ()
    assert tuple((fence_root / "readers").glob("*.json")) == ()
    assert (fence_root / "writer-intent.lock").exists() is False


@pytest.mark.parametrize("lease_kind", ["read", "mutation"])
def test_stale_recovery_keeps_live_process_owner_lock(
    tmp_path: Path,
    lease_kind: str,
) -> None:
    project_id = "project.activation-fence-live-owner-lock"
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release_process = context.Event()
    process = context.Process(
        target=_hold_lease_until_released,
        args=(
            str(tmp_path),
            project_id,
            lease_kind,
            ready,
            release_process,
        ),
    )
    process.start()
    try:
        assert ready.wait(timeout=10)
        fence_root = activation_fence._fence_root(tmp_path, project_id)
        owner_locks = tuple((fence_root / "owner-locks").glob("*.lock"))
        assert len(owner_locks) == 1
        marker = (
            next((fence_root / "readers").glob("*.json"))
            if lease_kind == "read"
            else fence_root / "writer-intent.lock"
        )

        assert activation_fence._clear_stale_owner(marker) is False

        assert owner_locks[0].is_file()
        assert marker.is_file()
        assert process.is_alive()
    finally:
        release_process.set()
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert process.exitcode == 0
