"""SessionEvent 真值链、不可变评审工件与可修复投影存储。"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    ShortFileLock,
    bind_repository_project,
    create_json_exclusive,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.session_artifact_store import (
    _SessionArtifactStoreMixin as SessionArtifactStoreMixin,
)
from ai_sdlc.core.stage_review.session_budget_grant_authority_store import (
    _SessionBudgetGrantAuthorityStoreMixin as SessionBudgetGrantAuthorityStoreMixin,
)
from ai_sdlc.core.stage_review.session_budget_grant_authority_store import (
    ensure_shared_state_binding_id,
)
from ai_sdlc.core.stage_review.session_budget_grant_operation_store import (
    _SessionBudgetGrantOperationStoreMixin as SessionBudgetGrantOperationStoreMixin,
)
from ai_sdlc.core.stage_review.session_contracts import (
    SessionCommand,
    SessionEventKind,
    SessionIntegrityError,
)
from ai_sdlc.core.stage_review.session_models import (
    SessionEvent,
    SessionOperation,
    SessionOperationPointer,
    StageReviewSession,
)
from ai_sdlc.core.stage_review.session_operation_pointer import (
    can_reject_preflight,
    discard_rejected_pointer,
    mark_effects_started,
    read_operation_pointer,
    release_operation,
)
from ai_sdlc.core.stage_review.session_operation_registry import (
    acquire_operation,
    discover_pending_operation,
    operation_is_rejected,
    operation_rejection_path,
    record_operation_rejection,
    require_operation_not_rejected,
    validate_operation_identity,
)
from ai_sdlc.core.stage_review.session_operation_replay import (
    _audit_budget_grant_operations as audit_budget_grant_operations,
)
from ai_sdlc.core.stage_review.session_operation_replay import (
    audit_event_operations,
    operation_is_complete,
)
from ai_sdlc.core.stage_review.session_paths import _scope_parts as scope_parts
from ai_sdlc.core.stage_review.session_paths import (
    _session_scope_root as session_scope_root,
)
from ai_sdlc.core.stage_review.session_projection_store import (
    SessionProjectionStoreMixin,
)
from ai_sdlc.core.stage_review.session_reducer import reduce_session_events
from ai_sdlc.core.stage_review.session_transaction import (
    EventBuilder,
    transact_claimed,
)

_EVENT_NAME = re.compile(
    r"^(?P<sequence>[0-9]{12})-(?P<event>session-event\.[0-9a-f]{24})\.json$"
)


class SessionEventStore(
    SessionBudgetGrantOperationStoreMixin,
    SessionBudgetGrantAuthorityStoreMixin,
    SessionProjectionStoreMixin,
    SessionArtifactStoreMixin,
):
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        lock_timeout_seconds: float = 2,
        session_observer: Callable[[StageReviewSession], None] | None = None,
    ) -> None:
        self.shared_root = resolve_canonical_shared_state(root, project_id)
        self.project_id = project_id
        self.shared_state_binding_id = ensure_shared_state_binding_id(
            self.shared_root,
            project_id,
        )
        self.root = self.shared_root / "stage-review-sessions"
        self.lock_timeout_seconds = lock_timeout_seconds
        self.session_observer = session_observer

    def transact(
        self,
        command: SessionCommand,
        event_kinds: tuple[SessionEventKind, ...],
        *,
        clock: Callable[[], str],
        builder: EventBuilder,
    ) -> tuple[StageReviewSession, bool]:
        scope = command.scope
        with self._lock(scope):
            bind_repository_project(self.shared_root, self.project_id)
            pending = self.pending_operation(scope)
            operation = acquire_operation(
                command,
                event_kinds,
                clock(),
                pending,
                self._operation_path(scope, command.command_id),
                self._operation_pointer_path(scope),
                lambda item: operation_rejection_path(
                    self._session_root(scope), item
                ),
                lambda item: self._require_model(
                    item, SessionOperation, "session operation"
                ),
            )
            try:
                result = transact_claimed(self, command, operation, builder)
            except Exception:
                self._release_rejected_preflight(operation)
                raise
        if self.session_observer is not None:
            self.session_observer(result[0])
        return result

    def is_operation_complete(
        self,
        command: SessionCommand,
        event_kinds: tuple[SessionEventKind, ...],
    ) -> bool:
        with self._lock(command.scope):
            operation = self.get_operation(command.scope, command.command_id)
            if operation is None:
                return False
            validate_operation_identity(
                operation,
                command,
                event_kinds,
                canonical_digest(command, CanonicalizationPolicy()),
            )
            events = self.load_events(command.scope)
            self._audit_event_operations(events)
            matching = tuple(
                item for item in events if item.command_id == command.command_id
            )
            return bool(matching) and operation_is_complete(matching, operation)

    def rebuild(self, scope: FindingScope) -> StageReviewSession | None:
        events = self.load_events(scope)
        audit_budget_grant_operations(events, self.get_session_budget_grant_operation)
        session = reduce_session_events(scope, events)
        self._repair_projection(scope, session)
        return session

    def load_events(self, scope: FindingScope) -> tuple[SessionEvent, ...]:
        directory = self._session_root(scope) / "events"
        if not directory.exists():
            return ()
        indexed: list[tuple[int, Path]] = []
        for path in directory.glob("*.json"):
            match = _EVENT_NAME.fullmatch(path.name)
            if match is None:
                raise SessionIntegrityError("session event filename is invalid")
            indexed.append((int(match.group("sequence")), path))
        indexed.sort(key=lambda item: item[0])
        return tuple(
            self._read_event(scope, sequence, path) for sequence, path in indexed
        )

    def projection_path(self, scope: FindingScope) -> Path:
        return self._session_root(scope) / "session.json"

    def get_operation(
        self,
        scope: FindingScope,
        command_id: str,
    ) -> SessionOperation | None:
        path = self._operation_path(scope, command_id)
        if not path.exists():
            return None
        return self._require_model(path, SessionOperation, "session operation")

    def pending_operation(self, scope: FindingScope) -> SessionOperation | None:
        events = self.load_events(scope)
        event_pending = self._audit_event_operations(events)
        pointer = self._read_operation_pointer(scope)
        discovered = discover_pending_operation(
            self._session_root(scope) / "operations",
            events,
            lambda path: self._require_model(
                path, SessionOperation, "session operation"
            ),
            lambda operation: operation_rejection_path(
                self._session_root(scope), operation
            ),
        )
        if pointer is None:
            if (
                event_pending is not None
                and discovered is not None
                and event_pending.command_id != discovered.command_id
            ):
                raise SessionIntegrityError("session operation recovery facts diverged")
            return event_pending or discovered
        operation = self.get_operation(scope, pointer.command_id)
        if operation is None or operation.operation_digest != pointer.operation_digest:
            raise SessionIntegrityError("active session operation pointer is invalid")
        if operation_is_rejected(
            operation_rejection_path(self._session_root(scope), operation), operation
        ):
            discard_rejected_pointer(
                self._operation_pointer_path(operation.scope), operation
            )
            return discovered
        if (
            event_pending is not None
            and event_pending.command_id != operation.command_id
        ):
            raise SessionIntegrityError("active session operation pointer diverged")
        if discovered is not None and discovered.command_id != operation.command_id:
            raise SessionIntegrityError("active session operation fact diverged")
        return operation

    def mark_operation_effects_started(self, operation: SessionOperation) -> None:
        mark_effects_started(self._operation_pointer_path(operation.scope), operation)

    def completed_session(self, command: SessionCommand) -> StageReviewSession | None:
        operation = self.get_operation(command.scope, command.command_id)
        if operation is None:
            return None
        rejection = operation_rejection_path(self._session_root(command.scope), operation)
        require_operation_not_rejected(rejection, operation)
        pointer = self._read_operation_pointer(command.scope)
        if pointer is not None and pointer.command_id == command.command_id:
            return None
        digest = canonical_digest(command, CanonicalizationPolicy())
        validate_operation_identity(
            operation,
            command,
            operation.expected_event_kinds,
            digest,
        )
        events = tuple(
            item
            for item in self.load_events(command.scope)
            if item.command_id == command.command_id
        )
        if not events or not operation_is_complete(events, operation):
            return None
        return self.rebuild(command.scope)

    def _release_rejected_preflight(self, operation: SessionOperation) -> None:
        has_matching = any(
            item.command_id == operation.command_id
            for item in self.load_events(operation.scope)
        )
        pointer_path = self._operation_pointer_path(operation.scope)
        if not can_reject_preflight(
            pointer_path, operation, has_matching_events=has_matching
        ):
            return
        record_operation_rejection(
            operation_rejection_path(
                self._session_root(operation.scope), operation
            ),
            operation,
        )
        release_operation(pointer_path, operation)

    def _release_operation(self, operation: SessionOperation) -> None:
        release_operation(self._operation_pointer_path(operation.scope), operation)

    def _read_operation_pointer(
        self,
        scope: FindingScope,
    ) -> SessionOperationPointer | None:
        return read_operation_pointer(self._operation_pointer_path(scope), scope)

    def _require_resumable_operation(
        self,
        events: tuple[SessionEvent, ...],
        matching: tuple[SessionEvent, ...],
        operation: SessionOperation,
    ) -> None:
        if matching:
            if events[-len(matching) :] != matching:
                raise SessionIntegrityError(
                    "session incomplete operation is not at head"
                )
            kinds = tuple(item.event_kind for item in matching)
            if kinds != operation.expected_event_kinds[: len(kinds)]:
                raise SessionIntegrityError("session operation event sequence diverged")

    def _audit_event_operations(
        self,
        events: tuple[SessionEvent, ...],
    ) -> SessionOperation | None:
        if not events:
            return None
        scope = events[0].scope
        return audit_event_operations(
            events,
            lambda command_id: self.get_operation(scope, command_id),
        )

    def _validate_expected_events(
        self,
        base: StageReviewSession | None,
        events: tuple[SessionEvent, ...],
        operation: SessionOperation,
    ) -> None:
        if tuple(item.event_kind for item in events) != operation.expected_event_kinds:
            raise SessionIntegrityError("session builder event sequence is invalid")
        previous_id = base.head_event_id if base is not None else ""
        previous_digest = base.head_event_digest if base is not None else ""
        sequence = base.revision + 1 if base is not None else 1
        for event in events:
            if (
                event.sequence != sequence
                or event.command_id != operation.command_id
                or event.command_digest != operation.command_digest
                or event.previous_event_id != previous_id
                or event.previous_event_digest != previous_digest
            ):
                raise SessionIntegrityError("session builder event lineage is invalid")
            previous_id = event.event_id
            previous_digest = event.event_digest
            sequence += 1

    def _append_event(self, event: SessionEvent) -> None:
        path = self._event_path(event.scope, event.sequence, event.event_id)
        payload = event.model_dump(mode="json")
        if not create_json_exclusive(path, payload):
            existing = self._require_model(path, SessionEvent, "session event")
            if existing.event_digest != event.event_digest:
                raise SessionIntegrityError("session event immutable fork")

    def _read_event(
        self,
        scope: FindingScope,
        sequence: int,
        path: Path,
    ) -> SessionEvent:
        event = self._require_model(path, SessionEvent, "session event")
        if event.scope != scope or event.sequence != sequence:
            raise SessionIntegrityError("session event path lineage mismatch")
        if path != self._event_path(scope, sequence, event.event_id):
            raise SessionIntegrityError("session event identity differs from filename")
        return event

    def _lock(self, scope: FindingScope) -> ShortFileLock:
        return ShortFileLock(
            self._session_root(scope) / "mutation.lock",
            timeout_seconds=self.lock_timeout_seconds,
        )

    def _operation_path(self, scope: FindingScope, command_id: str) -> Path:
        artifact_id = stable_id(
            "session-operation", *scope_parts(scope), command_id
        )
        return self._session_root(scope) / "operations" / f"{artifact_id}.json"

    def _operation_pointer_path(self, scope: FindingScope) -> Path:
        return self._session_root(scope) / "active-operation.json"

    def _event_path(
        self,
        scope: FindingScope,
        sequence: int,
        event_id: str,
    ) -> Path:
        name = f"{sequence:012d}-{event_id}.json"
        if _EVENT_NAME.fullmatch(name) is None:
            raise ValueError("session event identity is invalid")
        return self._session_root(scope) / "events" / name

    def _session_root(self, scope: FindingScope) -> Path:
        return session_scope_root(self.root, self.project_id, scope)
