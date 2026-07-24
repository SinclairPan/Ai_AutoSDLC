"""Session Operation 事件分组、完整性与链尾恢复判定。"""

from __future__ import annotations

from collections.abc import Callable

from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.session_budget_grant_operation import (
    SessionBudgetGrantOperation,
)
from ai_sdlc.core.stage_review.session_contracts import SessionIntegrityError
from ai_sdlc.core.stage_review.session_models import SessionEvent, SessionOperation


def _audit_budget_grant_operations(
    events: tuple[SessionEvent, ...],
    operation_for: Callable[[FindingScope, str], SessionBudgetGrantOperation],
) -> None:
    for event in events:
        if event.event_kind not in {
            "budget_grant_applied",
            "budget_grant_reconciled",
        }:
            continue
        operation = operation_for(
            event.scope,
            event.projection_after.last_budget_grant_operation_id,
        )
        if (
            operation.target_event_digest != event.event_digest
            or operation.target_event != event
        ):
            raise SessionIntegrityError(
                "session BudgetGrantOperation target event diverged"
            )


def audit_event_operations(
    events: tuple[SessionEvent, ...],
    operation_for: Callable[[str], SessionOperation | None],
) -> SessionOperation | None:
    groups = _event_groups(events)
    seen: set[str] = set()
    pending: SessionOperation | None = None
    for index, group in enumerate(groups):
        command_id = group[0].command_id
        if command_id in seen:
            raise SessionIntegrityError("session operation events are not contiguous")
        seen.add(command_id)
        operation = operation_for(command_id)
        if operation is None:
            raise SessionIntegrityError("session event operation fact is missing")
        validate_operation_events(group, operation)
        if len(group) != len(operation.expected_event_kinds):
            if index != len(groups) - 1:
                raise SessionIntegrityError(
                    "incomplete session operation is historical"
                )
            pending = operation
    return pending


def operation_is_complete(
    events: tuple[SessionEvent, ...],
    operation: SessionOperation,
) -> bool:
    validate_operation_events(events, operation)
    return len(events) == len(operation.expected_event_kinds)


def validate_operation_events(
    events: tuple[SessionEvent, ...],
    operation: SessionOperation,
) -> None:
    kinds = tuple(item.event_kind for item in events)
    valid = (
        kinds == operation.expected_event_kinds[: len(kinds)],
        len(events) <= len(operation.expected_event_kinds),
        all(item.command_digest == operation.command_digest for item in events),
    )
    if not all(valid):
        raise SessionIntegrityError("session operation event sequence diverged")


def _event_groups(
    events: tuple[SessionEvent, ...],
) -> tuple[tuple[SessionEvent, ...], ...]:
    groups: list[list[SessionEvent]] = []
    for event in events:
        if not groups or groups[-1][-1].command_id != event.command_id:
            groups.append([event])
        else:
            groups[-1].append(event)
    return tuple(tuple(group) for group in groups)
