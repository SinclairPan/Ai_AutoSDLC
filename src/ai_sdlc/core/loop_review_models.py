"""Minimal persisted values for bounded Loop-native expert review."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.review_kernel import (
    LoopReviewType,
    ReviewExecutionStatus,
    ReviewFinding,
)

ReviewOverlayStatus = Literal[
    "review_missing",
    "failed",
    "needs_fix",
    "needs_user",
    "passed",
]


class LoopReviewOutcome(BaseModel):
    """One immutable completed result or retryable failed execution."""

    model_config = ConfigDict(extra="forbid")

    loop_id: str
    loop_type: LoopReviewType
    round_number: int = Field(ge=1, le=2)
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: ReviewExecutionStatus
    expert_roles: list[str] = Field(min_length=1, max_length=2)
    findings: list[ReviewFinding] = Field(default_factory=list)
    failure_kind: str = ""
    failure_reason: str = ""
    recorded_at: str

    @field_validator("loop_id", "recorded_at")
    @classmethod
    def _require_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("review outcome text is required")
        return text

    @model_validator(mode="after")
    def _validate_outcome_shape(self) -> LoopReviewOutcome:
        roles = [role.strip() for role in self.expert_roles]
        if any(not role for role in roles):
            raise ValueError("expert role cannot be empty")
        if len(roles) != len(set(roles)):
            raise ValueError("expert roles must be unique")
        if {finding.role for finding in self.findings} - set(roles):
            raise ValueError("finding role must be selected for this outcome")
        self.expert_roles = roles

        failure_kind = self.failure_kind.strip()
        failure_reason = self.failure_reason.strip()
        if self.status == "completed":
            if failure_kind or failure_reason:
                raise ValueError("completed outcome cannot carry failure state")
        else:
            if not failure_kind or not failure_reason:
                raise ValueError("failed outcome requires failure details")
            if self.findings:
                raise ValueError("failed outcome cannot carry findings")
        self.failure_kind = failure_kind
        self.failure_reason = failure_reason
        return self


class ReviewStatusOverlay(BaseModel):
    """Derived display state; never a reusable Close credential."""

    model_config = ConfigDict(extra="forbid")

    status: ReviewOverlayStatus
    reason: str
    next_action: str
    round_number: int = Field(ge=0, le=2)

    @field_validator("reason", "next_action")
    @classmethod
    def _require_overlay_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("review overlay text is required")
        return text


__all__ = [
    "LoopReviewOutcome",
    "ReviewOverlayStatus",
    "ReviewStatusOverlay",
]
