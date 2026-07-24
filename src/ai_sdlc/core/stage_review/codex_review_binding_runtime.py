"""Codex Reviewer 的 Binding、Allocation 与隔离事实组合。"""

from __future__ import annotations

from pathlib import Path

from ai_sdlc.core.stage_review.binding_models import (
    BindingAuthoritySnapshot,
    HostCapabilitySnapshot,
    IsolationExecutionEvidence,
    ProviderBindingDescriptor,
    ReviewerRuntimeAllocation,
)
from ai_sdlc.core.stage_review.binding_policy import BindingPolicy
from ai_sdlc.core.stage_review.bindings import (
    ReviewerBindingService,
    build_binding_authority_snapshot,
    build_isolation_execution_evidence,
    build_runtime_allocation,
)
from ai_sdlc.core.stage_review.candidate import candidate_binding_digest
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.codex_isolation_host_probe import (
    CodexIsolationHostProbe,
)
from ai_sdlc.core.stage_review.codex_provider_authority import (
    _codex_provider_descriptors,
)
from ai_sdlc.core.stage_review.isolation_backend_identity import (
    TrustedBackendReleaseManifest,
)
from ai_sdlc.core.stage_review.isolation_runtime_layout import (
    FilesystemAllocationPathResolver,
)
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan, ReviewerSlot
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.stage_review_execution import (
    StageReviewExecutionRequest,
)


class _AuthorityResolver:
    def __init__(self, authority: BindingAuthoritySnapshot) -> None:
        self._authority = authority

    def resolve(self, plan: ReviewerPanelPlan) -> BindingAuthoritySnapshot:
        if plan.plan_digest != self._authority.plan_digest:
            raise ValueError("Codex authority plan binding changed")
        return self._authority


class _RuntimeBroker:
    def __init__(
        self,
        root: Path,
        request: StageReviewExecutionRequest,
        paths: FilesystemAllocationPathResolver,
    ) -> None:
        self._root = root
        self._request = request
        self._paths = paths

    def allocate(
        self,
        operation_id: str,
        plan: ReviewerPanelPlan,
        authority: BindingAuthoritySnapshot,
    ) -> tuple[ReviewerRuntimeAllocation, ...]:
        descriptors = {
            item.role_contract_digests[0]: item
            for item in authority.provider_descriptors
        }
        allocations = tuple(
            _allocation(
                operation_id,
                slot,
                descriptors[slot.role_contract_digest],
                candidate_binding_digest(self._request.candidate),
            )
            for slot in plan.proposal.required_slots
        )
        for allocation in allocations:
            self._paths.materialize_candidate_snapshot(
                allocation,
                self._root,
                candidate=self._request.candidate,
                source_snapshot=self._request.source_snapshot,
            )
            self._paths.provision_runtime(allocation)
        return allocations


class _IsolationAdapter:
    def prepare(
        self,
        operation_id: str,
        allocations: tuple[ReviewerRuntimeAllocation, ...],
        host_snapshot: HostCapabilitySnapshot,
        visibility_barrier_id: str,
    ) -> tuple[IsolationExecutionEvidence, ...]:
        enforced = _host_enforces_review_boundary(host_snapshot)
        return tuple(
            _isolation_evidence(
                operation_id,
                item,
                host_snapshot,
                visibility_barrier_id,
                enforced,
            )
            for item in allocations
        )


def build_codex_binding_service(
    root: Path,
    request: StageReviewExecutionRequest,
    paths: FilesystemAllocationPathResolver,
    *,
    executable: str,
    release: TrustedBackendReleaseManifest,
) -> tuple[ReviewerBindingService, BindingAuthoritySnapshot]:
    binding_policy = _binding_policy(request)
    authority = _authority(request, release)
    service = ReviewerBindingService(
        root,
        project_id=request.candidate.project_id,
        resource_governor=request.governor,
        authority_resolver=_AuthorityResolver(authority),
        host_probe=CodexIsolationHostProbe(executable, release_manifest=release),
        runtime_broker=_RuntimeBroker(root, request, paths),
        isolation_adapter=_IsolationAdapter(),
        binding_policy=binding_policy,
    )
    return service, authority


