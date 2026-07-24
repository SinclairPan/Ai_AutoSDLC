"""可恢复 BudgetGrant apply Session Operation 的专用存储动作。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, cast

from ai_sdlc.core.stage_review.artifacts import ShortFileLock
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.session_contracts import (
    BudgetGrantApplyCommand,
    SessionCommand,
    SessionIntegrityError,
)
from ai_sdlc.core.stage_review.session_models import SessionEvent, SessionOperation
from ai_sdlc.core.stage_review.session_operation_registry import (
    operation_is_rejected,
    operation_rejection_path,
    prepare_operation,
    record_operation_rejection,
    validate_operation_identity,
)
from ai_sdlc.core.stage_review.session_operation_replay import operation_is_complete


class _BudgetGrantOperationStoreHost(Protocol):
    def get_operation(
        self,
        scope: FindingScope,
        command_id: str,
    ) -> SessionOperation | None: ...

    def load_events(self, scope: FindingScope) -> tuple[SessionEvent, ...]: ...

    def pending_operation(self, scope: FindingScope) -> SessionOperation | None: ...

    def _lock(self, scope: FindingScope) -> ShortFileLock: ...

    def _session_root(self, scope: FindingScope) -> Path: ...

    def _operation_path(self, scope: FindingScope, command_id: str) -> Path: ...

    def _operation_pointer_path(self, scope: FindingScope) -> Path: ...


class _SessionBudgetGrantOperationStoreMixin:
    def operation_was_rejected(
        self,
        scope: FindingScope,
        command_id: str,
    ) -> bool:
        host = cast(_BudgetGrantOperationStoreHost, self)
        operation = host.get_operation(scope, command_id)
        if operation is None:
            return False
        return operation_is_rejected(
            operation_rejection_path(host._session_root(scope), operation),
            operation,
        )

    def operation_events_are_complete(self, command: SessionCommand) -> bool:
        host = cast(_BudgetGrantOperationStoreHost, self)
        operation = host.get_operation(command.scope, command.command_id)
        if operation is None:
            return False
        validate_operation_identity(
            operation,
            command,
            operation.expected_event_kinds,
            canonical_digest(command, CanonicalizationPolicy()),
        )
        events = tuple(
            event
            for event in host.load_events(command.scope)
            if event.command_id == command.command_id
        )
        return bool(events) and operation_is_complete(events, operation)

    def abandon_budget_grant_apply(
        self,
        command: BudgetGrantApplyCommand,
        prepared_at: str,
    ) -> None:
        host = cast(_BudgetGrantOperationStoreHost, self)
        with host._lock(command.scope):
            operation = host.get_operation(command.scope, command.command_id)
            if operation is None:
                operation = prepare_operation(
                    command,
                    ("budget_grant_applied",),
                    prepared_at,
                    host._operation_path(command.scope, command.command_id),
                    lambda _: _require_prepared_operation(host, command),
                )
            _validate_abandonment(host, operation, command)
            rejection_path = operation_rejection_path(
                host._session_root(command.scope),
                operation,
            )
            if operation_is_rejected(rejection_path, operation):
                host.pending_operation(command.scope)
                return
            record_operation_rejection(rejection_path, operation)
            host.pending_operation(command.scope)


def _validate_abandonment(
    host: _BudgetGrantOperationStoreHost,
    operation: SessionOperation,
    command: BudgetGrantApplyCommand,
) -> None:
    validate_operation_identity(
        operation,
        command,
        ("budget_grant_applied",),
        canonical_digest(command, CanonicalizationPolicy()),
    )
    if any(
        event.command_id == command.command_id
        for event in host.load_events(command.scope)
    ):
        raise SessionIntegrityError("committed budget grant apply cannot be abandoned")


def _require_prepared_operation(
    host: _BudgetGrantOperationStoreHost,
    command: BudgetGrantApplyCommand,
) -> SessionOperation:
    operation = host.get_operation(command.scope, command.command_id)
    if operation is None:
        raise SessionIntegrityError("budget grant apply operation is missing")
    return operation
