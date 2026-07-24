"""过期 Provider 保守计量与晚到实际用量的单账本对账。"""

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
    ResourceGovernorState,
    ResourceLedgerEvent,
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
    result,
    utc_now,
)
from ai_sdlc.core.stage_review.resource_store import ResourceEventStore


def reconcile_expired_provider_call(
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
    try:
        actual = ResourceAmounts.model_validate(actual_usage.model_dump(mode="json"))
        if not is_complete_provider_actual_usage(actual):
            raise ValueError("provider reconciliation requires complete actual usage")
        with store.locked():
            return _reconcile_locked(
                store,
                reservation_id,
                invocation_id,
                actual,
                lease_owner,
                expected_fencing_token,
                operation_id,
                utc_now(now),
            )
    except ResourceLockUnavailableError:
        return result("lock_unavailable")
    except SharedStateIntegrityError:
        return result("state_corrupt")
    except (ValidationError, ValueError, AttributeError):
        return result("invalid_input")


def _reconcile_locked(
    store: ResourceEventStore,
    reservation_id: str,
    invocation_id: str,
    actual: ResourceAmounts,
    lease_owner: str,
    fencing_token: int,
    operation_id: str,
    now: datetime,
) -> ResourceReservationResult:
    state = prepare_state(store, now)
    effect = _reconciliation_effect(
        reservation_id, invocation_id, actual, lease_owner, fencing_token
    )
    repeated = idempotent_result(store, state, operation_id, reservation_id, effect)
    if repeated is not None:
        return repeated
    current = state.reservations.get(reservation_id)
    source = _conservative_event(store, state, current, invocation_id)
    if current is None or source is None or current.state != "expired":
        return result("invalid_reservation", current)
    permit = source.provider_permit
    if permit is None or not _source_authorized(
        source.reservation,
        lease_owner,
        fencing_token,
        invocation_id,
    ):
        return result("stale_fencing", current)
    conservative = permit.anticipated_usage.model_copy(update={"parallelism": 0})
    corrected = subtract_resources(current.usage, conservative) + actual
    reconciled = update_reservation(
        current,
        operation_id=operation_id,
        operation_effect_digest=effect,
        usage=corrected,
        observed_overrun=current.observed_overrun
        + resource_difference(actual, permit.anticipated_usage),
    )
    return commit_reservation(
        store,
        state,
        "provider_call_reconciled",
        operation_id,
        reconciled,
        provider_permit=permit,
        actual_usage=actual,
        reconciled_event_digest=source.event_digest,
    )


def _reconciliation_effect(
    reservation_id: str,
    invocation_id: str,
    actual: ResourceAmounts,
    lease_owner: str,
    fencing_token: int,
) -> str:
    return resource_operation_effect_digest(
        "reconcile_expired_provider_call",
        {
            "reservation_id": reservation_id,
            "invocation_id": invocation_id,
            "actual_usage": actual,
            "lease_owner": lease_owner,
            "expected_fencing_token": fencing_token,
        },
    )


def _conservative_event(
    store: ResourceEventStore,
    state: ResourceGovernorState,
    reservation: ResourceReservation | None,
    invocation_id: str,
) -> ResourceLedgerEvent | None:
    if reservation is None:
        return None
    permit_id = stable_id("provider-permit", reservation.reservation_id, invocation_id)
    source_operation = stable_id(
        "expire-provider-call",
        reservation.reservation_id,
        permit_id,
        reservation.lease_expires_at,
    )
    source = store.event_for_operation(state, source_operation)
    if (
        source is not None
        and store.provider_reconciliation_for(source.event_digest) is not None
    ):
        raise SharedStateIntegrityError("provider call was already reconciled")
    return source


def _source_authorized(
    source_reservation: ResourceReservation,
    lease_owner: str,
    fencing_token: int,
    invocation_id: str,
) -> bool:
    return (
        source_reservation.lease_owner == lease_owner
        and source_reservation.fencing_token == fencing_token
        and invocation_id in source_reservation.provider_invocation_ids
    )
