from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_sdlc.core.source_snapshot import (
    SourceSnapshotOptions,
    build_source_snapshot,
)
from ai_sdlc.core.stage_review.binding_builders import (
    build_provider_binding_descriptor,
    build_runtime_allocation,
)
from ai_sdlc.core.stage_review.candidate import (
    CandidateBuildContext,
    _build_candidate_manifest,
    candidate_binding_digest,
)
from ai_sdlc.core.stage_review.isolation_layout_identity import _runtime_layout_digest
from ai_sdlc.core.stage_review.isolation_runtime_layout import (
    FilesystemAllocationPathResolver,
    IsolationRuntimeLayout,
)
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderRecoveryCapabilities,
)


def test_resolver_maps_opaque_allocation_ids_to_canonical_paths(tmp_path: Path) -> None:
    source, candidate, snapshot = _candidate_authority(tmp_path)
    allocation = _allocation(
        "../must-not-be-a-path", candidate_binding_digest(candidate)
    )
    resolver = FilesystemAllocationPathResolver(
        tmp_path / "trusted-layouts",
        protected_home_root=tmp_path / "protected-home",
        protected_config_roots=(tmp_path / "protected-home" / ".gitconfig",),
    )
    _materialize(resolver, allocation, source, candidate, snapshot)

    layout = resolver.resolve(
        allocation,
        peer_allocations=(),
        assignment_digest="sha256:assignment",
    )

    trusted_root = (tmp_path / "trusted-layouts").resolve()
    assert Path(layout.normalized_run_root).is_relative_to(trusted_root)
    assert "must-not-be-a-path" not in layout.normalized_run_root
    assert layout.allocation_digest == allocation.allocation_digest
    assert layout.assignment_digest == "sha256:assignment"


def test_calibration_candidate_contains_a_readable_probe_file(tmp_path: Path) -> None:
    from tests.unit.stage_review.test_isolation_execution import _api, _host

    from ai_sdlc.core.stage_review.codex_isolation_policy import (
        _calibration_context as calibration_context,
    )
    from ai_sdlc.core.stage_review.codex_isolation_policy import (
        _seed_calibration_targets as seed_calibration_targets,
    )
    from ai_sdlc.core.stage_review.isolation_launcher import IsolationLaunchContext

    source, candidate, snapshot = _candidate_authority(tmp_path)
    allocation = _allocation("cwd.opaque", candidate_binding_digest(candidate))
    resolver = FilesystemAllocationPathResolver(
        tmp_path / "trusted-layouts",
        protected_home_root=tmp_path / "protected-home",
        protected_config_roots=(tmp_path / "protected-home" / ".gitconfig",),
    )
    _materialize(resolver, allocation, source, candidate, snapshot)
    layout = resolver.resolve(
        allocation,
        peer_allocations=(),
        assignment_digest="sha256:assignment",
    )
    context = calibration_context(
        IsolationLaunchContext.from_layout(
            layout,
            host_snapshot=_host(_api()),
            adapter_grade="enforced",
        )
    )

    seed_calibration_targets(context)

    assert tuple(Path(context.candidate_root).glob("*"))


def test_runtime_layout_digest_rejects_path_tampering(tmp_path: Path) -> None:
    source, candidate, snapshot = _candidate_authority(tmp_path)
    allocation = _allocation("cwd.opaque", candidate_binding_digest(candidate))
    resolver = FilesystemAllocationPathResolver(
        tmp_path / "trusted-layouts",
        protected_home_root=tmp_path / "protected-home",
        protected_config_roots=(tmp_path / "protected-home" / ".gitconfig",),
    )
    _materialize(resolver, allocation, source, candidate, snapshot)
    layout = resolver.resolve(
        allocation,
        peer_allocations=(),
        assignment_digest="sha256:assignment",
    )
    payload = layout.model_dump(mode="json")
    payload["candidate_root"] = str(tmp_path / "swapped-candidate")

    with pytest.raises(ValidationError, match="layout digest"):
        IsolationRuntimeLayout.model_validate(payload)


