from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.unit.stage_review.test_resources import _capacity, _governor, _policy

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    create_json_exclusive,
    read_json_object,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.optimization import statistics as statistics_module
from ai_sdlc.core.stage_review.optimization.controller import (
    OfflineOptimizationController,
    OptimizationStepExecutor,
)
from ai_sdlc.core.stage_review.optimization.controller_models import (
    MaintenanceBudget,
    OptimizationConstitution,
    OptimizationEpoch,
    OptimizationStepResult,
    OptimizationTriggerEvent,
    bundled_legacy_runtime_bundle_manifests,
)
from ai_sdlc.core.stage_review.optimization.controller_store import (
    OptimizationControllerStore,
    OptimizationEpochLeaseBusyError,
)
from ai_sdlc.core.stage_review.optimization.defaults import baseline_constitution
from ai_sdlc.core.stage_review.optimization.maintenance_window import (
    EpochLeaseGuard,
)
from ai_sdlc.core.stage_review.optimization.maintenance_window import (
    _acquire_resource_window as acquire_resource_window,
)
from ai_sdlc.core.stage_review.optimization.maintenance_window import (
    _optimization_resource_session_id as optimization_resource_session_id,
)
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationStatisticsPolicy,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    OptimizationSessionObservation,
)
from ai_sdlc.core.stage_review.optimization.statistics import (
    baseline_statistics_policy,
)
from ai_sdlc.core.stage_review.provider_journal import ProviderInvocationJournal
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts

_RUNTIME_MANIFEST = "sha256:test-runtime-bundle"


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


def test_legacy_v1_trigger_and_epoch_preserve_source_digest_on_decode(
    tmp_path: Path,
) -> None:
    constitution = baseline_constitution()
    trigger = OptimizationTriggerEvent(
        trigger_id="optimization-trigger.legacy",
        project_id="project.shared",
        session_sequence_high_watermark=30,
        trigger_fingerprint="sha256:legacy-trigger-fingerprint",
        constitution_digest=constitution.constitution_digest,
        baseline_snapshot_digest="sha256:baseline",
        candidate_domain_registry_digest=(
            constitution.candidate_domain_registry_digest
        ),
        statistics_policy_digest=constitution.statistics_policy_digest,
        evaluator_registry_digest=constitution.evaluator_registry_digest,
        auto_promotion_policy_digest=constitution.auto_promotion_policy_digest,
        runtime_bundle_manifest_digest=_RUNTIME_MANIFEST,
        trigger_facts=(),
        new_session_count=30,
        triggered=True,
    )
    epoch = OptimizationEpoch(
        epoch_id="optimization-epoch.legacy",
        project_id="project.shared",
        trigger_fingerprint=trigger.trigger_fingerprint,
        trigger_digest=trigger.trigger_digest,
        constitution_digest=constitution.constitution_digest,
        baseline_snapshot_digest="sha256:baseline",
        candidate_domain_registry_digest=(
            constitution.candidate_domain_registry_digest
        ),
        statistics_policy_digest=constitution.statistics_policy_digest,
        evaluator_registry_digest=constitution.evaluator_registry_digest,
        auto_promotion_policy_digest=constitution.auto_promotion_policy_digest,
        runtime_bundle_manifest_digest=_RUNTIME_MANIFEST,
        session_sequence_high_watermark=30,
        new_session_count=30,
        state="snapshotting",
        revision=1,
    )
    legacy_trigger = _legacy_v1_payload(
        trigger,
        schema_version="optimization-trigger-event.v1",
        digest_field="trigger_digest",
    )
    legacy_epoch = _legacy_v1_payload(
        epoch,
        schema_version="optimization-epoch.v1",
        digest_field="epoch_digest",
    )

    context = {
        "optimization_constitutions": {
            constitution.constitution_digest: constitution,
        },
        "optimization_legacy_runtime_bundle_manifests": (
            bundled_legacy_runtime_bundle_manifests()
        ),
    }
    decoded_trigger = OptimizationTriggerEvent.model_validate(
        legacy_trigger, context=context
    )
    decoded_epoch = OptimizationEpoch.model_validate(legacy_epoch, context=context)
    alternate_context = {
        **context,
        "optimization_runtime_bundle_manifests": {
            constitution.constitution_digest: "sha256:new-current-runtime",
        },
    }
    assert (
        OptimizationTriggerEvent.model_validate(
            legacy_trigger,
            context=alternate_context,
        )
        == decoded_trigger
    )

    assert decoded_trigger.trigger_digest == legacy_trigger["trigger_digest"]
    assert decoded_epoch.epoch_digest == legacy_epoch["epoch_digest"]
    assert decoded_trigger.compatibility_mode == "strict"
    assert decoded_epoch.compatibility_mode == "strict"
    assert (
        decoded_trigger.extensions["legacy_source_digest"]
        == legacy_trigger["trigger_digest"]
    )
    assert (
        decoded_epoch.evaluator_registry_digest
        == constitution.evaluator_registry_digest
    )
    store = OptimizationControllerStore(
        tmp_path,
        project_id="project.shared",
        lock_timeout_seconds=1,
        runtime_bundle_manifests={
            constitution.constitution_digest: _RUNTIME_MANIFEST,
        },
    )
    assert store.accounting.persist_json_exclusive(
        store.root
        / "triggers"
        / f"{decoded_trigger.trigger_fingerprint}.json",
        legacy_trigger,
    )
    assert store.accounting.persist_json_exclusive(
        store.root
        / "epochs"
        / decoded_epoch.epoch_id
        / "00000000000000000001.json",
        legacy_epoch,
    )

    assert store.triggers() == (decoded_trigger,)
    assert store.epoch(decoded_epoch.epoch_id) == decoded_epoch
    migrated_payload = decoded_epoch.model_dump(mode="json")
    migrated_extensions = dict(decoded_epoch.extensions)
    migrated_extensions.pop("legacy_source_digest")
    migrated_extensions.pop("legacy_source_schema_version")
    migrated_extensions.pop("legacy_source_extensions")
    migrated_extensions["migrated_from_digest"] = decoded_epoch.epoch_digest
    migrated_payload.update(
        schema_version="optimization-epoch.v2",
        compatibility_mode="strict",
        extensions=migrated_extensions,
        revision=2,
        previous_epoch_digest=decoded_epoch.epoch_digest,
        epoch_digest="",
    )
    migrated = store.append_epoch(
        OptimizationEpoch.model_validate(migrated_payload)
    )

    assert migrated.schema_version == "optimization-epoch.v2"
    assert migrated.previous_epoch_digest == decoded_epoch.epoch_digest
    assert store.epoch(decoded_epoch.epoch_id) == migrated


