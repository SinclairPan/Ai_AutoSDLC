from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.unit.stage_review.test_resources import _capacity, _governor, _policy

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.controller import (
    OfflineOptimizationController,
    OptimizationStepExecutor,
)
from ai_sdlc.core.stage_review.optimization.controller_models import (
    MaintenanceBudget,
    OptimizationConstitution,
    OptimizationEpoch,
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
    _optimization_resource_session_id as optimization_resource_session_id,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    OptimizationSessionObservation,
)
from ai_sdlc.core.stage_review.provider_journal import ProviderInvocationJournal
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts


class _NoChangeExecutor(OptimizationStepExecutor):
    def __init__(self) -> None:
        self.calls = 0

    def advance(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        *,
        authorize_effect: object,
    ) -> OptimizationStepResult:
        del budget
        assert callable(authorize_effect)
        authorize_effect()
        self.calls += 1
        return OptimizationStepResult(next_state="no_change", reason="no_candidate")


class _InvalidExecutor(OptimizationStepExecutor):
    def advance(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        *,
        authorize_effect: object,
    ) -> OptimizationStepResult:
        del epoch, budget
        assert callable(authorize_effect)
        authorize_effect()
        return OptimizationStepResult(next_state="promoted")


class _TwoStepExecutor(OptimizationStepExecutor):
    def __init__(self) -> None:
        self.calls = 0

    def advance(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        *,
        authorize_effect: object,
    ) -> OptimizationStepResult:
        del budget
        assert callable(authorize_effect)
        authorize_effect()
        self.calls += 1
        if epoch.state == "snapshotting":
            return OptimizationStepResult(next_state="generating")
        return OptimizationStepResult(next_state="no_change", reason="done")


class _PromoteExecutor(OptimizationStepExecutor):
    _NEXT = {
        "snapshotting": "generating",
        "generating": "replaying",
        "replaying": "holdout_evaluating",
        "holdout_evaluating": "shadow_observing",
        "shadow_observing": "evaluating",
        "evaluating": "promoting",
        "promoting": "promoted",
    }

    def advance(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        *,
        authorize_effect: object,
    ) -> OptimizationStepResult:
        del budget
        assert callable(authorize_effect)
        authorize_effect()
        return OptimizationStepResult(next_state=self._NEXT[epoch.state])


class _FailOnceExecutor(_NoChangeExecutor):
    def advance(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        *,
        authorize_effect: object,
    ) -> OptimizationStepResult:
        if self.calls == 0:
            assert callable(authorize_effect)
            authorize_effect()
            self.calls += 1
            raise RuntimeError("executor failed")
        return super().advance(
            epoch,
            budget,
            authorize_effect=authorize_effect,
        )


class _UsageExecutor(_TwoStepExecutor):
    governor: object | None = None

    def advance(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        *,
        authorize_effect: object,
    ) -> OptimizationStepResult:
        assert callable(authorize_effect)
        authorize_effect()
        assert self.governor is not None
        reservation = self.governor.get_reservation(epoch.reservation_id)
        self.governor.record_usage(
            epoch.reservation_id,
            delta=ResourceAmounts(tokens=1_000, cost=0.1, active_wall_clock=1),
            lease_owner=reservation.lease_owner,
            expected_fencing_token=reservation.fencing_token,
            operation_id=f"optimization-usage.{self.calls}",
        )
        return super().advance(epoch, budget, authorize_effect=authorize_effect)


def test_constitution_freezes_required_baseline_limits() -> None:
    constitution = _constitution()

    assert constitution.minimum_created_sessions == 30
    assert constitution.minimum_evaluable_sessions == 20
    assert constitution.holdout_ratio == 0.2
    assert constitution.minimum_holdout_sessions == 10
    assert constitution.minimum_shadow_sessions == 10
    assert constitution.minimum_shadow_days == 14
    assert constitution.candidate_family_limit == 8
    assert constitution.no_change_new_session_cooldown == 10
    assert constitution.promotion_new_session_cooldown == 10
    assert constitution.promotion_day_cooldown == 7
    assert constitution.familywise_alpha == 0.05
    assert constitution.constitution_digest