def test_runtime_layout_digest_is_stable_across_resolution_times(
    tmp_path: Path,
) -> None:
    source, candidate, snapshot = _candidate_authority(tmp_path)
    allocation = _allocation("cwd.opaque", candidate_binding_digest(candidate))
    resolver = FilesystemAllocationPathResolver(
        tmp_path / "trusted-layouts",
        protected_home_root=tmp_path / "protected-home",
        protected_config_roots=(tmp_path / "protected-home" / ".gitconfig",),
    )
    _materialize(resolver, allocation, source, candidate, snapshot)
    first = resolver.resolve(
        allocation,
        peer_allocations=(),
        assignment_digest="sha256:assignment",
    )
    draft = first.model_copy(
        update={"created_at": "2099-01-01T00:00:00Z", "layout_digest": ""}
    )
    repeated = IsolationRuntimeLayout.model_validate(
        draft.model_copy(update={"layout_digest": _runtime_layout_digest(draft)})
    )

    assert repeated.created_at != first.created_at
    assert repeated.layout_digest == first.layout_digest


def test_resolver_rejects_unmaterialized_candidate_snapshot(tmp_path: Path) -> None:
    _, candidate, _ = _candidate_authority(tmp_path)
    allocation = _allocation("cwd.opaque", candidate_binding_digest(candidate))
    resolver = FilesystemAllocationPathResolver(
        tmp_path / "trusted-layouts",
        protected_home_root=tmp_path / "protected-home",
        protected_config_roots=(tmp_path / "protected-home" / ".gitconfig",),
    )

    with pytest.raises(ValueError, match="candidate snapshot"):
        resolver.resolve(
            allocation,
            peer_allocations=(),
            assignment_digest="sha256:assignment",
        )


def test_resolver_detects_materialized_candidate_tampering(tmp_path: Path) -> None:
    source, candidate, snapshot = _candidate_authority(tmp_path)
    allocation = _allocation("cwd.opaque", candidate_binding_digest(candidate))
    resolver = FilesystemAllocationPathResolver(
        tmp_path / "trusted-layouts",
        protected_home_root=tmp_path / "protected-home",
        protected_config_roots=(tmp_path / "protected-home" / ".gitconfig",),
    )
    materialized = _materialize(
        resolver, allocation, source, candidate, snapshot
    )
    (materialized / "candidate.txt").write_text("tampered", encoding="utf-8")

    with pytest.raises(ValueError, match="candidate snapshot"):
        resolver.resolve(
            allocation,
            peer_allocations=(),
            assignment_digest="sha256:assignment",
        )


def test_materializer_rejects_valid_manifest_with_swapped_source_tree(
    tmp_path: Path,
) -> None:
    source, candidate, snapshot = _candidate_authority(tmp_path)
    allocation = _allocation("cwd.opaque", candidate_binding_digest(candidate))
    (source / "candidate.txt").write_text("swapped", encoding="utf-8")
    resolver = FilesystemAllocationPathResolver(
        tmp_path / "trusted-layouts",
        protected_home_root=tmp_path / "protected-home",
        protected_config_roots=(tmp_path / "protected-home" / ".gitconfig",),
    )

    with pytest.raises(ValueError, match="source tree"):
        resolver.materialize_candidate_snapshot(
            allocation,
            source,
            candidate=candidate,
            source_snapshot=snapshot,
        )


def test_materializer_excludes_files_added_after_bound_source_snapshot(
    tmp_path: Path,
) -> None:
    source, candidate, snapshot = _candidate_authority(tmp_path)
    allocation = _allocation("cwd.opaque", candidate_binding_digest(candidate))
    (source / "late-untracked-secret.txt").write_text("secret", encoding="utf-8")
    review_root = source / candidate.review_artifact_exclusion_set[0]
    review_root.mkdir(parents=True)
    (review_root / "review-output.json").write_text("review", encoding="utf-8")
    peer = source / ".ai-sdlc" / "reviews" / "peer-output.json"
    peer.parent.mkdir(parents=True, exist_ok=True)
    peer.write_text("peer", encoding="utf-8")
    resolver = FilesystemAllocationPathResolver(
        tmp_path / "trusted-layouts",
        protected_home_root=tmp_path / "protected-home",
        protected_config_roots=(tmp_path / "protected-home" / ".gitconfig",),
    )

    materialized = resolver.materialize_candidate_snapshot(
        allocation,
        source,
        candidate=candidate,
        source_snapshot=snapshot,
    )

    assert (materialized / "candidate.txt").is_file()
    assert not (materialized / "late-untracked-secret.txt").exists()
    assert not (materialized / candidate.review_artifact_exclusion_set[0]).exists()
    assert not (materialized / ".ai-sdlc" / "reviews" / "peer-output.json").exists()