def test_enriched_legacy_v1_requires_catalog_and_rejects_lineage_tampering() -> None:
    constitution = baseline_constitution()
    epoch = OptimizationEpoch(
        epoch_id="optimization-epoch.enriched-legacy",
        project_id="project.shared",
        trigger_fingerprint="sha256:trigger",
        trigger_digest="sha256:trigger-event",
        constitution_digest=constitution.constitution_digest,
        baseline_snapshot_digest="sha256:baseline",
        candidate_domain_registry_digest=(
            constitution.candidate_domain_registry_digest
        ),
        statistics_policy_digest=constitution.statistics_policy_digest,
        evaluator_registry_digest=constitution.evaluator_registry_digest,
        auto_promotion_policy_digest=constitution.auto_promotion_policy_digest,
        runtime_bundle_manifest_digest=_RUNTIME_MANIFEST,
        session_sequence_high_watermark=30,
        new_session_count=30,
        state="snapshotting",
        revision=1,
    )
    context = {
        "optimization_constitutions": {
            constitution.constitution_digest: constitution,
        },
        "optimization_legacy_runtime_bundle_manifests": (
            bundled_legacy_runtime_bundle_manifests()
        ),
    }
    legacy = _legacy_v1_payload(
        epoch,
        schema_version="optimization-epoch.v1",
        digest_field="epoch_digest",
    )
    enriched = OptimizationEpoch.model_validate(
        legacy,
        context=context,
    ).model_dump(mode="json")
    enriched["evaluator_registry_digest"] = "sha256:tampered-evaluator"
    enriched["runtime_bundle_manifest_digest"] = "sha256:tampered-runtime"

    with pytest.raises(
        ValueError,
        match="trusted legacy optimization context is required",
    ):
        OptimizationEpoch.model_validate(enriched)
    with pytest.raises(
        ValueError,
        match="legacy optimization policy lineage diverged",
    ):
        OptimizationEpoch.model_validate(enriched, context=context)