def _binding_policy(request: StageReviewExecutionRequest) -> BindingPolicy:
    payload = request.proposal.optimization_snapshot.policy_payload.get(
        "binding_policy"
    )
    return BindingPolicy.model_validate(payload)


def _authority(
    request: StageReviewExecutionRequest,
    release: TrustedBackendReleaseManifest,
) -> BindingAuthoritySnapshot:
    descriptors = tuple(
        descriptor
        for slot in request.plan.proposal.required_slots
        for descriptor in _codex_provider_descriptors(slot, release.manifest_digest)
    )
    return build_binding_authority_snapshot(
        plan=request.plan,
        risk_level=request.proposal.risk_profile.risk_level,
        enforcement_mode="enforce",
        provider_descriptors=descriptors,
        attestor_id="ai-sdlc.codex-runtime",
        attestor_version="1.0.0",
        attestation_evidence_digest=release.manifest_digest,
    )
def _allocation(
    operation_id: str,
    slot: ReviewerSlot,
    descriptor: ProviderBindingDescriptor,
    candidate_digest: str,
) -> ReviewerRuntimeAllocation:
    identity = stable_id("codex-runtime", operation_id, slot.slot_id)
    return build_runtime_allocation(
        allocation_id=f"allocation.{identity}",
        slot_id=slot.slot_id,
        actor_id=f"actor.{identity}",
        session_id=f"provider-session.{identity}",
        provider_descriptor=descriptor,
        candidate_manifest_digest=candidate_digest,
        candidate_snapshot_id=f"candidate-snapshot.{identity}",
        working_directory_id=f"cwd.{identity}",
        disposable_home_id=f"home.{identity}",
        disposable_config_id=f"config.{identity}",
        disposable_credential_view_id=f"credential.{identity}",
        output_directory_id=f"output.{identity}",
        allocation_operation_id=f"operation.{identity}",
    )


def _isolation_evidence(
    operation_id: str,
    allocation: ReviewerRuntimeAllocation,
    host: HostCapabilitySnapshot,
    barrier_id: str,
    enforced: bool,
) -> IsolationExecutionEvidence:
    return build_isolation_execution_evidence(
        operation_id=operation_id,
        allocation=allocation,
        host_snapshot=host,
        visibility_barrier_id=barrier_id,
        isolation_grade="enforced" if enforced else "unproven",
        isolation_backend="codex.permission-profile",
        candidate_snapshot_isolated=enforced,
        candidate_write_enforced=enforced,
        peer_outputs_hidden=enforced,
        disposable_home=enforced,
        disposable_config=enforced,
        disposable_credentials=enforced,
        output_isolated=enforced,
        user_home_protected=enforced,
        global_config_protected=enforced,
        network_policy_enforced=enforced,
        sentinel_environment_disposable=enforced,
        evidence_bundle_digest=_isolation_evidence_digest(
            allocation,
            host,
            barrier_id,
        ),
    )


def _host_enforces_review_boundary(host: HostCapabilitySnapshot) -> bool:
    required = {
        "agent_execution",
        "isolation.codex.permission-profile",
        "network_enforcement.codex.permission-profile",
    }
    return bool(
        required <= set(host.capability_ids)
        and host.backend_release_manifest_digest
        and host.backend_runtime_identity_digest
    )


def _isolation_evidence_digest(
    allocation: ReviewerRuntimeAllocation,
    host: HostCapabilitySnapshot,
    barrier_id: str,
) -> str:
    return canonical_digest(
        {
            "allocation_digest": allocation.allocation_digest,
            "host_snapshot_digest": host.snapshot_digest,
            "visibility_barrier_id": barrier_id,
        },
        CanonicalizationPolicy(),
    )


__all__ = ["build_codex_binding_service"]