def test_record_observation_triggers_once_at_threshold(tmp_path: Path) -> None:
    controller, _ = _controller(tmp_path)
    last = None
    for sequence in range(1, 31):
        last = controller._record_session_observation(_observation(sequence))
        assert last.triggered is (sequence == 30)

    assert last is not None
    repeated = controller._record_session_observation(_observation(30))

    assert repeated == last
    assert repeated.new_session_count == 30
    assert repeated.session_sequence_high_watermark == 30
    assert controller._trigger_events() == (last,)


def test_refresh_trigger_uses_observations_recovered_by_another_component(
    tmp_path: Path,
) -> None:
    controller, _ = _controller(tmp_path)
    for sequence in range(1, 31):
        controller.observations.append(_observation(sequence))

    event = controller.refresh_trigger()

    assert event.triggered
    assert controller._trigger_events() == (event,)


def test_same_trigger_creates_one_offline_epoch_and_one_reservation(
    tmp_path: Path,
) -> None:
    executor = _NoChangeExecutor()
    controller, governor = _controller(tmp_path, executor=executor)
    _record_threshold(controller)

    first = controller.advance_optimization(
        "project.shared", _maintenance_budget(), owner_id="controller.worker-one"
    )
    repeated = controller.advance_optimization(
        "project.shared", _maintenance_budget(), owner_id="controller.worker-two"
    )

    assert first.result_code == "no_change"
    assert first.epoch is not None
    assert first.epoch.state == "no_change"
    assert repeated.epoch == first.epoch
    assert executor.calls == 1
    snapshot = governor.snapshot()
    assert snapshot.reservation_count == 1
    reservation = governor.get_reservation_by_session(
        optimization_resource_session_id(first.epoch.epoch_id, 1)
    )
    assert reservation is not None
    assert reservation.pool == "offline_optimization"
    assert reservation.state == "released"
    assert reservation.hard_limits.provider_calls == 2
    assert reservation.hard_limits.tokens == min(_policy().hard_tokens, 100_000)
    assert reservation.hard_limits.cost == 2
    assert reservation.hard_limits.active_wall_clock == min(
        _policy().hard_wall_clock, 300
    )
    assert first.epoch.reservation_id == ""


def test_foreground_preemption_pauses_before_new_work_and_resume_is_idempotent(
    tmp_path: Path,
) -> None:
    executor = _NoChangeExecutor()
    foreground_requested = True
    controller, _ = _controller(
        tmp_path,
        executor=executor,
        foreground_requested=lambda: foreground_requested,
    )
    _record_threshold(controller)

    paused = controller.advance_optimization(
        "project.shared", _maintenance_budget(), owner_id="controller.worker-one"
    )
    assert paused.result_code == "paused"
    assert paused.epoch is not None
    assert paused.epoch.state == "paused"
    assert executor.calls == 0
    assert controller.resource_governor.snapshot().reservation_count == 0

    foreground_requested = False
    resumed = controller.advance_optimization(
        "project.shared", _maintenance_budget(), owner_id="controller.worker-two"
    )

    assert resumed.result_code == "no_change"
    assert resumed.epoch is not None
    assert resumed.epoch.epoch_id == paused.epoch.epoch_id
    assert executor.calls == 1


def test_no_change_requires_ten_new_sessions_before_another_regular_epoch(
    tmp_path: Path,
) -> None:
    controller, _ = _controller(tmp_path)
    _record_threshold(controller)
    first = controller.advance_optimization(
        "project.shared", _maintenance_budget(), owner_id="controller.worker-one"
    )
    assert first.result_code == "no_change"

    for sequence in range(31, 40):
        event = controller._record_session_observation(_observation(sequence))
        assert not event.triggered
    event = controller._record_session_observation(_observation(40))

    assert event.triggered
    assert event.trigger_fingerprint != first.epoch.trigger_fingerprint  # type: ignore[union-attr]