def test_v2_artifact_cannot_self_declare_legacy_digest_trust() -> None:
    constitution = baseline_constitution()
    epoch = OptimizationEpoch(
        epoch_id="optimization-epoch.forged-legacy-mode",
        project_id="project.shared",
        trigger_fingerprint="sha256:trigger",
        trigger_digest="sha256:trigger-event",
        constitution_digest=constitution.constitution_digest,
        baseline_snapshot_digest="sha256:baseline",
        candidate_domain_registry_digest=(
            constitution.candidate_domain_registry_digest
        ),
        statistics_policy_digest=constitution.statistics_policy_digest,
        evaluator_registry_digest=constitution.evaluator_registry_digest,
        auto_promotion_policy_digest=constitution.auto_promotion_policy_digest,
        runtime_bundle_manifest_digest=_RUNTIME_MANIFEST,
        session_sequence_high_watermark=30,
        new_session_count=30,
        state="snapshotting",
        revision=1,
    )
    forged = epoch.model_dump(mode="json")
    forged.update(
        compatibility_mode="read-only-legacy",
        extensions={"source_digest": "sha256:forged"},
        baseline_snapshot_digest="sha256:attacker-baseline",
        epoch_digest="sha256:forged",
    )

    with pytest.raises(
        ValueError,
        match="trusted legacy source verification",
    ):
        OptimizationEpoch.model_validate(forged)


def test_store_decodes_non_baseline_v1_with_injected_constitution_catalog(
    tmp_path: Path,
) -> None:
    constitution = _constitution()
    epoch = OptimizationEpoch(
        epoch_id="optimization-epoch.non-baseline-legacy",
        project_id="project.shared",
        trigger_fingerprint="sha256:trigger",
        trigger_digest="sha256:trigger-event",
        constitution_digest=constitution.constitution_digest,
        baseline_snapshot_digest="sha256:baseline",
        candidate_domain_registry_digest=(
            constitution.candidate_domain_registry_digest
        ),
        statistics_policy_digest=constitution.statistics_policy_digest,
        evaluator_registry_digest=constitution.evaluator_registry_digest,
        auto_promotion_policy_digest=constitution.auto_promotion_policy_digest,
        runtime_bundle_manifest_digest=_RUNTIME_MANIFEST,
        session_sequence_high_watermark=30,
        new_session_count=30,
        state="snapshotting",
        revision=1,
    )
    legacy = _legacy_v1_payload(
        epoch,
        schema_version="optimization-epoch.v1",
        digest_field="epoch_digest",
    )
    store = OptimizationControllerStore(
        tmp_path,
        project_id="project.shared",
        lock_timeout_seconds=1,
        constitution_bundles={
            constitution.constitution_digest: constitution,
        },
        runtime_bundle_manifests={
            baseline_constitution().constitution_digest: "sha256:baseline-runtime",
            constitution.constitution_digest: _RUNTIME_MANIFEST,
        },
        legacy_runtime_bundle_manifests={
            (
                "optimization-epoch.v1:"
                f"{constitution.constitution_digest}"
            ): "sha256:historical-runtime-bundle",
        },
    )
    assert store.accounting.persist_json_exclusive(
        store.root
        / "epochs"
        / epoch.epoch_id
        / "00000000000000000001.json",
        legacy,
    )

    decoded = store.epoch(epoch.epoch_id)

    assert decoded is not None
    assert decoded.epoch_digest == legacy["epoch_digest"]
    assert decoded.evaluator_registry_digest == constitution.evaluator_registry_digest


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


