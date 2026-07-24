"""Prospective Shadow Provider 执行端口与单窗口调用上限。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ai_sdlc.core.stage_review.optimization.controller_models import OptimizationEpoch
from ai_sdlc.core.stage_review.optimization.models import OptimizationCandidate
from ai_sdlc.core.stage_review.optimization.shadow import (
    OptimizationShadowAssignment,
)
from ai_sdlc.core.stage_review.optimization.shadow_observations import (
    OptimizationShadowObservationStore,
)


class ShadowAssignmentExecutor(Protocol):
    def execute(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        assignment: OptimizationShadowAssignment,
        authorize_effect: Callable[[], None],
    ) -> bool: ...


class ShadowExecutionNoChangeError(RuntimeError):
    """候选无法在受治理 Shadow 预算内形成可比较结果。"""


class ShadowExecutionUnrecoverableError(RuntimeError):
    """Provider 已进入不可安全重试的 Shadow 调用窗口。"""


def execute_pending_assignments(
    *,
    executor: ShadowAssignmentExecutor | None,
    observations: OptimizationShadowObservationStore,
    epoch: OptimizationEpoch,
    candidate: OptimizationCandidate,
    assignments: tuple[OptimizationShadowAssignment, ...],
    maximum_provider_calls: int,
    authorize_effect: Callable[[], None],
) -> int:
    if executor is None or maximum_provider_calls <= 0:
        return 0
    pending = tuple(
        item
        for item in assignments
        if observations.read_assignment(item.assignment_id) is None
    )
    completed = 0
    for assignment in pending[:maximum_provider_calls]:
        authorize_effect()
        if executor.execute(epoch, candidate, assignment, authorize_effect):
            completed += 1
    return completed


__all__ = [
    "ShadowAssignmentExecutor",
    "ShadowExecutionNoChangeError",
    "ShadowExecutionUnrecoverableError",
    "execute_pending_assignments",
]
