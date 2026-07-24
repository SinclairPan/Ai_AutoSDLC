"""Pending BudgetGrant apply 的可信恢复编排。"""

from __future__ import annotations

from typing import Protocol, cast

from ai_sdlc.core.stage_review.session_budget_grant_authority import (
    _build_request_proof as build_request_proof,
)
from ai_sdlc.core.stage_review.session_budget_grant_models import (
    BudgetGrantResourceApplication,
)
from ai_sdlc.core.stage_review.session_budget_grant_request import (
    BudgetGrantRequestProof,
)
from ai_sdlc.core.stage_review.session_budget_grant_transitions import (
    SessionBudgetGrantTransitions,
)
from ai_sdlc.core.stage_review.session_contracts import (
    BudgetGrantApplyCommand,
    BudgetGrantRequestCommand,
    SessionIntegrityError,
    parse_session_command,
)
from ai_sdlc.core.stage_review.session_models import (
    SessionEvent,
    SessionMutationResult,
    StageReviewSession,
)
from ai_sdlc.core.stage_review.session_runtime import SessionRuntime


class _BudgetGrantRecoveryHost(Protocol):
    _runtime: SessionRuntime
    _transitions: SessionBudgetGrantTransitions

    def _request_event(self, command: BudgetGrantRequestCommand) -> SessionEvent: ...

    def _finish(
        self,
        request: BudgetGrantRequestCommand,
        event: SessionEvent,
        session: StageReviewSession,
        proof: BudgetGrantRequestProof,
        application: BudgetGrantResourceApplication,
        request_replay: bool,
    ) -> SessionMutationResult: ...


class _SessionBudgetGrantRecoveryMixin:
    def resume_apply(
        self,
        command: BudgetGrantApplyCommand,
    ) -> SessionMutationResult:
        host = cast(_BudgetGrantRecoveryHost, self)
        if host._runtime.store.operation_events_are_complete(command):
            return host._transitions.complete_committed_apply(command)
        operation = host._runtime.store.get_operation(
            command.scope,
            command.request_command_id,
        )
        if operation is None:
            raise SessionIntegrityError("budget grant request operation is missing")
        request = parse_session_command(
            operation.command_type,
            operation.command_payload,
        )
        if not isinstance(request, BudgetGrantRequestCommand):
            raise SessionIntegrityError("budget grant request operation type is invalid")
        event = host._request_event(request)
        proof = build_request_proof(host._runtime, request, event)
        session = host._runtime.store.rebuild(command.scope)
        application = host._runtime.store.get_budget_grant_application(
            command.scope,
            command.application.grant.grant_id,
        )
        if (
            session is None
            or application != command.application
            or command.request_event_digest != event.event_digest
        ):
            raise SessionIntegrityError("budget grant apply recovery diverged")
        return host._finish(request, event, session, proof, application, True)
