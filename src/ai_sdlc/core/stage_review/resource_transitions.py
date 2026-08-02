from __future__ import annotations

from typing import Never

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.resource_builders import resource_difference as _overrun
from ai_sdlc.core.stage_review.resource_builders import subtract_resources as _subtract
from ai_sdlc.core.stage_review.resource_grant_models import BudgetGrantOperation
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceLedgerEvent,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.resource_models import (
    ResourceAmounts,
    is_complete_provider_actual_usage,
)
from ai_sdlc.core.stage_review.resource_provider_transitions import (
    verify_provider_reconciliation,
)

_TRANSITIONS = {
    "reservation_finalized": ({"admission"}, "final"),
    "reservation_expanded": ({"final"}, "final"),
    "reservation_renewed": ({"admission", "final"}, None),
    "reservation_released": ({"admission", "final"}, "released"),
    "admission_reused": ({"admission", "final"}, None),
    "provider_call_authorized": ({"final"}, "final"),
    "provider_call_settled": ({"final"}, "final"),
    "provider_call_reconciled": ({"expired"}, "expired"),
    "usage_recorded": ({"final"}, "final"),
    "reservation_expired": ({"admission", "final"}, "expired"),
    "reservation_reconciled": ({"final"}, "reconciled"),
    "budget_grant_reconciled": ({"final"}, "final"),
}
_ROTATING_EVENTS = set(_TRANSITIONS) - {
    "admission_reused",
    "provider_call_authorized",
    "provider_call_settled",
    "provider_call_reconciled",
    "usage_recorded",
}
_IMMUTABLE_FIELDS = (
    "reservation_id",
    "project_id",
    "work_item_id",
    "stage_review_session_id",
    "pool",
    "admission_operation_id",
    "idempotency_key",
    "budget_envelope_digest",
    "budget_policy_digest",
    "policy_hard_limits",
    "lease_owner",
)


def verify_resource_transition(
    project_id: str,
    event: ResourceLedgerEvent,
    previous: ResourceReservation | None,
    grant_operation: BudgetGrantOperation | None = None,
    prior_events: dict[str, ResourceLedgerEvent] | None = None,
) -> None:
    current = event.reservation
    if previous is None:
        _verify_admission(project_id, event)
        return
    if event.previous_reservation_digest != previous.reservation_digest:
        _invalid("resource event previous reservation digest mismatch")
    if any(
        getattr(previous, field) != getattr(current, field)
        for field in _IMMUTABLE_FIELDS
    ):
        _invalid("resource reservation immutable lineage changed")
    if current.revision != previous.revision + 1:
        _invalid("resource reservation revision is not monotonic")
    _verify_state_and_fencing(event, previous)
    _verify_allocation_and_proposal(event, previous)
    _verify_usage_transition(event, previous, prior_events or {})
    _verify_budget_transition(event, previous, grant_operation)
    _verify_reconciliation(event, previous)


def _verify_admission(project_id: str, event: ResourceLedgerEvent) -> None:
    current = event.reservation
    expected = (
        event.event_kind == "admission_reserved",
        event.previous_reservation_digest == "",
        current.project_id == project_id,
        current.state == "admission",
        current.revision == 1,
        current.fencing_token >= 1,
        not current.proposal_digest,
        not current.proposal_lineage_digest,
        not current.provider_permits,
        not current.provider_invocation_ids,
        current.budget_revision == 0,
        current.reserved == current.hard_limits == current.policy_hard_limits,
        current.usage == ResourceAmounts(),
        current.authorized_pending == ResourceAmounts(),
    )
    if not all(expected):
        _invalid("resource ledger must begin each reservation with Admission")


def _verify_state_and_fencing(
    event: ResourceLedgerEvent,
    previous: ResourceReservation,
) -> None:
    current = event.reservation
    rule = _TRANSITIONS.get(event.event_kind)
    if rule is None or previous.state not in rule[0]:
        _invalid("resource reservation state transition is invalid")
    expected_state = previous.state if rule[1] is None else rule[1]
    if current.state != expected_state:
        _invalid("resource reservation target state is invalid")
    rotates = event.event_kind in _ROTATING_EVENTS
    if rotates != (current.fencing_token > previous.fencing_token):
        _invalid("resource reservation fencing transition is invalid")
    if not rotates and current.fencing_token != previous.fencing_token:
        _invalid("resource accounting cannot rotate fencing")


