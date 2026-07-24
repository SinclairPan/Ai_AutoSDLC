"""ResourceGovernor 的事件提交、容量判断与保险丝内部运行逻辑。"""

from __future__ import annotations

from datetime import UTC, datetime

from ai_sdlc.core.stage_review.resource_builders import (
    build_reconciliation,
    build_resource_event,
    parse_utc,
    stable_id,
    subtract_resources,
)
from ai_sdlc.core.stage_review.resource_digests import (
    resource_operation_effect_digest,
)
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ProviderCallPermit,
    ResourceGovernorState,
    ResourceReconciliation,
    ResourceReservation,
    ResourceReservationResult,
)
from ai_sdlc.core.stage_review.resource_models import (
    BudgetPressure,
    ReservationResultCode,
    ResourceAmounts,
    ResourceEventKind,
    ResourcePool,
)
from ai_sdlc.core.stage_review.resource_projection_builder import update_reservation
from ai_sdlc.core.stage_review.resource_store import ResourceEventStore

_RESULT_BY_EVENT: dict[ResourceEventKind, ReservationResultCode] = {
    "admission_reserved": "reserved",
    "admission_reused": "reserved",
    "reservation_finalized": "finalized",
    "reservation_expanded": "expanded",
    "reservation_renewed": "renewed",
    "reservation_released": "released",
    "provider_call_authorized": "authorized",
    "provider_call_settled": "settled",
    "provider_call_reconciled": "settled",
    "budget_grant_reconciled": "reconciled",
    "usage_recorded": "recorded",
    "reservation_expired": "lease_expired",
    "reservation_reconciled": "reconciled",
}


def prepare_state(
    store: ResourceEventStore,
    now: datetime,
) -> ResourceGovernorState:
    state = store.load_state()
    reservations = sorted(
        state.reservations.values(), key=lambda item: item.reservation_id
    )
    for reservation in reservations:
        if reservation.state not in {"admission", "final"}:
            continue
        if parse_utc(reservation.lease_expires_at) > now:
            continue
        operation_id = stable_id(
            "expire", reservation.reservation_id, reservation.lease_expires_at
        )
        if operation_id in state.operation_events:
            continue
        state = _settle_expired_provider_calls(store, state, reservation)
        reservation = state.reservations[reservation.reservation_id]
        effect_digest = resource_operation_effect_digest(
            "reservation_expired",
            {
                "reservation_id": reservation.reservation_id,
                "lease_expires_at": reservation.lease_expires_at,
            },
        )
        reconciliation = build_reconciliation(
            reservation,
            operation_id=operation_id,
            fencing_token=state.next_fencing_token,
        )
        expired = update_reservation(
            reservation,
            operation_id=operation_id,
            operation_effect_digest=effect_digest,
            state="expired",
            fencing_token=state.next_fencing_token,
        )
        state = append_event(
            store,
            state,
            "reservation_expired",
            operation_id,
            expired,
            reconciliation=reconciliation,
        )
    return state


def _settle_expired_provider_calls(
    store: ResourceEventStore,
    state: ResourceGovernorState,
    reservation: ResourceReservation,
) -> ResourceGovernorState:
    for permit in reservation.provider_permits:
        current = state.reservations[reservation.reservation_id]
        operation_id = stable_id(
            "expire-provider-call",
            current.reservation_id,
            permit.permit_id,
            current.lease_expires_at,
        )
        actual = permit.anticipated_usage.model_copy(update={"parallelism": 0})
        effect = resource_operation_effect_digest(
            "expire_provider_call_conservatively",
            {"permit_id": permit.permit_id, "actual_usage": actual},
        )
        settled = update_reservation(
            current,
            operation_id=operation_id,
            operation_effect_digest=effect,
            usage=current.usage + actual,
            authorized_pending=subtract_resources(
                current.authorized_pending, permit.anticipated_usage
            ),
            provider_permits=tuple(
                item for item in current.provider_permits if item != permit
            ),
        )
        state = append_event(
            store,
            state,
            "provider_call_settled",
            operation_id,
            settled,
            provider_permit=permit,
            actual_usage=actual,
        )
    return state


def commit_reservation(
    store: ResourceEventStore,
    state: ResourceGovernorState,
    event_kind: ResourceEventKind,
    operation_id: str,
    reservation: ResourceReservation,
    *,
    provider_permit: ProviderCallPermit | None = None,
    actual_usage: ResourceAmounts | None = None,
    reconciled_event_digest: str = "",
    reconciliation: ResourceReconciliation | None = None,
) -> ResourceReservationResult:
    next_state = append_event(
        store,
        state,
        event_kind,
        operation_id,
        reservation,
        provider_permit=provider_permit,
        actual_usage=actual_usage,
        reconciled_event_digest=reconciled_event_digest,
        reconciliation=reconciliation,
    )
    current = next_state.reservations[reservation.reservation_id]
    return result(
        _RESULT_BY_EVENT[event_kind],
        current,
        pressure(current),
        reconciliation,
        operation_reservation=reservation,
        provider_permit=provider_permit,
    )