def test_promoted_epoch_cooldown_allows_ten_sessions_or_seven_days(
    tmp_path: Path,
) -> None:
    current = ["2026-07-01T00:00:00+00:00"]
    controller, _ = _controller(
        tmp_path,
        executor=_PromoteExecutor(),
        clock=lambda: current[0],
    )
    _record_threshold(controller)
    result = None
    for _ in range(7):
        result = controller.advance_optimization(
            "project.shared",
            _maintenance_budget(),
            owner_id="controller.promoter",
        )
    assert result is not None and result.result_code == "promoted"
    assert result.epoch is not None
    promoted_at = datetime.fromisoformat(result.epoch.terminal_at)

    current[0] = (promoted_at + timedelta(days=7, seconds=-1)).isoformat()
    assert not controller._record_session_observation(_observation(31)).triggered
    current[0] = (promoted_at + timedelta(days=7)).isoformat()
    assert controller.refresh_trigger().triggered


def test_consumed_critical_fact_does_not_bypass_later_cooldown(
    tmp_path: Path,
) -> None:
    controller, _ = _controller(tmp_path)
    _record_threshold(controller)
    controller.advance_optimization(
        "project.shared", _maintenance_budget(), owner_id="controller.worker-one"
    )
    first_fact = controller._record_session_observation(_critical_observation(31))
    assert first_fact.triggered
    controller.advance_optimization(
        "project.shared", _maintenance_budget(), owner_id="controller.worker-two"
    )

    one_more_session = controller._record_session_observation(_observation(32))
    second_fact = controller._record_session_observation(_critical_observation(33))

    assert not one_more_session.triggered
    assert second_fact.triggered


def test_trigger_freezes_current_active_snapshot_digest(tmp_path: Path) -> None:
    controller, _ = _controller(
        tmp_path, active_snapshot_digest=lambda: "sha256:active-challenger"
    )

    _record_threshold(controller)

    assert controller._trigger_events()[-1].baseline_snapshot_digest == (
        "sha256:active-challenger"
    )


def test_controller_rejects_executor_that_skips_governed_pipeline(tmp_path: Path) -> None:
    controller, _ = _controller(tmp_path, executor=_InvalidExecutor())
    _record_threshold(controller)

    with pytest.raises(SharedStateIntegrityError, match="transition"):
        controller.advance_optimization(
            "project.shared",
            _maintenance_budget(),
            owner_id="controller.worker",
        )


def test_failed_executor_releases_epoch_lease_for_retry(tmp_path: Path) -> None:
    executor = _FailOnceExecutor()
    controller, _ = _controller(tmp_path, executor=executor)
    _record_threshold(controller)

    with pytest.raises(RuntimeError, match="executor failed"):
        controller.advance_optimization(
            "project.shared",
            _maintenance_budget(),
            owner_id="controller.worker-one",
        )

    retried = controller.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.worker-two",
    )

    assert retried.result_code == "no_change"
    assert executor.calls == 2


def test_active_epoch_lease_prevents_competing_executor(tmp_path: Path) -> None:
    nested: list[object] = []
    second, _ = _controller(tmp_path, executor=_NoChangeExecutor())

    class _CompetingExecutor(_NoChangeExecutor):
        def advance(
            self,
            epoch: OptimizationEpoch,
            budget: MaintenanceBudget,
            *,
            authorize_effect: object,
        ) -> OptimizationStepResult:
            assert callable(authorize_effect)
            authorize_effect()
            nested.append(
                second.advance_optimization(
                    "project.shared",
                    _maintenance_budget(),
                    owner_id="controller.worker-two",
                )
            )
            return super().advance(
                epoch,
                budget,
                authorize_effect=authorize_effect,
            )

    first, _ = _controller(tmp_path, executor=_CompetingExecutor())
    _record_threshold(first)

    result = first.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.worker-one",
    )

    assert result.result_code == "no_change"
    assert len(nested) == 1
    assert nested[0].result_code == "paused"  # type: ignore[union-attr]
    assert nested[0].reason == "epoch_lease_busy"  # type: ignore[union-attr]


