"""Binding 间独立性的唯一规范化计算与校验。"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Protocol

from ai_sdlc.core.stage_review.binding_models import IndependenceGrade


class BindingIndependenceView(Protocol):
    slot_id: str
    model_family: str
    physical_provider_id: str
    physical_equivalence_class_id: str
    supported_independence_grade: IndependenceGrade


class IndependenceProofView(Protocol):
    left_slot_id: str
    right_slot_id: str
    independence_grade: IndependenceGrade
    reason_id: str


@dataclass(frozen=True, slots=True)
class _CanonicalIndependenceProof:
    left_slot_id: str
    right_slot_id: str
    independence_grade: IndependenceGrade
    reason_id: str


def _canonical_independence_proofs(
    bindings: tuple[BindingIndependenceView, ...],
) -> tuple[_CanonicalIndependenceProof, ...]:
    ordered = tuple(sorted(bindings, key=lambda item: item.slot_id))
    return tuple(
        _CanonicalIndependenceProof(
            left_slot_id=left.slot_id,
            right_slot_id=right.slot_id,
            independence_grade=_independence_grade(left, right),
            reason_id=_independence_reason(left, right),
        )
        for left, right in combinations(ordered, 2)
    )


def validate_canonical_independence_proofs(
    bindings: tuple[BindingIndependenceView, ...],
    proofs: tuple[IndependenceProofView, ...],
) -> None:
    actual = tuple(
        _CanonicalIndependenceProof(
            item.left_slot_id,
            item.right_slot_id,
            item.independence_grade,
            item.reason_id,
        )
        for item in proofs
    )
    if actual != _canonical_independence_proofs(bindings):
        raise ValueError("binding independence proof differs from physical identity")


def _independence_grade(
    left: BindingIndependenceView,
    right: BindingIndependenceView,
) -> IndependenceGrade:
    same_physical = (
        left.physical_equivalence_class_id == right.physical_equivalence_class_id
        or left.physical_provider_id == right.physical_provider_id
    )
    actual: IndependenceGrade = (
        "session_independent"
        if same_physical or left.model_family == right.model_family
        else "model_diversity_proven"
    )
    grades: tuple[IndependenceGrade, ...] = (
        "session_independent",
        "provider_independent",
        "model_diversity_proven",
    )
    return grades[
        min(
            grades.index(actual),
            grades.index(left.supported_independence_grade),
            grades.index(right.supported_independence_grade),
        )
    ]


def _independence_reason(
    left: BindingIndependenceView,
    right: BindingIndependenceView,
) -> str:
    if (
        left.physical_equivalence_class_id == right.physical_equivalence_class_id
        or left.physical_provider_id == right.physical_provider_id
    ):
        return "binding.independence.physical-session-only"
    if left.model_family == right.model_family:
        return "binding.independence.session-only"
    return "binding.independence.model-diversity"


__all__ = [
    "validate_canonical_independence_proofs",
]
