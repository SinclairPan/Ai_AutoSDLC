"""从冻结 Provider 描述符重放 Binding 与 Assignment 的可信执行身份。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.binding_lineage import (
    dispatch_assignment_matches_binding,
)
from ai_sdlc.core.stage_review.binding_models import (
    BindingAuthoritySnapshot,
    ProviderBindingDescriptor,
)
from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBinding,
    ReviewerBindingSet,
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan
from ai_sdlc.core.stage_review.provider_authority_registry import (
    _validate_registered_provider_authority,
)
from ai_sdlc.core.stage_review.provider_transport_trust import (
    _reviewer_transport_contract,
    build_reviewer_execution_identity,
)


def _validate_binding_authority_snapshot(
    plan: ReviewerPanelPlan,
    authority: BindingAuthoritySnapshot,
    binding_set: ReviewerBindingSet,
    assignments: tuple[ReviewerDispatchAssignment, ...],
) -> None:
    _validate_registered_provider_authority(authority, plan)
    proposal = plan.proposal
    lineage = {
        "snapshot": authority.snapshot_digest
        == binding_set.authority_snapshot_digest,
        "plan": authority.plan_digest
        == plan.plan_digest
        == binding_set.plan_digest,
        "finalization": authority.plan_finalization_digest
        == plan.finalization_digest
        == binding_set.plan_finalization_digest,
        "request": authority.request_digest == proposal.request_digest,
        "optimization": authority.optimization_snapshot_digest
        == proposal.optimization_snapshot_digest,
        "registry": authority.registry_digest == proposal.registry_digest,
        "selection": authority.selection_policy_digest
        == proposal.selection_policy_digest,
        "mode": authority.enforcement_mode == binding_set.enforcement_mode,
    }
    failed = tuple(name for name, valid in lineage.items() if not valid)
    if failed:
        raise ValueError(
            f"binding authority snapshot lineage diverged: {','.join(failed)}"
        )
    bindings = {item.slot_id: item for item in binding_set.bindings}
    for binding in binding_set.bindings:
        _validate_binding_against_descriptor(
            authority,
            binding,
            None,
        )
    for assignment in assignments:
        binding = bindings.get(assignment.slot_id)
        if binding is None or not dispatch_assignment_matches_binding(
            binding_set,
            binding,
            assignment,
        ):
            raise ValueError("binding authority assignment lineage diverged")
        _validate_binding_against_descriptor(authority, binding, assignment)


def _validate_binding_against_descriptor(
    authority: BindingAuthoritySnapshot,
    binding: ReviewerBinding,
    assignment: ReviewerDispatchAssignment | None,
) -> ProviderBindingDescriptor:
    descriptor = _descriptor_for_binding(authority, binding)
    identity = build_reviewer_execution_identity(descriptor)
    contract = _reviewer_transport_contract(descriptor)
    route = descriptor.execution_route
    expected = (
        binding.provider_id == descriptor.provider_id,
        binding.provider_descriptor_digest == descriptor.descriptor_digest,
        binding.equivalence_class_id == descriptor.equivalence_class_id,
        binding.model_family == descriptor.model_family,
        set(binding.capability_ids).issubset(descriptor.capability_ids),
        set(binding.tool_allowlist).issubset(descriptor.tool_allowlist),
        binding.recovery_capabilities == descriptor.recovery_capabilities,
        binding.isolation_backend == descriptor.isolation_backend,
        binding.supported_independence_grade
        == descriptor.supported_independence_grade,
        binding.physical_provider_id == route.physical_provider_id,
        binding.physical_equivalence_class_id
        == route.physical_equivalence_class_id,
        binding.execution_identity == identity,
        binding.transport_profile_digest == route.transport_profile_digest,
        binding.transport_contract_digest == contract.contract_digest,
        binding.transport_authority_digest == contract.authority_artifact_digest,
    )
    if not all(expected):
        raise ValueError("binding diverged from trusted provider descriptor")
    if assignment is not None and (
        assignment.provider_descriptor_digest != descriptor.descriptor_digest
        or assignment.provider_execution_identity_digest != identity.identity_digest
        or assignment.transport_profile_digest != route.transport_profile_digest
        or assignment.transport_contract_digest != contract.contract_digest
        or assignment.transport_authority_digest
        != contract.authority_artifact_digest
    ):
        raise ValueError("assignment diverged from trusted provider descriptor")
    return descriptor


def _descriptor_for_binding(
    authority: BindingAuthoritySnapshot,
    binding: ReviewerBinding,
) -> ProviderBindingDescriptor:
    matches = tuple(
        item
        for item in authority.provider_descriptors
        if item.descriptor_digest == binding.provider_descriptor_digest
    )
    if len(matches) != 1:
        raise ValueError("trusted provider descriptor is unavailable or ambiguous")
    return matches[0]


__all__: list[str] = []
