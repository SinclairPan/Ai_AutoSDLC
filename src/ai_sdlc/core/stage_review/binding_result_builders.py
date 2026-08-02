"""BindingSet、结果、RebindDirective 与派发授权构建器。"""

from __future__ import annotations

from typing import Literal

from ai_sdlc.core.stage_review.binding_digests import (
    binding_result_digest,
    dispatch_assignment_digest,
    rebind_directive_digest,
    reviewer_binding_digest,
    reviewer_binding_set_digest,
)
from ai_sdlc.core.stage_review.binding_independence import (
    _canonical_independence_proofs,
)
from ai_sdlc.core.stage_review.binding_models import (
    BindingAttemptOperation,
    BindingResultCode,
    HostCapabilitySnapshot,
    IsolationExecutionEvidence,
    ProviderBindingDescriptor,
    ReviewerRuntimeAllocation,
)
from ai_sdlc.core.stage_review.binding_result_models import (
    BindingIndependenceProof,
    RebindDirective,
    ReviewerBinding,
    ReviewerBindingResult,
    ReviewerBindingSet,
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerSlot
from ai_sdlc.core.stage_review.provider_transport_trust import (
    _reviewer_transport_contract,
    build_reviewer_execution_identity,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceLedgerEvent,
    ResourceReservation,
)


def build_reviewer_binding(
    *,
    slot: ReviewerSlot,
    allocation: ReviewerRuntimeAllocation,
    descriptor: ProviderBindingDescriptor,
    evidence: IsolationExecutionEvidence,
    input_packet_digest: str,
    enforce_eligible: bool,
) -> ReviewerBinding:
    values = {
        "binding_id": stable_id(
            "reviewer-binding", slot.slot_id, allocation.allocation_digest
        ),
        "slot_id": slot.slot_id,
        "slot_kind": slot.slot_kind,
        "role_profile_id": slot.role_profile_id,
        "role_contract_digest": slot.role_contract_digest,
        "capability_ids": tuple(sorted(slot.capability_ids)),
        "actor_id": allocation.actor_id,
        "provider_id": allocation.provider_id,
        "model_family": allocation.model_family,
        "session_id": allocation.session_id,
        "provider_descriptor_digest": descriptor.descriptor_digest,
        "equivalence_class_id": descriptor.equivalence_class_id,
        "physical_provider_id": descriptor.execution_route.physical_provider_id,
        "physical_equivalence_class_id": (
            descriptor.execution_route.physical_equivalence_class_id
        ),
        **_binding_transport_fields(descriptor),
        "allocation_digest": allocation.allocation_digest,
        "input_packet_digest": input_packet_digest,
        "tool_allowlist": tuple(sorted(slot.tool_permission_ids)),
        "isolation_evidence_digest": evidence.isolation_evidence_digest,
        "isolation_grade": evidence.isolation_grade,
        "isolation_backend": evidence.isolation_backend,
        "supported_independence_grade": descriptor.supported_independence_grade,
        "visibility_barrier_id": evidence.visibility_barrier_id,
        "binding_status": "active",
        "recovery_capabilities": descriptor.recovery_capabilities,
        "eligible_for_enforce_quorum": enforce_eligible,
    }
    return ReviewerBinding.model_validate(
        {
            **values,
            "binding_digest": reviewer_binding_digest(values),
        }
    )


def _binding_transport_fields(
    descriptor: ProviderBindingDescriptor,
) -> dict[str, object]:
    contract = _reviewer_transport_contract(descriptor)
    return {
        "execution_identity": build_reviewer_execution_identity(descriptor),
        "transport_profile_digest": descriptor.execution_route.transport_profile_digest,
        "transport_contract_digest": contract.contract_digest,
        "transport_authority_digest": contract.authority_artifact_digest,
    }


def build_independence_proofs(
    bindings: tuple[ReviewerBinding, ...],
) -> tuple[BindingIndependenceProof, ...]:
    return tuple(
        BindingIndependenceProof(
            left_slot_id=item.left_slot_id,
            right_slot_id=item.right_slot_id,
            independence_grade=item.independence_grade,
            reason_id=item.reason_id,
        )
        for item in _canonical_independence_proofs(bindings)
    )


def build_binding_set(
    *,
    operation: BindingAttemptOperation,
    host_snapshot: HostCapabilitySnapshot,
    reservation: ResourceReservation,
    resource_event: ResourceLedgerEvent,
    bindings: tuple[ReviewerBinding, ...],
    unbound_slot_ids: tuple[str, ...],
) -> ReviewerBindingSet:
    request = operation.request
    ordered = tuple(sorted(bindings, key=lambda item: item.slot_id))
    values = {
        "binding_set_id": stable_id("reviewer-binding-set", operation.operation_id),
        "project_id": request.project_id,
        "work_item_id": request.work_item_id,
        "stage_review_session_id": request.stage_review_session_id,
        "candidate_manifest_digest": request.candidate_manifest_digest,
        "plan_digest": request.plan_digest,
        "plan_finalization_digest": request.plan_finalization_digest,
        "final_reservation_id": request.final_reservation_id,
        "final_reservation_digest": request.final_reservation_digest,
        "resource_fencing_token": request.resource_fencing_token,
        "charged_reservation_digest": reservation.reservation_digest,
        "resource_operation_id": operation.resource_operation_id,
        "resource_event_digest": resource_event.event_digest,
        "budget_policy_digest": request.budget_policy_digest,
        "authority_snapshot_digest": operation.authority_snapshot.snapshot_digest,
        "host_snapshot_digest": host_snapshot.snapshot_digest,
        "attempt_operation_id": operation.operation_id,
        "attempt_operation_digest": operation.operation_digest,
        "attempt_index": request.attempt_index,
        "previous_binding_set_digest": request.previous_binding_set_digest,
        "enforcement_mode": operation.authority_snapshot.enforcement_mode,
        "execution_mode": _execution_mode(ordered),
        "bindings": ordered,
        "unbound_slot_ids": tuple(sorted(set(unbound_slot_ids))),
        "independence_proofs": build_independence_proofs(ordered),
    }
    draft = ReviewerBindingSet.model_construct(
        **values,  # type: ignore[arg-type]
        binding_set_digest="",
    )
    return ReviewerBindingSet.model_validate(
        {**values, "binding_set_digest": reviewer_binding_set_digest(draft)}
    )


def build_rebind_directive(
    operation: BindingAttemptOperation,
    binding_set: ReviewerBindingSet,
) -> RebindDirective | None:
    request = operation.request
    if not request.previous_binding_set_digest:
        return None
    values = {
        "directive_id": stable_id("rebind-directive", binding_set.binding_set_digest),
        "previous_binding_set_digest": request.previous_binding_set_digest,
        "new_binding_set_digest": binding_set.binding_set_digest,
        "expected_cohort_id": request.expected_cohort_id,
        "expected_pass_head_digest": request.expected_pass_head_digest,
        "rebind_reason": request.rebind_reason,
        "unavailable_provider_ids": request.unavailable_provider_ids,
        "requires_session_cas": True,
    }
    draft = RebindDirective.model_construct(
        **values,  # type: ignore[arg-type]
        directive_digest="",
    )
    return RebindDirective.model_validate(
        {**values, "directive_digest": rebind_directive_digest(draft)}
    )


def build_binding_result(
    *,
    result_code: BindingResultCode,
    operation_id: str,
    binding_set: ReviewerBindingSet | None = None,
    rebind_directive: RebindDirective | None = None,
    reason_ids: tuple[str, ...] = (),
    diagnostic_evidence_digests: tuple[str, ...] = (),
) -> ReviewerBindingResult:
    values = {
        "result_code": result_code,
        "operation_id": operation_id,
        "binding_set": binding_set,
        "rebind_directive": rebind_directive,
        "reason_ids": tuple(sorted(set(reason_ids))),
        "diagnostic_evidence_digests": tuple(sorted(set(diagnostic_evidence_digests))),
    }
    draft = ReviewerBindingResult.model_construct(
        **values,  # type: ignore[arg-type]
        result_digest="",
    )
    return ReviewerBindingResult.model_validate(
        {**values, "result_digest": binding_result_digest(draft)}
    )


def build_dispatch_assignment(
    *,
    binding_set: ReviewerBindingSet,
    binding: ReviewerBinding,
    host_snapshot: HostCapabilitySnapshot,
    evidence: IsolationExecutionEvidence,
    cohort_id: str,
    expected_pass_head_digest: str,
) -> ReviewerDispatchAssignment:
    values = {
        "assignment_id": dispatch_assignment_id(
            binding_set,
            binding,
            host_snapshot,
            cohort_id=cohort_id,
            expected_pass_head_digest=expected_pass_head_digest,
        ),
        "binding_set_digest": binding_set.binding_set_digest,
        "binding_digest": binding.binding_digest,
        "host_snapshot_digest": host_snapshot.snapshot_digest,
        "isolation_evidence_digest": evidence.isolation_evidence_digest,
        "cohort_id": cohort_id,
        "expected_pass_head_digest": expected_pass_head_digest,
        "slot_id": binding.slot_id,
        "candidate_manifest_digest": binding_set.candidate_manifest_digest,
        "provider_id": binding.provider_id,
        "provider_descriptor_digest": binding.provider_descriptor_digest,
        "provider_execution_identity_digest": (
            binding.execution_identity.identity_digest
        ),
        "physical_provider_id": binding.physical_provider_id,
        "physical_equivalence_class_id": binding.physical_equivalence_class_id,
        "transport_profile_digest": binding.transport_profile_digest,
        "transport_contract_digest": binding.transport_contract_digest,
        "transport_authority_digest": binding.transport_authority_digest,
        "model_family": binding.model_family,
        "session_id": binding_set.stage_review_session_id,
        "recovery_capabilities": binding.recovery_capabilities,
    }
    draft = ReviewerDispatchAssignment.model_construct(
        **values,  # type: ignore[arg-type]
        assignment_digest="",
    )
    return ReviewerDispatchAssignment.model_validate(
        {**values, "assignment_digest": dispatch_assignment_digest(draft)}
    )


def dispatch_assignment_id(
    binding_set: ReviewerBindingSet,
    binding: ReviewerBinding,
    host_snapshot: HostCapabilitySnapshot,
    *,
    cohort_id: str,
    expected_pass_head_digest: str,
) -> str:
    return stable_id(
        "reviewer-dispatch",
        binding_set.binding_set_digest,
        binding.binding_digest,
        host_snapshot.snapshot_digest,
        cohort_id,
        expected_pass_head_digest,
    )


def _execution_mode(
    bindings: tuple[ReviewerBinding, ...],
) -> Literal["enforce_eligible", "shadow_only"]:
    required = tuple(item for item in bindings if item.slot_kind == "required")
    if required and all(item.eligible_for_enforce_quorum for item in required):
        return "enforce_eligible"
    return "shadow_only"
