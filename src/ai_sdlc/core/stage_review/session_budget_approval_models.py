"""用户或治理系统签发的可信 BudgetGrant 审批事实。"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.resource_builders import parse_utc
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class BudgetGrantApproval(ArtifactCompatibility):
    """精确绑定 Session、Reservation、Revision 与增量的批准。"""

    model_config = _MODEL_CONFIG

    schema_version: Literal["budget-grant-approval.v1"] = "budget-grant-approval.v1"
    approval_id: str
    scope: FindingScope
    final_reservation_id: str
    final_reservation_digest: str
    final_reservation_revision: int
    final_fencing_token: int
    expected_budget_revision: int
    increment: ResourceAmounts
    authority_id: str
    approved_at: str
    approval_digest: str = ""

    @field_validator(
        "approval_id",
        "final_reservation_id",
        "final_reservation_digest",
        "authority_id",
    )
    @classmethod
    def _identity_is_present(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("budget grant approval identity is invalid")
        return value

    @model_validator(mode="after")
    def _validate_approval(self) -> BudgetGrantApproval:
        valid = (
            self.expected_budget_revision >= 0,
            self.final_reservation_revision >= 1,
            self.final_fencing_token >= 1,
            self.increment.any_positive(),
        )
        if not all(valid):
            raise ValueError("budget grant approval authority is invalid")
        parse_utc(self.approved_at)
        return fill_artifact_digest(self, "approval_digest")


class BudgetGrantApprovalState(ArtifactCompatibility):
    """审批治理 Authority 的单调状态与 ABA 防护代次。"""

    model_config = _MODEL_CONFIG

    schema_version: Literal["budget-grant-approval-state.v1"] = (
        "budget-grant-approval-state.v1"
    )
    authority_id: str
    approval_digest: str
    generation: int = Field(ge=1)
    active: bool
    state_digest: str = ""

    @field_validator("authority_id", "approval_digest")
    @classmethod
    def _identity_is_present(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("budget grant approval state identity is invalid")
        return value

    @model_validator(mode="after")
    def _validate_state(self) -> BudgetGrantApprovalState:
        return fill_artifact_digest(self, "state_digest")
