"""BudgetGrant 基于最新 Resource Reservation 的锁内补偿。"""

from __future__ import annotations

from datetime import datetime

from pydantic import ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
)
from ai_sdlc.core.stage_review.resource_grant_models import BudgetGrant
from ai_sdlc.core.stage_review.resource_grants import _execute_locked, _trusted_inputs
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservationResult
from ai_sdlc.core.stage_review.resource_runtime import prepare_state, result, utc_now
from ai_sdlc.core.stage_review.resource_store import ResourceEventStore


def _reconcile_budget_grant_current(
    store: ResourceEventStore,
    grant: BudgetGrant,
    *,
    now: datetime | None,
) -> ResourceReservationResult:
    """在同一 Resource 锁内读取最新 Reservation 并释放 Grant。"""
    try:
        current_time = utc_now(now)
        with store.locked():
            state = prepare_state(store, current_time)
            current = state.reservations.get(grant.final_reservation_id)
            if current is None:
                return result("invalid_reservation")
            trusted, operation_id, effect = _trusted_inputs(
                grant,
                "reconciled_released",
                current.lease_owner,
                current.revision,
                current.reservation_digest,
                current.fencing_token,
            )
            return _execute_locked(
                store,
                trusted,
                "reconciled_released",
                current.lease_owner,
                current.revision,
                current.reservation_digest,
                current.fencing_token,
                operation_id,
                effect,
                current_time,
            )
    except ResourceLockUnavailableError:
        return result("lock_unavailable")
    except SharedStateIntegrityError:
        return result("state_corrupt")
    except (ValidationError, ValueError, AttributeError):
        return result("invalid_input")
