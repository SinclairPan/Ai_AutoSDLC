"""最小 Required Slot 集合的确定性 Branch-and-Bound 求解。"""

from __future__ import annotations

from collections.abc import Sequence

from ai_sdlc.core.stage_review.panel_models import (
    CapabilityCoverageRequirement,
    PanelPlanningError,
    ReviewerBudgetPolicy,
    ReviewerPlanRequest,
    ReviewerQuorumPolicy,
    ReviewerRoleOption,
)
from ai_sdlc.core.stage_review.panel_selection_common import (
    all_operationally_distinct,
    coverage_counts,
    distinct_from,
    group_score,
    option_capabilities,
    option_key,
    option_within_own_ceiling,
    within_budget,
)
from ai_sdlc.core.stage_review.panel_selection_diagnostics import (
    no_feasible_reasons,
)
from ai_sdlc.core.stage_review.registry_models import (
    CapabilityDefinition,
    ReviewerSelectionPolicy,
)


def select_required(
    request: ReviewerPlanRequest,
    selection: ReviewerSelectionPolicy,
    quorum: ReviewerQuorumPolicy,
    budget: ReviewerBudgetPolicy,
    options: Sequence[ReviewerRoleOption],
    capabilities: dict[str, CapabilityDefinition],
) -> list[ReviewerRoleOption]:
    candidates = [
        item
        for item in options
        if "required" in item.eligible_slot_kinds
        and item.role_contract.capability_mode == "active"
        and option_within_own_ceiling(item)
    ]
    minimum = max(selection.minimum_slots, quorum.minimum_pass_count)
    maximum = min(budget.maximum_slots, len(candidates))
    for count in range(minimum, maximum + 1):
        selected = _best_required_group(
            candidates, count, request, selection, budget, capabilities
        )
        if selected is not None:
            return list(selected)
    reasons, missing = no_feasible_reasons(
        candidates, minimum, request, selection, budget, capabilities
    )
    raise PanelPlanningError(
        "no_feasible_panel",
        "panel.no-feasible-required-set",
        reason_ids=reasons,
        missing=missing,
    )


def _best_required_group(
    candidates: Sequence[ReviewerRoleOption],
    target: int,
    request: ReviewerPlanRequest,
    selection: ReviewerSelectionPolicy,
    budget: ReviewerBudgetPolicy,
    capabilities: dict[str, CapabilityDefinition],
) -> tuple[ReviewerRoleOption, ...] | None:
    best: list[tuple[ReviewerRoleOption, ...] | None] = [
        _greedy_seed(candidates, target, request, selection, budget, capabilities)
    ]

    def visit(index: int, group: tuple[ReviewerRoleOption, ...]) -> None:
        needed = target - len(group)
        remaining = candidates[index:]
        if needed == 0:
            _record_if_better(
                best, group, request, selection, budget, capabilities
            )
            return
        if needed > len(remaining) or not within_budget(group, budget):
            return
        if not _can_still_satisfy(
            group, remaining, needed, request, selection, capabilities
        ):
            return
        if best[0] is not None and _optimistic_score(group, remaining, needed) >= (
            group_score(best[0])
        ):
            return
        candidate = candidates[index]
        if distinct_from(candidate, group):
            visit(index + 1, (*group, candidate))
        visit(index + 1, group)

    visit(0, ())
    return best[0]


def _record_if_better(
    best: list[tuple[ReviewerRoleOption, ...] | None],
    group: tuple[ReviewerRoleOption, ...],
    request: ReviewerPlanRequest,
    selection: ReviewerSelectionPolicy,
    budget: ReviewerBudgetPolicy,
    capabilities: dict[str, CapabilityDefinition],
) -> None:
    if not _required_group_is_feasible(
        group, request, selection, budget, capabilities
    ):
        return
    if best[0] is None or group_score(group) < group_score(best[0]):
        best[0] = group


def _greedy_seed(
    candidates: Sequence[ReviewerRoleOption],
    target: int,
    request: ReviewerPlanRequest,
    selection: ReviewerSelectionPolicy,
    budget: ReviewerBudgetPolicy,
    capabilities: dict[str, CapabilityDefinition],
) -> tuple[ReviewerRoleOption, ...] | None:
    selected: list[ReviewerRoleOption] = []
    available = list(candidates)
    while len(selected) < target:
        compatible = [item for item in available if distinct_from(item, selected)]
        if not compatible:
            return None
        candidate = min(
            compatible,
            key=lambda item: (
                *_candidate_gain(item, selected, request, selection, capabilities),
                *option_key(item),
            ),
        )
        selected.append(candidate)
        available.remove(candidate)
    result = tuple(selected)
    if _required_group_is_feasible(
        result, request, selection, budget, capabilities
    ):
        return result
    return None


