"""BudgetGrant 的资源侧两阶段 CAS、补偿与崩溃恢复。"""

from __future__ import annotations

from datetime import datetime

from pydantic import ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
)
from ai_sdlc.core.stage_review.resource_builders import (
    build_resource_event,
    stable_id,
    subtract_resources,
)
from ai_sdlc.core.stage_review.resource_digests import (
    budget_grant_digest,
    budget_grant_operation_digest,
    resource_operation_effect_digest,
)
from ai_sdlc.core.stage_review.resource_grant_models import (
    BudgetGrant,
    BudgetGrantOperation,
    budget_grant_idempotency_key,
)
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceGovernorState,
    ResourceReservation,
    ResourceReservationResult,
)
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts, ResourceEventKind
from ai_sdlc.core.stage_review.resource_projection_builder import update_reservation
from ai_sdlc.core.stage_review.resource_runtime import (
    idempotent_result,
    prepare_state,
    pressure,
    replacement_fits,
    reservation_failure,
    result,
    utc_now,
)
from ai_sdlc.core.stage_review.resource_store import ResourceEventStore


def build_budget_grant(
    *,
    project_id: str,
    work_item_id: str,
    stage_review_session_id: str,
    final_reservation_id: str,
    expected_budget_revision: int,
    increment: ResourceAmounts,
    requested_event_digest: str,
) -> BudgetGrant:
    draft = BudgetGrant.model_construct(
        grant_id="",
        project_id=project_id,
        work_item_id=work_item_id,
        stage_review_session_id=stage_review_session_id,
        final_reservation_id=final_reservation_id,
        expected_budget_revision=expected_budget_revision,
        increment=increment,
        requested_event_digest=requested_event_digest,
        grant_digest="",
        idempotency_key="",
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["grant_digest"] = budget_grant_digest(draft)
    content_addressed = BudgetGrant.model_construct(**payload)
    key = budget_grant_idempotency_key(content_addressed)
    payload.update({"grant_id": key, "idempotency_key": key})
    return BudgetGrant.model_validate(payload)


def apply_budget_grant(
    store: ResourceEventStore,
    grant: BudgetGrant,
    *,
    lease_owner: str,
    expected_reservation_revision: int,
    expected_reservation_digest: str,
    expected_fencing_token: int,
    now: datetime | None,
) -> ResourceReservationResult:
    return _execute_grant_operation(
        store,
        grant,
        operation_kind="resource_applied",
        lease_owner=lease_owner,
        expected_reservation_revision=expected_reservation_revision,
        expected_reservation_digest=expected_reservation_digest,
        expected_fencing_token=expected_fencing_token,
        now=now,
    )


def reconcile_budget_grant(
    store: ResourceEventStore,
    grant: BudgetGrant,
    *,
    lease_owner: str,
    expected_reservation_revision: int,
    expected_reservation_digest: str,
    expected_fencing_token: int,
    now: datetime | None,
) -> ResourceReservationResult:
    return _execute_grant_operation(
        store,
        grant,
        operation_kind="reconciled_released",
        lease_owner=lease_owner,
        expected_reservation_revision=expected_reservation_revision,
        expected_reservation_digest=expected_reservation_digest,
        expected_fencing_token=expected_fencing_token,
        now=now,
    )


def _execute_grant_operation(
    store: ResourceEventStore,
    grant: BudgetGrant,
    *,
    operation_kind: str,
    lease_owner: str,
    expected_reservation_revision: int,
    expected_reservation_digest: str,
    expected_fencing_token: int,
    now: datetime | None,
) -> ResourceReservationResult:
    try:
        trusted, operation_id, effect = _trusted_inputs(
            grant,
            operation_kind,
            lease_owner,
            expected_reservation_revision,
            expected_reservation_digest,
            expected_fencing_token,
        )
        with store.locked():
            return _execute_locked(
                store,
                trusted,
                operation_kind,
                lease_owner,
                expected_reservation_revision,
                expected_reservation_digest,
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


def _execute_locked(
    store: ResourceEventStore,
    grant: BudgetGrant,
    operation_kind: str,
    lease_owner: str,
    expected_revision: int,
    expected_digest: str,
    fencing_token: int,
    operation_id: str,
    effect: str,
    now: datetime,
) -> ResourceReservationResult:
    state = prepare_state(store, now)
    repeated = idempotent_result(
        store, state, operation_id, grant.final_reservation_id, effect
    )
    if repeated is not None:
        return repeated
    current = state.reservations.get(grant.final_reservation_id)
    failure = _grant_failure(
        current,
        grant,
        operation_kind,
        lease_owner,
        expected_revision,
        expected_digest,
        fencing_token,
        now,
    )
    if failure is not None:
        return failure
    assert current is not None
    target = _grant_target(
        store, state, current, grant, operation_kind, operation_id, effect
    )
    if isinstance(target, ResourceReservationResult):
        return target
    store.persist_budget_grant(grant)
    operation = _build_operation(
        state, current, target, grant, operation_kind, operation_id, effect
    )
    store.persist_budget_grant_operation(operation)
    next_state = store.append_event(operation.target_event)
    committed = next_state.reservations[current.reservation_id]
    result_code = "expanded" if operation_kind == "resource_applied" else "reconciled"
    return _grant_result(result_code, committed, target)


def _grant_result(
    result_code: str,
    committed: ResourceReservation,
    target: ResourceReservation,
) -> ResourceReservationResult:
    if result_code == "expanded":
        return result(
            "expanded",
            committed,
            pressure(committed),
            operation_reservation=target,
        )
    return result(
        "reconciled",
        committed,
        pressure(committed),
        operation_reservation=target,
    )


def _trusted_inputs(
    grant: BudgetGrant,
    operation_kind: str,
    lease_owner: str,
    expected_revision: int,
    expected_digest: str,
    fencing_token: int,
) -> tuple[BudgetGrant, str, str]:
    trusted = BudgetGrant.model_validate(grant.model_dump(mode="json"))
    if operation_kind not in {"resource_applied", "reconciled_released"}:
        raise ValueError("unsupported budget grant operation")
    if not lease_owner.strip() or lease_owner != lease_owner.strip():
        raise ValueError("budget grant operation requires lease owner")
    suffix = "apply" if operation_kind == "resource_applied" else "reconcile"
    operation_id = stable_id("budget-grant-operation", trusted.idempotency_key, suffix)
    effect = resource_operation_effect_digest(
        f"budget_grant_{suffix}",
        {
            "grant_digest": trusted.grant_digest,
            "lease_owner": lease_owner,
            "expected_reservation_revision": expected_revision,
            "expected_reservation_digest": expected_digest,
            "expected_fencing_token": fencing_token,
        },
    )
    return trusted, operation_id, effect


def _grant_failure(
    current: ResourceReservation | None,
    grant: BudgetGrant,
    operation_kind: str,
    lease_owner: str,
    expected_revision: int,
    expected_digest: str,
    fencing_token: int,
    now: datetime,
) -> ResourceReservationResult | None:
    failure = reservation_failure(
        current,
        fencing_token,
        now,
        "final",
        lease_owner=lease_owner,
    )
    if failure is not None:
        return failure
    assert current is not None
    lineage = (
        current.project_id == grant.project_id,
        current.work_item_id == grant.work_item_id,
        current.stage_review_session_id == grant.stage_review_session_id,
        current.revision == expected_revision,
        current.reservation_digest == expected_digest,
    )
    if not all(lineage):
        return result("cas_conflict", current)
    if operation_kind == "resource_applied":
        if current.budget_revision != grant.expected_budget_revision:
            return result("cas_conflict", current)
        if grant.grant_id in current.budget_grant_ids:
            return result("state_corrupt", current)
    elif (
        grant.grant_id not in current.budget_grant_ids
        or grant.grant_id in current.reconciled_budget_grant_ids
    ):
        return result("invalid_reservation", current)
    return None


def _grant_target(
    store: ResourceEventStore,
    state: ResourceGovernorState,
    current: ResourceReservation,
    grant: BudgetGrant,
    operation_kind: str,
    operation_id: str,
    effect: str,
) -> ResourceReservation | ResourceReservationResult:
    applying = operation_kind == "resource_applied"
    reserved = (
        current.reserved + grant.increment
        if applying
        else subtract_resources(current.reserved, grant.increment)
    )
    hard = (
        current.hard_limits + grant.increment
        if applying
        else subtract_resources(current.hard_limits, grant.increment)
    )
    if applying and not replacement_fits(store, state, current, reserved):
        return result("capacity_exhausted", current)
    if not (current.usage + current.authorized_pending).fits_within(reserved):
        return result("hard_limit_exceeded", current)
    return update_reservation(
        current,
        operation_id=operation_id,
        operation_effect_digest=effect,
        reserved=reserved,
        hard_limits=hard,
        budget_revision=(
            grant.expected_budget_revision + 1 if applying else current.budget_revision
        ),
        last_budget_grant_operation_id=operation_id,
        budget_grant_ids=(*current.budget_grant_ids, grant.grant_id),
        reconciled_budget_grant_ids=(
            current.reconciled_budget_grant_ids
            if applying
            else (*current.reconciled_budget_grant_ids, grant.grant_id)
        ),
        fencing_token=state.next_fencing_token,
    )


def _build_operation(
    state: ResourceGovernorState,
    current: ResourceReservation,
    target: ResourceReservation,
    grant: BudgetGrant,
    operation_kind: str,
    operation_id: str,
    effect: str,
) -> BudgetGrantOperation:
    event_kind: ResourceEventKind = (
        "reservation_expanded"
        if operation_kind == "resource_applied"
        else "budget_grant_reconciled"
    )
    event = build_resource_event(
        sequence=state.head_sequence + 1,
        event_kind=event_kind,
        operation_id=operation_id,
        previous_event_digest=state.head_digest,
        previous_reservation_digest=current.reservation_digest,
        reservation=target,
    )
    draft = BudgetGrantOperation.model_construct(
        operation_id=operation_id,
        operation_kind=operation_kind,
        grant=grant,
        expected_reservation_revision=current.revision,
        expected_reservation_digest=current.reservation_digest,
        operation_effect_digest=effect,
        target_projection_digest=target.reservation_digest,
        target_event_id=event.event_id,
        target_event_digest=event.event_digest,
        target_event=event,
        operation_digest="",
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["grant"] = grant
    payload["target_event"] = event
    payload["operation_digest"] = budget_grant_operation_digest(draft)
    return BudgetGrantOperation.model_validate(payload)