def test_runtime_upgrade_supersedes_active_epoch_and_starts_current_runtime(
    tmp_path: Path,
) -> None:
    controller, _ = _controller(tmp_path, executor=_TwoStepExecutor())
    _record_threshold(controller)
    first = controller.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.old-runtime",
    )
    assert first.epoch is not None and first.epoch.state == "generating"

    # 这个 manifest 产生的 fingerprint 小于旧 trigger，防止按哈希排序假装时序。
    upgraded_manifest = "sha256:runtime-upgrade-2"
    controller.runtime_bundle_manifests[
        controller.constitution.constitution_digest
    ] = upgraded_manifest
    resumed = controller.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.current-runtime",
    )

    epochs = controller.store.epochs()
    superseded = next(item for item in epochs if item.epoch_id == first.epoch.epoch_id)
    assert superseded.state == "superseded_runtime_upgrade"
    assert superseded.terminal_at
    assert not superseded.reservation_id
    assert resumed.epoch is not None
    assert resumed.epoch.epoch_id != first.epoch.epoch_id
    assert resumed.epoch.runtime_bundle_manifest_digest == upgraded_manifest
    assert resumed.epoch.state == "generating"
    current_trigger = next(
        item
        for item in controller.store.triggers()
        if item.runtime_bundle_manifest_digest == upgraded_manifest
    )
    old_trigger = next(
        item
        for item in controller.store.triggers()
        if item.runtime_bundle_manifest_digest == _RUNTIME_MANIFEST
    )
    assert current_trigger.trigger_fingerprint < old_trigger.trigger_fingerprint

    second_manifest = "sha256:runtime-upgrade-3"
    controller.runtime_bundle_manifests[
        controller.constitution.constitution_digest
    ] = second_manifest
    second_upgrade = controller.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.second-upgrade",
    )
    assert second_upgrade.epoch is not None
    assert second_upgrade.epoch.runtime_bundle_manifest_digest == second_manifest

    restarted, _ = _controller(
        tmp_path,
        executor=_TwoStepExecutor(),
        runtime_manifest=second_manifest,
    )
    after_restart = restarted.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.after-restart",
    )
    assert after_restart.epoch is not None
    assert after_restart.epoch.epoch_id == second_upgrade.epoch.epoch_id
    assert after_restart.epoch.runtime_bundle_manifest_digest == second_manifest


def test_runtime_upgrade_terminal_epoch_preserves_cooldown_order_after_restart(
    tmp_path: Path,
) -> None:
    controller, _ = _controller(tmp_path, executor=_TwoStepExecutor())
    _record_threshold(controller)
    old = controller.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.old-runtime",
    )
    assert old.epoch is not None and old.epoch.state == "generating"

    current_manifest = "sha256:runtime-19"
    controller.runtime_bundle_manifests[
        controller.constitution.constitution_digest
    ] = current_manifest
    current = controller.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.current-runtime",
    )
    terminal = controller.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.current-runtime-terminal",
    )

    assert current.epoch is not None
    assert terminal.epoch is not None and terminal.epoch.state == "no_change"
    old_trigger = next(
        item
        for item in controller.store.triggers()
        if item.runtime_bundle_manifest_digest == _RUNTIME_MANIFEST
    )
    current_trigger = next(
        item
        for item in controller.store.triggers()
        if item.runtime_bundle_manifest_digest == current_manifest
    )
    assert current_trigger.trigger_fingerprint < old_trigger.trigger_fingerprint
    assert not controller._record_session_observation(_observation(31)).triggered

    restarted, _ = _controller(
        tmp_path,
        executor=_TwoStepExecutor(),
        runtime_manifest=current_manifest,
    )
    assert not restarted.refresh_trigger().triggered


