"""Binding Authority、Host、运行实例与 Attempt 构建器。"""

from __future__ import annotations

from collections.abc import Mapping

from ai_sdlc.core.stage_review.binding_availability_models import (
    ProviderAvailabilityAttestation,
)
from ai_sdlc.core.stage_review.binding_digests import (
    binding_attempt_operation_digest,
    binding_attempt_request_digest,
    binding_authority_digest,
    host_capability_digest,
    isolation_evidence_digest,
    provider_descriptor_digest,
    runtime_allocation_digest,
)
from ai_sdlc.core.stage_review.binding_models import (
    BindingAttemptOperation,
    BindingAttemptRequest,
    BindingAuthoritySnapshot,
    HostCapabilitySnapshot,
    IndependenceGrade,
    IsolationExecutionEvidence,
    IsolationGrade,
    ProviderBindingDescriptor,
    RebindReason,
    ReviewerRuntimeAllocation,
)
from ai_sdlc.core.stage_review.contracts import RiskSeverity
from ai_sdlc.core.stage_review.panel_models import EnforcementMode
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderRecoveryCapabilities,
)
from ai_sdlc.core.stage_review.provider_route_models import (
    ProviderExecutionRoute,
    _unavailable_provider_execution_route,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts


def build_provider_binding_descriptor(
    *,
    descriptor_id: str,
    provider_id: str,
    equivalence_class_id: str,
    model_family: str,
    role_contract_digests: tuple[str, ...],
    capability_ids: tuple[str, ...],
    provider_tags: tuple[str, ...],
    tool_allowlist: tuple[str, ...],
    recovery_capabilities: ProviderRecoveryCapabilities,
    execution_route: ProviderExecutionRoute | None = None,
    isolation_backend: str,
    network_enforcement: bool,
    supported_independence_grade: IndependenceGrade,
    provider_policy_evidence_digest: str,
) -> ProviderBindingDescriptor:
    values = {
        "descriptor_id": descriptor_id,
        "provider_id": provider_id,
        "equivalence_class_id": equivalence_class_id,
        "model_family": model_family,
        "role_contract_digests": tuple(sorted(set(role_contract_digests))),
        "capability_ids": tuple(sorted(set(capability_ids))),
        "provider_tags": tuple(sorted(set(provider_tags))),
        "tool_allowlist": tuple(sorted(set(tool_allowlist))),
        "recovery_capabilities": recovery_capabilities,
        "execution_route": execution_route
        or _unavailable_provider_execution_route(provider_id, equivalence_class_id),
        "isolation_backend": isolation_backend,
        "network_enforcement": network_enforcement,
        "supported_independence_grade": supported_independence_grade,
        "provider_policy_evidence_digest": provider_policy_evidence_digest,
    }
    draft = ProviderBindingDescriptor.model_construct(
        **values,  # type: ignore[arg-type]
        descriptor_digest="",
    )
    return ProviderBindingDescriptor.model_validate(
        {**values, "descriptor_digest": provider_descriptor_digest(draft)}
    )


def build_binding_authority_snapshot(
    *,
    plan: ReviewerPanelPlan,
    risk_level: RiskSeverity,
    enforcement_mode: EnforcementMode,
    provider_descriptors: tuple[ProviderBindingDescriptor, ...],
    attestor_id: str,
    attestor_version: str,
    attestation_evidence_digest: str,
) -> BindingAuthoritySnapshot:
    descriptors = tuple(
        sorted(provider_descriptors, key=lambda item: item.descriptor_digest)
    )
    snapshot_id = stable_id(
        "binding-authority",
        plan.plan_digest,
        plan.proposal.optimization_snapshot_digest,
    )
    values = {
        "snapshot_id": snapshot_id,
        "plan_digest": plan.plan_digest,
        "plan_finalization_digest": plan.finalization_digest,
        "request_digest": plan.proposal.request_digest,
        "optimization_snapshot_digest": plan.proposal.optimization_snapshot_digest,
        "registry_digest": plan.proposal.registry_digest,
        "selection_policy_digest": plan.proposal.selection_policy_digest,
        "risk_level": risk_level,
        "enforcement_mode": enforcement_mode,
        "provider_descriptors": descriptors,
        "attestor_id": attestor_id,
        "attestor_version": attestor_version,
        "attestation_evidence_digest": attestation_evidence_digest,
    }
    draft = BindingAuthoritySnapshot.model_construct(
        **values,  # type: ignore[arg-type]
        snapshot_digest="",
    )
    return BindingAuthoritySnapshot.model_validate(
        {**values, "snapshot_digest": binding_authority_digest(draft)}
    )


def build_host_capability_snapshot(
    *,
    host_adapter_id: str,
    host_adapter_version: str,
    host_session_id: str,
    capability_ids: tuple[str, ...],
    capability_source: str,
    evidence_digest: str,
    backend_id: str = "",
    backend_contract_version: str = "",
    backend_release_manifest_digest: str = "",
    backend_runtime_identity_digest: str = "",
    previous_snapshot_digest: str,
    authorization_transition: str,
    issued_at: str,
    expires_at: str,
) -> HostCapabilitySnapshot:
    snapshot_id = stable_id(
        "host-capability",
        host_adapter_id,
        host_session_id,
        evidence_digest,
        expires_at,
    )
    values = {
        "snapshot_id": snapshot_id,
        "host_adapter_id": host_adapter_id,
        "host_adapter_version": host_adapter_version,
        "host_session_id": host_session_id,
        "capability_ids": tuple(sorted(set(capability_ids))),
        "capability_source": capability_source,
        "evidence_digest": evidence_digest,
        "backend_id": backend_id,
        "backend_contract_version": backend_contract_version,
        "backend_release_manifest_digest": backend_release_manifest_digest,
        "backend_runtime_identity_digest": backend_runtime_identity_digest,
        "previous_snapshot_digest": previous_snapshot_digest,
        "authorization_transition": authorization_transition,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    draft = HostCapabilitySnapshot.model_construct(
        **values,  # type: ignore[arg-type]
        snapshot_digest="",
    )
    return HostCapabilitySnapshot.model_validate(
        {**values, "snapshot_digest": host_capability_digest(draft)}
    )


def build_runtime_allocation(
    *,
    allocation_id: str,
    slot_id: str,
    actor_id: str,
    session_id: str,
    provider_descriptor: ProviderBindingDescriptor,
    candidate_manifest_digest: str,
    candidate_snapshot_id: str,
    working_directory_id: str,
    disposable_home_id: str,
    disposable_config_id: str,
    disposable_credential_view_id: str,
    output_directory_id: str,
    allocation_operation_id: str,
) -> ReviewerRuntimeAllocation:
    values = {
        "allocation_id": allocation_id,
        "slot_id": slot_id,
        "actor_id": actor_id,
        "session_id": session_id,
        "provider_descriptor_digest": provider_descriptor.descriptor_digest,
        "provider_id": provider_descriptor.provider_id,
        "equivalence_class_id": provider_descriptor.equivalence_class_id,
        "physical_provider_id": provider_descriptor.execution_route.physical_provider_id,
        "physical_equivalence_class_id": (
            provider_descriptor.execution_route.physical_equivalence_class_id
        ),
        "model_family": provider_descriptor.model_family,
        "candidate_manifest_digest": candidate_manifest_digest,
        "candidate_snapshot_id": candidate_snapshot_id,
        "working_directory_id": working_directory_id,
        "disposable_home_id": disposable_home_id,
        "disposable_config_id": disposable_config_id,
        "disposable_credential_view_id": disposable_credential_view_id,
        "output_directory_id": output_directory_id,
        "allocation_operation_id": allocation_operation_id,
    }
    draft = ReviewerRuntimeAllocation.model_construct(
        **values,  # type: ignore[arg-type]
        allocation_digest="",
    )
    return ReviewerRuntimeAllocation.model_validate(
        {**values, "allocation_digest": runtime_allocation_digest(draft)}
    )


def build_isolation_execution_evidence(
    *,
    operation_id: str,
    allocation: ReviewerRuntimeAllocation,
    host_snapshot: HostCapabilitySnapshot,
    visibility_barrier_id: str,
    isolation_grade: IsolationGrade,
    isolation_backend: str,
    candidate_snapshot_isolated: bool,
    candidate_write_enforced: bool,
    peer_outputs_hidden: bool,
    disposable_home: bool,
    disposable_config: bool,
    disposable_credentials: bool,
    output_isolated: bool,
    user_home_protected: bool,
    global_config_protected: bool,
    network_policy_enforced: bool,
    sentinel_environment_disposable: bool,
    evidence_bundle_digest: str,
) -> IsolationExecutionEvidence:
    evidence_id = stable_id(
        "isolation-evidence",
        operation_id,
        allocation.allocation_digest,
        host_snapshot.snapshot_digest,
    )
    values = locals() | {
        "evidence_id": evidence_id,
        "allocation_digest": allocation.allocation_digest,
        "host_snapshot_digest": host_snapshot.snapshot_digest,
    }
    values.pop("allocation")
    values.pop("host_snapshot")
    draft = IsolationExecutionEvidence.model_construct(
        **values,
        isolation_evidence_digest="",
    )
    return IsolationExecutionEvidence.model_validate(
        {
            **values,
            "isolation_evidence_digest": isolation_evidence_digest(draft),
        }
    )


def build_binding_attempt_request(
    *,
    plan: ReviewerPanelPlan,
    final_reservation: ResourceReservation,
    candidate_manifest_digest: str,
    input_packet_digest: str,
    visibility_barrier_id: str,
    attempt_index: int,
    previous_binding_set_digest: str,
    expected_cohort_id: str,
    expected_pass_head_digest: str,
    rebind_reason: RebindReason,
    availability_attestation: ProviderAvailabilityAttestation | None,
) -> BindingAttemptRequest:
    operation_id = stable_id(
        "binding-attempt",
        final_reservation.stage_review_session_id,
        str(attempt_index),
    )
    supplied = {
        "candidate_manifest_digest": candidate_manifest_digest,
        "input_packet_digest": input_packet_digest,
        "visibility_barrier_id": visibility_barrier_id,
        "previous_binding_set_digest": previous_binding_set_digest,
        "expected_cohort_id": expected_cohort_id,
        "expected_pass_head_digest": expected_pass_head_digest,
        "rebind_reason": rebind_reason,
    }
    values = _binding_request_values(
        plan,
        final_reservation,
        operation_id,
        attempt_index,
        supplied,
        availability_attestation,
    )
    draft = BindingAttemptRequest.model_construct(
        **values,  # type: ignore[arg-type]
        request_digest="",
    )
    return BindingAttemptRequest.model_validate(
        {**values, "request_digest": binding_attempt_request_digest(draft)}
    )


def _binding_request_values(
    plan: ReviewerPanelPlan,
    reservation: ResourceReservation,
    operation_id: str,
    attempt_index: int,
    supplied: Mapping[str, object],
    availability: ProviderAvailabilityAttestation | None,
) -> dict[str, object]:
    return {
        "request_id": stable_id("binding-request", operation_id),
        "project_id": reservation.project_id,
        "work_item_id": reservation.work_item_id,
        "stage_review_session_id": reservation.stage_review_session_id,
        "plan_digest": plan.plan_digest,
        "plan_finalization_digest": plan.finalization_digest,
        "final_reservation_id": reservation.reservation_id,
        "final_reservation_digest": plan.final_reservation_digest,
        "resource_fencing_token": plan.resource_fencing_token,
        "budget_policy_digest": plan.proposal.budget_policy_digest,
        "attempt_index": attempt_index,
        "provider_availability_attestation_digest": (
            availability.attestation_digest if availability is not None else ""
        ),
        "unavailable_provider_ids": (
            availability.unavailable_provider_ids if availability is not None else ()
        ),
        "provider_retry_delta": int(
            supplied["rebind_reason"]
            in {"provider_unavailable", "session_creation_retry"}
        ),
        "operation_id": operation_id,
        **supplied,
    }


def build_binding_attempt_operation(
    request: BindingAttemptRequest,
    authority: BindingAuthoritySnapshot,
    availability_attestation: ProviderAvailabilityAttestation | None = None,
) -> BindingAttemptOperation:
    values = {
        "operation_id": request.operation_id,
        "request": request,
        "authority_snapshot": authority,
        "availability_attestation": availability_attestation,
        "resource_delta": ResourceAmounts(
            binding_attempts=1,
            provider_retries=request.provider_retry_delta,
        ),
        "resource_operation_id": stable_id(
            "binding-resource-usage", request.operation_id
        ),
    }
    draft = BindingAttemptOperation.model_construct(
        **values,  # type: ignore[arg-type]
        operation_digest="",
    )
    return BindingAttemptOperation.model_validate(
        {**values, "operation_digest": binding_attempt_operation_digest(draft)}
    )
