"""BudgetGrant 的 Session 侧 CAS 前置 Operation。"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_grant_models import BudgetGrantDecisionClaim
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.session_budget_grant_models import (
    BudgetGrantResourceApplication,
)
from ai_sdlc.core.stage_review.session_budget_reconciliation_models import (
    BudgetGrantResourceReconciliation,
)
from ai_sdlc.core.stage_review.session_models import SessionEvent

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class SessionBudgetGrantOperation(ArtifactCompatibility):
    """在追加 session_applied 事件前持久化的完整目标。"""

    model_config = _MODEL_CONFIG

    schema_version: Literal["budget-grant-session-operation.v1"] = (
        "budget-grant-session-operation.v1"
    )
    operation_id: str
    operation_kind: Literal["session_applied", "reconciled_released"]
    request_command_id: str
    apply_command_id: str
    application: BudgetGrantResourceApplication
    decision: BudgetGrantDecisionClaim
    reconciliation: BudgetGrantResourceReconciliation | None = None
    expected_session_revision: int = Field(ge=1)
    expected_session_digest: str
    operation_effect_digest: str
    target_projection_digest: str
    target_event_id: str
    target_event_digest: str
    target_event: SessionEvent
    operation_digest: str = ""

    @model_validator(mode="after")
    def _validate_operation(self) -> SessionBudgetGrantOperation:
        if not all(_operation_expectations(self)):
            raise ValueError("budget grant session operation target is invalid")
        return fill_artifact_digest(self, "operation_digest")


def _session_grant_effect_digest(
    operation_kind: str,
    grant_digest: str,
    resource_proof_digest: str,
    expected_session_digest: str,
) -> str:
    return canonical_digest(
        {
            "effect_kind": "budget_grant_session_transition",
            "operation_kind": operation_kind,
            "grant_digest": grant_digest,
            "resource_proof_digest": resource_proof_digest,
            "expected_session_digest": expected_session_digest,
        },
        CanonicalizationPolicy(),
    )


def _resource_proof_digest(operation: SessionBudgetGrantOperation) -> str:
    if operation.reconciliation is not None:
        return operation.reconciliation.reconciliation_digest
    return operation.decision.decision_digest


def _operation_expectations(
    operation: SessionBudgetGrantOperation,
) -> tuple[bool, ...]:
    grant = operation.application.grant
    event = operation.target_event
    projection = event.projection_after
    expected_id = stable_id(
        "budget-grant-session-operation",
        grant.idempotency_key,
        operation.operation_kind,
    )
    expected_effect = _session_grant_effect_digest(
        operation.operation_kind,
        grant.grant_digest,
        _resource_proof_digest(operation),
        operation.expected_session_digest,
    )
    applying = operation.operation_kind == "session_applied"
    resource = _operation_resource(operation)
    grant_ids = _operation_grant_ids(operation)
    ref_valid = len(event.artifact_refs) == 1 and (
        event.artifact_refs[0].artifact_id == grant.grant_id
        and event.artifact_refs[0].artifact_digest == _resource_proof_digest(operation)
    )
    decision_valid = _decision_is_valid(operation, applying)
    return (
        bool(operation.request_command_id),
        operation.operation_id == expected_id,
        operation.operation_effect_digest == expected_effect,
        decision_valid,
        operation.decision.grant.grant_digest == grant.grant_digest,
        operation.decision.request_proof_digest
        == operation.application.request_proof_digest,
        event.event_kind
        == ("budget_grant_applied" if applying else "budget_grant_reconciled"),
        event.command_id == operation.apply_command_id,
        event.event_id == operation.target_event_id,
        event.event_digest == operation.target_event_digest,
        canonical_digest(projection, CanonicalizationPolicy())
        == operation.target_projection_digest,
        projection.last_budget_grant_operation_id == operation.operation_id,
        projection.budget_grant_operation_effect_digest == expected_effect,
        grant.grant_id in grant_ids,
        (operation.reconciliation is None) == applying,
        projection.resource_reservation_digest == resource.reservation_digest,
        projection.resource_fencing_epoch == resource.fencing_token,
        ref_valid,
    )


def _decision_is_valid(
    operation: SessionBudgetGrantOperation,
    applying: bool,
) -> bool:
    if applying:
        return operation.decision.decision_kind == "session_apply"
    return (
        operation.reconciliation is not None
        and operation.decision == operation.reconciliation.decision
    )


def _operation_resource(
    operation: SessionBudgetGrantOperation,
) -> ResourceReservation:
    if operation.reconciliation is None:
        return operation.decision.resource_reservation
    return operation.reconciliation.resource_operation.target_event.reservation


def _operation_grant_ids(
    operation: SessionBudgetGrantOperation,
) -> tuple[str, ...]:
    projection = operation.target_event.projection_after
    if operation.operation_kind == "session_applied":
        return projection.budget_grant_ids
    return projection.reconciled_budget_grant_ids
