"""BudgetGrant 与资源侧崩溃恢复 Operation 合同。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.contracts import StageReviewArtifactModel
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_digests import (
    budget_grant_digest,
    budget_grant_operation_digest,
)
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceLedgerEvent,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.session_budget_approval_models import (
    BudgetGrantApprovalState,
)

BudgetGrantDecisionKind = Literal["session_apply", "reconcile"]


class BudgetGrantResourceError(RuntimeError):
    """资源侧 Grant 结果的稳定分类，供 Session 决定重试或终止。"""

    RETRYABLE_CODES = frozenset({"lock_unavailable"})
    INTEGRITY_CODES = frozenset({"state_corrupt", "invalid_input"})

    def __init__(self, result_code: str) -> None:
        self.result_code = result_code
        self.retryable = result_code in self.RETRYABLE_CODES
        self.integrity_failure = (
            result_code in self.INTEGRITY_CODES
            or result_code.startswith("reconciliation_")
        )
        super().__init__(f"budget grant resource failure: {result_code}")


class BudgetGrant(StageReviewArtifactModel):
    """用户授权的不可变 Session 预算增量。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["reviewer-budget-grant"] = "reviewer-budget-grant"
    grant_id: str
    project_id: str
    work_item_id: str
    stage_review_session_id: str
    final_reservation_id: str
    expected_budget_revision: int = Field(ge=0)
    increment: ResourceAmounts
    requested_event_digest: str
    grant_digest: str
    idempotency_key: str

    @field_validator(
        "project_id",
        "work_item_id",
        "stage_review_session_id",
        "final_reservation_id",
        "requested_event_digest",
    )
    @classmethod
    def _identity_is_present(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("budget grant identity cannot be empty")
        return value

    @model_validator(mode="after")
    def _verify_grant(self) -> Self:
        if not self.increment.any_positive():
            raise ValueError("budget grant increment must be positive")
        if self.grant_digest != budget_grant_digest(self):
            raise ValueError("budget grant digest does not match content")
        expected = budget_grant_idempotency_key(self)
        if self.idempotency_key != expected or self.grant_id != expected:
            raise ValueError("budget grant idempotency lineage is invalid")
        return self


class BudgetGrantOperation(StageReviewArtifactModel):
    """CAS 前落盘的完整资源目标，用于崩溃后确定性补写。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["budget-grant-resource-operation"] = (
        "budget-grant-resource-operation"
    )
    operation_id: str
    operation_kind: Literal["resource_applied", "reconciled_released"]
    grant: BudgetGrant
    expected_reservation_revision: int = Field(ge=1)
    expected_reservation_digest: str
    operation_effect_digest: str
    target_projection_digest: str
    target_event_id: str
    target_event_digest: str
    target_event: ResourceLedgerEvent
    operation_digest: str

    @model_validator(mode="after")
    def _verify_operation(self) -> Self:
        suffix = "apply" if self.operation_kind == "resource_applied" else "reconcile"
        expected_id = stable_id(
            "budget-grant-operation", self.grant.idempotency_key, suffix
        )
        target = self.target_event
        expected = (
            self.operation_id == expected_id,
            target.operation_id == self.operation_id,
            target.reservation.operation_effect_digest == self.operation_effect_digest,
            target.reservation.reservation_digest == self.target_projection_digest,
            target.event_id == self.target_event_id,
            target.event_digest == self.target_event_digest,
        )
        if not all(expected):
            raise ValueError("budget grant operation target lineage is invalid")
        if self.operation_digest != budget_grant_operation_digest(self):
            raise ValueError("budget grant operation digest does not match content")
        return self


class BudgetGrantDecisionClaim(ArtifactCompatibility):
    """Resource 锁域内线性化 Session apply 与 reconcile 的唯一决策。"""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    schema_version: Literal["budget-grant-decision-claim.v1"] = (
        "budget-grant-decision-claim.v1"
    )
    decision_id: str
    decision_kind: BudgetGrantDecisionKind
    grant: BudgetGrant
    request_proof_digest: str
    approval_state: BudgetGrantApprovalState
    resource_reservation_revision: int = Field(ge=1)
    resource_reservation_digest: str
    resource_fencing_token: int = Field(ge=1)
    resource_reservation: ResourceReservation
    claimed_at: str
    decision_digest: str = ""

    @model_validator(mode="after")
    def _verify_decision(self) -> Self:
        from ai_sdlc.core.stage_review.resource_builders import parse_utc

        expected_id = stable_id("budget-grant-decision", self.grant.idempotency_key)
        identities = (
            self.request_proof_digest,
            self.resource_reservation_digest,
        )
        reservation = self.resource_reservation
        valid = (
            self.decision_id == expected_id,
            not any(not item.strip() for item in identities),
            reservation.reservation_id == self.grant.final_reservation_id,
            reservation.revision == self.resource_reservation_revision,
            reservation.reservation_digest == self.resource_reservation_digest,
            reservation.fencing_token == self.resource_fencing_token,
            self.grant.grant_id in reservation.budget_grant_ids,
            self.grant.grant_id not in reservation.reconciled_budget_grant_ids,
            self.approval_state.approval_digest
            != "",
            self.approval_state.active or self.decision_kind == "reconcile",
        )
        if not all(valid):
            raise ValueError("budget grant decision lineage is invalid")
        parse_utc(self.claimed_at)
        return fill_artifact_digest(self, "decision_digest")


def budget_grant_idempotency_key(grant: BudgetGrant) -> str:
    return stable_id(
        "budget-grant",
        grant.work_item_id,
        grant.stage_review_session_id,
        grant.grant_digest,
        str(grant.expected_budget_revision),
        grant.final_reservation_id,
    )