def test_each_maintenance_window_uses_and_releases_fresh_reservation(
    tmp_path: Path,
) -> None:
    executor = _TwoStepExecutor()
    controller, governor = _controller(tmp_path, executor=executor)
    _record_threshold(controller)

    first = controller.advance_optimization(
        "project.shared", _maintenance_budget(), owner_id="controller.worker-one"
    )
    second = controller.advance_optimization(
        "project.shared", _maintenance_budget(), owner_id="controller.worker-two"
    )

    assert first.result_code == "advanced"
    assert second.result_code == "no_change"
    assert first.epoch is not None and second.epoch is not None
    for fencing in (1, 2):
        reservation = governor.get_reservation_by_session(
            optimization_resource_session_id(first.epoch.epoch_id, fencing)
        )
        assert reservation is not None
        assert reservation.state == "released"
    assert first.epoch.reservation_id == second.epoch.reservation_id == ""


def test_new_trigger_cannot_orphan_an_active_epoch(tmp_path: Path) -> None:
    controller, _ = _controller(tmp_path, executor=_TwoStepExecutor())
    _record_threshold(controller)
    first = controller.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.worker-one",
    )
    assert first.result_code == "advanced"
    assert first.epoch is not None and first.epoch.state == "generating"

    trigger = controller._record_session_observation(_critical_observation(31))
    assert trigger.triggered
    resumed = controller.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.worker-two",
    )

    assert resumed.epoch is not None
    assert resumed.epoch.epoch_id == first.epoch.epoch_id
    assert len(controller.store.epochs()) == 1


def test_epoch_usage_accumulates_across_maintenance_windows(tmp_path: Path) -> None:
    executor = _UsageExecutor()
    controller, governor = _controller(tmp_path, executor=executor)
    executor.governor = governor
    _record_threshold(controller)

    first = controller.advance_optimization(
        "project.shared", _maintenance_budget(), owner_id="controller.worker-one"
    )
    second = controller.advance_optimization(
        "project.shared", _maintenance_budget(), owner_id="controller.worker-two"
    )

    assert first.epoch is not None and second.epoch is not None
    assert first.epoch.cumulative_usage.tokens == 1_000
    assert second.epoch.cumulative_usage.tokens == 2_000
    assert second.epoch.cumulative_usage.cost == pytest.approx(0.2)


def test_next_worker_recovers_resource_created_before_epoch_binding(
    tmp_path: Path,
) -> None:
    controller, governor = _controller(tmp_path)
    _record_threshold(controller)
    epoch = controller._resolve_epoch(controller._trigger_events()[-1])
    with controller.store.locked():
        claim = controller.store.acquire_lease(
            epoch.epoch_id,
            owner_id="controller.crashed-worker",
            lease_seconds=360,
        )
    guard = EpochLeaseGuard(controller.store, claim)
    orphan = acquire_resource_window(
        governor,
        epoch,
        claim,
        _maintenance_budget(),
        project_id="project.shared",
        policy=_policy(),
        authorize_effect=guard.authorize,
        now=None,
    )
    guard.release()

    recovered = controller.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.recovery-worker",
    )

    assert recovered.result_code == "no_change"
    assert governor.get_reservation(orphan.reservation_id).state == "released"