def _verify_usage_transition(
    event: ResourceLedgerEvent,
    previous: ResourceReservation,
    prior_events: dict[str, ResourceLedgerEvent],
) -> None:
    current = event.reservation
    if event.event_kind == "provider_call_authorized":
        _verify_provider_authorization(event, previous)
        return
    if event.event_kind == "provider_call_settled":
        _verify_provider_settlement(event, previous)
        return
    if event.event_kind == "provider_call_reconciled":
        verify_provider_reconciliation(event, previous, prior_events)
        return
    if event.event_kind == "usage_recorded":
        _verify_usage_record(event, previous)
        return
    if (
        event.actual_usage is not None
        or event.reconciled_event_digest
        or current.usage != previous.usage
    ):
        _invalid("non-accounting event changed actual usage")
    _verify_provider_fields_unchanged(current, previous)


def _verify_provider_authorization(
    event: ResourceLedgerEvent,
    previous: ResourceReservation,
) -> None:
    current = event.reservation
    permit = event.provider_permit
    if (
        permit is None
        or event.actual_usage is not None
        or event.reconciled_event_digest
        or current.usage != previous.usage
    ):
        _invalid("provider authorization payload is incomplete")
    expected_pending = previous.authorized_pending + permit.anticipated_usage
    expected_permits = tuple(
        sorted((*previous.provider_permits, permit), key=lambda item: item.permit_id)
    )
    expected_invocations = tuple(
        sorted((*previous.provider_invocation_ids, permit.invocation_id))
    )
    if (
        current.authorized_pending != expected_pending
        or current.provider_permits != expected_permits
        or current.provider_invocation_ids != expected_invocations
    ):
        _invalid("provider authorization accounting mismatch")


def _verify_provider_settlement(
    event: ResourceLedgerEvent,
    previous: ResourceReservation,
) -> None:
    current = event.reservation
    permit = event.provider_permit
    actual = event.actual_usage
    if (
        permit is None
        or permit not in previous.provider_permits
        or actual is None
        or event.reconciled_event_digest
    ):
        _invalid("provider settlement actual usage is incomplete")
    if not is_complete_provider_actual_usage(actual):
        _invalid("provider settlement actual usage is not complete")
    expected_pending = _subtract(previous.authorized_pending, permit.anticipated_usage)
    expected_overrun = previous.observed_overrun + _overrun(
        actual, permit.anticipated_usage
    )
    expected_permits = tuple(
        item for item in previous.provider_permits if item != permit
    )
    if (
        current.usage != previous.usage + actual
        or current.observed_overrun != expected_overrun
        or current.authorized_pending != expected_pending
        or current.provider_permits != expected_permits
        or current.provider_invocation_ids != previous.provider_invocation_ids
    ):
        _invalid("provider settlement actual usage mismatch")


def _verify_usage_record(
    event: ResourceLedgerEvent,
    previous: ResourceReservation,
) -> None:
    actual = event.actual_usage
    if actual is None or not actual.any_positive() or event.reconciled_event_digest:
        _invalid("recorded actual usage is incomplete")
    if event.reservation.usage != previous.usage + actual:
        _invalid("recorded actual usage mismatch")
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
        _invalid("non-provider event changed provider accounting")


def _verify_allocation_and_proposal(
    event: ResourceLedgerEvent,
    previous: ResourceReservation,
) -> None:
    current = event.reservation
    if event.event_kind == "reservation_finalized":
        has_panel_lineage = bool(
            current.proposal_digest and current.proposal_lineage_digest
        )
        has_offline_lineage = (
            current.pool == "offline_optimization"
            and not current.proposal_digest
            and not current.proposal_lineage_digest
            and not current.provider_scope_ids
        )
        if (
            previous.proposal_digest
            or previous.proposal_lineage_digest
            or not (has_panel_lineage or has_offline_lineage)
            or not current.reserved.fits_within(previous.reserved)
        ):
            _invalid("FinalReservation proposal or allocation lineage is invalid")
        return
    if (
        current.proposal_digest != previous.proposal_digest
        or current.proposal_lineage_digest != previous.proposal_lineage_digest
        or current.provider_scope_ids != previous.provider_scope_ids
    ):
        _invalid("non-finalization event changed proposal lineage")
    if (
        event.event_kind not in {"reservation_expanded", "budget_grant_reconciled"}
        and current.reserved != previous.reserved
    ):
        _invalid("non-allocation event changed reserved resources")


