"""BudgetGrant Session 提交前的血缘与审批代次校验。"""

from __future__ import annotations

from contextlib import AbstractContextManager

from ai_sdlc.core.stage_review.session_authority import hard_budget_reached
from ai_sdlc.core.stage_review.session_budget_grant_authority_contracts import (
    BudgetGrantApprovalResolver,
)
from ai_sdlc.core.stage_review.session_contracts import (
    BudgetGrantApplyCommand,
    BudgetGrantApprovalChangedError,
    BudgetGrantReconcileCommand,
    SessionIntegrityError,
)
from ai_sdlc.core.stage_review.session_models import StageReviewSession


def _approval_commit_guard(
    command: BudgetGrantApplyCommand,
    resolver: BudgetGrantApprovalResolver | None,
) -> AbstractContextManager[None]:
    expected = command.decision.approval_state
    if resolver is None or resolver.authority_id != expected.authority_id:
        raise BudgetGrantApprovalChangedError(
            "budget grant approval changed before session apply"
        )
    return resolver.hold_session_apply(
        expected,
        decision_digest=command.decision.decision_digest,
        command_id=command.command_id,
    )


def _validate_apply_base(
    session: StageReviewSession,
    command: BudgetGrantApplyCommand,
) -> None:
    application = command.application
    grant = application.grant
    valid = (
        session.state == "needs_user",
        session.budget_resume_state is not None,
        session.pending_budget_grant_command_id == command.request_command_id,
        grant.requested_event_digest == command.request_event_digest,
        grant.expected_budget_revision == session.budget_revision,
        grant.final_reservation_id == session.resource_reservation_id,
        application.previous_reservation_digest == session.resource_reservation_digest,
        application.reservation.usage == session.resource_usage,
        command.decision.resource_reservation.usage == session.resource_usage,
        not hard_budget_reached(command.decision.resource_reservation),
        command.decision.decision_kind == "session_apply",
        command.decision.grant.grant_digest == grant.grant_digest,
        command.decision.request_proof_digest == application.request_proof_digest,
    )
    if not all(valid):
        raise SessionIntegrityError("budget grant session apply lineage is invalid")


def _validate_reconcile_base(
    session: StageReviewSession,
    command: BudgetGrantReconcileCommand,
) -> None:
    reconciliation = command.reconciliation
    application = reconciliation.application
    grant = application.grant
    valid = (
        session.state == "needs_user",
        session.budget_resume_state is not None,
        session.pending_budget_grant_command_id == command.request_command_id,
        grant.requested_event_digest == command.request_event_digest,
        grant.expected_budget_revision == session.budget_revision,
        grant.final_reservation_id == session.resource_reservation_id,
        session.resource_usage.fits_within(
            reconciliation.resource_operation.target_event.reservation.usage
        ),
    )
    if not all(valid):
        raise SessionIntegrityError("budget grant reconciliation lineage is invalid")
