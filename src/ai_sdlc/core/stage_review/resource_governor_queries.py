"""ResourceGovernor 的只读 Reservation、Operation 与快照查询。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceGovernorSnapshot,
    ResourceLedgerEvent,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.resource_store import ResourceEventStore


class _ResourceGovernorQueryMixin:
    _store: ResourceEventStore

    def get_reservation(self, reservation_id: str) -> ResourceReservation:
        return self._store.get_reservation(reservation_id)

    def get_reservation_by_session(
        self, stage_review_session_id: str
    ) -> ResourceReservation | None:
        with self._store.locked():
            matches = tuple(
                item
                for item in self._store.load_state().reservations.values()
                if item.stage_review_session_id == stage_review_session_id
            )
        if len(matches) > 1:
            raise SharedStateIntegrityError(
                "resource session is bound to multiple reservations"
            )
        return matches[0] if matches else None

    def get_reservation_ancestor(
        self,
        reservation_id: str,
        reservation_digest: str,
    ) -> ResourceReservation | None:
        return self._store.reservation_at_digest(reservation_id, reservation_digest)

    def get_operation_event(self, operation_id: str) -> ResourceLedgerEvent | None:
        with self._store.locked():
            state = self._store.load_state()
            return self._store.event_for_operation(state, operation_id)

    def snapshot(self) -> ResourceGovernorSnapshot:
        return self._store.snapshot()
