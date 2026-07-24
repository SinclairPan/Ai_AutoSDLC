"""Stage Close 使用的唯一产品级 Review Executor 组合入口。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from ai_sdlc.core.config import load_project_config
from ai_sdlc.core.stage_review.canonical_stage_review_support import needs_user
from ai_sdlc.core.stage_review.close_gate_models import (
    GateApplicabilityDecision,
    PreparedStageClose,
)
from ai_sdlc.core.stage_review.codex_review_runtime import CodexStageReviewExecutor
from ai_sdlc.core.stage_review.shadow_planning_runtime import (
    ShadowPlanningPreflight,
)
from ai_sdlc.core.stage_review.stage_review_execution import (
    StageCloseGateUnavailableError,
    StageReviewExecutionOutcome,
    StageReviewExecutionRequest,
    StageReviewExecutor,
)


class StageReviewRuntime(StageReviewExecutor, Protocol):
    def enforce_close(
        self,
        prepared: PreparedStageClose,
        decision: GateApplicabilityDecision,
        preflight: ShadowPlanningPreflight,
        writer: Callable[[], object],
    ) -> object: ...


class UnavailableStageReviewExecutor:
    """宿主没有可证明的可信远端 Provider 时显式拒绝。"""

    def __init__(self, reason_code: str) -> None:
        self._reason_code = reason_code

    def execute(
        self,
        request: StageReviewExecutionRequest,
    ) -> StageReviewExecutionOutcome:
        del request
        return needs_user(self._reason_code)

    def enforce_close(
        self,
        prepared: PreparedStageClose,
        decision: GateApplicabilityDecision,
        preflight: ShadowPlanningPreflight,
        writer: Callable[[], object],
    ) -> object:
        del prepared, decision, preflight, writer
        raise StageCloseGateUnavailableError(self._reason_code)


def build_stage_review_executor(root: Path) -> StageReviewRuntime:
    """解析产品运行时；不得用本地替身或合成结果降级。"""

    config = load_project_config(root)
    if config.agent_target.strip().lower() != "codex":
        return UnavailableStageReviewExecutor("review-provider-unavailable")
    return CodexStageReviewExecutor(root)


__all__ = ["UnavailableStageReviewExecutor", "build_stage_review_executor"]
