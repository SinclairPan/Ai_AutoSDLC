"""ResourceGrant 被释放后的可信补偿证明。"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.resource_grant_models import (
    BudgetGrantDecisionClaim,
    BudgetGrantOperation,
)
from ai_sdlc.core.stage_review.session_budget_grant_models import (
    BudgetGrantResourceApplication,
)

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class BudgetGrantResourceReconciliation(ArtifactCompatibility):
    """资源增量已被同一 Grant 幂等释放的事实。"""

    model_config = _MODEL_CONFIG

    schema_version: Literal["budget-grant-resource-reconciliation.v1"] = (
        "budget-grant-resource-reconciliation.v1"
    )
    application: BudgetGrantResourceApplication
    decision: BudgetGrantDecisionClaim
    resource_operation: BudgetGrantOperation
    reconciliation_digest: str = ""

    @model_validator(mode="after")
    def _validate_reconciliation(self) -> BudgetGrantResourceReconciliation:
        application = self.application
        operation = self.resource_operation
        grant = application.grant
        reservation = operation.target_event.reservation
        valid = (
            operation.operation_kind == "reconciled_released",
            self.decision.grant.grant_digest == grant.grant_digest,
            self.decision.request_proof_digest == application.request_proof_digest,
            operation.grant.grant_digest == grant.grant_digest,
            operation.expected_reservation_revision >= application.reservation.revision,
            operation.expected_reservation_revision
            >= self.decision.resource_reservation_revision,
            operation.expected_reservation_digest
            == operation.target_event.previous_reservation_digest,
            reservation.budget_revision == grant.expected_budget_revision + 1,
            grant.grant_id in reservation.budget_grant_ids,
            grant.grant_id in reservation.reconciled_budget_grant_ids,
            reservation.reserved + grant.increment == application.reservation.reserved,
            reservation.hard_limits + grant.increment
            == application.reservation.hard_limits,
            application.reservation.usage.fits_within(reservation.usage),
            reservation.last_budget_grant_operation_id == operation.operation_id,
            reservation.last_operation_id == operation.operation_id,
            reservation.fencing_token > application.reservation.fencing_token,
            reservation.fencing_token > self.decision.resource_fencing_token,
        )
        if not all(valid):
            raise ValueError("budget grant resource reconciliation is invalid")
        return fill_artifact_digest(self, "reconciliation_digest")
