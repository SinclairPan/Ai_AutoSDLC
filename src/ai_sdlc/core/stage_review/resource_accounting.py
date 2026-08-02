"""Final Reservation 的计量、调用授权与对账操作。"""

from __future__ import annotations

from datetime import datetime

from pydantic import ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
)
from ai_sdlc.core.stage_review.resource_builders import (
    build_reconciliation,
)
from ai_sdlc.core.stage_review.resource_digests import (
    resource_operation_effect_digest,
)
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceGovernorState,
    ResourceReservation,
    ResourceReservationResult,
)
from ai_sdlc.core.stage_review.resource_models import (
    ReservationState,
    ResourceAmounts,
    ResourceEventKind,
)
from ai_sdlc.core.stage_review.resource_projection_builder import update_reservation
from ai_sdlc.core.stage_review.resource_runtime import (
    commit_reservation,
    idempotent_result,
    prepare_state,
    pressure,
    reservation_failure,
    result,
    utc_now,
)
from ai_sdlc.core.stage_review.resource_store import ResourceEventStore


def record_resource_usage(
    store: ResourceEventStore,
    reservation_id: str,
    *,
    delta: ResourceAmounts,
    lease_owner: str,
    expected_fencing_token: int,
    operation_id: str,
    now: datetime | None,
) -> ResourceReservationResult:
    try:
        trusted_delta, effect_digest = _usage_operation_effect(
            "record_usage",
            reservation_id,
            delta,
            lease_owner,
            expected_fencing_token,
        )
        with store.locked():
            return _record_locked(
                store,
                reservation_id,
                trusted_delta,
                lease_owner,
                expected_fencing_token,
                operation_id,
                effect_digest,
                utc_now(now),
            )
    except ResourceLockUnavailableError:
        return result("lock_unavailable")
    except SharedStateIntegrityError:
        return result("state_corrupt")
    except (ValidationError, ValueError, AttributeError):
        return result("invalid_input")


def _record_locked(
    store: ResourceEventStore,
    reservation_id: str,
    delta: ResourceAmounts,
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
        current, fencing_token, now, "final", lease_owner=lease_owner
    )
    if failure is not None:
        return failure
    assert current is not None
    usage = current.usage + delta
    committed = usage + current.authorized_pending
    if not committed.fits_within(current.reserved) or not committed.fits_within(
        current.hard_limits
    ):
        return result("hard_limit_exceeded", current)
    recorded = update_reservation(
        current,
        operation_id=operation_id,
        operation_effect_digest=effect,
        usage=usage,
    )
    outcome = commit_reservation(
        store,
        state,
        "usage_recorded",
        operation_id,
        recorded,
        actual_usage=delta,
    )
    return outcome.model_copy(update={"pressure": pressure(recorded)})


def reconcile_reservation(
    store: ResourceEventStore,
    reservation_id: str,
    *,
    lease_owner: str,
    expected_fencing_token: int,
    operation_id: str,
    now: datetime | None,
) -> ResourceReservationResult:
    try:
        effect_digest = _simple_operation_effect(
            "reconcile", reservation_id, lease_owner, expected_fencing_token
        )
        current_time = utc_now(now)
        with store.locked():
            state = prepare_state(store, current_time)
            repeated = idempotent_result(
                store, state, operation_id, reservation_id, effect_digest
            )
            if repeated is not None:
                return repeated
            current = state.reservations.get(reservation_id)
            failure = reservation_failure(
                current,
                expected_fencing_token,
                current_time,
                "final",
                lease_owner=lease_owner,
            )
            if failure is not None:
                return failure
            assert current is not None
            if current.provider_permits:
                return result("invalid_reservation", current)
            return _commit_closed_reservation(
                store,
                state,
                current,
                effect_digest,
                operation_id,
                "reconciled",
                "reservation_reconciled",
            )
    except ResourceLockUnavailableError:
        return result("lock_unavailable")
    except SharedStateIntegrityError:
        return result("state_corrupt")
    except (ValidationError, ValueError, AttributeError):
        return result("invalid_input")


def _usage_operation_effect(
    kind: str,
    reservation_id: str,
    amounts: ResourceAmounts,
    lease_owner: str,
    fencing_token: int,
) -> tuple[ResourceAmounts, str]:
    trusted = ResourceAmounts.model_validate(amounts.model_dump(mode="json"))
    if not trusted.any_positive():
        raise ValueError("usage operation requires a positive delta")
    if not lease_owner.strip() or lease_owner != lease_owner.strip():
        raise ValueError("usage operation requires lease owner")
    effect = resource_operation_effect_digest(
        kind,
        {
            "reservation_id": reservation_id,
            "amounts": trusted,
            "lease_owner": lease_owner,
            "expected_fencing_token": fencing_token,
        },
    )
    return trusted, effect


def _simple_operation_effect(
    kind: str,
    reservation_id: str,
    lease_owner: str,
    fencing_token: int,
) -> str:
    if not lease_owner.strip() or lease_owner != lease_owner.strip():
        raise ValueError("resource operation requires lease owner")
    return resource_operation_effect_digest(
        kind,
        {
            "reservation_id": reservation_id,
            "lease_owner": lease_owner,
            "expected_fencing_token": fencing_token,
        },
    )


def _commit_closed_reservation(
    store: ResourceEventStore,
    state: ResourceGovernorState,
    current: ResourceReservation,
    effect_digest: str,
    operation_id: str,
    reservation_state: ReservationState,
    event_kind: ResourceEventKind,
) -> ResourceReservationResult:
    reconciliation = build_reconciliation(
        current,
        operation_id=operation_id,
        fencing_token=state.next_fencing_token,
    )
    closed = update_reservation(
        current,
        operation_id=operation_id,
        operation_effect_digest=effect_digest,
        state=reservation_state,
        fencing_token=state.next_fencing_token,
    )
    return commit_reservation(
        store,
        state,
        event_kind,
        operation_id,
        closed,
        reconciliation=reconciliation,
    )
