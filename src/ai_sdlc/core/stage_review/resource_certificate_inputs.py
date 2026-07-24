"""在 Resource 短锁内冻结证书签发所需的最终对账事实。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceReconciliation,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.resource_runtime import prepare_state, utc_now
from ai_sdlc.core.stage_review.resource_store import ResourceEventStore


class _ResourceCertificateInputsMixin:
    _store: ResourceEventStore

    @contextmanager
    def hold_certificate_inputs(
        self,
        reservation_id: str,
        final_reservation_digest: str,
        reconciliation_digest: str,
    ) -> Iterator[
        tuple[ResourceReservation, ResourceReservation, ResourceReconciliation]
    ]:
        with self._store.locked():
            state = prepare_state(self._store, utc_now(None))
            current = state.reservations.get(reservation_id)
            reconciliation = next(
                (
                    item
                    for item in state.reconciliations.values()
                    if item.reconciliation_digest == reconciliation_digest
                ),
                None,
            )
            final = self._store._reservation_at_digest_locked(
                reservation_id,
                final_reservation_digest,
            )
            if current is None or reconciliation is None or final is None:
                raise SharedStateIntegrityError(
                    "certificate resource lineage is unavailable"
                )
            expected = (
                final.state == "final",
                reconciliation.reservation_id == reservation_id,
                reconciliation.reservation_digest == final.reservation_digest,
                current.state == "reconciled",
                current.fencing_token == reconciliation.fencing_token,
                current.last_operation_id == reconciliation.operation_id,
                not current.provider_permits,
                not current.authorized_pending.any_positive(),
            )
            if not all(expected):
                raise SharedStateIntegrityError(
                    "certificate resource reconciliation is not current"
                )
            yield final, current, reconciliation

    def get_reconciliation(self, reconciliation_digest: str) -> ResourceReconciliation:
        with self._store.locked():
            state = self._store.load_state()
            value = next(
                (
                    item
                    for item in state.reconciliations.values()
                    if item.reconciliation_digest == reconciliation_digest
                ),
                None,
            )
            if value is None:
                raise KeyError(reconciliation_digest)
            return value
