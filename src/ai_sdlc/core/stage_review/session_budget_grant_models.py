"""Session 消费的受信 BudgetGrant 资源侧提交证明。"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.resource_grant_models import (
    BudgetGrant,
    BudgetGrantOperation,
)
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class BudgetGrantResourceApplication(ArtifactCompatibility):
    """ResourceGovernor 已完成扩容且可由 Session 精确验证的证明。"""

    model_config = _MODEL_CONFIG

    schema_version: Literal["budget-grant-resource-application.v1"] = (
        "budget-grant-resource-application.v1"
    )
    grant: BudgetGrant
    request_proof_digest: str
    previous_reservation_digest: str
    reservation: ResourceReservation
    resource_operation: BudgetGrantOperation
    application_digest: str = ""

    @property
    def resource_operation_id(self) -> str:
        return self.resource_operation.operation_id

    @property
    def resource_operation_digest(self) -> str:
        return self.resource_operation.operation_digest

    @property
    def resource_event_digest(self) -> str:
        return self.resource_operation.target_event_digest

    @model_validator(mode="after")
    def _validate_application(self) -> BudgetGrantResourceApplication:
        grant = self.grant
        reservation = self.reservation
        operation = self.resource_operation
        lineage = (
            operation.operation_kind == "resource_applied",
            operation.grant.grant_digest == grant.grant_digest,
            operation.expected_reservation_digest
            == self.previous_reservation_digest,
            operation.target_event.reservation == reservation,
            reservation.project_id == grant.project_id,
            reservation.work_item_id == grant.work_item_id,
            reservation.stage_review_session_id == grant.stage_review_session_id,
            reservation.reservation_id == grant.final_reservation_id,
            reservation.budget_revision == grant.expected_budget_revision + 1,
            grant.grant_id in reservation.budget_grant_ids,
            grant.grant_id not in reservation.reconciled_budget_grant_ids,
            reservation.last_budget_grant_operation_id
            == operation.operation_id,
            reservation.last_operation_id == operation.operation_id,
            reservation.reservation_digest != self.previous_reservation_digest,
        )
        identities = (
            self.request_proof_digest,
            self.previous_reservation_digest,
        )
        if not all(lineage) or any(not item.strip() for item in identities):
            raise ValueError("budget grant resource application lineage is invalid")
        return fill_artifact_digest(self, "application_digest")