def append_event(
    store: ResourceEventStore,
    state: ResourceGovernorState,
    event_kind: ResourceEventKind,
    operation_id: str,
    reservation: ResourceReservation,
    *,
    provider_permit: ProviderCallPermit | None = None,
    actual_usage: ResourceAmounts | None = None,
    reconciled_event_digest: str = "",
    reconciliation: ResourceReconciliation | None = None,
) -> ResourceGovernorState:
    previous = state.reservations.get(reservation.reservation_id)
    event = build_resource_event(
        sequence=state.head_sequence + 1,
        event_kind=event_kind,
        operation_id=operation_id,
        previous_event_digest=state.head_digest,
        previous_reservation_digest=(
            "" if previous is None else previous.reservation_digest
        ),
        reservation=reservation,
        provider_permit=provider_permit,
        actual_usage=actual_usage,
        reconciled_event_digest=reconciled_event_digest,
        reconciliation=reconciliation,
    )
    return store.append_event(event)


def idempotent_result(
    store: ResourceEventStore,
    state: ResourceGovernorState,
    operation_id: str,
    reservation_id: str | None = None,
    expected_effect_digest: str | None = None,
) -> ResourceReservationResult | None:
    if not operation_id.strip() or operation_id != operation_id.strip():
        return result("invalid_input")
    event = store.event_for_operation(state, operation_id)
    if event is None:
        return None
    if (
        reservation_id is not None
        and event.reservation.reservation_id != reservation_id
    ):
        return result("state_corrupt")
    if (
        expected_effect_digest is not None
        and event.reservation.operation_effect_digest != expected_effect_digest
    ):
        return result("state_corrupt")
    current = state.reservations[event.reservation.reservation_id]
    return result(
        _RESULT_BY_EVENT[event.event_kind],
        current,
        pressure(current),
        event.reconciliation,
        operation_reservation=event.reservation,
        provider_permit=event.provider_permit,
    )


def capacity_available(
    store: ResourceEventStore,
    state: ResourceGovernorState,
    pool: ResourcePool,
    increment: ResourceAmounts,
) -> bool:
    used = reserved_for_pool(state, pool)
    return (used + increment).fits_within(_capacity(store, pool))


def replacement_fits(
    store: ResourceEventStore,
    state: ResourceGovernorState,
    current: ResourceReservation,
    replacement: ResourceAmounts,
) -> bool:
    used = reserved_for_pool(
        state,
        current.pool,
        excluded_reservation_id=current.reservation_id,
    )
    return (used + replacement).fits_within(_capacity(store, current.pool))


def reservation_failure(
    reservation: ResourceReservation | None,
    expected_fencing_token: int,
    now: datetime,
    expected_state: str | tuple[str, ...],
    *,
    lease_owner: str | None = None,
) -> ResourceReservationResult | None:
    if reservation is None:
        return result("invalid_reservation")
    if reservation.fencing_token != expected_fencing_token:
        return result("stale_fencing", reservation)
    if lease_owner is not None and reservation.lease_owner != lease_owner:
        return result("invalid_reservation", reservation)
    if reservation.state == "expired":
        return result("stale_fencing", reservation)
    allowed_states = (
        (expected_state,) if isinstance(expected_state, str) else expected_state
    )
    if reservation.state not in allowed_states:
        return result("not_final", reservation)
    if parse_utc(reservation.lease_expires_at) <= now:
        return result("lease_expired", reservation)
    return None


def reserved_for_pool(
    state: ResourceGovernorState,
    pool: ResourcePool,
    *,
    excluded_reservation_id: str = "",
) -> ResourceAmounts:
    total = ResourceAmounts()
    for reservation in state.reservations.values():
        if reservation.reservation_id == excluded_reservation_id:
            continue
        if reservation.pool == pool and reservation.state in {"admission", "final"}:
            total = total + reservation.reserved
        elif reservation.pool == pool and reservation.authorized_pending.any_positive():
            total = total + reservation.authorized_pending
    return total


def pressure(reservation: ResourceReservation) -> BudgetPressure:
    usage = reservation.usage + reservation.authorized_pending
    if _reaches(usage, reservation.hard_limits):
        return "hard_limit_reached"
    if _reaches(usage, reservation.soft_limits):
        return "soft_limit_reached"
    return "within"


def result(
    result_code: ReservationResultCode,
    reservation: ResourceReservation | None = None,
    current_pressure: BudgetPressure = "within",
    reconciliation: ResourceReconciliation | None = None,
    *,
    operation_reservation: ResourceReservation | None = None,
    provider_permit: ProviderCallPermit | None = None,
) -> ResourceReservationResult:
    return ResourceReservationResult(
        result_code=result_code,
        reservation=reservation,
        operation_reservation=operation_reservation,
        provider_permit=provider_permit,
        pressure=current_pressure,
        reconciliation=reconciliation,
    )


def utc_now(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("resource timestamp must be timezone-aware")
    return current.astimezone(UTC)


def _capacity(store: ResourceEventStore, pool: ResourcePool) -> ResourceAmounts:
    config = store.expected_config
    if pool == "foreground":
        return config.foreground_capacity
    return config.offline_optimization_capacity


def _reaches(usage: object, limits: object) -> bool:
    return any(
        getattr(usage, name) > 0 and getattr(usage, name) >= getattr(limits, name)
        for name in ResourceAmounts.ALL_FIELDS
    )
