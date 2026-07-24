"""Session active-operation 指针的持久化与阶段转换。"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    atomic_write_json,
    read_json_object,
)
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.session_contracts import SessionIntegrityError
from ai_sdlc.core.stage_review.session_models import (
    SessionOperation,
    SessionOperationPointer,
)


def read_operation_pointer(
    path: Path,
    scope: FindingScope,
) -> SessionOperationPointer | None:
    if not path.exists():
        return None
    try:
        pointer = SessionOperationPointer.model_validate(read_json_object(path))
    except FileNotFoundError:
        return None
    except (
        json.JSONDecodeError,
        ValidationError,
        ValueError,
        SharedStateIntegrityError,
    ) as exc:
        raise SessionIntegrityError(
            "session operation pointer artifact is invalid"
        ) from exc
    if pointer.scope != scope:
        raise SessionIntegrityError("active session operation scope diverged")
    return pointer


def claim_operation(path: Path, operation: SessionOperation) -> None:
    pointer = read_operation_pointer(path, operation.scope)
    if pointer is not None:
        _require_identity(pointer, operation)
        return
    pointer = SessionOperationPointer(
        scope=operation.scope,
        command_id=operation.command_id,
        operation_digest=operation.operation_digest,
    )
    atomic_write_json(path, pointer.model_dump(mode="json"))


def mark_effects_started(path: Path, operation: SessionOperation) -> None:
    pointer = _require_pointer(path, operation)
    if pointer.phase == "effects_started":
        return
    updated = pointer.model_copy(update={"phase": "effects_started"})
    atomic_write_json(path, updated.model_dump(mode="json"))


def can_reject_preflight(
    path: Path,
    operation: SessionOperation,
    *,
    has_matching_events: bool,
) -> bool:
    pointer = read_operation_pointer(path, operation.scope)
    return (
        pointer is not None
        and pointer.phase == "prepared"
        and not has_matching_events
    )


def discard_rejected_pointer(path: Path, operation: SessionOperation) -> None:
    pointer = read_operation_pointer(path, operation.scope)
    if pointer is None:
        return
    _require_identity(pointer, operation)
    path.unlink(missing_ok=True)


def release_operation(path: Path, operation: SessionOperation) -> None:
    _require_pointer(path, operation)
    path.unlink()


def _require_pointer(
    path: Path, operation: SessionOperation
) -> SessionOperationPointer:
    pointer = read_operation_pointer(path, operation.scope)
    if pointer is None:
        raise SessionIntegrityError("active session operation pointer is missing")
    _require_identity(pointer, operation)
    return pointer


def _require_identity(
    pointer: SessionOperationPointer,
    operation: SessionOperation,
) -> None:
    if (
        pointer.command_id != operation.command_id
        or pointer.operation_digest != operation.operation_digest
    ):
        raise SessionIntegrityError("active session operation pointer diverged")
