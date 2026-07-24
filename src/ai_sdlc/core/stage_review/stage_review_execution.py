"""Stage Close Gateway 到唯一 Review Session 执行组合的内部端口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, model_validator

from ai_sdlc.core.source_snapshot import SourceSnapshot
from ai_sdlc.core.stage_review.activation_models import GateMode
from ai_sdlc.core.stage_review.candidate import CandidateManifest
from ai_sdlc.core.stage_review.panel_models import ReviewerBudgetPolicy
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan
from ai_sdlc.core.stage_review.resources import ResourceGovernor
from ai_sdlc.core.stage_review.shadow_planner import ShadowPanelProposal


class StageReviewExecutionOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["completed", "needs_user", "blocked"]
    reason_code: str = ""
    review_session_digest: str = ""
    review_completion_digest: str = ""

    @model_validator(mode="after")
    def _validate_outcome(self) -> Self:
        completed = self.status == "completed"
        digests = (self.review_session_digest, self.review_completion_digest)
        if completed != all(_valid_sha256(item) for item in digests):
            raise ValueError("stage review execution completion is incomplete")
        if not completed and not self.reason_code.strip():
            raise ValueError("incomplete stage review execution requires a reason")
        return self


@dataclass(frozen=True, slots=True)
class StageReviewExecutionRequest:
    candidate: CandidateManifest
    source_snapshot: SourceSnapshot
    proposal: ShadowPanelProposal
    plan: ReviewerPanelPlan
    budget_policy: ReviewerBudgetPolicy
    governor: ResourceGovernor
    lease_owner: str
    mode: GateMode

    def __post_init__(self) -> None:
        if self.proposal.request.enforcement_mode != self.mode:
            raise ValueError("stage review execution mode diverges from planner request")


class StageReviewExecutor(Protocol):
    def execute(
        self,
        request: StageReviewExecutionRequest,
    ) -> StageReviewExecutionOutcome: ...


class StageCloseGateUnavailableError(RuntimeError):
    """Enforce 关闭缺少可信执行或授权前置条件。"""


def _valid_sha256(value: str) -> bool:
    if not value.startswith("sha256:") or len(value) != 71:
        return False
    try:
        int(value[7:], 16)
    except ValueError:
        return False
    return True
