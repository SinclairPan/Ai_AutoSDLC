"""Reviewer Panel 的 Required 与补充 Slot 确定性选择入口。"""

from __future__ import annotations

from collections.abc import Sequence

from ai_sdlc.core.stage_review.panel_models import (
    ReviewerBudgetPolicy,
    ReviewerPlanRequest,
    ReviewerQuorumPolicy,
    ReviewerRoleOption,
    SlotKind,
)
from ai_sdlc.core.stage_review.panel_required_search import select_required
from ai_sdlc.core.stage_review.panel_selection_common import (
    covered_capabilities,
    distinct_from,
    option_capabilities,
    option_key,
    option_within_own_ceiling,
    within_budget,
)
from ai_sdlc.core.stage_review.registry_models import (
    CapabilityDefinition,
    ReviewerSelectionPolicy,
)


def select_panel_roles(
    *,
    request: ReviewerPlanRequest,
    selection: ReviewerSelectionPolicy,
    quorum: ReviewerQuorumPolicy,
    budget: ReviewerBudgetPolicy,
    options: Sequence[ReviewerRoleOption],
    capabilities: dict[str, CapabilityDefinition],
) -> dict[SlotKind, list[ReviewerRoleOption]]:
    """选择 Required 后，按 Policy 优先级消费剩余预算。"""

    required = select_required(
        request, selection, quorum, budget, options, capabilities
    )
    selected: dict[SlotKind, list[ReviewerRoleOption]] = {"required": required}
    used_ids = {item.role_contract.role_profile_id for item in required}
    covered = covered_capabilities(required, capabilities, request)
    for kind, limit in _supplemental_limits(selection):
        chosen = _select_supplemental(
            kind=kind,
            limit=limit,
            options=options,
            used_ids=used_ids,
            covered=covered,
            selected=selected,
            budget=budget,
            capabilities=capabilities,
            request=request,
        )
        selected[kind] = chosen
        used_ids.update(item.role_contract.role_profile_id for item in chosen)
        covered.update(covered_capabilities(chosen, capabilities, request))
    return selected


def _select_supplemental(
    *,
    kind: SlotKind,
    limit: int,
    options: Sequence[ReviewerRoleOption],
    used_ids: set[str],
    covered: set[str],
    selected: dict[SlotKind, list[ReviewerRoleOption]],
    budget: ReviewerBudgetPolicy,
    capabilities: dict[str, CapabilityDefinition],
    request: ReviewerPlanRequest,
) -> list[ReviewerRoleOption]:
    chosen: list[ReviewerRoleOption] = []
    existing = [value for values in selected.values() for value in values]
    candidates = [
        item
        for item in options
        if kind in item.eligible_slot_kinds
        and item.role_contract.role_profile_id not in used_ids
        and option_within_own_ceiling(item)
        and distinct_from(item, existing)
    ]
    while len(chosen) < limit:
        ranked = _rank_supplemental(
            kind, candidates, chosen, covered, capabilities, request
        )
        if not ranked:
            break
        accepted = next(
            (
                item
                for _, item in ranked
                if within_budget(_all_selected(selected, chosen, item), budget)
            ),
            None,
        )
        if accepted is None:
            break
        chosen.append(accepted)
        covered.update(option_capabilities(accepted, capabilities, request))
        candidates.remove(accepted)
    return chosen


def _rank_supplemental(
    kind: SlotKind,
    candidates: Sequence[ReviewerRoleOption],
    chosen: Sequence[ReviewerRoleOption],
    covered: set[str],
    capabilities: dict[str, CapabilityDefinition],
    request: ReviewerPlanRequest,
) -> list[tuple[int, ReviewerRoleOption]]:
    return sorted(
        (
            (-len(option_capabilities(item, capabilities, request) - covered), item)
            for item in candidates
            if distinct_from(item, chosen)
            and (
                kind != "optional"
                or option_capabilities(item, capabilities, request) - covered
            )
        ),
        key=lambda pair: (pair[0], *option_key(pair[1])),
    )


def _supplemental_limits(
    policy: ReviewerSelectionPolicy,
) -> tuple[tuple[SlotKind, int], ...]:
    return (
        ("optional", policy.optional_slot_limit),
        ("advisory", policy.advisory_slot_limit),
        ("shadow", policy.shadow_slot_limit),
    )


def _all_selected(
    selected: dict[SlotKind, list[ReviewerRoleOption]],
    current: Sequence[ReviewerRoleOption],
    candidate: ReviewerRoleOption,
) -> list[ReviewerRoleOption]:
    return [item for values in selected.values() for item in values] + [
        *current,
        candidate,
    ]
