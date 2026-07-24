"""Provider Invocation Journal 的短锁、追加事件与投影重建。"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
    ShortFileLock,
    atomic_write_json,
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
)
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocation,
    ProviderInvocationEvent,
    ProviderInvocationRequest,
    ProviderInvocationState,
    ProviderSubmission,
)
from ai_sdlc.core.stage_review.provider_journal_reducer import (
    STATE_ORDER,
    build_provider_event,
    rebuild_provider_invocation,
    verify_repeated_transition,
)

_INVOCATION_ID = re.compile(r"^provider-invocation\.[0-9a-f]{24}$")


class ProviderJournalStore:
    def __init__(
        self,
        shared_root: Path,
        *,
        project_id: str,
        lock_timeout_seconds: float,
    ) -> None:
        self.shared_root = shared_root
        self.project_id = project_id
        self.root = shared_root / "provider-invocation-journal"
        self.lock_timeout_seconds = lock_timeout_seconds

    @contextmanager
    def locked(self, invocation_id: str) -> Iterator[None]:
        with ShortFileLock(
            self._invocation_root(invocation_id) / "journal.lock",
            timeout_seconds=self.lock_timeout_seconds,
        ):
            bind_repository_project(self.shared_root, self.project_id)
            yield

    def get(self, invocation_id: str) -> ProviderInvocation | None:
        with self.locked(invocation_id):
            return self._load(invocation_id)

    def events(self, invocation_id: str) -> tuple[ProviderInvocationEvent, ...]:
        with self.locked(invocation_id):
            return self._read_events(invocation_id)

    @contextmanager
    def provider_call_claim(self, invocation_id: str) -> Iterator[bool]:
        """调用期间只保留单航班标记，不持有 Journal 状态锁。"""

        claim = ShortFileLock(
            self._invocation_root(invocation_id) / "provider-call.claim",
            timeout_seconds=min(self.lock_timeout_seconds, 0.05),
            poll_seconds=0.005,
        )
        try:
            claim.__enter__()
        except ResourceLockUnavailableError as exc:
            if exc.__cause__ is not None:
                raise
            yield False
            return
        try:
            yield True
        finally:
            claim.__exit__(None, None, None)

    def advance(
        self,
        request: ProviderInvocationRequest,
        target_state: ProviderInvocationState,
        *,
        authorized_reservation_digest: str = "",
        submission_digest: str = "",
        isolation_receipt_digests: tuple[str, ...] = (),
        egress_receipt_digests: tuple[str, ...] = (),
        execution_evidence_root_digest: str = "",
        validation_digest: str = "",
        resource_settlement_operation_id: str = "",
        settlement_reservation_digest: str = "",
        resource_settlement_event_digest: str = "",
    ) -> tuple[ProviderInvocation, bool]:
        with self.locked(request.invocation_id):
            current = self._load(request.invocation_id)
            return self._advance_locked(
                request,
                current,
                target_state,
                authorized_reservation_digest,
                submission_digest,
                isolation_receipt_digests,
                egress_receipt_digests,
                execution_evidence_root_digest,
                validation_digest,
                resource_settlement_operation_id,
                settlement_reservation_digest,
                resource_settlement_event_digest,
            )

    def persist_submission(self, submission: ProviderSubmission) -> None:
        path = self.submission_path(submission.invocation_id)
        if create_json_exclusive(path, submission.model_dump(mode="json")):
            return
        try:
            existing = ProviderSubmission.model_validate(read_json_object(path))
        except (ValidationError, ValueError) as exc:
            raise SharedStateIntegrityError("provider submission is invalid") from exc
        if existing.submission_digest != submission.submission_digest:
            raise SharedStateIntegrityError(
                "provider submission identity fork detected"
            )

    def load_submission(
        self,
        request: ProviderInvocationRequest,
    ) -> ProviderSubmission | None:
        path = self.submission_path(request.invocation_id)
        if not path.exists():
            return None
        try:
            submission = ProviderSubmission.model_validate(read_json_object(path))
        except (ValidationError, ValueError) as exc:
            raise SharedStateIntegrityError("provider submission is invalid") from exc
        expected = (
            submission.invocation_id == request.invocation_id,
            submission.idempotency_key == request.idempotency_key,
            submission.request_artifact_digest == request.request_artifact_digest,
            submission.provider_id == request.provider_id,
        )
        if not all(expected):
            raise SharedStateIntegrityError("provider submission lineage diverged")
        return submission

    def submission_path(self, invocation_id: str) -> Path:
        return self._invocation_root(invocation_id) / "submission.json"

    def _advance_locked(
        self,
        request: ProviderInvocationRequest,
        current: ProviderInvocation | None,
        target_state: ProviderInvocationState,
        authorized_digest: str,
        submission_digest: str,
        isolation_receipt_digests: tuple[str, ...],
        egress_receipt_digests: tuple[str, ...],
        execution_evidence_root_digest: str,
        validation_digest: str,
        settlement_operation_id: str,
        settlement_digest: str,
        settlement_event_digest: str,
    ) -> tuple[ProviderInvocation, bool]:
        target_revision = STATE_ORDER[target_state]
        self._verify_request(current, request)
        if current is not None and current.revision >= target_revision:
            verify_repeated_transition(
                current,
                authorized_digest,
                submission_digest,
                validation_digest,
                settlement_operation_id,
                settlement_digest,
                settlement_event_digest,
                isolation_receipt_digests,
                egress_receipt_digests,
                execution_evidence_root_digest,
            )
            return current, False
        _verify_transition_order(current, target_revision)
        event = build_provider_event(
            request,
            current,
            target_state,
            authorized_digest,
            submission_digest,
            validation_digest,
            settlement_operation_id,
            settlement_digest,
            settlement_event_digest,
            isolation_receipt_digests,
            egress_receipt_digests,
            execution_evidence_root_digest,
        )
        return self._append_event(request.invocation_id, event)

    def _append_event(
        self,
        invocation_id: str,
        event: ProviderInvocationEvent,
    ) -> tuple[ProviderInvocation, bool]:
        events = self._read_events(invocation_id)
        prospective = rebuild_provider_invocation((*events, event))
        self._verify_submission_binding(prospective)
        path = self._event_path(invocation_id, event.sequence)
        created = create_json_exclusive(path, event.model_dump(mode="json"))
        if not created:
            existing = ProviderInvocationEvent.model_validate(read_json_object(path))
            if existing.event_digest != event.event_digest:
                raise SharedStateIntegrityError("provider journal event fork detected")
        atomic_write_json(
            self._projection_path(invocation_id),
            prospective.model_dump(mode="json"),
        )
        return prospective, created

    def _load(self, invocation_id: str) -> ProviderInvocation | None:
        events = self._read_events(invocation_id)
        if not events:
            return None
        projection = rebuild_provider_invocation(events)
        self._verify_submission_binding(projection)
        if not self._projection_matches(invocation_id, projection):
            atomic_write_json(
                self._projection_path(invocation_id),
                projection.model_dump(mode="json"),
            )
        return projection

    def _read_events(
        self,
        invocation_id: str,
    ) -> tuple[ProviderInvocationEvent, ...]:
        events_dir = self._events_dir(invocation_id)
        if not events_dir.exists():
            return ()
        events: list[ProviderInvocationEvent] = []
        for path in sorted(events_dir.glob("*.json")):
            try:
                event = ProviderInvocationEvent.model_validate(read_json_object(path))
            except (ValidationError, ValueError) as exc:
                raise SharedStateIntegrityError(
                    f"provider journal event is invalid: {path}"
                ) from exc
            if (
                event.invocation_id != invocation_id
                or event.request.project_id != self.project_id
            ):
                raise SharedStateIntegrityError(
                    "provider journal event directory identity diverged"
                )
            events.append(event)
        return tuple(events)

    def _verify_request(
        self,
        current: ProviderInvocation | None,
        request: ProviderInvocationRequest,
    ) -> None:
        if request.project_id != self.project_id:
            raise SharedStateIntegrityError("provider invocation project diverged")
        if (
            current is not None
            and current.request.request_artifact_digest
            != request.request_artifact_digest
        ):
            raise SharedStateIntegrityError("provider invocation request changed")

    def _projection_matches(
        self,
        invocation_id: str,
        rebuilt: ProviderInvocation,
    ) -> bool:
        path = self._projection_path(invocation_id)
        if not path.exists():
            return False
        try:
            projected = ProviderInvocation.model_validate(read_json_object(path))
        except (ValidationError, ValueError):
            return False
        if projected.revision > rebuilt.revision:
            raise SharedStateIntegrityError("provider projection is ahead of journal")
        if (
            projected.revision == rebuilt.revision
            and projected.projection_digest != rebuilt.projection_digest
        ):
            raise SharedStateIntegrityError("provider projection diverged from journal")
        return projected.projection_digest == rebuilt.projection_digest

    def _verify_submission_binding(self, invocation: ProviderInvocation) -> None:
        if invocation.revision < 3 or invocation.state == "refused":
            return
        submission = self.load_submission(invocation.request)
        if (
            submission is None
            or submission.submission_digest != invocation.submission_digest
            or submission.isolation_receipt_digests
            != invocation.isolation_receipt_digests
            or submission.egress_receipt_digests != invocation.egress_receipt_digests
            or submission.execution_evidence_root_digest
            != invocation.execution_evidence_root_digest
        ):
            raise SharedStateIntegrityError(
                "provider submission does not match journal projection"
            )

    def _invocation_root(self, invocation_id: str) -> Path:
        if _INVOCATION_ID.fullmatch(invocation_id) is None:
            raise ValueError("provider invocation identity is invalid")
        return self.root / invocation_id

    def _events_dir(self, invocation_id: str) -> Path:
        return self._invocation_root(invocation_id) / "events"

    def _event_path(self, invocation_id: str, sequence: int) -> Path:
        return self._events_dir(invocation_id) / f"{sequence:02d}.json"

    def _projection_path(self, invocation_id: str) -> Path:
        return self._invocation_root(invocation_id) / "state.json"


def _verify_transition_order(
    current: ProviderInvocation | None,
    target_revision: int,
) -> None:
    expected_revision = 1 if current is None else current.revision + 1
    if target_revision != expected_revision:
        raise SharedStateIntegrityError("provider invocation transition skipped state")
