"""把纯求解结果冻结为可重放的 Reviewer Panel Plan。"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import combinations

from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.panel_digests import panel_proposal_digest
from ai_sdlc.core.stage_review.panel_models import (
    ReviewerBudgetPolicy,
    ReviewerPlanRequest,
    ReviewerQuorumPolicy,
    ReviewerRoleOption,
    SlotKind,
)
from ai_sdlc.core.stage_review.panel_plan_models import (
    CapabilityCoverageProof,
    FrozenQuorumPolicy,
    PanelResourceRequirement,
    ReviewerDifference,
    ReviewerPanelProposal,
    ReviewerSlot,
)

_PLANNING_EXPLANATIONS = (
    "panel.minimum-required-set",
    "panel.stable-score-tie-break",
    "panel.supplemental-marginal-gain",
)


def build_panel_proposal(
    *,
    request: ReviewerPlanRequest,
    quorum: ReviewerQuorumPolicy,
    budget: ReviewerBudgetPolicy,
    options: Sequence[ReviewerRoleOption],
    selected: dict[SlotKind, list[ReviewerRoleOption]],
) -> ReviewerPanelProposal:
    """生成供 ResourceGovernor 最终预留使用的纯求解结果。"""

    slots = {
        kind: tuple(_slot(kind, item, request, quorum) for item in values)
        for kind, values in selected.items()
    }
    required = slots.get("required", ())
    all_slots = tuple(slot for kind in _slot_kinds() for slot in slots.get(kind, ()))
    selected_ids = {item.role_profile_id for item in all_slots}
    draft = ReviewerPanelProposal.model_construct(
        request_digest=request.request_digest,
        planning_context_digest=request.planning_context_digest,
        solver_version=request.solver_version,
        registry_digest=request.registry_digest,
        role_catalog_digest=request.role_catalog_digest,
        selection_policy_digest=request.selection_policy_digest,
        quorum_policy_digest=request.quorum_policy_digest,
        budget_policy_digest=request.budget_policy_digest,
        budget_envelope_digest=request.budget_envelope_digest,
        optimization_snapshot_digest=request.optimization_snapshot_digest,
        required_slots=required,
        optional_slots=slots.get("optional", ()),
        advisory_slots=slots.get("advisory", ()),
        shadow_slots=slots.get("shadow", ()),
        coverage_proof=_coverage_proof(request, required),
        difference_matrix=_differences(all_slots),
        quorum=_frozen_quorum(request, quorum, required),
        resource_requirement=_resources(required, all_slots, budget),
        rejected_role_reasons=tuple(
            f"panel.not-selected:{item.role_contract.role_profile_id}"
            for item in options
            if item.role_contract.role_profile_id not in selected_ids
        ),
        planning_explanations=_PLANNING_EXPLANATIONS,
        proposal_digest="",
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["proposal_digest"] = panel_proposal_digest(draft)
    return ReviewerPanelProposal.model_validate(payload)


def _slot(
    kind: SlotKind,
    option: ReviewerRoleOption,
    request: ReviewerPlanRequest,
    quorum: ReviewerQuorumPolicy,
) -> ReviewerSlot:
    role = option.role_contract
    digest = canonical_digest(
        {
            "kind": kind,
            "planning_context_digest": request.planning_context_digest,
            "role_contract_digest": role.role_contract_digest,
        },
        CanonicalizationPolicy(),
    )
    return ReviewerSlot(
        slot_id=f"slot.{kind}.{digest.removeprefix('sha256:')[:16]}",
        slot_kind=kind,
        role_profile_id=role.role_profile_id,
        role_contract_digest=role.role_contract_digest,
        capability_ids=tuple(role.capability_ids),
        blocking_authority=(
            tuple(
                sorted(
                    set(role.blocking_authority) & set(request.blocking_capability_ids)
                )
            )
            if kind == "required"
            else ()
        ),
        primary_dimensions=tuple(role.primary_dimensions),
        prompt_template_digest=option.prompt_template_digest,
        provider_constraints=tuple(role.provider_constraints),
        tool_permission_ids=tuple(option.tool_permission_ids),
        evidence_source_ids=tuple(option.evidence_source_ids),
        independence_key=option.independence_key,
        counts_for_quorum=kind == "required",
        allows_abstain=kind in quorum.allowed_abstentions,
        selection_reason_ids=(f"panel.selected.{kind}",),
        estimated_provider_calls=option.estimated_provider_calls,
        estimated_review_passes=option.estimated_review_passes,
        estimated_tokens=option.estimated_tokens,
        estimated_cost=option.estimated_cost,
        estimated_wall_clock=option.estimated_wall_clock,
    )


def _coverage_proof(
    request: ReviewerPlanRequest,
    required: Sequence[ReviewerSlot],
) -> tuple[CapabilityCoverageProof, ...]:
    return tuple(
        CapabilityCoverageProof(
            capability_id=item.capability_id,
            required_slot_ids=tuple(
                slot.slot_id
                for slot in required
                if item.capability_id in slot.capability_ids
            ),
            minimum_required_slots=item.minimum_required_slots,
            blocking_slot_ids=tuple(
                slot.slot_id
                for slot in required
                if item.capability_id in slot.blocking_authority
            ),
        )
        for item in request.coverage_requirements
    )


def _differences(slots: Sequence[ReviewerSlot]) -> tuple[ReviewerDifference, ...]:
    result: list[ReviewerDifference] = []
    for left, right in combinations(slots, 2):
        dimensions = tuple(
            name
            for name, left_value, right_value in (
                ("capability", left.capability_ids, right.capability_ids),
                ("prompt", left.prompt_template_digest, right.prompt_template_digest),
                ("provider", left.provider_constraints, right.provider_constraints),
                ("tool", left.tool_permission_ids, right.tool_permission_ids),
                ("evidence", left.evidence_source_ids, right.evidence_source_ids),
            )
            if left_value != right_value
        )
        result.append(
            ReviewerDifference(
                left_slot_id=left.slot_id,
                right_slot_id=right.slot_id,
                difference_dimensions=dimensions,
            )
        )
    return tuple(result)


def _frozen_quorum(
    request: ReviewerPlanRequest,
    policy: ReviewerQuorumPolicy,
    required: Sequence[ReviewerSlot],
) -> FrozenQuorumPolicy:
    return FrozenQuorumPolicy(
        required_slot_ids=tuple(item.slot_id for item in required),
        required_capability_expressions=tuple(
            f"{item.capability_id}>={item.minimum_required_slots}"
            for item in request.coverage_requirements
        ),
        minimum_pass_count=len(required),
        veto_authorities=tuple(
            item
            for item in policy.veto_authorities
            if item in request.blocking_capability_ids
        ),
        allowed_abstentions=policy.allowed_abstentions,
        source_policy_digest=policy.policy_digest,
    )


def _resources(
    required: Sequence[ReviewerSlot],
    all_slots: Sequence[ReviewerSlot],
    budget: ReviewerBudgetPolicy,
) -> PanelResourceRequirement:
    return PanelResourceRequirement(
        required_slot_count=len(required),
        total_slot_count=len(all_slots),
        required_provider_calls=sum(item.estimated_provider_calls for item in required),
        total_provider_calls=sum(item.estimated_provider_calls for item in all_slots),
        required_review_passes=sum(item.estimated_review_passes for item in required),
        total_review_passes=sum(item.estimated_review_passes for item in all_slots),
        required_tokens=sum(item.estimated_tokens for item in required),
        total_tokens=sum(item.estimated_tokens for item in all_slots),
        required_cost=sum(item.estimated_cost for item in required),
        total_cost=sum(item.estimated_cost for item in all_slots),
        required_wall_clock=sum(item.estimated_wall_clock for item in required),
        total_wall_clock=sum(item.estimated_wall_clock for item in all_slots),
        parallelism=min(len(all_slots), budget.hard_parallelism),
    )


def _slot_kinds() -> tuple[SlotKind, ...]:
    return ("required", "optional", "advisory", "shadow")