def test_legacy_trigger_migration_preserves_runtime_upgrade_cooldown(
    tmp_path: Path,
) -> None:
    observed_time = ["2026-07-26T00:00:00+00:00"]
    controller, _ = _controller(
        tmp_path,
        executor=_TwoStepExecutor(),
        clock=lambda: observed_time[-1],
    )
    _record_threshold(controller)
    old = controller.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.old-runtime",
    )
    assert old.epoch is not None and old.epoch.state == "generating"

    current_manifest = "sha256:runtime-19"
    observed_time.append("2026-07-25T00:00:00+00:00")
    controller.runtime_bundle_manifests[
        controller.constitution.constitution_digest
    ] = current_manifest
    current = controller.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.current-runtime",
    )
    terminal = controller.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.current-runtime-terminal",
    )
    assert current.epoch is not None
    assert terminal.epoch is not None and terminal.epoch.state == "no_change"

    order_root = controller.store.root / "trigger-order"
    legacy_root = controller.store.root / "triggers"
    for path in sorted(order_root.glob("*.json")):
        payload = read_json_object(path)
        event = OptimizationTriggerEvent.model_validate(payload["event"])
        assert create_json_exclusive(
            legacy_root / f"{event.trigger_fingerprint}.json",
            event.model_dump(mode="json"),
        )
        path.unlink()
    order_root.rmdir()

    restarted, _ = _controller(
        tmp_path,
        executor=_TwoStepExecutor(),
        runtime_manifest=current_manifest,
    )
    assert not restarted.refresh_trigger().triggered
    migration = read_json_object(
        restarted.store.root / "legacy-trigger-order.json"
    )
    ordered = tuple(migration["ordered_trigger_digests"])
    epochs = restarted.store.epochs()
    assert ordered.index(epochs[-2].trigger_digest) < ordered.index(
        epochs[-1].trigger_digest
    )

    restarted_again, _ = _controller(
        tmp_path,
        executor=_TwoStepExecutor(),
        runtime_manifest=current_manifest,
    )
    assert not restarted_again.refresh_trigger().triggered
    assert read_json_object(
        restarted_again.store.root / "legacy-trigger-order.json"
    ) == migration


def test_ambiguous_legacy_trigger_order_fails_closed_without_new_state(
    tmp_path: Path,
) -> None:
    controller, _ = _controller(tmp_path, executor=_TwoStepExecutor())
    _record_threshold(controller)
    order_root = controller.store.root / "trigger-order"
    legacy_root = controller.store.root / "triggers"
    ordered_path = next(order_root.glob("*.json"))
    payload = read_json_object(ordered_path)
    original = OptimizationTriggerEvent.model_validate(payload["event"])
    divergent = original.model_copy(
        update={
            "trigger_id": "optimization-trigger.ambiguous-runtime",
            "trigger_fingerprint": "sha256:ambiguous-runtime",
            "runtime_bundle_manifest_digest": "sha256:ambiguous-runtime",
            "trigger_digest": "",
        }
    )
    assert create_json_exclusive(
        legacy_root / f"{original.trigger_fingerprint}.json",
        original.model_dump(mode="json"),
    )
    assert create_json_exclusive(
        legacy_root / f"{divergent.trigger_fingerprint}.json",
        divergent.model_dump(mode="json"),
    )
    ordered_path.unlink()
    order_root.rmdir()

    with pytest.raises(
        SharedStateIntegrityError,
        match="trigger order is ambiguous",
    ):
        controller.refresh_trigger()

    assert not (controller.store.root / "legacy-trigger-order.json").exists()
    assert not (controller.store.root / "epochs").exists()
    assert not (controller.store.root / "trigger-order").exists()


def test_legacy_trigger_migration_groups_control_equivalent_superseded_history(
    tmp_path: Path,
) -> None:
    observed_time = ["2026-07-27T00:00:00+00:00"]
    controller, _ = _controller(
        tmp_path,
        executor=_TwoStepExecutor(),
        clock=lambda: observed_time[-1],
    )
    _record_threshold(controller)
    old = controller.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.old-runtime",
    )
    assert old.epoch is not None

    observed_time.append("2026-07-25T00:00:00+00:00")
    middle_manifest = "sha256:runtime-middle"
    controller.runtime_bundle_manifests[
        controller.constitution.constitution_digest
    ] = middle_manifest
    middle = controller.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.middle-runtime",
    )
    assert middle.epoch is not None

    observed_time.append("2026-07-26T00:00:00+00:00")
    current_manifest = "sha256:runtime-current"
    controller.runtime_bundle_manifests[
        controller.constitution.constitution_digest
    ] = current_manifest
    current = controller.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.current-runtime",
    )
    terminal = controller.advance_optimization(
        "project.shared",
        _maintenance_budget(),
        owner_id="controller.current-terminal",
    )
    assert current.epoch is not None
    assert terminal.epoch is not None and terminal.epoch.state == "no_change"

    order_root = controller.store.root / "trigger-order"
    legacy_root = controller.store.root / "triggers"
    for path in sorted(order_root.glob("*.json")):
        payload = read_json_object(path)
        event = OptimizationTriggerEvent.model_validate(payload["event"])
        assert create_json_exclusive(
            legacy_root / f"{event.trigger_fingerprint}.json",
            event.model_dump(mode="json"),
        )
        path.unlink()
    order_root.rmdir()

    restarted, _ = _controller(
        tmp_path,
        executor=_TwoStepExecutor(),
        runtime_manifest=current_manifest,
    )
    assert not restarted.refresh_trigger().triggered
    migration = read_json_object(
        restarted.store.root / "legacy-trigger-order.json"
    )
    groups = tuple(
        tuple(group) for group in migration["ordered_trigger_groups"]
    )
    assert len(groups) == 2
    assert len(groups[0]) == 2
    assert groups[1] == (terminal.epoch.trigger_digest,)

    restarted_again, _ = _controller(
        tmp_path,
        executor=_TwoStepExecutor(),
        runtime_manifest=current_manifest,
    )
    assert not restarted_again.refresh_trigger().triggered
    assert read_json_object(
        restarted_again.store.root / "legacy-trigger-order.json"
    ) == migration


