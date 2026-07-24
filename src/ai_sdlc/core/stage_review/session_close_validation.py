"""Session Close Event 的终态、引用与不可变血缘校验。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.session_contracts import SessionIntegrityError
from ai_sdlc.core.stage_review.session_models import (
    SessionEvent,
    SessionProjectionData,
)

CLOSE_EVENT_KINDS = frozenset(
    {
        "close_consumption_started",
        "close_receipt_committed",
        "governed_close_abort",
        "reconciled_new_certificate_issued",
        "macro_rebaseline_accepted",
    }
)
_CLOSE_FIELDS = (
    "active_close_certificate_id",
    "active_close_certificate_digest",
    "active_close_claim_id",
    "active_close_claim_digest",
    "close_consumption_receipt_id",
    "close_consumption_receipt_digest",
    "close_governance_decision_digest",
    "close_failure_reason",
)


def _validate_close_lineage(
    before: SessionProjectionData,
    after: SessionProjectionData,
    event: SessionEvent,
) -> None:
    changed = any(getattr(before, name) != getattr(after, name) for name in _CLOSE_FIELDS)
    if changed and event.event_kind not in CLOSE_EVENT_KINDS:
        raise SessionIntegrityError("session close lineage changed unexpectedly")
    if before.state in {"consuming", "consumed"} and (
        event.event_kind not in CLOSE_EVENT_KINDS
    ):
        raise SessionIntegrityError("session close consumption is terminal")
    recovery_events = {
        "reconciled_new_certificate_issued",
        "macro_rebaseline_accepted",
    }
    if before.close_failure_reason and event.event_kind not in recovery_events:
        raise SessionIntegrityError("aborted session close is terminal")


def _validate_close_event(
    before: SessionProjectionData,
    after: SessionProjectionData,
    event: SessionEvent,
) -> None:
    if event.event_kind == "close_consumption_started":
        _validate_started(before, after, event)
    elif event.event_kind == "close_receipt_committed":
        _validate_committed(before, after, event)
    elif event.event_kind == "governed_close_abort":
        _validate_aborted(before, after, event)
    elif event.event_kind == "reconciled_new_certificate_issued":
        _validate_reauthorized(before, after, event)
    else:
        _validate_superseded(before, after, event)


def _validate_started(
    before: SessionProjectionData,
    after: SessionProjectionData,
    event: SessionEvent,
) -> None:
    refs = event.artifact_refs
    empty = not any(getattr(before, name) for name in _CLOSE_FIELDS)
    recovered = _identity(before) == _identity(after) and not any(
        getattr(before, name) for name in _CLOSE_FIELDS[4:]
    )
    expected = (
        before.state == "authorized",
        after.state == "consuming",
        empty or recovered,
        len(refs) == 2,
        len(refs) == 2 and refs[0].artifact_id == after.active_close_certificate_id,
        len(refs) == 2
        and refs[0].artifact_digest == after.active_close_certificate_digest,
        len(refs) == 2 and refs[1].artifact_id == after.active_close_claim_id,
        len(refs) == 2 and refs[1].artifact_digest == after.active_close_claim_digest,
    )
    if not all(expected):
        raise SessionIntegrityError("session close start transition is invalid")


def _validate_committed(
    before: SessionProjectionData,
    after: SessionProjectionData,
    event: SessionEvent,
) -> None:
    refs = event.artifact_refs
    expected = (
        before.state == "consuming",
        after.state == "consumed",
        _identity(before) == _identity(after),
        len(refs) == 1,
        len(refs) == 1 and refs[0].artifact_id == after.close_consumption_receipt_id,
        len(refs) == 1
        and refs[0].artifact_digest == after.close_consumption_receipt_digest,
    )
    if not all(expected):
        raise SessionIntegrityError("session close receipt transition is invalid")


def _validate_aborted(
    before: SessionProjectionData,
    after: SessionProjectionData,
    event: SessionEvent,
) -> None:
    expected = (
        before.state == "consuming",
        after.state == "needs_user",
        _identity(before) == _identity(after),
        after.close_failure_reason == "governed_close_abort",
        bool(after.close_governance_decision_digest),
        not event.artifact_refs,
    )
    if not all(expected):
        raise SessionIntegrityError("session governed close abort is invalid")


def _validate_reauthorized(
    before: SessionProjectionData,
    after: SessionProjectionData,
    event: SessionEvent,
) -> None:
    refs = event.artifact_refs
    expected = (
        before.state == "needs_user",
        before.close_failure_reason == "governed_close_abort",
        after.state == "authorized",
        not after.close_failure_reason,
        not after.close_governance_decision_digest,
        _identity(before) != _identity(after),
        len(refs) == 3,
        len(refs) == 3 and refs[1].artifact_id == after.active_close_certificate_id,
        len(refs) == 3 and refs[2].artifact_id == after.active_close_claim_id,
    )
    if not all(expected):
        raise SessionIntegrityError("session close reauthorization is invalid")


def _validate_superseded(
    before: SessionProjectionData,
    after: SessionProjectionData,
    event: SessionEvent,
) -> None:
    expected = (
        before.state == "needs_user",
        before.close_failure_reason == "governed_close_abort",
        after.state == "superseded",
        not any(getattr(after, name) for name in _CLOSE_FIELDS),
        len(event.artifact_refs) == 1,
    )
    if not all(expected):
        raise SessionIntegrityError("aborted session supersession is invalid")


def _identity(projection: SessionProjectionData) -> tuple[str, ...]:
    return tuple(getattr(projection, name) for name in _CLOSE_FIELDS[:4])
