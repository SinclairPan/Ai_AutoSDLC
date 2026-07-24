"""Offline Optimization 单步维护窗口的排他执行。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol, cast

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.controller_models import (
    EpochState,
    MaintenanceBudget,
    MaintenanceResultCode,
    OptimizationEpoch,
    OptimizationMaintenanceResult,
    OptimizationStepResult,
)
from ai_sdlc.core.stage_review.optimization.controller_store import (
    OptimizationControllerStore,
    OptimizationEpochLeaseBusyError,
)
from ai_sdlc.core.stage_review.optimization.maintenance_window import (
    EpochLeaseGuard,
)
from ai_sdlc.core.stage_review.optimization.maintenance_window import (
    _acquire_resource_window as acquire_resource_window,
)
from ai_sdlc.core.stage_review.optimization.maintenance_window import (
    _recover_resource_windows as recover_resource_windows,
)
from ai_sdlc.core.stage_review.optimization.maintenance_window import (
    _release_resource_window as release_resource_window,
)
from ai_sdlc.core.stage_review.panel_models import ReviewerBudgetPolicy
from ai_sdlc.core.stage_review.resource_builders import utc_iso
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.resource_runtime import utc_now
from ai_sdlc.core.stage_review.resources import ResourceGovernor

TERMINAL_EPOCH_STATES = frozenset({"promoted", "no_change", "failed"})
_NEXT_PIPELINE_STATE = {
    "snapshotting": "generating",
    "generating": "replaying",
    "replaying": "holdout_evaluating",
    "holdout_evaluating": "shadow_observing",
    "shadow_observing": "evaluating",
    "evaluating": "promoting",
    "promoting": "promoted",
}


class OptimizationStepExecutor(Protocol):
    def advance(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        *,
        authorize_effect: Callable[[], None],
    ) -> OptimizationStepResult: ...


class OptimizationMaintenanceRunner:
    def __init__(
        self,
        *,
        project_id: str,
        store: OptimizationControllerStore,
        resource_governor: ResourceGovernor,
        budget_policy: ReviewerBudgetPolicy,
        step_executor: OptimizationStepExecutor,
        foreground_requested: Callable[[], bool],
    ) -> None:
        self.project_id = project_id
        self.store = store
        self.resource_governor = resource_governor
        self.budget_policy = budget_policy
        self.step_executor = step_executor
        self.foreground_requested = foreground_requested

    def advance(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        *,
        owner_id: str,
        now: datetime | None,
    ) -> OptimizationMaintenanceResult:
        try:
            epoch, guard = self._claim_and_resume(
                epoch, budget=budget, owner_id=owner_id, now=now
            )
        except OptimizationEpochLeaseBusyError:
            return OptimizationMaintenanceResult(
                result_code="paused", epoch=epoch, reason="epoch_lease_busy"
            )
        failed = True
        try:
            result = self._advance_claimed(epoch, budget, guard, now=now)
            failed = False
            return result
        finally:
            try:
                guard.release()
            except SharedStateIntegrityError:
                if not failed:
                    raise

    def _claim_and_resume(
        self,
        epoch: OptimizationEpoch,
        *,
        budget: MaintenanceBudget,
        owner_id: str,
        now: datetime | None,
    ) -> tuple[OptimizationEpoch, EpochLeaseGuard]:
        with self.store.locked():
            current = self.store.epoch(epoch.epoch_id)
            if current is None:
                raise SharedStateIntegrityError("optimization epoch disappeared")
            claim = self.store.acquire_lease(
                current.epoch_id,
                owner_id=owner_id,
                now=now,
                lease_seconds=budget.maximum_active_wall_clock + 60,
            )
            advanced = self._append_epoch_locked(
                current,
                state=_resume_state(current),
                resume_state=None,
                lease_fencing_epoch=claim.fencing_epoch,
            )
            return advanced, EpochLeaseGuard(self.store, claim, now)

    def _advance_claimed(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        guard: EpochLeaseGuard,
        *,
        now: datetime | None,
    ) -> OptimizationMaintenanceResult:
        if self.foreground_requested():
            paused = self._append_epoch_guarded(
                epoch, guard, state="paused", resume_state=epoch.state
            )
            return _maintenance_result(paused)
        if _epoch_budget_exhausted(epoch, self.budget_policy):
            stopped = self._append_epoch_guarded(
                epoch,
                guard,
                state="no_change",
                terminal_at=utc_iso(now or utc_now(None)),
            )
            return _maintenance_result(stopped, reason="epoch_budget_exhausted")
        bound, reservation = self._open_resource_window(
            epoch, budget, guard, now=now
        )
        try:
            step = self.step_executor.advance(
                bound, budget, authorize_effect=guard
            )
            advanced = self._commit_step(bound, step, guard, now=now)
        except BaseException as error:
            self._cleanup_failed_window(bound, reservation, guard, error)
            raise
        cleaned = self._close_resource_window(advanced, reservation, guard)
        return _maintenance_result(cleaned, reason=step.reason)

    def _open_resource_window(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        guard: EpochLeaseGuard,
        *,
        now: datetime | None,
    ) -> tuple[OptimizationEpoch, ResourceReservation]:
        guard.authorize()
        recover_resource_windows(
            self.resource_governor,
            epoch,
            self.store.lease_claims(epoch.epoch_id),
            authorize_effect=guard.authorize,
        )
        if epoch.reservation_id:
            epoch = self._append_epoch_guarded(
                epoch, guard, reservation_id="", reservation_fencing_token=0
            )
        reservation = acquire_resource_window(
            self.resource_governor,
            epoch,
            guard.claim,
            budget,
            project_id=self.project_id,
            policy=self.budget_policy,
            authorize_effect=guard.authorize,
            now=now,
        )
        bound = self._append_epoch_guarded(
            epoch,
            guard,
            reservation_id=reservation.reservation_id,
            reservation_fencing_token=reservation.fencing_token,
        )
        return bound, reservation

    def _commit_step(
        self,
        epoch: OptimizationEpoch,
        result: OptimizationStepResult,
        guard: EpochLeaseGuard,
        *,
        now: datetime | None,
    ) -> OptimizationEpoch:
        with self.store.locked():
            guard.authorize_locked()
            current = self.store.epoch(epoch.epoch_id)
            if current is None or current.epoch_digest != epoch.epoch_digest:
                raise SharedStateIntegrityError("optimization step lost its epoch lease")
            _verify_pipeline_transition(current.state, result.next_state)
            terminal_at = (
                utc_iso(now or utc_now(None))
                if result.next_state in TERMINAL_EPOCH_STATES
                else current.terminal_at
            )
            return self._append_epoch_locked(
                current,
                state=result.next_state,
                dataset_digest=result.dataset_digest or current.dataset_digest,
                finalist_candidate_digest=(
                    result.finalist_candidate_digest
                    or current.finalist_candidate_digest
                ),
                failure_reason=result.reason if result.next_state == "failed" else "",
                terminal_at=terminal_at,
            )

    def _close_resource_window(
        self,
        epoch: OptimizationEpoch,
        reservation: ResourceReservation,
        guard: EpochLeaseGuard,
    ) -> OptimizationEpoch:
        guard.authorize()
        release_resource_window(self.resource_governor, reservation, guard.claim)
        usage = self._epoch_usage(epoch.epoch_id)
        return self._append_epoch_guarded(
            epoch,
            guard,
            reservation_id="",
            reservation_fencing_token=0,
            cumulative_usage=usage,
        )

    def _epoch_usage(self, epoch_id: str) -> ResourceAmounts:
        total = ResourceAmounts()
        for claim in self.store.lease_claims(epoch_id):
            reservation = self.resource_governor.get_reservation_by_session(
                f"{epoch_id}.window.{claim.fencing_epoch:020d}"
            )
            if reservation is not None:
                total += reservation.usage
        return total

    def _cleanup_failed_window(
        self,
        epoch: OptimizationEpoch,
        reservation: ResourceReservation,
        guard: EpochLeaseGuard,
        error: BaseException,
    ) -> None:
        try:
            self._close_resource_window(epoch, reservation, guard)
        except BaseException as cleanup_error:
            error.add_note(f"resource cleanup deferred: {cleanup_error}")

    def _append_epoch_guarded(
        self,
        epoch: OptimizationEpoch,
        guard: EpochLeaseGuard,
        **changes: object,
    ) -> OptimizationEpoch:
        with self.store.locked():
            guard.authorize_locked()
            current = self.store.epoch(epoch.epoch_id)
            if current is None or current.epoch_digest != epoch.epoch_digest:
                raise SharedStateIntegrityError("optimization epoch CAS is stale")
            return self._append_epoch_locked(current, **changes)

    def _append_epoch_locked(
        self, epoch: OptimizationEpoch, **changes: object
    ) -> OptimizationEpoch:
        payload = epoch.model_dump(mode="json")
        payload.update(changes)
        payload.update(
            revision=epoch.revision + 1,
            previous_epoch_digest=epoch.epoch_digest,
            epoch_digest="",
        )
        return self.store.append_epoch(OptimizationEpoch.model_validate(payload))


def _maintenance_result(
    epoch: OptimizationEpoch, *, reason: str = ""
) -> OptimizationMaintenanceResult:
    raw_code = (
        epoch.state
        if epoch.state in TERMINAL_EPOCH_STATES
        else "paused"
        if epoch.state == "paused"
        else "advanced"
    )
    return OptimizationMaintenanceResult(
        result_code=cast(MaintenanceResultCode, raw_code),
        epoch=epoch,
        reason=reason,
    )


def _resume_state(epoch: OptimizationEpoch) -> EpochState:
    if epoch.state == "paused":
        return epoch.resume_state or "snapshotting"
    if epoch.state == "queued":
        return "snapshotting"
    return epoch.state


def _verify_pipeline_transition(current: str, target: str) -> None:
    allowed = {"no_change", "failed", "retry_wait"}
    expected = _NEXT_PIPELINE_STATE.get(current)
    if expected is not None:
        allowed.add(expected)
    if current == "shadow_observing":
        allowed.add("shadow_observing")
    if target not in allowed:
        raise SharedStateIntegrityError("optimization pipeline transition is invalid")


def _epoch_budget_exhausted(
    epoch: OptimizationEpoch,
    policy: ReviewerBudgetPolicy,
) -> bool:
    usage = epoch.cumulative_usage
    return any(
        (
            usage.provider_calls >= policy.hard_provider_calls,
            usage.tokens >= policy.hard_tokens,
            usage.cost >= policy.hard_cost,
            usage.active_wall_clock >= policy.hard_wall_clock,
        )
    )