def test_epoch_copies_versioned_policy_lineage_from_its_trigger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _ = _controller(tmp_path)
    _record_threshold(controller)
    historical = OptimizationStatisticsPolicy(
        policy_id="statistics.optimization-controller-test",
        policy_version="0.9.0",
    )
    monkeypatch.setattr(
        statistics_module,
        "bundled_statistics_policies",
        lambda: (historical, baseline_statistics_policy()),
    )
    historical_constitution = OptimizationConstitution.model_validate(
        {
            **controller.constitution.model_dump(
                mode="json",
                exclude={"constitution_digest"},
            ),
            "candidate_domain_registry_digest": "sha256:historical-registry",
            "statistics_policy_digest": historical.policy_digest,
            "familywise_alpha": historical.familywise_alpha,
        }
    )
    historical_manifest = "sha256:historical-runtime-bundle"
    controller.constitutions[
        historical_constitution.constitution_digest
    ] = historical_constitution
    controller.runtime_bundle_manifests[
        historical_constitution.constitution_digest
    ] = historical_manifest
    trigger = controller._trigger_events()[-1].model_copy(
        update={
            "constitution_digest": historical_constitution.constitution_digest,
            "candidate_domain_registry_digest": "sha256:historical-registry",
            "statistics_policy_digest": historical.policy_digest,
            "runtime_bundle_manifest_digest": historical_manifest,
            "trigger_digest": "",
        }
    )

    epoch = controller._resolve_epoch(trigger)

    assert epoch.constitution_digest == trigger.constitution_digest
    assert (
        epoch.candidate_domain_registry_digest
        == trigger.candidate_domain_registry_digest
    )
    assert epoch.statistics_policy_digest == trigger.statistics_policy_digest
    assert epoch.evaluator_registry_digest == trigger.evaluator_registry_digest
    assert (
        epoch.auto_promotion_policy_digest
        == trigger.auto_promotion_policy_digest
    )


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
    runtime_manifest: str = _RUNTIME_MANIFEST,
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
            runtime_bundle_manifests={
                _constitution().constitution_digest: runtime_manifest,
            },
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


def _legacy_v1_payload(
    artifact: OptimizationTriggerEvent | OptimizationEpoch,
    *,
    schema_version: str,
    digest_field: str,
) -> dict[str, object]:
    payload = artifact.model_dump(mode="json")
    for field_name in (
        "statistics_policy_digest",
        "evaluator_registry_digest",
        "auto_promotion_policy_digest",
        "runtime_bundle_manifest_digest",
    ):
        payload.pop(field_name)
    payload["schema_version"] = schema_version
    payload.pop(digest_field)
    payload[digest_field] = canonical_digest(
        payload,
        CanonicalizationPolicy(),
    )
    return payload


def _constitution() -> OptimizationConstitution:
    return OptimizationConstitution(
        constitution_version="1.0.0",
        epoch_budget_policy_digest=_policy().policy_digest,
        attribution_policy_digest="sha256:attribution-policy",
        evaluator_registry_digest="sha256:evaluator-registry",
        auto_promotion_policy_digest="sha256:auto-promotion-policy",
        storage_policy_digest="sha256:storage-policy",
        candidate_domain_registry_digest="sha256:registry",
        statistics_policy_digest=baseline_statistics_policy().policy_digest,
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
