"""单次离线优化维护窗口的 Epoch 与资源租约。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.controller_models import (
    MaintenanceBudget,
    OptimizationEpoch,
    OptimizationEpochLeaseClaim,
)
from ai_sdlc.core.stage_review.optimization.controller_store import (
    OptimizationControllerStore,
)
from ai_sdlc.core.stage_review.panel import build_budget_policy
from ai_sdlc.core.stage_review.panel_models import ReviewerBudgetPolicy
from ai_sdlc.core.stage_review.resource_builders import build_budget_envelope, stable_id
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.resources import ResourceGovernor

_TERMINAL_RESERVATION_STATES = frozenset({"expired", "released", "reconciled"})
_RESOURCE_LEASE_GRACE_SECONDS = 60
T = TypeVar("T")


@dataclass(frozen=True)
class EpochLeaseGuard:
    store: OptimizationControllerStore
    claim: OptimizationEpochLeaseClaim
    observed_at: datetime | None = None

    @property
    def epoch_fencing_epoch(self) -> int:
        return self.claim.fencing_epoch

    @property
    def epoch_claim_digest(self) -> str:
        return self.claim.claim_digest

    def __call__(self) -> None:
        self.authorize()

    def authorize(self) -> None:
        with self.store.locked():
            self.authorize_locked()

    def authorize_locked(self) -> None:
        self.store.require_current_lease(
            self.claim,
            owner_id=self.claim.owner_id,
            now=self.observed_at,
        )

    def commit(self, operation: Callable[[], T]) -> T:
        with self.store.locked():
            self.authorize_locked()
            return operation()

    def release(self) -> None:
        with self.store.locked():
            self.store.release_lease(
                self.claim,
                owner_id=self.claim.owner_id,
            )


def _acquire_resource_window(
    governor: ResourceGovernor,
    epoch: OptimizationEpoch,
    claim: OptimizationEpochLeaseClaim,
    budget: MaintenanceBudget,
    *,
    project_id: str,
    policy: ReviewerBudgetPolicy,
    authorize_effect: Callable[[], None],
    now: datetime | None,
) -> ResourceReservation:
    owner = _resource_owner(epoch.epoch_id, claim.fencing_epoch)
    window_policy = _maintenance_policy(policy, budget, epoch)
    envelope = build_budget_envelope(
        project_id=project_id,
        work_item_id="offline-optimization",
        stage_review_session_id=_optimization_resource_session_id(
            epoch.epoch_id, claim.fencing_epoch
        ),
        risk_level="low",
        budget_policy=window_policy,
        pool="offline_optimization",
    )
    authorize_effect()
    admission = governor.reserve_admission(
        envelope,
        budget_policy=window_policy,
        lease_owner=owner,
        operation_id=stable_id("optimization-admission", claim.claim_digest),
        lease_seconds=budget.maximum_active_wall_clock
        + _RESOURCE_LEASE_GRACE_SECONDS,
        now=now,
    )
    if admission.reservation is None:
        raise SharedStateIntegrityError("offline optimization admission failed")
    try:
        return _finalize_resource_window(
            governor,
            admission.reservation,
            claim,
            authorize_effect=authorize_effect,
            now=now,
        )
    except BaseException:
        _release_reservation(governor, admission.reservation, claim.claim_digest)
        raise


def _recover_resource_windows(
    governor: ResourceGovernor,
    epoch: OptimizationEpoch,
    claims: tuple[OptimizationEpochLeaseClaim, ...],
    *,
    authorize_effect: Callable[[], None],
) -> None:
    observed: set[str] = set()
    for claim in claims:
        reservation = governor.get_reservation_by_session(
            _optimization_resource_session_id(epoch.epoch_id, claim.fencing_epoch)
        )
        if reservation is None:
            continue
        observed.add(reservation.reservation_id)
        if reservation.state not in _TERMINAL_RESERVATION_STATES:
            authorize_effect()
            _release_reservation(governor, reservation, claim.claim_digest)
    if epoch.reservation_id and epoch.reservation_id not in observed:
        raise SharedStateIntegrityError("optimization reservation lineage diverged")


def _release_resource_window(
    governor: ResourceGovernor,
    reservation: ResourceReservation,
    claim: OptimizationEpochLeaseClaim,
) -> None:
    _release_reservation(governor, reservation, claim.claim_digest)


def _finalize_resource_window(
    governor: ResourceGovernor,
    reservation: ResourceReservation,
    claim: OptimizationEpochLeaseClaim,
    *,
    authorize_effect: Callable[[], None],
    now: datetime | None,
) -> ResourceReservation:
    authorize_effect()
    result = governor.finalize_offline_reservation(
        reservation.reservation_id,
        lease_owner=reservation.lease_owner,
        expected_fencing_token=reservation.fencing_token,
        operation_id=stable_id("optimization-finalization", claim.claim_digest),
        now=now,
    )
    if result.reservation is None or result.reservation.state != "final":
        raise SharedStateIntegrityError("offline optimization finalization failed")
    return result.reservation


def _release_reservation(
    governor: ResourceGovernor,
    reservation: ResourceReservation,
    claim_digest: str,
) -> None:
    if reservation.state in _TERMINAL_RESERVATION_STATES:
        return
    result = governor.release_reservation(
        reservation.reservation_id,
        lease_owner=reservation.lease_owner,
        expected_fencing_token=reservation.fencing_token,
        operation_id=stable_id(
            "optimization-release", claim_digest, str(reservation.revision)
        ),
    )
    if result.result_code not in {"released", "lease_expired"}:
        raise SharedStateIntegrityError(
            f"offline optimization release failed: {result.result_code}"
        )


def _resource_owner(epoch_id: str, fencing_epoch: int) -> str:
    return stable_id("optimization-owner", epoch_id, str(fencing_epoch))


def _optimization_resource_session_id(epoch_id: str, fencing_epoch: int) -> str:
    return f"{epoch_id}.window.{fencing_epoch:020d}"


def _maintenance_policy(
    policy: ReviewerBudgetPolicy,
    budget: MaintenanceBudget,
    epoch: OptimizationEpoch,
) -> ReviewerBudgetPolicy:
    usage = epoch.cumulative_usage
    values = policy.model_dump(mode="json", exclude={"policy_digest"})
    values.update(
        maximum_slots=min(policy.maximum_slots, budget.maximum_parallelism),
        hard_provider_calls=min(
            policy.hard_provider_calls - int(usage.provider_calls),
            budget.maximum_provider_calls,
        ),
        hard_review_passes=min(
            policy.hard_review_passes, budget.maximum_provider_calls
        ),
        hard_tokens=min(
            policy.hard_tokens - int(usage.tokens), budget.maximum_tokens
        ),
        hard_cost=min(policy.hard_cost - usage.cost, budget.maximum_cost),
        hard_wall_clock=min(
            policy.hard_wall_clock - usage.active_wall_clock,
            budget.maximum_active_wall_clock,
        ),
        hard_parallelism=min(
            policy.hard_parallelism, budget.maximum_parallelism
        ),
        hard_role_replans=1,
        hard_provider_retries=min(
            policy.hard_provider_retries, budget.maximum_provider_calls
        ),
        hard_binding_attempts=min(
            policy.hard_binding_attempts, budget.maximum_provider_calls
        ),
    )
    return build_budget_policy(**values)
