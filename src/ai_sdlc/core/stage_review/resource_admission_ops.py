"""ResourceGovernor 的 Admission 与 Final 两阶段 CAS。"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
)
from ai_sdlc.core.stage_review.panel_models import ReviewerBudgetPolicy
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelProposal
from ai_sdlc.core.stage_review.resource_builders import (
    admission_idempotency_key,
    build_admission_reservation,
)
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceGovernorState,
    ResourceReservationResult,
)
from ai_sdlc.core.stage_review.resource_models import BudgetEnvelope
from ai_sdlc.core.stage_review.resource_projection_builder import update_reservation
from ai_sdlc.core.stage_review.resource_reservation_ops import (
    ResourceLineageError,
    commit_final_reservation,
    trusted_admission_inputs,
    trusted_finalization_inputs,
)
from ai_sdlc.core.stage_review.resource_reservation_ops import (
    _commit_offline_final_reservation as commit_offline_final_reservation,
)
from ai_sdlc.core.stage_review.resource_reservation_ops import (
    _offline_finalization_effect as offline_finalization_effect,
)
from ai_sdlc.core.stage_review.resource_runtime import (
    capacity_available,
    commit_reservation,
    idempotent_result,
    prepare_state,
    reservation_failure,
    result,
    utc_now,
)
from ai_sdlc.core.stage_review.resource_store import ResourceEventStore


def reserve_admission(
    store: ResourceEventStore,
    envelope: BudgetEnvelope,
    *,
    budget_policy: ReviewerBudgetPolicy,
    lease_owner: str,
    operation_id: str,
    lease_seconds: float,
    now: datetime | None,
) -> ResourceReservationResult:
    try:
        trusted, effect = trusted_admission_inputs(
            envelope,
            budget_policy,
            lease_owner,
            lease_seconds,
            store.project_id,
        )
        current_time = utc_now(now)
        with store.locked():
            state = prepare_state(store, current_time)
            return _reserve_locked(
                store,
                state,
                trusted,
                lease_owner,
                lease_seconds,
                operation_id,
                effect,
                current_time,
            )
    except ResourceLockUnavailableError:
        return result("lock_unavailable")
    except ResourceLineageError:
        return result("invalid_reservation")
    except SharedStateIntegrityError:
        return result("state_corrupt")
    except (ValidationError, ValueError, AttributeError):
        return result("invalid_input")


def finalize_reservation(
    store: ResourceEventStore,
    reservation_id: str,
    *,
    proposal: ReviewerPanelProposal,
    lease_owner: str,
    expected_fencing_token: int,
    operation_id: str,
    now: datetime | None,
) -> ResourceReservationResult:
    try:
        trusted, effect = trusted_finalization_inputs(
            proposal, reservation_id, lease_owner, expected_fencing_token
        )
        current_time = utc_now(now)
        with store.locked():
            state = prepare_state(store, current_time)
            repeated = idempotent_result(
                store, state, operation_id, reservation_id, effect
            )
            if repeated is not None:
                return repeated
            current = state.reservations.get(reservation_id)
            failure = reservation_failure(
                current,
                expected_fencing_token,
                current_time,
                "admission",
                lease_owner=lease_owner,
            )
            if failure is not None:
                return failure
            assert current is not None
            return commit_final_reservation(
                store, state, current, trusted, effect, operation_id
            )
    except ResourceLockUnavailableError:
        return result("lock_unavailable")
    except SharedStateIntegrityError:
        return result("state_corrupt")
    except (ValidationError, ValueError, AttributeError):
        return result("invalid_input")


def _finalize_offline_reservation(
    store: ResourceEventStore,
    reservation_id: str,
    *,
    lease_owner: str,
    expected_fencing_token: int,
    operation_id: str,
    now: datetime | None,
) -> ResourceReservationResult:
    try:
        effect = offline_finalization_effect(
            reservation_id, lease_owner, expected_fencing_token
        )
        current_time = utc_now(now)
        with store.locked():
            state = prepare_state(store, current_time)
            repeated = idempotent_result(
                store, state, operation_id, reservation_id, effect
            )
            if repeated is not None:
                return repeated
            current = state.reservations.get(reservation_id)
            failure = reservation_failure(
                current,
                expected_fencing_token,
                current_time,
                "admission",
                lease_owner=lease_owner,
            )
            if failure is not None:
                return failure
            assert current is not None
            return commit_offline_final_reservation(
                store, state, current, effect, operation_id
            )
    except ResourceLockUnavailableError:
        return result("lock_unavailable")
    except SharedStateIntegrityError:
        return result("state_corrupt")
    except (ValidationError, ValueError, AttributeError):
        return result("invalid_input")


def _reserve_locked(
    store: ResourceEventStore,
    state: ResourceGovernorState,
    envelope: BudgetEnvelope,
    lease_owner: str,
    lease_seconds: float,
    operation_id: str,
    effect: str,
    now: datetime,
) -> ResourceReservationResult:
    repeated = idempotent_result(
        store, state, operation_id, expected_effect_digest=effect
    )
    if repeated is not None:
        return repeated
    candidate = build_admission_reservation(
        envelope,
        operation_id=operation_id,
        operation_effect_digest=effect,
        lease_owner=lease_owner,
        fencing_token=state.next_fencing_token,
        lease_expires_at=now + timedelta(seconds=lease_seconds),
    )
    existing = state.reservations.get(candidate.reservation_id)
    if existing is not None:
        if existing.idempotency_key != admission_idempotency_key(envelope):
            return result("state_corrupt")
        if existing.lease_owner != lease_owner:
            return result("invalid_reservation", existing)
        if existing.state not in {"admission", "final"}:
            return result("invalid_reservation", existing)
        replayed = update_reservation(
            existing,
            operation_id=operation_id,
            operation_effect_digest=effect,
        )
        return commit_reservation(
            store,
            state,
            "admission_reused",
            operation_id,
            replayed,
        )
    if not capacity_available(
        store, state, envelope.pool, envelope.admission_requirement
    ):
        return result("capacity_exhausted")
    return commit_reservation(
        store, state, "admission_reserved", operation_id, candidate
    )