def test_epoch_lease_cannot_advance_until_holder_releases(tmp_path: Path) -> None:
    store = OptimizationControllerStore(
        tmp_path,
        project_id="project.shared",
        lock_timeout_seconds=1,
    )
    now = datetime(2026, 7, 22, tzinfo=UTC)
    with store.locked():
        first = store.acquire_lease(
            "epoch.one",
            owner_id="worker.one",
            now=now,
            lease_seconds=30,
        )
        with pytest.raises(OptimizationEpochLeaseBusyError, match="still active"):
            store.acquire_lease(
                "epoch.one",
                owner_id="worker.two",
                now=now,
                lease_seconds=30,
            )
        store.release_lease(first, owner_id="worker.one", now=now)
        second = store.acquire_lease(
            "epoch.one",
            owner_id="worker.two",
            now=now,
            lease_seconds=30,
        )

    assert second.fencing_epoch == first.fencing_epoch + 1
    with pytest.raises(SharedStateIntegrityError, match="fenced"):
        store.require_current_lease(first, owner_id="worker.one", now=now)


def _controller(
    root: Path,
    *,
    executor: OptimizationStepExecutor | None = None,
    foreground_requested: object | None = None,
    active_snapshot_digest: object | None = None,
    clock: object | None = None,
) -> tuple[OfflineOptimizationController, object]:
    governor = _governor(root, offline_capacity=_capacity())
    journal = ProviderInvocationJournal(
        root,
        project_id="project.shared",
        resource_governor=governor,
        lock_timeout_seconds=5,
    )
    callback = foreground_requested if callable(foreground_requested) else lambda: False
    return (
        OfflineOptimizationController(
            root,
            project_id="project.shared",
            constitution=_constitution(),
            baseline_snapshot_digest="sha256:baseline",
            epoch_budget_policy=_policy(),
            resource_governor=governor,
            provider_journal=journal,
            step_executor=executor or _NoChangeExecutor(),
            foreground_requested=callback,
            active_snapshot_digest=(
                active_snapshot_digest
                if callable(active_snapshot_digest)
                else None
            ),
            clock=clock if callable(clock) else None,
            lock_timeout_seconds=5,
        ),
        governor,
    )


def _constitution() -> OptimizationConstitution:
    return OptimizationConstitution(
        constitution_version="1.0.0",
        epoch_budget_policy_digest=_policy().policy_digest,
        attribution_policy_digest="sha256:attribution-policy",
        evaluator_registry_digest="sha256:evaluator-registry",
        auto_promotion_policy_digest="sha256:auto-promotion-policy",
        storage_policy_digest="sha256:storage-policy",
        candidate_domain_registry_digest="sha256:registry",
    )


def _maintenance_budget() -> MaintenanceBudget:
    return MaintenanceBudget(
        maximum_provider_calls=2,
        maximum_tokens=100_000,
        maximum_cost=2,
        maximum_active_wall_clock=300,
        maximum_parallelism=1,
    )


def _record_threshold(controller: OfflineOptimizationController) -> None:
    for sequence in range(1, 31):
        controller._record_session_observation(_observation(sequence))


def _observation(sequence: int) -> OptimizationSessionObservation:
    return OptimizationSessionObservation(
        observation_id=f"observation.{sequence:03d}",
        project_id="project.shared",
        session_id=f"session.{sequence:03d}",
        initial_candidate_digest=f"sha256:candidate-{sequence:03d}",
        sequence=sequence,
        observation_kind="created",
        occurred_at=datetime(2026, 7, 1, tzinfo=UTC).isoformat(),
        stage_key="implementation",
        risk_level="medium",
        candidate_size_bucket="small",
        active_snapshot_digest="sha256:baseline",
    )


def _critical_observation(sequence: int) -> OptimizationSessionObservation:
    return OptimizationSessionObservation(
        observation_id=f"critical-observation.{sequence:03d}",
        project_id="project.shared",
        session_id=f"session.critical-{sequence:03d}",
        initial_candidate_digest=f"sha256:critical-candidate-{sequence:03d}",
        sequence=sequence,
        observation_kind="blocked",
        occurred_at=datetime(2026, 7, 1, tzinfo=UTC).isoformat(),
        stage_key="implementation",
        risk_level="high",
        candidate_size_bucket="small",
        active_snapshot_digest="sha256:baseline",
        terminal_reason="late_critical_finding",
    )
