"""从不可变 Operation 与预检终态中发现可恢复事务。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from ai_sdlc.core.stage_review import session_operation_pointer
from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    create_json_exclusive,
    read_json_object,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.session_contracts import (
    SessionCommand,
    SessionEventKind,
    SessionIntegrityError,
    session_command_type,
)
from ai_sdlc.core.stage_review.session_models import (
    SessionEvent,
    SessionOperation,
    SessionOperationRejection,
)
from ai_sdlc.core.stage_review.session_operation_replay import operation_is_complete


def acquire_operation(
    command: SessionCommand,
    event_kinds: tuple[SessionEventKind, ...],
    prepared_at: str,
    pending: SessionOperation | None,
    operation_path: Path,
    pointer_path: Path,
    rejection_path: Callable[[SessionOperation], Path],
    reader: Callable[[Path], SessionOperation],
) -> SessionOperation:
    if pending is not None and pending.command_id != command.command_id:
        raise SessionIntegrityError("another session operation requires recovery")
    operation = prepare_operation(
        command,
        event_kinds,
        prepared_at,
        operation_path,
        reader,
    )
    require_operation_not_rejected(rejection_path(operation), operation)
    try:
        session_operation_pointer.claim_operation(pointer_path, operation)
    except SessionIntegrityError as exc:
        raise SessionIntegrityError(
            "another session operation requires recovery"
        ) from exc
    return operation


def prepare_operation(
    command: SessionCommand,
    event_kinds: tuple[SessionEventKind, ...],
    prepared_at: str,
    path: Path,
    reader: Callable[[Path], SessionOperation],
) -> SessionOperation:
    digest = canonical_digest(command, CanonicalizationPolicy())
    if path.exists():
        existing = reader(path)
        validate_operation_identity(existing, command, event_kinds, digest)
        return existing
    operation = SessionOperation(
        scope=command.scope,
        command_type=session_command_type(command),
        command_payload=command.model_dump(mode="json"),
        command_id=command.command_id,
        idempotency_key=command.idempotency_key,
        command_digest=digest,
        expected_revision=command.expected_revision,
        expected_event_kinds=event_kinds,
        prepared_at=prepared_at,
    )
    if create_json_exclusive(path, operation.model_dump(mode="json")):
        return operation
    existing = reader(path)
    validate_operation_identity(existing, command, event_kinds, digest)
    return existing


def validate_operation_identity(
    existing: SessionOperation,
    command: SessionCommand,
    event_kinds: tuple[SessionEventKind, ...],
    command_digest: str,
) -> None:
    identity = (
        existing.scope == command.scope,
        existing.command_type == session_command_type(command),
        existing.command_payload == command.model_dump(mode="json"),
        existing.command_id == command.command_id,
        existing.idempotency_key == command.idempotency_key,
        existing.command_digest == command_digest,
        existing.expected_revision == command.expected_revision,
        existing.expected_event_kinds == event_kinds,
    )
    if not all(identity):
        raise SessionIntegrityError("session command identity was reused")


def operation_rejection_path(
    session_root: Path,
    operation: SessionOperation,
) -> Path:
    artifact_id = stable_id(
        "session-operation-rejection", operation.operation_digest
    )
    return session_root / "operation-rejections" / f"{artifact_id}.json"


def discover_pending_operation(
    directory: Path,
    events: tuple[SessionEvent, ...],
    reader: Callable[[Path], SessionOperation],
    rejection_path: Callable[[SessionOperation], Path],
) -> SessionOperation | None:
    if not directory.exists():
        return None
    candidates: list[SessionOperation] = []
    for path in sorted(directory.glob("*.json")):
        operation = reader(path)
        matching = tuple(
            item for item in events if item.command_id == operation.command_id
        )
        if operation_is_rejected(rejection_path(operation), operation):
            if matching:
                raise SessionIntegrityError(
                    "rejected session operation unexpectedly owns events"
                )
            continue
        if matching and operation_is_complete(matching, operation):
            continue
        candidates.append(operation)
    if len(candidates) > 1:
        raise SessionIntegrityError("multiple session operations require recovery")
    return candidates[0] if candidates else None


def record_operation_rejection(path: Path, operation: SessionOperation) -> None:
    rejection = SessionOperationRejection(
        scope=operation.scope,
        command_id=operation.command_id,
        operation_digest=operation.operation_digest,
    )
    payload = rejection.model_dump(mode="json")
    if create_json_exclusive(path, payload):
        return
    existing = _read_rejection(path)
    if existing != rejection:
        raise SessionIntegrityError("session operation rejection immutable fork")


def operation_is_rejected(path: Path, operation: SessionOperation) -> bool:
    if not path.exists():
        return False
    rejection = _read_rejection(path)
    if (
        rejection.scope != operation.scope
        or rejection.command_id != operation.command_id
        or rejection.operation_digest != operation.operation_digest
    ):
        raise SessionIntegrityError("session operation rejection lineage is invalid")
    return True


def require_operation_not_rejected(
    path: Path,
    operation: SessionOperation,
) -> None:
    if operation_is_rejected(path, operation):
        raise SessionIntegrityError(
            "rejected session operation identity cannot be reused"
        )


def _read_rejection(path: Path) -> SessionOperationRejection:
    try:
        return SessionOperationRejection.model_validate(read_json_object(path))
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        ValidationError,
        ValueError,
        SharedStateIntegrityError,
    ) as exc:
        raise SessionIntegrityError(
            "session operation rejection artifact is invalid"
        ) from exc
