"""`no_feasible_panel` 的稳定结构化原因诊断。"""

from __future__ import annotations

from collections.abc import Sequence

from ai_sdlc.core.stage_review.panel_digests import (
    role_option_difference_dimensions,
)
from ai_sdlc.core.stage_review.panel_models import (
    ReviewerBudgetPolicy,
    ReviewerPlanRequest,
    ReviewerRoleOption,
)
from ai_sdlc.core.stage_review.panel_selection_common import option_capabilities
from ai_sdlc.core.stage_review.registry_models import (
    CapabilityDefinition,
    ReviewerSelectionPolicy,
)


def no_feasible_reasons(
    candidates: Sequence[ReviewerRoleOption],
    minimum: int,
    request: ReviewerPlanRequest,
    selection: ReviewerSelectionPolicy,
    budget: ReviewerBudgetPolicy,
    capabilities: dict[str, CapabilityDefinition],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    reasons: list[str] = []
    if len(candidates) < minimum or budget.maximum_slots < minimum:
        reasons.append("panel.required-slot-capacity")
    dimensions = {
        item for option in candidates for item in option.role_contract.primary_dimensions
    }
    if len(dimensions) < selection.minimum_distinct_primary_dimensions:
        reasons.append("panel.primary-dimension-gap")
    missing = _coverage_gap_ids(candidates, request, capabilities)
    reasons.extend(f"panel.coverage-gap:{item}" for item in missing)
    if minimum > 1 and not any(
        role_option_difference_dimensions(left, right)
        for index, left in enumerate(candidates)
        for right in candidates[index + 1 :]
    ):
        reasons.append("panel.operational-difference-gap")
    reasons.extend(_budget_gap_ids(candidates, minimum, budget))
    if not reasons:
        reasons.append("panel.constraint-intersection")
    return tuple(reasons), missing


def _coverage_gap_ids(
    candidates: Sequence[ReviewerRoleOption],
    request: ReviewerPlanRequest,
    capabilities: dict[str, CapabilityDefinition],
) -> tuple[str, ...]:
    return tuple(
        item.capability_id
        for item in request.coverage_requirements
        if len(
            {
                option.independence_key
                for option in candidates
                if item.capability_id
                in option_capabilities(option, capabilities, request)
            }
        )
        < item.minimum_required_slots
    )


def _budget_gap_ids(
    candidates: Sequence[ReviewerRoleOption],
    minimum: int,
    budget: ReviewerBudgetPolicy,
) -> tuple[str, ...]:
    fields = (
        ("provider_calls", "estimated_provider_calls", budget.hard_provider_calls),
        ("review_passes", "estimated_review_passes", budget.hard_review_passes),
        ("tokens", "estimated_tokens", budget.hard_tokens),
        ("cost", "estimated_cost", budget.hard_cost),
        ("wall_clock", "estimated_wall_clock", budget.hard_wall_clock),
    )
    return tuple(
        f"panel.budget-gap:{label}"
        for label, field, limit in fields
        if sum(sorted(float(getattr(item, field)) for item in candidates)[:minimum])
        > float(limit)
    )