@pytest.mark.parametrize("link_kind", ("absolute", "relative", "broken"))
def test_materializer_rejects_frozen_view_symlinks_and_cleans_partial_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    link_kind: str,
) -> None:
    from contextlib import contextmanager

    from ai_sdlc.core.stage_review import isolation_runtime_layout as layout_api

    source, candidate, snapshot = _candidate_authority(tmp_path)
    allocation = _allocation("cwd.opaque", candidate_binding_digest(candidate))
    frozen = tmp_path / "frozen-view"
    frozen.mkdir()
    outside = tmp_path / "outside-secret"
    outside.write_text("secret", encoding="utf-8")
    target = {
        "absolute": outside,
        "relative": Path("../outside-secret"),
        "broken": Path("missing-target"),
    }[link_kind]
    (frozen / "unsafe-link").symlink_to(target)

    @contextmanager
    def fake_view(source_root, source_snapshot):
        yield frozen

    monkeypatch.setattr(layout_api, "materialized_source_view", fake_view)
    resolver = FilesystemAllocationPathResolver(
        tmp_path / "trusted-layouts",
        protected_home_root=tmp_path / "protected-home",
        protected_config_roots=(tmp_path / "protected-home" / ".gitconfig",),
    )

    with pytest.raises(ValueError, match="symlink"):
        resolver.materialize_candidate_snapshot(
            allocation,
            source,
            candidate=candidate,
            source_snapshot=snapshot,
        )

    candidate_parent = tmp_path / "trusted-layouts" / "candidates"
    assert not candidate_parent.exists() or not tuple(candidate_parent.iterdir())


def _materialize(resolver, allocation, source, candidate, snapshot) -> Path:
    return resolver.materialize_candidate_snapshot(
        allocation,
        source,
        candidate=candidate,
        source_snapshot=snapshot,
    )


def _candidate_authority(root: Path):
    source = root / "source-candidate"
    source.mkdir(exist_ok=True)
    _git(source, "init")
    _git(source, "config", "user.name", "AI SDLC Test")
    _git(source, "config", "user.email", "test@example.invalid")
    (source / "candidate.txt").write_text("base", encoding="utf-8")
    _git(source, "add", "candidate.txt")
    _git(source, "commit", "-m", "base")
    (source / "candidate.txt").write_text("candidate", encoding="utf-8")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=source, source_kind="local-unstaged")
    )
    context = CandidateBuildContext(
        work_item_id="work-item",
        project_id="project",
        loop_id="loop",
        loop_round_number=1,
        stage_key="implementation",
        stage_instance_id="stage",
        review_session_id="review",
        adapter_id="stage-candidate.implementation",
        adapter_version="1.0.0",
        adapter_contract_digest="sha256:adapter:implementation",
        input_artifacts=(),
        output_artifacts=(),
        test_evidence_digests=(),
        policy_digests=(),
        toolchain_ids=(),
        target_platform_ids=(),
        protected_source_set=("candidate.txt",),
    )
    return source, _build_candidate_manifest(source, snapshot, context), snapshot


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ("git", *args),
        cwd=root,
        capture_output=True,
        check=True,
    )


def _allocation(working_directory_id: str, candidate_digest: str):
    descriptor = build_provider_binding_descriptor(
        descriptor_id="descriptor.codex",
        provider_id="provider.codex",
        equivalence_class_id="class.codex",
        model_family="gpt-5",
        role_contract_digests=("sha256:role",),
        capability_ids=("agent_execution",),
        provider_tags=(),
        tool_allowlist=(),
        recovery_capabilities=ProviderRecoveryCapabilities(
            idempotency_support=True,
            invocation_query_support=True,
            cost_metering_support=True,
        ),
        isolation_backend="codex.permission-profile",
        network_enforcement=True,
        supported_independence_grade="model_diversity_proven",
        provider_policy_evidence_digest="sha256:policy",
    )
    return build_runtime_allocation(
        allocation_id="allocation.one",
        slot_id="slot.one",
        actor_id="actor.one",
        session_id="session.one",
        provider_descriptor=descriptor,
        candidate_manifest_digest=candidate_digest,
        candidate_snapshot_id="snapshot.one",
        working_directory_id=working_directory_id,
        disposable_home_id="home.one",
        disposable_config_id="config.one",
        disposable_credential_view_id="credential.one",
        output_directory_id="output.one",
        allocation_operation_id="operation.one",
    )
