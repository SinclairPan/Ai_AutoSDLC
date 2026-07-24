"""ResourceReservation Lease 的续租与拥有者释放。"""

from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite

from pydantic import ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
)
from ai_sdlc.core.stage_review.resource_builders import build_reconciliation
from ai_sdlc.core.stage_review.resource_digests import (
    resource_operation_effect_digest,
)
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceGovernorState,
    ResourceReservation,
    ResourceReservationResult,
)
from ai_sdlc.core.stage_review.resource_projection_builder import update_reservation
from ai_sdlc.core.stage_review.resource_runtime import (
    commit_reservation,
    idempotent_result,
    prepare_state,
    reservation_failure,
    result,
    utc_now,
)
from ai_sdlc.core.stage_review.resource_store import ResourceEventStore


def renew_reservation_lease(
    store: ResourceEventStore,
    reservation_id: str,
    *,
    lease_owner: str,
    lease_seconds: float,
    expected_fencing_token: int,
    operation_id: str,
    now: datetime | None,
) -> ResourceReservationResult:
    try:
        effect = _lease_effect(
            "renew_reservation",
            reservation_id,
            lease_owner,
            expected_fencing_token,
            lease_seconds,
        )
        with store.locked():
            return _renew_locked(
                store,
                reservation_id,
                lease_owner,
                lease_seconds,
                expected_fencing_token,
                operation_id,
                effect,
                utc_now(now),
            )
    except ResourceLockUnavailableError:
        return result("lock_unavailable")
    except SharedStateIntegrityError:
        return result("state_corrupt")
    except (ValidationError, ValueError, AttributeError):
        return result("invalid_input")


def release_reservation(
    store: ResourceEventStore,
    reservation_id: str,
    *,
    lease_owner: str,
    expected_fencing_token: int,
    operation_id: str,
    now: datetime | None,
) -> ResourceReservationResult:
    try:
        effect = _lease_effect(
            "release_reservation",
            reservation_id,
            lease_owner,
            expected_fencing_token,
        )
        with store.locked():
            return _release_locked(
                store,
                reservation_id,
                lease_owner,
                expected_fencing_token,
                operation_id,
                effect,
                utc_now(now),
            )
    except ResourceLockUnavailableError:
        return result("lock_unavailable")
    except SharedStateIntegrityError:
        return result("state_corrupt")
    except (ValidationError, ValueError, AttributeError):
        return result("invalid_input")


def _renew_locked(
    store: ResourceEventStore,
    reservation_id: str,
    lease_owner: str,
    lease_seconds: float,
    fencing_token: int,
    operation_id: str,
    effect: str,
    now: datetime,
) -> ResourceReservationResult:
    state = prepare_state(store, now)
    repeated = idempotent_result(store, state, operation_id, reservation_id, effect)
    if repeated is not None:
        return repeated
    current = state.reservations.get(reservation_id)
    failure = reservation_failure(
        current,
        fencing_token,
        now,
        ("admission", "final"),
        lease_owner=lease_owner,
    )
    if failure is not None:
        return failure
    assert current is not None
    renewed = update_reservation(
        current,
        operation_id=operation_id,
        operation_effect_digest=effect,
        fencing_token=state.next_fencing_token,
        lease_expires_at=now + timedelta(seconds=lease_seconds),
    )
    return commit_reservation(
        store, state, "reservation_renewed", operation_id, renewed
    )


def _release_locked(
    store: ResourceEventStore,
    reservation_id: str,
    lease_owner: str,
    fencing_token: int,
    operation_id: str,
    effect: str,
    now: datetime,
) -> ResourceReservationResult:
    state = prepare_state(store, now)
    repeated = idempotent_result(store, state, operation_id, reservation_id, effect)
    if repeated is not None:
        return repeated
    current = state.reservations.get(reservation_id)
    failure = reservation_failure(
        current,
        fencing_token,
        now,
        ("admission", "final"),
        lease_owner=lease_owner,
    )
    if failure is not None:
        return failure
    assert current is not None
    if current.provider_permits:
        return result("invalid_reservation", current)
    return _commit_release(store, state, current, operation_id, effect)


def _commit_release(
    store: ResourceEventStore,
    state: ResourceGovernorState,
    current: ResourceReservation,
    operation_id: str,
    effect: str,
) -> ResourceReservationResult:
    reconciliation = build_reconciliation(
        current,
        operation_id=operation_id,
        fencing_token=state.next_fencing_token,
    )
    released = update_reservation(
        current,
        operation_id=operation_id,
        operation_effect_digest=effect,
        state="released",
        fencing_token=state.next_fencing_token,
    )
    return commit_reservation(
        store,
        state,
        "reservation_released",
        operation_id,
        released,
        reconciliation=reconciliation,
    )


def _lease_effect(
    kind: str,
    reservation_id: str,
    lease_owner: str,
    fencing_token: int,
    lease_seconds: float | None = None,
) -> str:
    invalid_duration = lease_seconds is not None and (
        not isfinite(lease_seconds) or lease_seconds <= 0
    )
    if not lease_owner.strip() or lease_owner != lease_owner.strip() or invalid_duration:
        raise ValueError("lease operation requires owner and positive duration")
    return resource_operation_effect_digest(
        kind,
        {
            "reservation_id": reservation_id,
            "lease_owner": lease_owner,
            "lease_seconds": lease_seconds,
            "expected_fencing_token": fencing_token,
        },
    )
