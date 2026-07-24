"""BudgetGrant 的可信审批、请求证明与增量充分性校验。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.resource_grant_models import BudgetGrant
from ai_sdlc.core.stage_review.resource_grants import build_budget_grant
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.session_authority import hard_budget_reached
from ai_sdlc.core.stage_review.session_budget_approval_models import (
    BudgetGrantApproval,
)
from ai_sdlc.core.stage_review.session_budget_grant_authority_contracts import (
    BudgetGrantApprovalResolver,
)
from ai_sdlc.core.stage_review.session_budget_grant_request import (
    BudgetGrantRequestProof,
)
from ai_sdlc.core.stage_review.session_contracts import (
    BudgetGrantRequestCommand,
    SessionIntegrityError,
)
from ai_sdlc.core.stage_review.session_models import SessionEvent, StageReviewSession
from ai_sdlc.core.stage_review.session_runtime import SessionRuntime


def _require_budget_grant_approval(
    runtime: SessionRuntime,
    resolver: BudgetGrantApprovalResolver | None,
    command: BudgetGrantRequestCommand,
    session: StageReviewSession,
) -> tuple[BudgetGrantApproval, ResourceReservation]:
    reservation = runtime.resolver.resolve_reservation(
        session.resource_reservation_digest
    )
    if (
        reservation is None
        or session.state != "needs_user"
        or session.budget_resume_state is None
        or not hard_budget_reached(reservation)
    ):
        raise SessionIntegrityError("budget grant requires a hard budget stop")
    if resolver is None:
        raise SessionIntegrityError("budget grant approval authority is missing")
    approval = resolver.resolve(command.approval_digest)
    state = resolver.approval_state(command.approval_digest)
    if approval is None or state is None or not state.active:
        raise SessionIntegrityError("budget grant approval is not trusted")
    valid = (
        not session.pending_budget_grant_command_id,
        approval.approval_digest == command.approval_digest,
        state.approval_digest == command.approval_digest,
        state.authority_id == resolver.authority_id,
        approval.scope == command.scope,
        approval.final_reservation_id == session.resource_reservation_id,
        approval.final_reservation_digest == session.resource_reservation_digest,
        approval.final_reservation_revision == reservation.revision,
        approval.final_fencing_token == session.resource_fencing_epoch,
        approval.expected_budget_revision == command.expected_budget_revision,
        approval.expected_budget_revision == session.budget_revision,
        approval.increment == command.increment,
    )
    if not all(valid):
        raise SessionIntegrityError("budget grant approval authority diverged")
    _require_sufficient_increment(reservation, command.increment)
    return approval, reservation


def _require_sufficient_increment(
    reservation: ResourceReservation,
    increment: ResourceAmounts,
) -> None:
    exhausted = tuple(
        name
        for name in ResourceAmounts.ALL_FIELDS
        if getattr(reservation.hard_limits, name) > 0
        and getattr(reservation.usage, name) >= getattr(reservation.hard_limits, name)
    )
    resolves = all(
        getattr(reservation.usage, name)
        < getattr(reservation.hard_limits, name) + getattr(increment, name)
        for name in exhausted
    )
    if not exhausted or not resolves:
        raise SessionIntegrityError("budget grant does not resolve hard budget")


def _build_session_budget_grant(
    command: BudgetGrantRequestCommand,
    session: StageReviewSession,
    event: SessionEvent,
) -> BudgetGrant:
    return build_budget_grant(
        project_id=command.scope.project_id,
        work_item_id=command.scope.work_item_id,
        stage_review_session_id=command.scope.session_id,
        final_reservation_id=session.resource_reservation_id,
        expected_budget_revision=command.expected_budget_revision,
        increment=command.increment,
        requested_event_digest=event.event_digest,
    )


def _build_request_proof(
    runtime: SessionRuntime,
    command: BudgetGrantRequestCommand,
    event: SessionEvent,
) -> BudgetGrantRequestProof:
    operation = runtime.store.get_operation(command.scope, command.command_id)
    if operation is None or len(event.artifact_refs) != 1:
        raise SessionIntegrityError("budget grant request authority is missing")
    approval = runtime.store.get_budget_grant_approval(
        command.scope,
        event.artifact_refs[0].artifact_id,
    )
    proof = BudgetGrantRequestProof(
        approval=approval,
        request_operation=operation,
        requested_event=event,
    )
    runtime.store.persist_budget_grant_request_proof(proof)
    return proof


def _approval_is_active(
    resolver: BudgetGrantApprovalResolver | None,
    approval_digest: str,
) -> bool:
    if resolver is None:
        return False
    state = resolver.approval_state(approval_digest)
    return state is not None and state.active
