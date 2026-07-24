"""优化流水线的 Candidate registry 与维护预算前置校验。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.controller_models import (
    MaintenanceBudget,
    OptimizationEpoch,
)
from ai_sdlc.core.stage_review.optimization.models import OptimizationCandidate


def require_epoch_domain_registry(
    epoch: OptimizationEpoch,
    configured_registry_digest: str,
) -> None:
    if configured_registry_digest and (
        epoch.candidate_domain_registry_digest != configured_registry_digest
    ):
        raise SharedStateIntegrityError(
            "optimization epoch candidate domain registry diverged"
        )


def require_candidate_domain_registry(
    epoch: OptimizationEpoch,
    candidates: tuple[OptimizationCandidate, ...],
) -> None:
    if any(
        item.domain_registry_digest != epoch.candidate_domain_registry_digest
        for item in candidates
    ):
        raise SharedStateIntegrityError(
            "optimization candidate domain registry diverged"
        )


def candidate_budget_fits(
    candidates: tuple[OptimizationCandidate, ...],
    budget: MaintenanceBudget,
) -> bool:
    return (
        sum(item.estimated_provider_calls for item in candidates)
        <= budget.maximum_provider_calls
        and sum(item.estimated_tokens for item in candidates) <= budget.maximum_tokens
        and sum(item.estimated_cost for item in candidates) <= budget.maximum_cost
        and sum(item.estimated_active_wall_clock for item in candidates)
        <= budget.maximum_active_wall_clock
    )


__all__ = [
    "candidate_budget_fits",
    "require_candidate_domain_registry",
    "require_epoch_domain_registry",
]
