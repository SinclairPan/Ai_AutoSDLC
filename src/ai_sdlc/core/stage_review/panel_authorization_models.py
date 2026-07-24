"""Reviewer Panel 规划授权的纯值合同。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, field_validator, model_validator

from ai_sdlc.core.stage_review.contracts import StageReviewArtifactModel

PLANNING_AUTHORIZATION_VERSION: Literal["reviewer-planning-authorization/v1"] = (
    "reviewer-planning-authorization/v1"
)


class ReviewerPlanningAuthorization(StageReviewArtifactModel):
    """冻结本次 Planner 获准消费的治理工件集合。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["reviewer-planning-authorization"] = (
        "reviewer-planning-authorization"
    )
    contract_version: Literal["reviewer-planning-authorization/v1"] = (
        PLANNING_AUTHORIZATION_VERSION
    )
    registry_digest: str
    role_catalog_digest: str
    selection_policy_digest: str
    quorum_policy_digest: str
    budget_policy_digest: str
    authorization_digest: str

    @field_validator(
        "registry_digest",
        "role_catalog_digest",
        "selection_policy_digest",
        "quorum_policy_digest",
        "budget_policy_digest",
    )
    @classmethod
    def _require_digest(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("planning authorization digest reference cannot be empty")
        return value.strip()

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        from ai_sdlc.core.stage_review.panel_digests import (
            planning_authorization_digest,
        )

        if self.authorization_digest != planning_authorization_digest(self):
            raise ValueError("planning authorization digest does not match content")
        return self
