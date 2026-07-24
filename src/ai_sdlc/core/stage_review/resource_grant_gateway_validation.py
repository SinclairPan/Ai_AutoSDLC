"""Session BudgetGrant Resource 网关的可信证明与补偿判定。"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import ValidationError

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_grant_models import (
    BudgetGrant,
    BudgetGrantDecisionClaim,
    BudgetGrantOperation,
    BudgetGrantResourceError,
)
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceLedgerEvent,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.resource_store import ResourceEventStore
from ai_sdlc.core.stage_review.session_budget_approval_models import (
    BudgetGrantApprovalState,
)
from ai_sdlc.core.stage_review.session_budget_grant_authority_store import (
    BudgetGrantApplyStatus,
    BudgetGrantRequestAuthority,
)
from ai_sdlc.core.stage_review.session_budget_grant_request import (
    BudgetGrantRequestProof,
)
from ai_sdlc.core.stage_review.session_contracts import SessionIntegrityError

ReservationReader = Callable[[str], ResourceReservation]
EventReader = Callable[[str], ResourceLedgerEvent | None]


def require_grant_operation(
    store: ResourceEventStore,
    event_for: EventReader,
    grant: BudgetGrant,
    suffix: str,
    result_code: str,
) -> BudgetGrantOperation:
    success = "expanded" if suffix == "apply" else "reconciled"
    if result_code != success:
        raise BudgetGrantResourceError(result_code)
    operation_id = stable_id("budget-grant-operation", grant.idempotency_key, suffix)
    try:
        event = event_for(operation_id)
        operation = store.read_budget_grant_operations().get(operation_id)
    except (SharedStateIntegrityError, ValidationError) as exc:
        raise BudgetGrantResourceError("state_corrupt") from exc
    if (
        event is None
        or operation is None
        or event.event_digest != operation.target_event_digest
    ):
        raise BudgetGrantResourceError("state_corrupt")
    return operation


def _existing_reconciliation(
    store: ResourceEventStore,
    event_for: EventReader,
    grant: BudgetGrant,
) -> BudgetGrantOperation | None:
    operation_id = stable_id("budget-grant-operation", grant.idempotency_key, "reconcile")
    if store.read_budget_grant_operations().get(operation_id) is None:
        return None
    return require_grant_operation(
        store,
        event_for,
        grant,
        "reconcile",
        "reconciled",
    )


def verify_request(
    authority: BudgetGrantRequestAuthority,
    proof: BudgetGrantRequestProof,
) -> None:
    try:
        authority.verify_budget_grant_request(proof)
    except (SessionIntegrityError, SharedStateIntegrityError, ValidationError) as exc:
        raise BudgetGrantResourceError("state_corrupt") from exc


def _approval_state(
    authority: BudgetGrantRequestAuthority,
    proof: BudgetGrantRequestProof,
) -> BudgetGrantApprovalState:
    try:
        return authority.approval_state(proof)
    except (SessionIntegrityError, SharedStateIntegrityError, ValidationError) as exc:
        raise BudgetGrantResourceError("state_corrupt") from exc


def _require_decision(
    store: ResourceEventStore,
    decision: BudgetGrantDecisionClaim,
) -> BudgetGrantDecisionClaim:
    try:
        persisted = store.get_budget_grant_decision(decision.decision_id)
    except SharedStateIntegrityError as exc:
        raise BudgetGrantResourceError("state_corrupt") from exc
    if persisted is None or persisted != decision:
        raise BudgetGrantResourceError("state_corrupt")
    return persisted


def _validate_reconcile_decision(
    decision: BudgetGrantDecisionClaim,
    application: BudgetGrantOperation,
    proof: BudgetGrantRequestProof,
    apply_command_id: str,
) -> None:
    grant = application.grant
    if (
        decision.grant.grant_digest != grant.grant_digest
        or decision.request_proof_digest != proof.proof_digest
    ):
        raise BudgetGrantResourceError("state_corrupt")
    expected_apply_id = stable_id("budget-grant-session-apply", grant.grant_id)
    if decision.decision_kind == "session_apply" and (
        apply_command_id != expected_apply_id
    ):
        raise BudgetGrantResourceError("invalid_input")


def _decision_can_release(
    decision: BudgetGrantDecisionClaim,
    authority: BudgetGrantRequestAuthority,
    proof: BudgetGrantRequestProof,
    apply_command_id: str,
) -> bool:
    if decision.decision_kind == "reconcile":
        return True
    status = _apply_status(authority, proof, apply_command_id)
    return status == "superseded"


def _apply_status(
    authority: BudgetGrantRequestAuthority,
    proof: BudgetGrantRequestProof,
    apply_command_id: str,
) -> BudgetGrantApplyStatus:
    try:
        return authority.budget_grant_apply_status(proof, apply_command_id)
    except (SessionIntegrityError, SharedStateIntegrityError, ValidationError) as exc:
        raise BudgetGrantResourceError("state_corrupt") from exc
