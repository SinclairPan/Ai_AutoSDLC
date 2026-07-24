from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ai_sdlc.core.source_snapshot import (
    SourceSnapshotOptions,
    build_source_snapshot,
)
from ai_sdlc.core.stage_review.activation import baseline_activation_policy
from ai_sdlc.core.stage_review.bindings import (
    build_runtime_allocation,
)
from ai_sdlc.core.stage_review.candidate import (
    CandidateBuildContext,
    build_candidate_manifest,
)
from ai_sdlc.core.stage_review.codex_provider_authority import (
    _codex_provider_descriptors,
)
from ai_sdlc.core.stage_review.codex_trusted_releases import (
    _trusted_published_codex_release_digests,
)
from ai_sdlc.core.stage_review.isolation_runtime_layout import (
    FilesystemAllocationPathResolver,
)
from ai_sdlc.core.stage_review.provider_transport import TrustedProviderTransport
from ai_sdlc.core.stage_review.provider_transport_trust import (
    _reviewer_transport_authority,
    _reviewer_transport_contract,
)


def candidate(root: Path, *, source_kind: str = "local-unstaged"):
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    (root / "candidate.py").write_text("VALUE = 1\n", encoding="utf-8")
    policy_path = root / ".ai-sdlc/policies/stage-gate-activation-policy.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(
        json.dumps(
            baseline_activation_policy().model_dump(mode="json"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _git(root, "add", "candidate.py", policy_path.relative_to(root).as_posix())
    _git(root, "commit", "-m", "base")
    base_commit = _git_output(root, "rev-parse", "HEAD")
    (root / "candidate.py").write_text("VALUE = 2\n", encoding="utf-8")
    options = SourceSnapshotOptions(root=root, source_kind="local-unstaged")
    if source_kind == "local-git-range":
        _git(root, "add", "candidate.py")
        _git(root, "commit", "-m", "reviewed implementation")
        options = SourceSnapshotOptions(
            root=root,
            source_kind="local-git-range",
            base_ref=base_commit,
            head_ref=_git_output(root, "rev-parse", "HEAD"),
        )
    snapshot = build_source_snapshot(options)
    context = CandidateBuildContext(
        work_item_id="work-item.one",
        project_id="project.shared",
        loop_id="implementation.integration",
        loop_round_number=1,
        stage_key="implementation",
        stage_instance_id="implementation",
        review_session_id="session.one",
        adapter_id="stage-candidate.implementation",
        adapter_version="1.0.0",
        adapter_contract_digest="sha256:adapter:implementation",
        input_artifacts=(),
        output_artifacts=(),
        test_evidence_digests=(),
        policy_digests=("sha256:policy",),
        toolchain_ids=("python",),
        target_platform_ids=("local",),
        protected_source_set=("candidate.py",),
    )
    return build_candidate_manifest(
        root=root,
        source_snapshot=snapshot,
        context=context,
    ), snapshot


def descriptors(plan):
    release_digest = _trusted_published_codex_release_digests()[0]
    return tuple(
        descriptor
        for slot in plan.proposal.required_slots
        for descriptor in _codex_provider_descriptors(slot, release_digest)
    )


def allocations(plan, authority, candidate_digest):
    by_role = {
        item.role_contract_digests[0]: item
        for item in authority.provider_descriptors
    }
    return tuple(
        build_runtime_allocation(
            allocation_id=f"allocation.{slot.slot_id}",
            slot_id=slot.slot_id,
            actor_id=f"actor.{slot.slot_id}",
            session_id=f"provider-session.{slot.slot_id}",
            provider_descriptor=by_role[slot.role_contract_digest],
            candidate_manifest_digest=candidate_digest,
            candidate_snapshot_id=f"snapshot.{slot.slot_id}",
            working_directory_id=f"cwd.{slot.slot_id}",
            disposable_home_id=f"home.{slot.slot_id}",
            disposable_config_id=f"config.{slot.slot_id}",
            disposable_credential_view_id=f"credential.{slot.slot_id}",
            output_directory_id=f"output.{slot.slot_id}",
            allocation_operation_id=f"operation.{slot.slot_id}",
        )
        for slot in plan.proposal.required_slots
    )


def runtime_paths(root, allocations, candidate_manifest, snapshot):
    protected = root / ".git/test-protected"
    protected.mkdir()
    config = protected / ".gitconfig"
    config.write_text("protected", encoding="utf-8")
    resolver = FilesystemAllocationPathResolver(
        root / ".git/ai-sdlc-runtime",
        protected_home_root=protected,
        protected_config_roots=(config,),
    )
    for allocation in allocations:
        resolver.materialize_candidate_snapshot(
            allocation,
            root,
            candidate=candidate_manifest,
            source_snapshot=snapshot,
        )
        resolver.provision_runtime(allocation)
    return resolver


def transport(root, project_id, broker, descriptor):
    authority = _reviewer_transport_authority(descriptor)
    contract = _reviewer_transport_contract(descriptor)
    return TrustedProviderTransport(
        root,
        contract,
        project_id=project_id,
        broker=broker,
        authority=authority,
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=root, capture_output=True, check=True)


def _git_output(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args),
        cwd=root,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
