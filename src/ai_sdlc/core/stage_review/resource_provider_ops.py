"""Provider 调用的预授权额度与实际结算。"""

from __future__ import annotations

from datetime import datetime

from pydantic import ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
)
from ai_sdlc.core.stage_review.resource_builders import (
    resource_difference,
    stable_id,
    subtract_resources,
)
from ai_sdlc.core.stage_review.resource_digests import (
    resource_operation_effect_digest,
)
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ProviderCallPermit,
    ResourceReservation,
    ResourceReservationResult,
)
from ai_sdlc.core.stage_review.resource_models import (
    ResourceAmounts,
    is_complete_provider_actual_usage,
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


def authorize_provider_call(
    store: ResourceEventStore,
    reservation_id: str,
    *,
    invocation_id: str,
    anticipated_usage: ResourceAmounts,
    lease_owner: str,
    expected_fencing_token: int,
    operation_id: str,
    now: datetime | None,
) -> ResourceReservationResult:
    """在调用发出前原子占用所有最坏情况资源维度。"""

    try:
        permit, effect = _authorization_inputs(
            reservation_id,
            invocation_id,
            anticipated_usage,
            lease_owner,
            expected_fencing_token,
        )
        with store.locked():
            return _authorize_locked(
                store,
                reservation_id,
                invocation_id,
                lease_owner,
                expected_fencing_token,
                operation_id,
                permit,
                effect,
                utc_now(now),
            )
    except ResourceLockUnavailableError:
        return result("lock_unavailable")
    except SharedStateIntegrityError:
        return result("state_corrupt")
    except (ValidationError, ValueError, AttributeError):
        return result("invalid_input")


def settle_provider_call(
    store: ResourceEventStore,
    reservation_id: str,
    *,
    invocation_id: str,
    actual_usage: ResourceAmounts,
    lease_owner: str,
    expected_fencing_token: int,
    operation_id: str,
    now: datetime | None,
) -> ResourceReservationResult:
    """释放预授权占用并一次性记入 Provider 实际使用量。"""

    try:
        trusted, effect = _settlement_inputs(
            reservation_id,
            invocation_id,
            actual_usage,
            lease_owner,
            expected_fencing_token,
        )
        with store.locked():
            return _settle_locked(
                store,
                reservation_id,
                invocation_id,
                lease_owner,
                expected_fencing_token,
                operation_id,
                trusted,
                effect,
                utc_now(now),
            )
    except ResourceLockUnavailableError:
        return result("lock_unavailable")
    except SharedStateIntegrityError:
        return result("state_corrupt")
    except (ValidationError, ValueError, AttributeError):
        return result("invalid_input")


def _authorize_locked(
    store: ResourceEventStore,
    reservation_id: str,
    invocation_id: str,
    lease_owner: str,
    fencing_token: int,
    operation_id: str,
    permit: ProviderCallPermit,
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
    existing = _permit_for_invocation(current, invocation_id)
    if existing is not None:
        if existing != permit:
            return result("invalid_input", current)
        return result("authorized", current, provider_permit=existing)
    if invocation_id in current.provider_invocation_ids:
        return result("invalid_input", current)
    pending = current.authorized_pending + permit.anticipated_usage
    if not _committed_usage_fits(current, pending=pending):
        return result("hard_limit_exceeded", current)
    updated = update_reservation(
        current,
        operation_id=operation_id,
        operation_effect_digest=effect,
        authorized_pending=pending,
        provider_permits=tuple(
            sorted((*current.provider_permits, permit), key=lambda item: item.permit_id)
        ),
        provider_invocation_ids=(*current.provider_invocation_ids, invocation_id),
    )
    return commit_reservation(
        store,
        state,
        "provider_call_authorized",
        operation_id,
        updated,
        provider_permit=permit,
    )


def _settle_locked(
    store: ResourceEventStore,
    reservation_id: str,
    invocation_id: str,
    lease_owner: str,
    fencing_token: int,
    operation_id: str,
    actual: ResourceAmounts,
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
    permit = _permit_for_invocation(current, invocation_id)
    if permit is None:
        return result("invalid_input", current)
    pending = subtract_resources(current.authorized_pending, permit.anticipated_usage)
    usage = current.usage + actual
    observed_overrun = _provider_overrun(current, actual, permit)
    settled = update_reservation(
        current,
        operation_id=operation_id,
        operation_effect_digest=effect,
        usage=usage,
        observed_overrun=observed_overrun,
        authorized_pending=pending,
        provider_permits=tuple(
            item for item in current.provider_permits if item != permit
        ),
    )
    outcome = commit_reservation(
        store,
        state,
        "provider_call_settled",
        operation_id,
        settled,
        provider_permit=permit,
        actual_usage=actual,
    )
    return outcome.model_copy(update={"pressure": pressure(settled)})


def _provider_overrun(
    reservation: ResourceReservation,
    actual: ResourceAmounts,
    permit: ProviderCallPermit,
) -> ResourceAmounts:
    return reservation.observed_overrun + resource_difference(
        actual, permit.anticipated_usage
    )


def _authorization_inputs(
    reservation_id: str,
    invocation_id: str,
    amounts: ResourceAmounts,
    lease_owner: str,
    fencing_token: int,
) -> tuple[ProviderCallPermit, str]:
    trusted = ResourceAmounts.model_validate(amounts.model_dump(mode="json"))
    permit = ProviderCallPermit(
        permit_id=stable_id("provider-permit", reservation_id, invocation_id),
        invocation_id=invocation_id,
        anticipated_usage=trusted,
    )
    effect = _provider_effect(
        "authorize_provider_call", reservation_id, permit, lease_owner, fencing_token
    )
    return permit, effect


def _settlement_inputs(
    reservation_id: str,
    invocation_id: str,
    amounts: ResourceAmounts,
    lease_owner: str,
    fencing_token: int,
) -> tuple[ResourceAmounts, str]:
    trusted = ResourceAmounts.model_validate(amounts.model_dump(mode="json"))
    if not is_complete_provider_actual_usage(trusted):
        raise ValueError("provider settlement requires complete actual usage")
    effect = _provider_effect(
        "settle_provider_call",
        reservation_id,
        {"invocation_id": invocation_id, "actual_usage": trusted},
        lease_owner,
        fencing_token,
    )
    return trusted, effect


def _provider_effect(
    kind: str,
    reservation_id: str,
    detail: object,
    lease_owner: str,
    fencing_token: int,
) -> str:
    if not lease_owner.strip() or lease_owner != lease_owner.strip():
        raise ValueError("provider operation requires lease owner")
    return resource_operation_effect_digest(
        kind,
        {
            "reservation_id": reservation_id,
            "detail": detail,
            "lease_owner": lease_owner,
            "expected_fencing_token": fencing_token,
        },
    )


def _permit_for_invocation(
    reservation: ResourceReservation,
    invocation_id: str,
) -> ProviderCallPermit | None:
    return next(
        (
            item
            for item in reservation.provider_permits
            if item.invocation_id == invocation_id
        ),
        None,
    )


def _committed_usage_fits(
    reservation: ResourceReservation,
    *,
    usage: ResourceAmounts | None = None,
    pending: ResourceAmounts | None = None,
) -> bool:
    committed = (usage or reservation.usage) + (
        pending or reservation.authorized_pending
    )
    return committed.fits_within(reservation.reserved) and committed.fits_within(
        reservation.hard_limits
    )