def _candidate_gain(
    candidate: ReviewerRoleOption,
    selected: Sequence[ReviewerRoleOption],
    request: ReviewerPlanRequest,
    selection: ReviewerSelectionPolicy,
    capabilities: dict[str, CapabilityDefinition],
) -> tuple[int, int, int]:
    selected_dimensions = {
        value for item in selected for value in item.role_contract.primary_dimensions
    }
    covered = coverage_counts(selected, request, capabilities)
    candidate_caps = option_capabilities(candidate, capabilities, request)
    coverage_gain = sum(
        requirement.capability_id in candidate_caps
        and covered[requirement.capability_id] < requirement.minimum_required_slots
        for requirement in request.coverage_requirements
    )
    dimension_gain = min(
        len(set(candidate.role_contract.primary_dimensions) - selected_dimensions),
        max(0, selection.minimum_distinct_primary_dimensions - len(selected_dimensions)),
    )
    blocking_gain = sum(
        item in candidate.role_contract.blocking_authority
        for item in request.blocking_capability_ids
    )
    return -coverage_gain, -dimension_gain, -blocking_gain


def _required_group_is_feasible(
    group: Sequence[ReviewerRoleOption],
    request: ReviewerPlanRequest,
    selection: ReviewerSelectionPolicy,
    budget: ReviewerBudgetPolicy,
    capabilities: dict[str, CapabilityDefinition],
) -> bool:
    if not within_budget(group, budget):
        return False
    dimensions = {
        value for option in group for value in option.role_contract.primary_dimensions
    }
    if len(dimensions) < selection.minimum_distinct_primary_dimensions:
        return False
    if not all_operationally_distinct(group):
        return False
    return all(
        _requirement_is_covered(item, group, request, capabilities)
        for item in request.coverage_requirements
    )


def _requirement_is_covered(
    requirement: CapabilityCoverageRequirement,
    group: Sequence[ReviewerRoleOption],
    request: ReviewerPlanRequest,
    capabilities: dict[str, CapabilityDefinition],
) -> bool:
    capability_id = requirement.capability_id
    minimum = requirement.minimum_required_slots
    owners = [
        option
        for option in group
        if capability_id in option_capabilities(option, capabilities, request)
    ]
    if len({item.independence_key for item in owners}) < minimum:
        return False
    return capability_id not in request.blocking_capability_ids or any(
        capability_id in item.role_contract.blocking_authority for item in owners
    )


def _can_still_satisfy(
    group: Sequence[ReviewerRoleOption],
    remaining: Sequence[ReviewerRoleOption],
    slots_left: int,
    request: ReviewerPlanRequest,
    selection: ReviewerSelectionPolicy,
    capabilities: dict[str, CapabilityDefinition],
) -> bool:
    compatible = [item for item in remaining if distinct_from(item, group)]
    if len(compatible) < slots_left:
        return False
    return (
        _dimension_slots_needed(group, compatible, selection) <= slots_left
        and _coverage_slots_needed(group, compatible, request, capabilities)
        <= slots_left
        and _blocking_slots_needed(group, compatible, request) <= slots_left
    )


def _dimension_slots_needed(
    group: Sequence[ReviewerRoleOption],
    remaining: Sequence[ReviewerRoleOption],
    selection: ReviewerSelectionPolicy,
) -> int:
    current = {
        value for item in group for value in item.role_contract.primary_dimensions
    }
    missing = max(0, selection.minimum_distinct_primary_dimensions - len(current))
    maximum_gain = max(
        (len(set(item.role_contract.primary_dimensions) - current) for item in remaining),
        default=0,
    )
    return _ceil_div(missing, maximum_gain)


def _coverage_slots_needed(
    group: Sequence[ReviewerRoleOption],
    remaining: Sequence[ReviewerRoleOption],
    request: ReviewerPlanRequest,
    capabilities: dict[str, CapabilityDefinition],
) -> int:
    counts = coverage_counts(group, request, capabilities)
    deficits = {
        item.capability_id: max(
            0, item.minimum_required_slots - counts[item.capability_id]
        )
        for item in request.coverage_requirements
    }
    total = sum(deficits.values())
    maximum_gain = max(
        (
            sum(
                deficits[item] > 0
                for item in option_capabilities(option, capabilities, request)
                if item in deficits
            )
            for option in remaining
        ),
        default=0,
    )
    return _ceil_div(total, maximum_gain)


def _blocking_slots_needed(
    group: Sequence[ReviewerRoleOption],
    remaining: Sequence[ReviewerRoleOption],
    request: ReviewerPlanRequest,
) -> int:
    covered = {
        item for option in group for item in option.role_contract.blocking_authority
    }
    missing = set(request.blocking_capability_ids) - covered
    maximum_gain = max(
        (len(set(item.role_contract.blocking_authority) & missing) for item in remaining),
        default=0,
    )
    return _ceil_div(len(missing), maximum_gain)


def _ceil_div(value: int, divisor: int) -> int:
    if value == 0:
        return 0
    if divisor == 0:
        return value + 1
    return (value + divisor - 1) // divisor


def _optimistic_score(
    group: Sequence[ReviewerRoleOption],
    remaining: Sequence[ReviewerRoleOption],
    needed: int,
) -> tuple[object, ...]:
    def floor(field: str) -> float:
        values = sorted(float(getattr(item, field)) for item in remaining)
        return sum(float(getattr(item, field)) for item in group) + sum(
            values[:needed]
        )

    identities = [item.role_contract.role_profile_id for item in group]
    identities.extend(
        sorted(item.role_contract.role_profile_id for item in remaining)[:needed]
    )
    return (
        len(group) + needed,
        floor("estimated_cost"),
        int(floor("estimated_tokens")),
        int(floor("estimated_provider_calls")),
        tuple(sorted(identities)),
    )
