"""StageReviewSession 未完成操作与 BudgetGrant 的确定性恢复。"""

from __future__ import annotations

from collections.abc import Callable

from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.session_budget_grants import SessionBudgetGrantOps
from ai_sdlc.core.stage_review.session_contracts import (
    BudgetGrantRequestCommand,
    SessionIntegrityError,
    parse_session_command,
)
from ai_sdlc.core.stage_review.session_models import SessionMutationResult
from ai_sdlc.core.stage_review.session_store import SessionEventStore


def _resume_pending_session(
    store: SessionEventStore,
    budget: SessionBudgetGrantOps,
    dispatch: Callable[[object], SessionMutationResult],
    scope: FindingScope,
    incoming_id: str,
) -> None:
    pending = store.pending_operation(scope)
    if pending is not None and pending.command_id == incoming_id:
        return
    if pending is not None:
        command = parse_session_command(pending.command_type, pending.command_payload)
        dispatch(command)
        if store.pending_operation(scope) is not None:
            raise SessionIntegrityError("session operation recovery did not complete")
    session = store.rebuild(scope)
    if session is None or not session.pending_budget_grant_command_id:
        return
    request_id = session.pending_budget_grant_command_id
    if request_id == incoming_id:
        return
    request_operation = store.get_operation(scope, request_id)
    if request_operation is None:
        raise SessionIntegrityError("pending budget grant request operation is missing")
    request = parse_session_command(
        request_operation.command_type,
        request_operation.command_payload,
    )
    if not isinstance(request, BudgetGrantRequestCommand):
        raise SessionIntegrityError("pending budget grant request type is invalid")
    budget.extend(request)
    recovered = store.rebuild(scope)
    if recovered is None or recovered.pending_budget_grant_command_id:
        raise SessionIntegrityError("pending budget grant recovery did not complete")
