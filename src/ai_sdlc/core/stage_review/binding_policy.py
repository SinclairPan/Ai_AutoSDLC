"""版本化 Provider Binding 独立性策略。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.binding_models import IndependenceGrade

_GRADE_ORDER: tuple[IndependenceGrade, ...] = (
    "session_independent",
    "provider_independent",
    "model_diversity_proven",
)


class BindingPolicy(ArtifactCompatibility):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["reviewer-binding-policy.v1"] = (
        "reviewer-binding-policy.v1"
    )
    artifact_kind: Literal["reviewer-binding-policy"] = "reviewer-binding-policy"
    version: str
    require_independent_blocking_slots: bool
    minimum_blocking_independence_grade: IndependenceGrade
    policy_digest: str = ""

    @model_validator(mode="after")
    def _verify_policy(self) -> Self:
        if not self.version.strip():
            raise ValueError("binding policy version is missing")
        if (
            not self.require_independent_blocking_slots
            and self.minimum_blocking_independence_grade != "session_independent"
        ):
            raise ValueError("disabled binding independence cannot require a higher grade")
        return fill_artifact_digest(self, "policy_digest")


def baseline_binding_policy() -> BindingPolicy:
    return BindingPolicy(
        version="1.0.0",
        require_independent_blocking_slots=True,
        minimum_blocking_independence_grade="session_independent",
    )


def independence_satisfies(
    actual: IndependenceGrade,
    required: IndependenceGrade,
) -> bool:
    return _GRADE_ORDER.index(actual) >= _GRADE_ORDER.index(required)


__all__ = [
    "BindingPolicy",
    "baseline_binding_policy",
    "independence_satisfies",
]
