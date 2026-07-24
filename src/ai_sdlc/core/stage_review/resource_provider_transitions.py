"""过期 Provider 估算转实际用量的事件重放校验。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.resource_builders import (
    resource_difference,
    stable_id,
    subtract_resources,
)
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceLedgerEvent,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.resource_models import (
    is_complete_provider_actual_usage,
)


def verify_provider_reconciliation(
    event: ResourceLedgerEvent,
    previous: ResourceReservation,
    prior_events: dict[str, ResourceLedgerEvent],
) -> None:
    permit = event.provider_permit
    actual = event.actual_usage
    source = prior_events.get(event.reconciled_event_digest)
    if permit is None or actual is None or source is None:
        raise SharedStateIntegrityError("provider reconciliation source is incomplete")
    expected_operation = stable_id(
        "expire-provider-call",
        previous.reservation_id,
        permit.permit_id,
        previous.lease_expires_at,
    )
    conservative = permit.anticipated_usage.model_copy(update={"parallelism": 0})
    duplicate = any(
        item.event_kind == "provider_call_reconciled"
        and item.reconciled_event_digest == source.event_digest
        for item in prior_events.values()
    )
    source_matches = (
        source.event_kind == "provider_call_settled"
        and source.operation_id == expected_operation
        and source.provider_permit == permit
        and source.actual_usage == conservative
        and source.reservation.reservation_id == previous.reservation_id
    )
    if duplicate or not source_matches:
        raise SharedStateIntegrityError("provider reconciliation source diverged")
    if not is_complete_provider_actual_usage(actual) or not conservative.fits_within(
        previous.usage
    ):
        raise SharedStateIntegrityError(
            "provider reconciliation actual usage is invalid"
        )
    corrected = subtract_resources(previous.usage, conservative) + actual
    expected_overrun = previous.observed_overrun + resource_difference(
        actual, permit.anticipated_usage
    )
    if (
        event.reservation.usage != corrected
        or event.reservation.observed_overrun != expected_overrun
    ):
        raise SharedStateIntegrityError(
            "provider reconciliation usage correction diverged"
        )
    _verify_provider_fields_unchanged(event.reservation, previous)


def _verify_provider_fields_unchanged(
    current: ResourceReservation,
    previous: ResourceReservation,
) -> None:
    if (
        current.authorized_pending != previous.authorized_pending
        or current.provider_permits != previous.provider_permits
        or current.provider_invocation_ids != previous.provider_invocation_ids
    ):
        raise SharedStateIntegrityError(
            "provider reconciliation changed provider accounting"
        )