def _verify_budget_transition(
    event: ResourceLedgerEvent,
    previous: ResourceReservation,
    operation: BudgetGrantOperation | None,
) -> None:
    current = event.reservation
    if event.event_kind == "reservation_finalized":
        if current.hard_limits != current.reserved:
            _invalid("FinalReservation hard limit must match allocation")
    elif event.event_kind == "reservation_expanded":
        _verify_grant_event(event, previous, operation, "resource_applied")
    elif event.event_kind == "budget_grant_reconciled":
        _verify_grant_event(event, previous, operation, "reconciled_released")
    elif (
        operation is not None
        or current.reserved != previous.reserved
        or current.hard_limits != previous.hard_limits
        or current.budget_revision != previous.budget_revision
        or current.budget_grant_ids != previous.budget_grant_ids
        or current.reconciled_budget_grant_ids != previous.reconciled_budget_grant_ids
        or current.last_budget_grant_operation_id
        != previous.last_budget_grant_operation_id
    ):
        _invalid("non-BudgetGrant event changed budget authority")


def _verify_grant_event(
    event: ResourceLedgerEvent,
    previous: ResourceReservation,
    operation: BudgetGrantOperation | None,
    expected_kind: str,
) -> None:
    if operation is None or operation.operation_kind != expected_kind:
        _invalid("BudgetGrantOperation is required for resource grant event")
    if (
        operation.target_event != event
        or operation.expected_reservation_revision != previous.revision
        or operation.expected_reservation_digest != previous.reservation_digest
        or operation.operation_effect_digest != event.operation_effect_digest
    ):
        _invalid("BudgetGrantOperation target does not match ledger event")
    grant = operation.grant
    if grant.final_reservation_id != previous.reservation_id:
        _invalid("BudgetGrant reservation lineage is invalid")
    if expected_kind == "resource_applied":
        _verify_grant_expansion(
            previous, event.reservation, grant.grant_id, grant.increment
        )
    else:
        _verify_grant_reconciliation(
            previous, event.reservation, grant.grant_id, grant.increment
        )


def _verify_grant_expansion(
    previous: ResourceReservation,
    current: ResourceReservation,
    grant_id: str,
    increment: ResourceAmounts,
) -> None:
    expected_ids = tuple(sorted((*previous.budget_grant_ids, grant_id)))
    if (
        current.budget_revision != previous.budget_revision + 1
        or current.last_budget_grant_operation_id != current.last_operation_id
        or current.budget_grant_ids != expected_ids
        or current.reserved != previous.reserved + increment
        or current.hard_limits != previous.hard_limits + increment
    ):
        _invalid("reservation expansion lacks exact BudgetGrant authority")


def _verify_grant_reconciliation(
    previous: ResourceReservation,
    current: ResourceReservation,
    grant_id: str,
    increment: ResourceAmounts,
) -> None:
    expected_reconciled = tuple(
        sorted((*previous.reconciled_budget_grant_ids, grant_id))
    )
    if (
        current.budget_revision != previous.budget_revision
        or current.last_budget_grant_operation_id != current.last_operation_id
        or current.budget_grant_ids != previous.budget_grant_ids
        or current.reconciled_budget_grant_ids != expected_reconciled
        or current.reserved != _subtract(previous.reserved, increment)
        or current.hard_limits != _subtract(previous.hard_limits, increment)
    ):
        _invalid("BudgetGrant reconciliation lacks exact release authority")


def _verify_reconciliation(
    event: ResourceLedgerEvent,
    previous: ResourceReservation,
) -> None:
    required = event.event_kind in {
        "reservation_released",
        "reservation_expired",
        "reservation_reconciled",
    }
    if required != (event.reconciliation is not None):
        _invalid("resource reconciliation presence is invalid")
    if event.reconciliation is None:
        return
    reconciliation = event.reconciliation
    expected_released = _subtract(
        previous.reserved,
        previous.usage + previous.authorized_pending,
    )
    if (
        reconciliation.reservation_id != previous.reservation_id
        or reconciliation.reservation_digest != previous.reservation_digest
        or reconciliation.usage != previous.usage
        or reconciliation.authorized_pending != previous.authorized_pending
        or reconciliation.released != expected_released
    ):
        _invalid("resource reconciliation does not bind previous projection")


def _monotonic(previous: ResourceAmounts, current: ResourceAmounts) -> bool:
    return all(
        getattr(previous, name) <= getattr(current, name)
        for name in ResourceAmounts.ALL_FIELDS
    )


def _invalid(message: str) -> Never:
    raise SharedStateIntegrityError(message)
