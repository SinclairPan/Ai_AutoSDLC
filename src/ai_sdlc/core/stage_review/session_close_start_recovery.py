"""从 Session 不可变 Operation 恢复原始关闭命令。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.session_contracts import (
    CloseConsumptionStartCommand,
    SessionIntegrityError,
)
from ai_sdlc.core.stage_review.session_models import (
    SessionEvent,
    SessionOperation,
    StageReviewSession,
)
from ai_sdlc.core.stage_review.session_reducer import reduce_session_events
from ai_sdlc.core.stage_review.session_store import SessionEventStore

_CloseStartRecoveryContext = tuple[
    CloseConsumptionStartCommand,
    StageReviewSession,
    str,
]


class _SessionCloseStartRecoveryMixin:
    _store: SessionEventStore

    def _recover_close_start_command(
        self,
        scope: FindingScope,
    ) -> CloseConsumptionStartCommand | None:
        recovered = _read_close_start_context(self._store, scope)
        return recovered[0] if recovered is not None else None

    def _recover_close_start_context(
        self,
        scope: FindingScope,
    ) -> _CloseStartRecoveryContext | None:
        return _read_close_start_context(self._store, scope)

    def _authorized_completion_time(self, session: StageReviewSession) -> str:
        return _session_head_time(self._store.load_events(session.scope), session)


def _read_close_start_context(
    store: SessionEventStore,
    scope: FindingScope,
) -> _CloseStartRecoveryContext | None:
    session = store.rebuild(scope)
    if session is None or session.state not in {"consuming", "consumed"}:
        return None
    command, operation = _read_close_start_operation(store, session)
    if not store.is_operation_complete(command, ("close_consumption_started",)):
        raise SessionIntegrityError("session close start operation is incomplete")
    events = store.load_events(scope)
    positions = tuple(
        index
        for index, event in enumerate(events)
        if event.command_id == command.command_id
        and event.event_kind == "close_consumption_started"
    )
    if len(positions) != 1:
        raise SessionIntegrityError("session close start event is unavailable")
    position = positions[0]
    predecessor = reduce_session_events(scope, events[:position])
    consuming = reduce_session_events(scope, events[: position + 1])
    if predecessor is None or consuming is None:
        raise SessionIntegrityError("session close start predecessor is unavailable")
    _require_close_start_lineage(
        consuming,
        predecessor,
        events[position],
        operation,
        command,
    )
    if session.state == "consuming" and position != len(events) - 1:
        raise SessionIntegrityError("consuming session close start is not at head")
    if session.state == "consumed" and (
        position != len(events) - 2
        or events[-1].event_kind != "close_receipt_committed"
    ):
        raise SessionIntegrityError("consumed session close lineage diverged")
    return command, predecessor, _session_head_time(events[:position], predecessor)


def _read_close_start_operation(
    store: SessionEventStore,
    session: StageReviewSession,
) -> tuple[CloseConsumptionStartCommand, SessionOperation]:
    claim_digest = session.active_close_claim_digest
    if not claim_digest:
        raise SessionIntegrityError("consuming session lost active close claim")
    command_id = stable_id("session-close-start", claim_digest)
    operation = store.get_operation(session.scope, command_id)
    if operation is None:
        raise SessionIntegrityError("session close start operation is unavailable")
    command = CloseConsumptionStartCommand.model_validate(operation.command_payload)
    if command.command_id != command_id:
        raise SessionIntegrityError("session close start command identity diverged")
    return command, operation


def _require_close_start_lineage(
    session: StageReviewSession,
    predecessor: StageReviewSession,
    event: SessionEvent,
    operation: SessionOperation,
    command: CloseConsumptionStartCommand,
) -> None:
    certificate = command.certificate
    claim = command.claim
    checks = (
        command.scope == session.scope == claim.scope == certificate.scope,
        operation.expected_revision
        == command.expected_revision
        == certificate.session_revision,
        claim.claim_digest == session.active_close_claim_digest,
        claim.certificate_id == certificate.certificate_id,
        claim.certificate_digest == certificate.certificate_digest,
        claim.session_start_revision == certificate.session_revision,
        predecessor.state == "authorized",
        predecessor.revision == certificate.session_revision,
        predecessor.session_digest == certificate.session_digest,
        session.revision == predecessor.revision + 1,
        event.sequence == session.revision,
        event.event_kind == "close_consumption_started",
        event.command_id == command.command_id,
        event.previous_event_id == predecessor.head_event_id,
        event.previous_event_digest == predecessor.head_event_digest,
        event.event_digest == session.head_event_digest,
        session.active_candidate_digest == claim.candidate_manifest_digest,
        session.active_close_certificate_id == certificate.certificate_id,
        session.active_close_certificate_digest == certificate.certificate_digest,
        session.active_close_claim_id == claim.claim_id,
        session.active_close_claim_digest == claim.claim_digest,
    )
    if not all(checks):
        raise SessionIntegrityError("session close start recovery lineage diverged")


def _session_head_time(
    events: tuple[SessionEvent, ...],
    session: StageReviewSession,
) -> str:
    if (
        session.state != "authorized"
        or len(events) != session.revision
        or not events
        or events[-1].event_id != session.head_event_id
        or events[-1].event_digest != session.head_event_digest
    ):
        raise SessionIntegrityError("authorized session head event is unavailable")
    return events[-1].occurred_at
