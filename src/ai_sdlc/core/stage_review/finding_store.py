"""FindingEvent 链与可重建 Ledger 投影的共享存储。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    ShortFileLock,
    atomic_write_json,
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.finding_artifact_codec import (
    decode_finding_event,
    decode_finding_ledger,
    decode_finding_waiver,
    validate_finding_artifact_for_write,
)
from ai_sdlc.core.stage_review.finding_digests import ledger_digest
from ai_sdlc.core.stage_review.finding_models import (
    FindingEvent,
    FindingLedger,
    FindingScope,
)
from ai_sdlc.core.stage_review.finding_reducer import reduce_finding_events
from ai_sdlc.core.stage_review.finding_trust_models import FindingWaiver

_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EVENT_NAME = re.compile(
    r"^(?P<sequence>[0-9]{12})-(?P<event>finding-event\.[0-9a-f]{24})\.json$"
)
_OPERATION = re.compile(r"^finding-operation\.[0-9a-f]{24}$")
_HANDOFF = re.compile(r"^finding-handoff\.[0-9a-f]{24}$")
_WAIVER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class FindingEventStore:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        lock_timeout_seconds: float = 2,
    ) -> None:
        self.shared_root = resolve_canonical_shared_state(root, project_id)
        self.project_id = project_id
        self.root = self.shared_root / "finding-ledgers"
        self.lock_timeout_seconds = lock_timeout_seconds

    def lock(self, scope: FindingScope) -> ShortFileLock:
        return ShortFileLock(
            self.session_root(scope) / "mutation.lock",
            timeout_seconds=self.lock_timeout_seconds,
        )

    def session_root(self, scope: FindingScope) -> Path:
        if scope.project_id != self.project_id:
            raise SharedStateIntegrityError("finding scope project lineage mismatch")
        parts = (
            scope.work_item_id,
            scope.stage_instance_id,
            scope.session_id,
        )
        if any(_IDENTITY.fullmatch(part) is None for part in parts):
            raise ValueError("finding scope identity is invalid")
        return self.root / "sessions" / parts[0] / parts[1] / parts[2]

    def projection_path(self, scope: FindingScope) -> Path:
        return self.session_root(scope) / "ledger.json"

    def operation_path(self, operation_id: str) -> Path:
        if _OPERATION.fullmatch(operation_id) is None:
            raise ValueError("finding operation identity is invalid")
        return self.root / "operations" / f"{operation_id}.json"

    def handoff_path(self, handoff_id: str) -> Path:
        if _HANDOFF.fullmatch(handoff_id) is None:
            raise ValueError("finding handoff identity is invalid")
        return self.root / "cross-scope-inbox" / f"{handoff_id}.json"

    def handoff_receipt_path(self, handoff_id: str) -> Path:
        if _HANDOFF.fullmatch(handoff_id) is None:
            raise ValueError("finding handoff identity is invalid")
        return self.root / "cross-scope-receipts" / f"{handoff_id}.json"

    def bind_project(self) -> None:
        bind_repository_project(self.shared_root, self.project_id)

    def create_operation(self, operation_id: str, payload: dict[str, object]) -> bool:
        return create_json_exclusive(self.operation_path(operation_id), payload)

    def read_operation(self, operation_id: str) -> dict[str, object] | None:
        path = self.operation_path(operation_id)
        if not path.exists():
            return None
        return read_json_object(path)

    def append_event(self, event: FindingEvent) -> FindingEvent:
        validate_finding_artifact_for_write("finding-event", event)
        path = self._event_path(event.scope, event.sequence, event.event_id)
        if create_json_exclusive(path, event.model_dump(mode="json")):
            return event
        existing = decode_finding_event(read_json_object(path))
        if existing.event_digest != event.event_digest:
            raise SharedStateIntegrityError("finding event immutable fork")
        return existing

    def persist_waiver(self, waiver: FindingWaiver) -> None:
        validate_finding_artifact_for_write("finding-waiver", waiver)
        if _WAIVER.fullmatch(waiver.waiver_id) is None:
            raise ValueError("finding waiver identity is invalid")
        path = self.root / "waivers" / f"{waiver.waiver_id}.json"
        payload = waiver.model_dump(mode="json")
        if (
            not create_json_exclusive(path, payload)
            and read_json_object(path) != payload
        ):
            raise SharedStateIntegrityError("finding waiver immutable fork")

    def load_event_waivers(
        self,
        events: tuple[FindingEvent, ...],
    ) -> tuple[FindingWaiver, ...]:
        waivers: list[FindingWaiver] = []
        for event in events:
            if event.event_type != "waived":
                continue
            if event.waiver_id is None or _WAIVER.fullmatch(event.waiver_id) is None:
                raise SharedStateIntegrityError("finding waiver reference is invalid")
            path = self.root / "waivers" / f"{event.waiver_id}.json"
            if not path.exists():
                raise SharedStateIntegrityError("finding waiver artifact is missing")
            waiver = decode_finding_waiver(read_json_object(path))
            if waiver.waiver_digest != event.waiver_digest:
                raise SharedStateIntegrityError("finding waiver event digest mismatch")
            waivers.append(waiver)
        return tuple(sorted(waivers, key=lambda item: item.waiver_id))

    def load_events(self, scope: FindingScope) -> tuple[FindingEvent, ...]:
        directory = self.session_root(scope) / "events"
        if not directory.exists():
            return ()
        indexed: list[tuple[int, Path]] = []
        for path in directory.glob("*.json"):
            match = _EVENT_NAME.fullmatch(path.name)
            if match is None:
                raise SharedStateIntegrityError("finding event filename is invalid")
            indexed.append((int(match.group("sequence")), path))
        indexed.sort(key=lambda pair: pair[0])
        events = tuple(
            self._read_event(scope, sequence, path) for sequence, path in indexed
        )
        self._verify_chain(events)
        return events

    def rebuild(
        self,
        scope: FindingScope,
        validator: Callable[[tuple[FindingEvent, ...]], None] | None = None,
    ) -> FindingLedger:
        events = self.load_events(scope)
        if validator is not None:
            validator(events)
        ledger = reduce_finding_events(scope, events)
        self._materialize_handoffs(events)
        self._repair_projection(scope, ledger)
        return ledger

    def _read_event(
        self,
        scope: FindingScope,
        sequence: int,
        path: Path,
    ) -> FindingEvent:
        try:
            payload = read_json_object(path)
            event = decode_finding_event(payload)
        except json.JSONDecodeError as exc:
            raise SharedStateIntegrityError("finding event is invalid") from exc
        if event.scope != scope or event.sequence != sequence:
            raise SharedStateIntegrityError("finding event scope or sequence mismatch")
        if path != self._event_path(scope, sequence, event.event_id):
            raise SharedStateIntegrityError("finding event directory identity mismatch")
        return event

    def _verify_chain(self, events: tuple[FindingEvent, ...]) -> None:
        previous: FindingEvent | None = None
        commands: dict[str, str] = {}
        for expected, event in enumerate(events, start=1):
            if event.sequence != expected:
                raise SharedStateIntegrityError("finding event sequence gap")
            previous_id = previous.event_id if previous else ""
            previous_digest = previous.event_digest if previous else ""
            if (event.previous_event_id, event.previous_event_digest) != (
                previous_id,
                previous_digest,
            ):
                raise SharedStateIntegrityError("finding event chain fork")
            prior_digest = commands.setdefault(event.command_id, event.command_digest)
            if prior_digest != event.command_digest:
                raise SharedStateIntegrityError("finding command idempotency fork")
            previous = event

    def _repair_projection(self, scope: FindingScope, rebuilt: FindingLedger) -> None:
        path = self.projection_path(scope)
        persisted = self._read_projection(path)
        if persisted is not None and persisted.revision > rebuilt.revision:
            raise SharedStateIntegrityError(
                "finding projection is ahead of event truth"
            )
        if persisted is not None and persisted.revision == rebuilt.revision:
            if persisted.ledger_digest != rebuilt.ledger_digest:
                raise SharedStateIntegrityError("finding projection digest fork")
            return
        validate_finding_artifact_for_write("finding-ledger", rebuilt)
        atomic_write_json(path, rebuilt.model_dump(mode="json"))

    def _read_projection(self, path: Path) -> FindingLedger | None:
        if not path.exists():
            return None
        try:
            ledger = decode_finding_ledger(read_json_object(path))
        except (SharedStateIntegrityError, ValueError, json.JSONDecodeError):
            return None
        if ledger.ledger_digest != ledger_digest(ledger):
            return None
        return ledger

    def _materialize_handoffs(self, events: tuple[FindingEvent, ...]) -> None:
        for event in events:
            if not event.handoff_id or event.target_scope is None:
                continue
            if event.event_type == "cross_scope_handoff_resolved":
                if event.handoff_resolution == "accepted":
                    self._persist_handoff_receipt(event)
                continue
            payload = {
                "handoff_id": event.handoff_id,
                "source_scope": event.scope.model_dump(mode="json"),
                "target_scope": event.target_scope.model_dump(mode="json"),
                "source_event_id": event.event_id,
                "source_event_digest": event.event_digest,
                "evidence_bundle_digest": event.evidence_bundle_digest,
            }
            path = self.handoff_path(event.handoff_id)
            if (
                not create_json_exclusive(path, payload)
                and read_json_object(path) != payload
            ):
                raise SharedStateIntegrityError("cross-scope handoff fork")

    def _persist_handoff_receipt(self, event: FindingEvent) -> None:
        assert event.handoff_id is not None and event.target_scope is not None
        payload = {
            "handoff_id": event.handoff_id,
            "resolution": event.handoff_resolution,
            "target_scope": event.target_scope.model_dump(mode="json"),
            "target_receipt_digest": event.target_receipt_digest,
            "resolution_event_id": event.event_id,
            "resolution_event_digest": event.event_digest,
        }
        path = self.handoff_receipt_path(event.handoff_id)
        if (
            not create_json_exclusive(path, payload)
            and read_json_object(path) != payload
        ):
            raise SharedStateIntegrityError("cross-scope handoff receipt fork")

    def _event_path(self, scope: FindingScope, sequence: int, event_id: str) -> Path:
        name = f"{sequence:012d}-{event_id}.json"
        if _EVENT_NAME.fullmatch(name) is None:
            raise ValueError("finding event identity is invalid")
        return self.session_root(scope) / "events" / name
