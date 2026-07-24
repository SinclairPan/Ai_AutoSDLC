"""Panel Slot 选择器共享的差异、覆盖、预算和稳定排序原语。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ai_sdlc.core.stage_review.panel_digests import (
    role_option_difference_dimensions,
)
from ai_sdlc.core.stage_review.panel_models import (
    ReviewerBudgetPolicy,
    ReviewerPlanRequest,
    ReviewerRoleOption,
)
from ai_sdlc.core.stage_review.registry_models import CapabilityDefinition


def option_capabilities(
    option: ReviewerRoleOption,
    capabilities: dict[str, CapabilityDefinition],
    request: ReviewerPlanRequest,
) -> set[str]:
    return {
        item
        for item in option.role_contract.capability_ids
        if item in capabilities
        and request.stage_key in capabilities[item].applicable_stage
        and request.risk_level in capabilities[item].applicable_risk
    }


def covered_capabilities(
    options: Sequence[ReviewerRoleOption],
    capabilities: dict[str, CapabilityDefinition],
    request: ReviewerPlanRequest,
) -> set[str]:
    return {
        capability
        for option in options
        for capability in option_capabilities(option, capabilities, request)
    }


def coverage_counts(
    options: Sequence[ReviewerRoleOption],
    request: ReviewerPlanRequest,
    capabilities: dict[str, CapabilityDefinition],
) -> dict[str, int]:
    return {
        requirement.capability_id: sum(
            requirement.capability_id
            in option_capabilities(item, capabilities, request)
            for item in options
        )
        for requirement in request.coverage_requirements
    }


def within_budget(
    options: Sequence[ReviewerRoleOption], budget: ReviewerBudgetPolicy
) -> bool:
    return (
        len(options) <= budget.maximum_slots
        and sum(item.estimated_provider_calls for item in options)
        <= budget.hard_provider_calls
        and sum(item.estimated_review_passes for item in options)
        <= budget.hard_review_passes
        and sum(item.estimated_tokens for item in options) <= budget.hard_tokens
        and sum(item.estimated_cost for item in options) <= budget.hard_cost
        and sum(item.estimated_wall_clock for item in options)
        <= budget.hard_wall_clock
    )


def option_within_own_ceiling(option: ReviewerRoleOption) -> bool:
    return option.estimated_cost <= option.role_contract.cost_ceiling


def all_operationally_distinct(
    options: Sequence[ReviewerRoleOption],
) -> bool:
    return all(
        role_option_difference_dimensions(left, right)
        for index, left in enumerate(options)
        for right in options[index + 1 :]
    )


def distinct_from(
    candidate: ReviewerRoleOption,
    selected: Sequence[ReviewerRoleOption],
) -> bool:
    return all(
        role_option_difference_dimensions(candidate, item) for item in selected
    )


def group_score(group: Iterable[ReviewerRoleOption]) -> tuple[object, ...]:
    items = tuple(group)
    return (
        len(items),
        sum(item.estimated_cost for item in items),
        sum(item.estimated_tokens for item in items),
        sum(item.estimated_provider_calls for item in items),
        tuple(sorted(item.role_contract.role_profile_id for item in items)),
    )


def option_key(option: ReviewerRoleOption) -> tuple[object, ...]:
    return (
        option.estimated_cost,
        option.estimated_tokens,
        option.estimated_provider_calls,
        option.role_contract.role_profile_id,
        option.role_contract.role_contract_digest,
    )
