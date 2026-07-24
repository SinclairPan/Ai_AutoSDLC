"""冻结 Binding Policy 的跨 Slot 独立性校验。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.binding_independence import (
    validate_canonical_independence_proofs,
)
from ai_sdlc.core.stage_review.binding_policy import (
    BindingPolicy,
    independence_satisfies,
)
from ai_sdlc.core.stage_review.binding_result_models import (
    BindingIndependenceProof,
    ReviewerBinding,
)
from ai_sdlc.core.stage_review.binding_validation import BindingRefusal


def validate_binding_independence(
    *,
    required_slot_ids: tuple[str, ...],
    bindings: tuple[ReviewerBinding, ...],
    proofs: tuple[BindingIndependenceProof, ...],
    policy: BindingPolicy,
) -> None:
    """要求所有 Required Slot 对都满足冻结的 Binding Policy。"""

    try:
        validate_canonical_independence_proofs(bindings, proofs)
    except ValueError as exc:
        raise BindingRefusal(
            "independence_unproven",
            "binding.policy-independence-unproven",
        ) from exc
    if not policy.require_independent_blocking_slots or len(required_slot_ids) < 2:
        return
    required = set(required_slot_ids)
    expected_pairs = len(required) * (len(required) - 1) // 2
    related = tuple(
        item
        for item in proofs
        if item.left_slot_id in required and item.right_slot_id in required
    )
    if len(related) != expected_pairs or any(
        not independence_satisfies(
            item.independence_grade,
            policy.minimum_blocking_independence_grade,
        )
        for item in related
    ):
        raise BindingRefusal(
            "independence_unproven",
            "binding.policy-independence-unproven",
        )


__all__ = ["validate_binding_independence"]
