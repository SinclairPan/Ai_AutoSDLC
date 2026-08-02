"""Reviewer Panel 请求、策略与计划的 canonical digest。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
    canonical_payload,
)
from ai_sdlc.core.stage_review.panel_authorization_models import (
    ReviewerPlanningAuthorization,
)
from ai_sdlc.core.stage_review.panel_models import (
    ReviewerBudgetPolicy,
    ReviewerPlanRequest,
    ReviewerQuorumPolicy,
    ReviewerRoleOption,
)
from ai_sdlc.core.stage_review.panel_plan_models import (
    ReviewerPanelPlan,
    ReviewerPanelProposal,
)

_RUNTIME_FIELDS = frozenset({"created_at", "created_by", "ai_sdlc_version"})
_BUDGET_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME_FIELDS | {"policy_digest"},
)
_QUORUM_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME_FIELDS | {"policy_digest"},
    set_like_fields=frozenset(
        {
            "veto_authorities",
            "allowed_abstentions",
            "substitutable_required_role_groups",
        }
    ),
)
_PLANNING_AUTHORIZATION_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME_FIELDS | {"authorization_digest"},
)
_PLANNING_CONTEXT_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME_FIELDS
    | {
        "request_id",
        "request_digest",
        "planning_context_digest",
        "candidate_manifest_ref",
        "task_risk_profile_ref",
        "registry_ref",
        "role_catalog_ref",
        "selection_policy_ref",
        "quorum_policy_ref",
        "budget_policy_ref",
        "optimization_snapshot_ref",
    },
    set_like_fields=frozenset(
        {
            "required_capability_ids",
            "coverage_requirements",
            "blocking_capability_ids",
        }
    ),
)
_REQUEST_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME_FIELDS | {"request_digest"},
    set_like_fields=_PLANNING_CONTEXT_POLICY.set_like_fields,
)
_PANEL_PROPOSAL_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME_FIELDS | {"request_digest", "proposal_digest"},
    set_like_fields=frozenset(
        {
            "coverage_proof",
            "difference_matrix",
            "rejected_role_reasons",
            "planning_explanations",
        }
    ),
)


def budget_policy_digest(policy: ReviewerBudgetPolicy) -> str:
    return canonical_digest(policy, _BUDGET_POLICY)


def quorum_policy_digest(policy: ReviewerQuorumPolicy) -> str:
    return canonical_digest(policy, _QUORUM_POLICY)


def planning_authorization_digest(
    authorization: ReviewerPlanningAuthorization,
) -> str:
    return canonical_digest(authorization, _PLANNING_AUTHORIZATION_POLICY)


def planning_context_digest(request: ReviewerPlanRequest) -> str:
    return canonical_digest(request, _PLANNING_CONTEXT_POLICY)


def plan_request_digest(request: ReviewerPlanRequest) -> str:
    return canonical_digest(request, _REQUEST_POLICY)


def panel_proposal_digest(proposal: ReviewerPanelProposal) -> str:
    return canonical_digest(proposal, _PANEL_PROPOSAL_POLICY)


def panel_proposal_lineage_digest(proposal: ReviewerPanelProposal) -> str:
    """绑定语义相同 Proposal 所对应的精确 Request 血缘。"""

    return canonical_digest(
        {
            "request_digest": proposal.request_digest,
            "proposal_digest": proposal.proposal_digest,
        },
        CanonicalizationPolicy(),
    )


def panel_proposal_semantic_payload(proposal: ReviewerPanelProposal) -> object:
    """返回排除运行时字段的求解语义，用于可信重放比较。"""

    return canonical_payload(proposal, _PANEL_PROPOSAL_POLICY)


def reviewer_panel_plan_digest(plan: ReviewerPanelPlan) -> str:
    return canonical_digest(
        {"proposal": panel_proposal_semantic_payload(plan.proposal)},
        CanonicalizationPolicy(),
    )


def reviewer_panel_finalization_digest(plan: ReviewerPanelPlan) -> str:
    return canonical_digest(
        {
            "plan_digest": plan.plan_digest,
            "proposal_lineage_digest": plan.proposal_lineage_digest,
            "final_reservation_id": plan.final_reservation_id,
            "final_reservation_digest": plan.final_reservation_digest,
            "resource_fencing_token": plan.resource_fencing_token,
        },
        CanonicalizationPolicy(),
    )


def role_option_independence_key(option: ReviewerRoleOption) -> str:
    """只从受治理的五类运行差异计算逻辑独立身份。"""

    return canonical_digest(_role_option_axes(option), CanonicalizationPolicy())


def role_option_difference_dimensions(
    left: ReviewerRoleOption,
    right: ReviewerRoleOption,
) -> tuple[str, ...]:
    left_axes = _role_option_axes(left)
    right_axes = _role_option_axes(right)
    return tuple(name for name in left_axes if left_axes[name] != right_axes[name])


def role_option_catalog_digest(options: object) -> str:
    if not isinstance(options, (list, tuple)):
        raise ValueError("role option catalog must be an ordered collection")
    payloads = [_role_option_payload(item) for item in options]
    ordered = sorted(
        payloads,
        key=lambda item: canonical_digest(item, CanonicalizationPolicy()),
    )
    return canonical_digest(ordered, CanonicalizationPolicy())


def _role_option_axes(option: ReviewerRoleOption) -> dict[str, object]:
    return {
        "capability": tuple(option.role_contract.capability_ids),
        "prompt": option.prompt_template_digest,
        "provider": tuple(option.role_contract.provider_constraints),
        "tool": tuple(option.tool_permission_ids),
        "evidence": tuple(option.evidence_source_ids),
    }


def _role_option_payload(option: object) -> dict[str, object]:
    if not isinstance(option, ReviewerRoleOption):
        raise ValueError("role option catalog contains invalid item")
    return {
        "role_contract_digest": option.role_contract.role_contract_digest,
        "eligible_slot_kinds": tuple(option.eligible_slot_kinds),
        "operational_axes": _role_option_axes(option),
        "independence_key": option.independence_key,
        "estimated_provider_calls": option.estimated_provider_calls,
        "estimated_review_passes": option.estimated_review_passes,
        "estimated_tokens": option.estimated_tokens,
        "estimated_cost": option.estimated_cost,
        "estimated_wall_clock": option.estimated_wall_clock,
    }
