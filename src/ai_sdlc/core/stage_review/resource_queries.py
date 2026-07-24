"""Resource Ledger 不可变事件的只读定位。"""

from __future__ import annotations

from collections.abc import Iterable

from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceLedgerEvent,
    ResourceReservation,
)


def reservation_at_digest(
    events: Iterable[ResourceLedgerEvent],
    reservation_id: str,
    reservation_digest: str,
) -> ResourceReservation | None:
    return next(
        (
            event.reservation
            for event in events
            if event.reservation.reservation_id == reservation_id
            and event.reservation.reservation_digest == reservation_digest
        ),
        None,
    )


def provider_reconciliation_for(
    events: Iterable[ResourceLedgerEvent],
    source_event_digest: str,
) -> ResourceLedgerEvent | None:
    return next(
        (
            event
            for event in events
            if event.event_kind == "provider_call_reconciled"
            and event.reconciled_event_digest == source_event_digest
        ),
        None,
    )
