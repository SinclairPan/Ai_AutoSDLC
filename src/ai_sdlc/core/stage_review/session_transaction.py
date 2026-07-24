"""已取得 active-operation 指针后的 Session 事务提交。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.session_contracts import (
    SessionCasConflictError,
    SessionCommand,
    SessionIntegrityError,
)
from ai_sdlc.core.stage_review.session_models import (
    SessionEvent,
    SessionOperation,
    StageReviewSession,
)
from ai_sdlc.core.stage_review.session_operation_replay import operation_is_complete
from ai_sdlc.core.stage_review.session_reducer import reduce_session_events

EventBuilder = Callable[
    [StageReviewSession | None, SessionOperation], tuple[SessionEvent, ...]
]


class SessionTransactionHost(Protocol):
    def load_events(self, scope: FindingScope) -> tuple[SessionEvent, ...]: ...

    def rebuild(self, scope: FindingScope) -> StageReviewSession | None: ...

    def _audit_event_operations(
        self, events: tuple[SessionEvent, ...]
    ) -> SessionOperation | None: ...

    def _require_resumable_operation(
        self,
        events: tuple[SessionEvent, ...],
        matching: tuple[SessionEvent, ...],
        operation: SessionOperation,
    ) -> None: ...

    def _validate_expected_events(
        self,
        base: StageReviewSession | None,
        events: tuple[SessionEvent, ...],
        operation: SessionOperation,
    ) -> None: ...

    def _append_event(self, event: SessionEvent) -> None: ...

    def _release_operation(self, operation: SessionOperation) -> None: ...


def transact_claimed(
    host: SessionTransactionHost,
    command: SessionCommand,
    operation: SessionOperation,
    builder: EventBuilder,
) -> tuple[StageReviewSession, bool]:
    events = host.load_events(command.scope)
    matching = tuple(item for item in events if item.command_id == command.command_id)
    pending = host._audit_event_operations(events)
    if pending is not None and pending.command_id != command.command_id:
        raise SessionIntegrityError("another session operation requires recovery")
    if matching and operation_is_complete(matching, operation):
        session = host.rebuild(command.scope)
        if session is None:
            raise SessionIntegrityError("completed operation lost session truth")
        host._release_operation(operation)
        return session, True
    host._require_resumable_operation(events, matching, operation)
    base_events = events[: len(events) - len(matching)] if matching else events
    base = reduce_session_events(command.scope, base_events)
    if not matching and (base.revision if base is not None else 0) != (
        command.expected_revision
    ):
        raise SessionCasConflictError("session expected revision is stale")
    expected = builder(base, operation)
    host._validate_expected_events(base, expected, operation)
    if reduce_session_events(command.scope, (*base_events, *expected)) is None:
        raise SessionIntegrityError("session transaction produced no projection")
    for persisted, candidate in zip(matching, expected, strict=False):
        if persisted.event_digest != candidate.event_digest:
            raise SessionIntegrityError("session operation replay diverged")
    for event in expected[len(matching) :]:
        host._append_event(event)
    session = host.rebuild(command.scope)
    if session is None:
        raise SessionIntegrityError("session transaction produced no projection")
    host._release_operation(operation)
    return session, len(matching) == len(expected)
