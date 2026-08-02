from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization import (
    controller_models as controller_models_module,
)
from ai_sdlc.core.stage_review.optimization import (
    local_holdout as local_holdout_module,
)
from ai_sdlc.core.stage_review.optimization import statistics as statistics_module
from ai_sdlc.core.stage_review.optimization.controller_models import (
    MaintenanceBudget,
    OptimizationConstitution,
    OptimizationEpoch,
)
from ai_sdlc.core.stage_review.optimization.evaluators import (
    EvaluationContext,
    EvaluatorContract,
    OptimizationEvaluatorRegistry,
    component_runtime_digest,
    fixed_holdout_evaluator_contract,
)
from ai_sdlc.core.stage_review.optimization.holdout_store import (
    HoldoutCommitmentStore,
)
from ai_sdlc.core.stage_review.optimization.local_holdout import (
    LocalHoldoutEvaluationPort,
)
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationEvaluationReport,
    OptimizationPatchOperation,
    OptimizationStatisticalSample,
    OptimizationStatisticsPolicy,
)
from ai_sdlc.core.stage_review.optimization.pipeline import (
    OptimizationPipelineExecutor,
    OptimizationRuntimeBundle,
    _select_finalist,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    CandidateGenerationResult,
    PipelineHoldoutResult,
    PipelinePromotionAuthorization,
    PipelinePromotionPackage,
    PipelinePublicationResult,
    PipelineReplayResult,
    PipelineShadowResult,
    PipelineSnapshotResult,
    ShadowComparisonMetrics,
)
from ai_sdlc.core.stage_review.optimization.pipeline_effects import (
    EpochRuntimeAuthorizer,
    allow_effect,
)
from ai_sdlc.core.stage_review.optimization.promotion import (
    AutoPromotionDecision,
    AutoPromotionEvidence,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import (
    OptimizationSnapshot,
)
from ai_sdlc.core.stage_review.optimization.statistics import (
    _apply_holm_bonferroni as apply_holm_bonferroni,
)
from ai_sdlc.core.stage_review.optimization.statistics import (
    _binary_improvement_statistics as binary_improvement_statistics,
)
from ai_sdlc.core.stage_review.optimization.statistics import (
    baseline_statistics_policy,
    resolve_statistics_policy,
)


def test_epoch_runtime_commit_linearizes_validation_before_publish() -> None:
    events: list[str] = []

    class LeaseAuthorizer:
        epoch_fencing_epoch = 1
        epoch_claim_digest = "sha256:claim"

        def __call__(self) -> None:
            events.append("lease-check")

        def commit(self, operation: Callable[[], object]) -> object:
            events.append("lease-commit")
            return operation()

    epoch = OptimizationEpoch(
        epoch_id="optimization-epoch.authority-test",
        project_id="project.shared",
        trigger_fingerprint="sha256:trigger",
        trigger_digest="sha256:trigger-event",
        constitution_digest="sha256:constitution",
        baseline_snapshot_digest="sha256:baseline",
        candidate_domain_registry_digest="sha256:registry",
        statistics_policy_digest="sha256:statistics",
        evaluator_registry_digest="sha256:evaluators",
        auto_promotion_policy_digest="sha256:promotion",
        runtime_bundle_manifest_digest="sha256:runtime",
        session_sequence_high_watermark=30,
        new_session_count=30,
        state="shadow_observing",
        revision=1,
    )
    authorizer = EpochRuntimeAuthorizer.for_epoch(
        LeaseAuthorizer(),
        lambda: events.append("runtime-check"),
        epoch,
    )

    result = authorizer.commit(lambda: events.append("publish") or "published")

    assert result == "published"
    assert events == ["lease-commit", "runtime-check", "publish"]


def test_fixed_pipeline_advances_without_candidate_domain_branches(
    tmp_path: Path,
) -> None:
    registry = OptimizationEvaluatorRegistry(
        statistics_authority=_StatisticsAuthority()
    )
    adapter = _EvaluatorAdapter()
    registry.register(_evaluator_contract("custom-evaluator"), adapter)
    registry.register_contract(
        fixed_holdout_evaluator_contract(("budget", "selection"))
    )
    constitution = _test_constitution(registry)
    publication = _PublicationPort()
    executor = OptimizationPipelineExecutor(
        tmp_path,
        project_id="project.shared",
        minimum_evaluable_sessions=20,
        candidate_family_limit=8,
        evaluator_registry=registry,
        evaluator_registry_digest=registry.registry_digest,
        configured_constitution=constitution,
        replay_evaluator_kinds=("custom-evaluator",),
        dataset_port=_DatasetPort(evaluable=20),
        candidate_port=_CandidatePort((_candidate("selection"),)),
        holdout_port=_HoldoutPort(),
        shadow_port=_ShadowPort(),
        promotion_port=_PromotionPort(),
        promotion_policy_digest="sha256:promotion-policy",
        publication_port=publication,
        promotion_authority=_PromotionAuthority(),
    )
    epoch = _epoch(executor)
    visited: list[str] = []

    for _ in range(7):
        visited.append(epoch.state)
        result = executor.advance(
            epoch, MaintenanceBudget(), authorize_effect=allow_effect
        )
        epoch = epoch.model_copy(
            update={
                "state": result.next_state,
                "dataset_digest": result.dataset_digest or epoch.dataset_digest,
                "finalist_candidate_digest": result.finalist_candidate_digest
                or epoch.finalist_candidate_digest,
            }
        )

    assert visited == [
        "snapshotting",
        "generating",
        "replaying",
        "holdout_evaluating",
        "shadow_observing",
        "evaluating",
        "promoting",
    ]
    assert epoch.state == "promoted"
    assert adapter.calls == 1
    assert publication.calls == 1


def test_pipeline_stops_when_evaluable_baseline_is_not_met(tmp_path: Path) -> None:
    executor = _executor(tmp_path, dataset_port=_DatasetPort(evaluable=19))

    result = executor.advance(
        _epoch(executor), MaintenanceBudget(), authorize_effect=allow_effect
    )

    assert result.next_state == "no_change"
    assert result.reason == "minimum_evaluable_sessions_not_met"


def test_runtime_manifest_binds_instance_configuration(tmp_path: Path) -> None:
    twenty = _executor(
        tmp_path / "twenty",
        dataset_port=_DatasetPort(evaluable=20),
    )
    nineteen = _executor(
        tmp_path / "nineteen",
        dataset_port=_DatasetPort(evaluable=19),
    )

    assert (
        next(iter(twenty.runtime_bundles.values())).manifest_digest
        != next(iter(nineteen.runtime_bundles.values())).manifest_digest
    )


def test_pipeline_rejects_epoch_with_another_runtime_manifest(
    tmp_path: Path,
) -> None:
    executor = _executor(tmp_path)
    epoch = _epoch(executor).model_copy(
        update={"runtime_bundle_manifest_digest": "sha256:another-runtime"}
    )

    with pytest.raises(
        SharedStateIntegrityError,
        match="runtime bundle manifest diverged",
    ):
        executor.advance(
            epoch,
            MaintenanceBudget(),
            authorize_effect=allow_effect,
        )


def test_pipeline_rechecks_mutated_port_configuration_before_effect(
    tmp_path: Path,
) -> None:
    dataset = _DatasetPort(evaluable=20)
    executor = _executor(tmp_path, dataset_port=dataset)
    epoch = _epoch(executor)
    dataset.evaluable = 19

    with pytest.raises(
        SharedStateIntegrityError,
        match="runtime bundle manifest diverged",
    ):
        executor.advance(
            epoch,
            MaintenanceBudget(),
            authorize_effect=allow_effect,
        )


def test_pipeline_rechecks_runtime_after_port_call_before_stage_write(
    tmp_path: Path,
) -> None:
    dataset = _DriftingDatasetPort(evaluable=20)
    executor = _executor(tmp_path, dataset_port=dataset)
    epoch = _epoch(executor)

    with pytest.raises(
        SharedStateIntegrityError,
        match="runtime bundle manifest diverged",
    ):
        executor.advance(
            epoch,
            MaintenanceBudget(),
            authorize_effect=allow_effect,
        )

    assert (
        executor.store.read(
            epoch.epoch_id,
            "snapshotting",
            PipelineSnapshotResult,
        )
        is None
    )


def test_runtime_identity_binds_transitive_executor_type() -> None:
    assert component_runtime_digest(
        _PortWithExecutor(_ExecutorOne())
    ) != component_runtime_digest(_PortWithExecutor(_ExecutorTwo()))


def test_holdout_runtime_identity_binds_live_partition_report_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = LocalHoldoutEvaluationPort(
        store=HoldoutCommitmentStore(
            tmp_path,
            project_id="project.shared",
            familywise_alpha=0.05,
        ),
        dataset_source=lambda _: None,  # type: ignore[arg-type]
        attribution_source=lambda: (),
    )
    original = component_runtime_digest(port)

    monkeypatch.setattr(
        local_holdout_module,
        "build_partition_report",
        lambda *args, **kwargs: None,
    )

    assert component_runtime_digest(port) != original


def test_pipeline_resumes_epoch_with_its_registered_statistics_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = baseline_statistics_policy()
    historical = OptimizationStatisticsPolicy(
        policy_id="statistics.optimization-historical-test",
        policy_version="0.9.0",
        null_improvement_rate=0.4,
        minimum_detectable_effect=0.1,
        minimum_statistical_power=0.1,
        familywise_alpha=0.01,
        shadow_alpha=0.01,
    )
    monkeypatch.setattr(
        statistics_module,
        "bundled_statistics_policies",
        lambda: (historical, current),
    )
    executor = _executor(tmp_path)
    historical_constitution = _constitution_with_statistics_policy(
        executor.constitution,
        historical,
    )
    executor.constitution = historical_constitution
    current_bundle = next(iter(executor.runtime_bundles.values()))
    executor.runtime_bundles = {
        historical_constitution.constitution_digest: replace(
            current_bundle,
            constitution=historical_constitution,
            statistics_policy=historical,
        )
    }
    epoch = _epoch(executor).model_copy(
        update={
            "constitution_digest": historical_constitution.constitution_digest,
            "statistics_policy_digest": historical.policy_digest,
        }
    )

    visited = []
    for _ in range(7):
        visited.append(epoch.state)
        result = executor.advance(
            epoch,
            MaintenanceBudget(),
            authorize_effect=allow_effect,
        )
        epoch = epoch.model_copy(
            update={
                "state": result.next_state,
                "dataset_digest": result.dataset_digest or epoch.dataset_digest,
                "finalist_candidate_digest": (
                    result.finalist_candidate_digest or epoch.finalist_candidate_digest
                ),
            }
        )

    replay = executor.store.read(
        epoch.epoch_id,
        "replaying",
        PipelineReplayResult,
    )
    assert visited == [
        "snapshotting",
        "generating",
        "replaying",
        "holdout_evaluating",
        "shadow_observing",
        "evaluating",
        "promoting",
    ]
    assert epoch.state == "promoted"
    assert replay is not None
    assert replay.reports[0].statistics_policy_digest == historical.policy_digest
    assert replay.reports[0].holm_threshold == historical.familywise_alpha


def test_pipeline_rejects_epoch_without_statistics_policy_lineage(
    tmp_path: Path,
) -> None:
    executor = _executor(tmp_path)
    epoch = _epoch(executor).model_copy(update={"statistics_policy_digest": ""})

    with pytest.raises(
        ValueError,
        match="statistics policy digest is required",
    ):
        executor.advance(
            epoch,
            MaintenanceBudget(),
            authorize_effect=allow_effect,
        )


def test_pipeline_rejects_cached_replay_from_another_statistics_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = baseline_statistics_policy()
    historical = OptimizationStatisticsPolicy(
        policy_id="statistics.cached-replay-historical-test",
        policy_version="0.9.0",
        familywise_alpha=0.01,
        shadow_alpha=0.01,
    )
    monkeypatch.setattr(
        statistics_module,
        "bundled_statistics_policies",
        lambda: (historical, current),
    )
    executor = _executor(tmp_path)
    historical_constitution = _constitution_with_statistics_policy(
        executor.constitution,
        historical,
    )
    executor.constitution = historical_constitution
    current_bundle = next(iter(executor.runtime_bundles.values()))
    executor.runtime_bundles = {
        historical_constitution.constitution_digest: replace(
            current_bundle,
            constitution=historical_constitution,
            statistics_policy=historical,
        )
    }
    candidate = _candidate("selection")
    epoch = _epoch(executor).model_copy(
        update={
            "state": "replaying",
            "dataset_digest": "sha256:cached-replay-dataset",
            "constitution_digest": historical_constitution.constitution_digest,
            "statistics_policy_digest": historical.policy_digest,
        }
    )
    executor.store.write(
        epoch.epoch_id,
        "generating",
        CandidateGenerationResult(candidates=(candidate,)),
    )
    current_report = _report(
        candidate,
        EvaluationContext(
            dataset_digest=epoch.dataset_digest,
            partition="validation",
            evaluation_binding_id="evaluation-binding.custom-evaluator",
            evaluation_provider_id="provider.local-evaluator",
            provider_capabilities=("local-read-only", "read-only"),
            resource_reservation_digest="sha256:reservation",
            statistics_policy_digest=current.policy_digest,
            statistical_alpha=current.familywise_alpha,
        ),
        "custom-evaluator",
    )
    adjusted = apply_holm_bonferroni(
        (current_report,),
        familywise_alpha=current.familywise_alpha,
    )
    executor.store.write(
        epoch.epoch_id,
        "replaying",
        PipelineReplayResult(
            reports=adjusted,
            finalist_candidate_digest=candidate.candidate_digest,
        ),
    )

    with pytest.raises(
        SharedStateIntegrityError,
        match="replay statistics policy lineage diverged",
    ):
        executor.advance(
            epoch,
            MaintenanceBudget(),
            authorize_effect=allow_effect,
        )


def test_pipeline_rejects_cached_replay_from_another_dataset(
    tmp_path: Path,
) -> None:
    executor = _executor(tmp_path)
    candidate = _candidate("selection")
    epoch = _epoch(executor).model_copy(
        update={
            "state": "replaying",
            "dataset_digest": "sha256:target-dataset",
        }
    )
    executor.store.write(
        epoch.epoch_id,
        "generating",
        CandidateGenerationResult(candidates=(candidate,)),
    )
    policy = baseline_statistics_policy()
    foreign = executor._evaluator_registry(epoch).evaluate(
        evaluator_kind="custom-evaluator",
        candidate=candidate,
        context=EvaluationContext(
            dataset_digest="sha256:foreign-dataset",
            partition="validation",
            evaluation_binding_id="evaluation-binding.custom-evaluator",
            evaluation_provider_id="provider.local-evaluator",
            provider_capabilities=("local-read-only", "read-only"),
            resource_reservation_digest=epoch.reservation_id,
            statistics_policy_digest=policy.policy_digest,
            statistical_alpha=policy.familywise_alpha,
        ),
    )
    adjusted = apply_holm_bonferroni(
        (foreign,),
        familywise_alpha=policy.familywise_alpha,
    )
    executor.store.write(
        epoch.epoch_id,
        "replaying",
        PipelineReplayResult(
            reports=adjusted,
            finalist_candidate_digest=candidate.candidate_digest,
        ),
    )

    with pytest.raises(
        SharedStateIntegrityError,
        match="replay evidence lineage diverged",
    ):
        executor.advance(
            epoch,
            MaintenanceBudget(),
            authorize_effect=allow_effect,
        )


def test_pipeline_rejects_holdout_and_shadow_with_wrong_alpha(
    tmp_path: Path,
) -> None:
    holdout_executor = _executor(tmp_path / "holdout")
    _replace_current_runtime(
        holdout_executor,
        holdout_port=_WrongAlphaHoldoutPort(),
    )
    holdout_epoch = _advance_to_state(
        holdout_executor,
        _epoch(holdout_executor),
        "holdout_evaluating",
    )

    with pytest.raises(
        SharedStateIntegrityError,
        match="holdout evidence lineage diverged",
    ):
        holdout_executor.advance(
            holdout_epoch,
            MaintenanceBudget(),
            authorize_effect=allow_effect,
        )

    shadow_executor = _executor(tmp_path / "shadow")
    _replace_current_runtime(
        shadow_executor,
        shadow_port=_WrongAlphaShadowPort(),
    )
    shadow_epoch = _advance_to_state(
        shadow_executor,
        _epoch(shadow_executor),
        "shadow_observing",
    )

    with pytest.raises(
        SharedStateIntegrityError,
        match="shadow statistical design lineage diverged",
    ):
        shadow_executor.advance(
            shadow_epoch,
            MaintenanceBudget(),
            authorize_effect=allow_effect,
        )


def test_pipeline_rejects_cached_holdout_from_foreign_lineage(
    tmp_path: Path,
) -> None:
    executor = _executor(tmp_path)
    epoch = _advance_to_state(executor, _epoch(executor), "holdout_evaluating")
    report = _HoldoutPort().evaluate(
        epoch,
        executor._finalist(epoch),
        allow_effect,
    )
    foreign = OptimizationEvaluationReport.model_validate(
        {
            **report.model_dump(mode="json", exclude={"report_digest"}),
            "candidate_digest": "sha256:foreign-finalist",
            "dataset_digest": "sha256:foreign-holdout",
            "evaluator_kind": "rogue-holdout",
            "evaluator_contract_digest": "sha256:rogue-holdout-contract",
            "evaluation_binding_id": "evaluation-binding.rogue-holdout",
        }
    )
    executor.store.write(
        epoch.epoch_id,
        "holdout_evaluating",
        PipelineHoldoutResult(report=foreign),
    )

    with pytest.raises(
        SharedStateIntegrityError,
        match="holdout evidence lineage diverged",
    ):
        executor.advance(
            epoch,
            MaintenanceBudget(),
            authorize_effect=allow_effect,
        )


def test_pipeline_rejects_cached_promotion_decision_for_other_shadow(
    tmp_path: Path,
) -> None:
    executor = _executor(tmp_path, shadow_port=_AdverseShadowPort())
    epoch = _advance_to_state(executor, _epoch(executor), "evaluating")
    package = _PromotionPort().evaluate(
        epoch,
        executor._finalist(epoch),
        executor._reports(epoch),
        _complete_shadow(),
    )
    executor.store.write(epoch.epoch_id, "evaluating", package)

    with pytest.raises(
        SharedStateIntegrityError,
        match="promotion package lineage diverged",
    ):
        executor.advance(
            epoch,
            MaintenanceBudget(),
            authorize_effect=allow_effect,
        )


def test_pipeline_rejects_unknown_epoch_constitution_before_effect(
    tmp_path: Path,
) -> None:
    dataset = _CountingDatasetPort()
    executor = _executor(tmp_path, dataset_port=dataset)
    epoch = _epoch(executor).model_copy(
        update={"constitution_digest": "sha256:unknown-constitution"}
    )

    with pytest.raises(
        SharedStateIntegrityError,
        match="optimization constitution is unavailable",
    ):
        executor.advance(
            epoch,
            MaintenanceBudget(),
            authorize_effect=allow_effect,
        )

    assert dataset.calls == 0


def test_pipeline_rejects_unavailable_historical_component_before_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _CountingDatasetPort()
    executor = _executor(tmp_path, dataset_port=dataset)
    historical = OptimizationConstitution.model_validate(
        {
            **executor.constitution.model_dump(
                mode="json",
                exclude={"constitution_digest"},
            ),
            "evaluator_registry_digest": "sha256:unavailable-registry",
        }
    )
    monkeypatch.setattr(
        controller_models_module,
        "bundled_optimization_constitutions",
        lambda: (historical, executor.constitution),
    )
    epoch = _epoch(executor).model_copy(
        update={
            "constitution_digest": historical.constitution_digest,
            "evaluator_registry_digest": historical.evaluator_registry_digest,
        }
    )

    with pytest.raises(
        SharedStateIntegrityError,
        match="optimization runtime bundle is unavailable",
    ):
        executor.advance(
            epoch,
            MaintenanceBudget(),
            authorize_effect=allow_effect,
        )

    assert dataset.calls == 0


def test_pipeline_resumes_historical_epoch_with_versioned_component_bundles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    historical_registry = _test_registry(evaluator_version="1.0.0")
    current_registry = _test_registry(evaluator_version="2.0.0")
    historical_constitution = _test_constitution(historical_registry)
    current_constitution = _test_constitution(
        current_registry,
        promotion_policy_digest="sha256:promotion-policy-v2",
    )
    monkeypatch.setattr(
        controller_models_module,
        "bundled_optimization_constitutions",
        lambda: (historical_constitution, current_constitution),
    )
    historical_holdout = _HoldoutPort()
    historical_promotion = _PromotionPort()
    historical_bundle = OptimizationRuntimeBundle(
        constitution=historical_constitution,
        maintenance_budget_limit=MaintenanceBudget(),
        evaluator_registry=historical_registry,
        replay_evaluator_kinds=("custom-evaluator",),
        dataset_port=_DatasetPort(evaluable=20),
        candidate_port=_CandidatePort((_candidate("selection"),)),
        holdout_port=historical_holdout,
        shadow_port=_ShadowPort(),
        promotion_port=historical_promotion,
        publication_port=_PublicationPort(),
        domain_registry_digest="sha256:registry",
        statistics_policy=baseline_statistics_policy(),
    )
    executor = OptimizationPipelineExecutor(
        tmp_path,
        project_id="project.shared",
        minimum_evaluable_sessions=20,
        candidate_family_limit=8,
        evaluator_registry=current_registry,
        evaluator_registry_digest=current_registry.registry_digest,
        configured_constitution=current_constitution,
        replay_evaluator_kinds=("custom-evaluator",),
        dataset_port=_DatasetPort(evaluable=20),
        candidate_port=_CandidatePort((_candidate("selection"),)),
        holdout_port=_HoldoutPort(),
        shadow_port=_ShadowPort(),
        promotion_port=_PromotionPort("sha256:promotion-policy-v2"),
        promotion_policy_digest="sha256:promotion-policy-v2",
        publication_port=_PublicationPort(),
        promotion_authority=_PromotionAuthority(),
        runtime_bundles={
            historical_constitution.constitution_digest: historical_bundle,
        },
    )
    epoch = _epoch(executor).model_copy(
        update={
            "constitution_digest": historical_constitution.constitution_digest,
            "evaluator_registry_digest": (
                historical_constitution.evaluator_registry_digest
            ),
            "auto_promotion_policy_digest": (
                historical_constitution.auto_promotion_policy_digest
            ),
            "runtime_bundle_manifest_digest": historical_bundle.manifest_digest,
        }
    )

    epoch = _advance_to_state(executor, epoch, "promoted")

    replay = executor._required(epoch, "replaying", PipelineReplayResult)
    package = executor._required(
        epoch,
        "evaluating",
        PipelinePromotionPackage,
    )
    assert replay.reports[0].evaluator_version == "1.0.0"
    assert (
        package.decision.policy_digest
        == historical_constitution.auto_promotion_policy_digest
    )


def test_historical_runtime_bundle_controls_execution_thresholds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    historical_registry = _test_registry(evaluator_version="1.0.0")
    current_registry = _test_registry(evaluator_version="2.0.0")
    historical_constitution = _test_constitution(
        historical_registry,
        minimum_evaluable_sessions=25,
    )
    current_constitution = _test_constitution(
        current_registry,
        promotion_policy_digest="sha256:promotion-policy-v2",
        minimum_evaluable_sessions=10,
    )
    monkeypatch.setattr(
        controller_models_module,
        "bundled_optimization_constitutions",
        lambda: (historical_constitution, current_constitution),
    )
    historical_bundle = OptimizationRuntimeBundle(
        constitution=historical_constitution,
        maintenance_budget_limit=MaintenanceBudget(),
        evaluator_registry=historical_registry,
        replay_evaluator_kinds=("custom-evaluator",),
        dataset_port=_DatasetPort(evaluable=20),
        candidate_port=_CandidatePort((_candidate("selection"),)),
        holdout_port=_HoldoutPort(),
        shadow_port=_ShadowPort(),
        promotion_port=_PromotionPort(),
        publication_port=_PublicationPort(),
        domain_registry_digest="sha256:registry",
        statistics_policy=baseline_statistics_policy(),
    )
    executor = OptimizationPipelineExecutor(
        tmp_path,
        project_id="project.shared",
        minimum_evaluable_sessions=10,
        candidate_family_limit=8,
        evaluator_registry=current_registry,
        evaluator_registry_digest=current_registry.registry_digest,
        configured_constitution=current_constitution,
        replay_evaluator_kinds=("custom-evaluator",),
        dataset_port=_DatasetPort(evaluable=20),
        candidate_port=_CandidatePort((_candidate("selection"),)),
        holdout_port=_HoldoutPort(),
        shadow_port=_ShadowPort(),
        promotion_port=_PromotionPort("sha256:promotion-policy-v2"),
        promotion_policy_digest="sha256:promotion-policy-v2",
        publication_port=_PublicationPort(),
        promotion_authority=_PromotionAuthority(),
        runtime_bundles={
            historical_constitution.constitution_digest: historical_bundle,
        },
    )
    epoch = _epoch(executor).model_copy(
        update={
            "constitution_digest": historical_constitution.constitution_digest,
            "evaluator_registry_digest": (
                historical_constitution.evaluator_registry_digest
            ),
                "auto_promotion_policy_digest": (
                    historical_constitution.auto_promotion_policy_digest
                ),
                "runtime_bundle_manifest_digest": (
                    historical_bundle.manifest_digest
                ),
        }
    )

    result = executor.advance(
        epoch,
        MaintenanceBudget(),
        authorize_effect=allow_effect,
    )

    assert result.next_state == "no_change"
    assert result.reason == "minimum_evaluable_sessions_not_met"


def test_pipeline_rejects_fabricated_cached_publication(
    tmp_path: Path,
) -> None:
    executor = _executor(tmp_path)
    epoch = _advance_to_state(executor, _epoch(executor), "promoting")
    package = executor._required(
        epoch,
        "evaluating",
        PipelinePromotionPackage,
    )
    forged = PipelinePublicationResult(
        control_event_digest="sha256:not-an-event",
        operation_id="operation.forged-publication",
        promotion_package_digest=package.package_digest,
        decision_digest=package.decision.decision_digest,
        snapshot_digest=package.snapshot.snapshot_digest,
        shadow_result_digest=package.snapshot.shadow_result_digest,
        evaluation_report_digests=package.snapshot.evaluation_report_digests,
        promotion_policy_digest=package.decision.policy_digest,
    )
    executor.store.write(epoch.epoch_id, "promoting", forged)

    with pytest.raises(
        SharedStateIntegrityError,
        match="cached publication control event is unavailable",
    ):
        executor.advance(
            epoch,
            MaintenanceBudget(),
            authorize_effect=allow_effect,
        )


def test_candidate_without_any_evaluation_report_cannot_be_finalist() -> None:
    candidate = _candidate("selection")

    assert _select_finalist((candidate,), ()) is None


def test_pipeline_rejects_family_or_per_advance_budget_overrun(tmp_path: Path) -> None:
    candidates = tuple(_candidate("budget", suffix=str(index)) for index in range(3))
    executor = _executor(
        tmp_path,
        candidate_port=_CandidatePort(candidates),
        candidate_family_limit=2,
    )
    epoch = _epoch(executor).model_copy(
        update={
            "constitution_digest": executor.constitution.constitution_digest,
        }
    )
    snapshot = executor.advance(
        epoch, MaintenanceBudget(), authorize_effect=allow_effect
    )
    generating = epoch.model_copy(
        update={"state": snapshot.next_state, "dataset_digest": snapshot.dataset_digest}
    )

    result = executor.advance(
        generating, MaintenanceBudget(), authorize_effect=allow_effect
    )

    assert result.next_state == "no_change"
    assert result.reason == "candidate_family_limit_exceeded"


def test_incomplete_shadow_sample_is_not_frozen_as_final_evidence(
    tmp_path: Path,
) -> None:
    shadow = _ProgressingShadowPort()
    executor = _executor(tmp_path, shadow_port=shadow)
    epoch = _epoch(executor)
    for _ in range(4):
        result = executor.advance(
            epoch, MaintenanceBudget(), authorize_effect=allow_effect
        )
        epoch = epoch.model_copy(
            update={
                "state": result.next_state,
                "dataset_digest": result.dataset_digest or epoch.dataset_digest,
                "finalist_candidate_digest": result.finalist_candidate_digest
                or epoch.finalist_candidate_digest,
            }
        )

    waiting = executor.advance(
        epoch, MaintenanceBudget(), authorize_effect=allow_effect
    )
    completed = executor.advance(
        epoch, MaintenanceBudget(), authorize_effect=allow_effect
    )

    assert waiting.next_state == "shadow_observing"
    assert waiting.reason == "minimum_shadow_window_not_met"
    assert completed.next_state == "evaluating"
    assert shadow.calls == 2


def test_expired_shadow_outcome_window_closes_with_no_change(
    tmp_path: Path,
) -> None:
    executor = _executor(tmp_path, shadow_port=_ExpiredShadowPort())
    epoch = _epoch(executor)
    for _ in range(4):
        result = executor.advance(
            epoch,
            MaintenanceBudget(),
            authorize_effect=allow_effect,
        )
        epoch = epoch.model_copy(
            update={
                "state": result.next_state,
                "dataset_digest": result.dataset_digest or epoch.dataset_digest,
                "finalist_candidate_digest": (
                    result.finalist_candidate_digest or epoch.finalist_candidate_digest
                ),
            }
        )

    result = executor.advance(
        epoch,
        MaintenanceBudget(),
        authorize_effect=allow_effect,
    )

    assert result.next_state == "no_change"
    assert result.reason == "shadow_outcome_maturity_expired"


def test_snapshot_write_is_fenced_after_external_freeze(tmp_path: Path) -> None:
    executor = _executor(tmp_path)
    authorizer = _LoseLeaseBeforeCommit()

    with pytest.raises(SharedStateIntegrityError, match="fenced"):
        executor.advance(
            _epoch(executor),
            MaintenanceBudget(),
            authorize_effect=authorizer,
        )

    assert authorizer.authorizations == 2
    assert (
        executor.store.read(
            _epoch(executor).epoch_id, "snapshotting", PipelineSnapshotResult
        )
        is None
    )


@dataclass
class _LoseLeaseBeforeCommit:
    authorizations: int = 0

    def __call__(self) -> None:
        self.authorizations += 1
        if self.authorizations == 2:
            raise SharedStateIntegrityError("optimization epoch lease was fenced")

    def commit(self, operation: object) -> object:
        self()
        assert callable(operation)
        return operation()


def test_pipeline_has_no_unfenced_write_default(tmp_path: Path) -> None:
    executor = _executor(tmp_path)

    with pytest.raises(TypeError, match="authorize_effect"):
        executor.advance(_epoch(executor), MaintenanceBudget())  # type: ignore[call-arg]


def test_publication_is_fenced_before_external_promotion(tmp_path: Path) -> None:
    publication = _PublicationPort()
    executor = _executor(tmp_path)
    _replace_current_runtime(executor, publication_port=publication)
    epoch = _epoch(executor)
    while epoch.state != "promoting":
        result = executor.advance(
            epoch, MaintenanceBudget(), authorize_effect=allow_effect
        )
        epoch = epoch.model_copy(
            update={
                "state": result.next_state,
                "dataset_digest": result.dataset_digest or epoch.dataset_digest,
                "finalist_candidate_digest": result.finalist_candidate_digest
                or epoch.finalist_candidate_digest,
            }
        )

    def reject_effect() -> None:
        raise SharedStateIntegrityError("optimization epoch lease was fenced")

    with pytest.raises(SharedStateIntegrityError, match="fenced"):
        executor.advance(
            epoch,
            MaintenanceBudget(),
            authorize_effect=reject_effect,
        )

    assert publication.calls == 0


@pytest.mark.parametrize("resume_state", ["evaluating", "promoting"])
@pytest.mark.parametrize(
    "divergence",
    ["candidate", "reports", "snapshot", "policy"],
)
def test_pipeline_rejects_cached_promotion_package_with_divergent_lineage(
    tmp_path: Path,
    resume_state: str,
    divergence: str,
) -> None:
    publication = _PublicationPort()
    executor = _executor(tmp_path)
    _replace_current_runtime(executor, publication_port=publication)
    epoch = _advance_to_state(executor, _epoch(executor), "evaluating")
    finalist = executor._finalist(epoch)
    reports = executor._reports(epoch)
    valid = _PromotionPort().evaluate(
        epoch,
        finalist,
        reports,
        executor._required(epoch, "shadow_observing", PipelineShadowResult),
    )
    candidate_digest = (
        "sha256:rogue-candidate"
        if divergence == "candidate"
        else finalist.candidate_digest
    )
    report_digests = (
        ("sha256:rogue-report",)
        if divergence == "reports"
        else valid.snapshot.evaluation_report_digests
    )
    parent_digest = (
        "sha256:rogue-baseline"
        if divergence == "snapshot"
        else epoch.baseline_snapshot_digest
    )
    rogue_snapshot = OptimizationSnapshot(
        snapshot_id=f"optimization-snapshot.rogue-{divergence}",
        project_id=epoch.project_id,
        parent_snapshot_digest=parent_digest,
        stable_fallback_digest=epoch.baseline_snapshot_digest,
        candidate_digest=candidate_digest,
        evaluation_report_digests=report_digests,
        shadow_result_digest=valid.snapshot.shadow_result_digest,
        policy_payload=valid.snapshot.policy_payload,
        created_at=valid.snapshot.created_at,
    )
    rogue_evidence = AutoPromotionEvidence.model_validate(
        {
            **valid.evidence.model_dump(
                mode="json",
                exclude={"evidence_digest"},
            ),
            "challenger_snapshot_digest": rogue_snapshot.snapshot_digest,
            "candidate_digest": candidate_digest,
            "evaluation_report_digests": report_digests,
        }
    )
    rogue_decision = AutoPromotionDecision(
        decision_id=f"promotion-decision.rogue-{divergence}",
        policy_digest=(
            "sha256:rogue-policy"
            if divergence == "policy"
            else valid.decision.policy_digest
        ),
        baseline_snapshot_digest=epoch.baseline_snapshot_digest,
        challenger_snapshot_digest=rogue_snapshot.snapshot_digest,
        candidate_digest=candidate_digest,
        evaluation_report_digests=report_digests,
        shadow_result_digest=valid.decision.shadow_result_digest,
        promotion_evidence_digest=rogue_evidence.evidence_digest,
        approved=True,
        failed_guards=(),
    )
    executor.store.write(
        epoch.epoch_id,
        "evaluating",
        PipelinePromotionPackage(
            epoch_id=valid.epoch_id,
            constitution_digest=valid.constitution_digest,
            decision=rogue_decision,
            evidence=rogue_evidence,
            snapshot=rogue_snapshot,
        ),
    )
    resumed = epoch.model_copy(update={"state": resume_state})

    with pytest.raises(
        SharedStateIntegrityError,
        match="promotion package lineage diverged",
    ):
        executor.advance(
            resumed,
            MaintenanceBudget(),
            authorize_effect=allow_effect,
        )

    assert publication.calls == 0


def _executor(
    root: Path,
    *,
    dataset_port: object | None = None,
    candidate_port: object | None = None,
    shadow_port: object | None = None,
    candidate_family_limit: int = 8,
) -> OptimizationPipelineExecutor:
    registry = OptimizationEvaluatorRegistry(
        statistics_authority=_StatisticsAuthority()
    )
    registry.register(_evaluator_contract("custom-evaluator"), _EvaluatorAdapter())
    registry.register_contract(
        fixed_holdout_evaluator_contract(("budget", "selection"))
    )
    constitution = _test_constitution(
        registry,
        candidate_family_limit=candidate_family_limit,
    )
    return OptimizationPipelineExecutor(
        root,
        project_id="project.shared",
        minimum_evaluable_sessions=20,
        candidate_family_limit=candidate_family_limit,
        evaluator_registry=registry,
        evaluator_registry_digest=registry.registry_digest,
        configured_constitution=constitution,
        replay_evaluator_kinds=("custom-evaluator",),
        dataset_port=dataset_port or _DatasetPort(evaluable=20),
        candidate_port=candidate_port or _CandidatePort((_candidate("selection"),)),
        holdout_port=_HoldoutPort(),
        shadow_port=shadow_port or _ShadowPort(),
        promotion_port=_PromotionPort(),
        promotion_policy_digest="sha256:promotion-policy",
        publication_port=_PublicationPort(),
        promotion_authority=_PromotionAuthority(),
    )


def _replace_current_runtime(
    executor: OptimizationPipelineExecutor,
    **changes: object,
) -> None:
    digest, bundle = next(iter(executor.runtime_bundles.items()))
    executor.runtime_bundles = {
        digest: replace(bundle, **changes),
    }


@dataclass
class _DatasetPort:
    evaluable: int

    def freeze(
        self, epoch: OptimizationEpoch, authorize_effect: object
    ) -> PipelineSnapshotResult:
        del authorize_effect
        return PipelineSnapshotResult(
            dataset_digest=f"sha256:dataset.{epoch.epoch_id}",
            evaluable_session_count=self.evaluable,
        )


@dataclass
class _DriftingDatasetPort(_DatasetPort):
    def freeze(
        self,
        epoch: OptimizationEpoch,
        authorize_effect: object,
    ) -> PipelineSnapshotResult:
        del authorize_effect
        self.evaluable = 19
        return PipelineSnapshotResult(
            dataset_digest=f"sha256:dataset.{epoch.epoch_id}",
            evaluable_session_count=20,
        )


class _ExecutorOne:
    pass


class _ExecutorTwo:
    pass


@dataclass
class _PortWithExecutor:
    executor: object


@dataclass
class _CountingDatasetPort(_DatasetPort):
    evaluable: int = 20
    calls: int = 0

    def freeze(
        self, epoch: OptimizationEpoch, authorize_effect: object
    ) -> PipelineSnapshotResult:
        self.calls += 1
        return super().freeze(epoch, authorize_effect)


@dataclass
class _CandidatePort:
    candidates: tuple[OptimizationCandidate, ...]

    def generate(
        self,
        epoch: OptimizationEpoch,
        dataset: PipelineSnapshotResult,
        family_limit: int,
    ) -> CandidateGenerationResult:
        return CandidateGenerationResult(candidates=self.candidates)


@dataclass
class _EvaluatorAdapter:
    calls: int = 0

    def evaluate(
        self,
        candidate: OptimizationCandidate,
        context: EvaluationContext,
        contract: EvaluatorContract,
    ) -> OptimizationEvaluationReport:
        self.calls += 1
        return _report(
            candidate,
            context,
            contract.evaluator_kind,
            evaluator_contract=contract,
        )


class _HoldoutPort:
    def evaluate(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        authorize_effect: object,
    ) -> OptimizationEvaluationReport:
        del authorize_effect
        policy = resolve_statistics_policy(
            epoch.statistics_policy_digest,
            configured_policy=baseline_statistics_policy(),
        )
        return _report(
            candidate,
            EvaluationContext(
                dataset_digest=epoch.dataset_digest,
                partition="holdout",
                evaluation_binding_id="evaluation-binding.local-holdout-v1",
                evaluation_provider_id="provider.local-evaluator",
                provider_capabilities=("local-read-only", "read-only"),
                resource_reservation_digest="sha256:reservation",
                statistics_policy_digest=policy.policy_digest,
                statistical_alpha=policy.familywise_alpha / 2,
            ),
            "fixed-holdout",
        )

    def validate_cached(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        report: OptimizationEvaluationReport,
    ) -> None:
        del epoch, candidate, report


class _WrongAlphaHoldoutPort:
    def evaluate(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        authorize_effect: object,
    ) -> OptimizationEvaluationReport:
        del authorize_effect
        policy = resolve_statistics_policy(
            epoch.statistics_policy_digest,
            configured_policy=baseline_statistics_policy(),
        )
        return _report(
            candidate,
            EvaluationContext(
                dataset_digest=epoch.dataset_digest,
                partition="holdout",
                evaluation_binding_id="evaluation-binding.local-holdout-v1",
                evaluation_provider_id="provider.local-evaluator",
                provider_capabilities=("local-read-only", "read-only"),
                resource_reservation_digest="sha256:reservation",
                statistics_policy_digest=policy.policy_digest,
                statistical_alpha=0.5,
            ),
            "fixed-holdout",
        )

    def validate_cached(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        report: OptimizationEvaluationReport,
    ) -> None:
        del epoch, candidate, report


class _ShadowPort:
    def observe(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        authorize_effect: object,
        maximum_provider_calls: int = 0,
    ) -> PipelineShadowResult:
        del authorize_effect, maximum_provider_calls
        policy = resolve_statistics_policy(
            epoch.statistics_policy_digest,
            configured_policy=baseline_statistics_policy(),
        )
        return _complete_shadow(policy)


class _WrongAlphaShadowPort:
    def observe(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        authorize_effect: object,
        maximum_provider_calls: int = 0,
    ) -> PipelineShadowResult:
        del candidate, authorize_effect, maximum_provider_calls
        policy = resolve_statistics_policy(
            epoch.statistics_policy_digest,
            configured_policy=baseline_statistics_policy(),
        )
        return _complete_shadow(policy, alpha=0.5)


class _AdverseShadowPort:
    def observe(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        authorize_effect: object,
        maximum_provider_calls: int = 0,
    ) -> PipelineShadowResult:
        del candidate, authorize_effect, maximum_provider_calls
        policy = resolve_statistics_policy(
            epoch.statistics_policy_digest,
            configured_policy=baseline_statistics_policy(),
        )
        good = _complete_shadow(policy)
        return PipelineShadowResult.model_validate(
            {
                **good.model_dump(
                    mode="json",
                    exclude={"shadow_result_digest"},
                ),
                "evidence_digest": "sha256:adverse-shadow",
                "guard_results": {"shadow_fixture": False},
            }
        )


@dataclass
class _ProgressingShadowPort:
    calls: int = 0

    def observe(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        authorize_effect: object,
        maximum_provider_calls: int = 0,
    ) -> PipelineShadowResult:
        del authorize_effect, maximum_provider_calls
        self.calls += 1
        if self.calls == 1:
            return PipelineShadowResult(
                complete=False,
                reason="minimum_shadow_window_not_met",
            )
        policy = resolve_statistics_policy(
            epoch.statistics_policy_digest,
            configured_policy=baseline_statistics_policy(),
        )
        return _complete_shadow(policy)


class _ExpiredShadowPort:
    def observe(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        authorize_effect: object,
        maximum_provider_calls: int = 0,
    ) -> PipelineShadowResult:
        del epoch, candidate, authorize_effect, maximum_provider_calls
        return PipelineShadowResult(
            complete=False,
            reason="shadow_outcome_maturity_expired",
            session_ids=("session.shadow-expired",),
        )


def _complete_shadow(
    policy: OptimizationStatisticsPolicy | None = None,
    *,
    alpha: float | None = None,
) -> PipelineShadowResult:
    policy = policy or baseline_statistics_policy()
    statistical_alpha = policy.shadow_alpha if alpha is None else alpha
    session_ids = tuple(f"session.shadow-{index:03d}" for index in range(60))
    _, power, lower = binary_improvement_statistics(
        60,
        len(session_ids),
        alpha=statistical_alpha,
        policy=policy,
    )
    return PipelineShadowResult(
        complete=True,
        evidence_digest="sha256:shadow",
        session_ids=session_ids,
        quality_confidence_lower=lower,
        metrics=ShadowComparisonMetrics(
            critical_detection_delta=1,
            late_critical_delta=0,
            reviewer_coverage_leak_delta=0,
            false_positive_delta=0,
            reversal_delta=0,
            stage_reopen_delta=0,
            needs_user_delta=0,
            blocked_delta=0,
            timeout_delta=0,
            abandon_delta=0,
            hard_budget_exhausted_delta=0,
            unknown_or_censored_delta=0,
        ),
        guard_results={"shadow_fixture": True},
        evaluation_binding_id="evaluation-binding.shadow",
        improved_count=60,
        sample_count=len(session_ids),
        statistics_policy_digest=policy.policy_digest,
        statistical_alpha=statistical_alpha,
        statistical_power=power,
    )


def _advance_to_state(
    executor: OptimizationPipelineExecutor,
    epoch: OptimizationEpoch,
    target_state: str,
) -> OptimizationEpoch:
    current = epoch
    while current.state != target_state:
        result = executor.advance(
            current,
            MaintenanceBudget(),
            authorize_effect=allow_effect,
        )
        current = current.model_copy(
            update={
                "state": result.next_state,
                "dataset_digest": result.dataset_digest or current.dataset_digest,
                "finalist_candidate_digest": (
                    result.finalist_candidate_digest
                    or current.finalist_candidate_digest
                ),
            }
        )
    return current


class _PromotionPort:
    def __init__(
        self,
        policy_digest: str = "sha256:promotion-policy",
    ) -> None:
        self.policy_digest = policy_digest

    def evaluate(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        reports: tuple[OptimizationEvaluationReport, ...],
        shadow: PipelineShadowResult,
    ) -> PipelinePromotionPackage:
        report_digests = tuple(sorted(item.report_digest for item in reports))
        snapshot = OptimizationSnapshot(
            snapshot_id="optimization-snapshot.challenger",
            project_id="project.shared",
            parent_snapshot_digest=epoch.baseline_snapshot_digest,
            stable_fallback_digest=epoch.baseline_snapshot_digest,
            candidate_digest=candidate.candidate_digest,
            evaluation_report_digests=report_digests,
            shadow_result_digest=shadow.shadow_result_digest,
            policy_payload={"selection_policy": {"maximum_slots": 2}},
            created_at="2026-07-22T00:00:00Z",
        )
        evidence = AutoPromotionEvidence(
            baseline_snapshot_digest=epoch.baseline_snapshot_digest,
            challenger_snapshot_digest=snapshot.snapshot_digest,
            candidate_digest=candidate.candidate_digest,
            evaluation_report_digests=report_digests,
            shadow_result_digest=shadow.shadow_result_digest,
            invariant_results={"pipeline_fixture": True},
            critical_detection_delta=1,
            late_critical_delta=0,
            reviewer_coverage_leak_delta=0,
            false_positive_delta=0,
            reversal_delta=0,
            stage_reopen_delta=0,
            needs_user_delta=0,
            blocked_delta=0,
            timeout_delta=0,
            abandon_delta=0,
            hard_budget_exhausted_delta=0,
            unknown_or_censored_delta=0,
            quality_confidence_lower=shadow.quality_confidence_lower,
            holdout_session_count=60,
            shadow_session_count=len(shadow.session_ids),
            shadow_observation_days=shadow.observation_days,
            resources_within_constitution=True,
            duties_independent=True,
        )
        decision = AutoPromotionDecision(
            decision_id="promotion-decision.pipeline",
            policy_digest=self.policy_digest,
            baseline_snapshot_digest=epoch.baseline_snapshot_digest,
            challenger_snapshot_digest=snapshot.snapshot_digest,
            candidate_digest=candidate.candidate_digest,
            evaluation_report_digests=report_digests,
            shadow_result_digest=shadow.shadow_result_digest,
            promotion_evidence_digest=evidence.evidence_digest,
            approved=True,
            failed_guards=(),
        )
        return PipelinePromotionPackage(
            epoch_id=epoch.epoch_id,
            constitution_digest=epoch.constitution_digest,
            decision=decision,
            evidence=evidence,
            snapshot=snapshot,
        )

    def validate_cached(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        reports: tuple[OptimizationEvaluationReport, ...],
        shadow: PipelineShadowResult,
        package: PipelinePromotionPackage,
    ) -> None:
        del epoch, candidate, reports, shadow, package


class _PromotionAuthority:
    def __init__(self) -> None:
        self.receipts: dict[str, PipelinePromotionAuthorization] = {}

    def issue_promotion_authorization(
        self,
        epoch: OptimizationEpoch,
        package: PipelinePromotionPackage,
        *,
        fencing_epoch: int,
        claim_digest: str,
    ) -> PipelinePromotionAuthorization:
        receipt = PipelinePromotionAuthorization(
            authorization_id=f"promotion-authorization.{package.snapshot.snapshot_id}",
            epoch_id=epoch.epoch_id,
            epoch_revision=epoch.revision,
            epoch_digest=epoch.epoch_digest,
            constitution_digest=epoch.constitution_digest,
            runtime_bundle_manifest_digest=epoch.runtime_bundle_manifest_digest,
            epoch_fencing_epoch=fencing_epoch,
            epoch_claim_digest=claim_digest,
            promotion_package_digest=package.package_digest,
            decision_digest=package.decision.decision_digest,
            promotion_evidence_digest=package.evidence.evidence_digest,
            snapshot_digest=package.snapshot.snapshot_digest,
            shadow_result_digest=package.snapshot.shadow_result_digest,
            evaluation_report_digests=package.snapshot.evaluation_report_digests,
        )
        self.receipts[package.package_digest] = receipt
        return receipt

    def promotion_authorization(
        self,
        package_digest: str,
    ) -> PipelinePromotionAuthorization | None:
        return self.receipts.get(package_digest)

    def verify_promotion_authorization(
        self,
        receipt: PipelinePromotionAuthorization,
        package: PipelinePromotionPackage,
    ) -> None:
        if self.receipts.get(package.package_digest) != receipt:
            raise SharedStateIntegrityError("promotion authorization lineage diverged")


@dataclass
class _PublicationPort:
    calls: int = 0
    issued_publication_digests: set[str] = field(default_factory=set)

    def promote(
        self, package: PipelinePromotionPackage, authorize_effect: object
    ) -> PipelinePublicationResult:
        del authorize_effect
        self.calls += 1
        result = PipelinePublicationResult(
            control_event_digest=f"sha256:published.{package.snapshot.snapshot_id}",
            operation_id=f"operation.publish.{package.snapshot.snapshot_id}",
            promotion_package_digest=package.package_digest,
            decision_digest=package.decision.decision_digest,
            snapshot_digest=package.snapshot.snapshot_digest,
            shadow_result_digest=package.snapshot.shadow_result_digest,
            evaluation_report_digests=package.snapshot.evaluation_report_digests,
            promotion_policy_digest=package.decision.policy_digest,
        )
        self.issued_publication_digests.add(result.publication_digest)
        return result

    def validate_cached(
        self,
        package: PipelinePromotionPackage,
        publication: PipelinePublicationResult,
    ) -> None:
        del package
        if publication.publication_digest not in self.issued_publication_digests:
            raise SharedStateIntegrityError(
                "cached publication control event is unavailable"
            )


def _epoch(executor: OptimizationPipelineExecutor) -> OptimizationEpoch:
    constitution = executor.constitution
    bundle = executor.runtime_bundles[constitution.constitution_digest]
    policy = bundle.statistics_policy
    return OptimizationEpoch(
        epoch_id="optimization-epoch.pipeline",
        project_id="project.shared",
        trigger_fingerprint="sha256:trigger",
        trigger_digest="sha256:trigger-event",
        constitution_digest=constitution.constitution_digest,
        baseline_snapshot_digest="sha256:baseline",
        candidate_domain_registry_digest="sha256:registry",
        statistics_policy_digest=policy.policy_digest,
        evaluator_registry_digest=constitution.evaluator_registry_digest,
        auto_promotion_policy_digest=(constitution.auto_promotion_policy_digest),
        runtime_bundle_manifest_digest=(
            bundle.manifest_digest
        ),
        session_sequence_high_watermark=30,
        new_session_count=30,
        state="snapshotting",
        revision=1,
        reservation_id="reservation.pipeline",
        reservation_fencing_token=1,
    )


def _test_constitution(
    registry: OptimizationEvaluatorRegistry,
    *,
    promotion_policy_digest: str = "sha256:promotion-policy",
    minimum_evaluable_sessions: int = 20,
    candidate_family_limit: int = 8,
) -> OptimizationConstitution:
    return OptimizationConstitution(
        constitution_version="1.0.0",
        epoch_budget_policy_digest="sha256:budget-policy",
        attribution_policy_digest="sha256:attribution-policy",
        evaluator_registry_digest=registry.registry_digest,
        auto_promotion_policy_digest=promotion_policy_digest,
        storage_policy_digest="sha256:storage-policy",
        candidate_domain_registry_digest="sha256:registry",
        statistics_policy_digest=baseline_statistics_policy().policy_digest,
        minimum_evaluable_sessions=minimum_evaluable_sessions,
        candidate_family_limit=candidate_family_limit,
    )


def _test_registry(
    *,
    evaluator_version: str,
) -> OptimizationEvaluatorRegistry:
    registry = OptimizationEvaluatorRegistry(
        statistics_authority=_StatisticsAuthority()
    )
    registry.register(
        _evaluator_contract(
            "custom-evaluator",
            evaluator_version=evaluator_version,
        ),
        _EvaluatorAdapter(),
    )
    registry.register_contract(
        fixed_holdout_evaluator_contract(("budget", "selection"))
    )
    return registry


def _constitution_with_statistics_policy(
    constitution: OptimizationConstitution,
    policy: OptimizationStatisticsPolicy,
) -> OptimizationConstitution:
    return OptimizationConstitution.model_validate(
        {
            **constitution.model_dump(
                mode="json",
                exclude={"constitution_digest"},
            ),
            "statistics_policy_digest": policy.policy_digest,
            "familywise_alpha": policy.familywise_alpha,
        }
    )


def _candidate(domain: str, *, suffix: str = "one") -> OptimizationCandidate:
    if domain == "selection":
        field_path = "selection_policy.capability_requirement_rules"
        value: object = [
            {
                "rule_id": f"optimization.coverage.{suffix}",
                "stage_keys": ["implementation"],
                "risk_levels": ["high"],
                "capability_ids": ["capability.security"],
                "coverage_count": 2,
            }
        ]
    else:
        field_path = "budget_policy.low.maximum_slots"
        value = 2
    return OptimizationCandidate(
        candidate_id=f"candidate.{suffix}",
        candidate_domain=domain,
        domain_contract_digest=f"sha256:contract.{domain}",
        domain_adapter_id=f"candidate-domain.{domain}",
        domain_adapter_version="1.0.0",
        domain_adapter_digest=f"sha256:adapter.{domain}",
        domain_registry_digest="sha256:registry",
        base_snapshot_digest="sha256:baseline",
        patch_operations=(
            OptimizationPatchOperation(
                operation="replace",
                field_path=field_path,
                value=value,
            ),
        ),
        expected_effect="improve quality",
        rollback_target="sha256:baseline",
        generator_identity="generator.pipeline",
        generator_provider_id="provider.generator",
        attribution_digests=(() if domain == "budget" else ("sha256:attribution",)),
        metric_evidence_digests=(
            ("sha256:metric-evidence",) if domain == "budget" else ()
        ),
        target_stratum_ids=("implementation:high",),
        dataset_partition_refs=("train",),
        estimated_provider_calls=1,
        estimated_tokens=1000,
        estimated_cost=0.5,
        estimated_active_wall_clock=30,
        evidence_refs=("sha256:evidence",),
    )


def _evaluator_contract(
    kind: str,
    *,
    evaluator_version: str = "1.0.0",
) -> EvaluatorContract:
    return EvaluatorContract(
        evaluator_kind=kind,
        evaluator_version=evaluator_version,
        candidate_schema_version="optimization-candidate.v1",
        report_schema_version="optimization-evaluation-report.v1",
        allowed_partitions=("validation",),
        compatible_candidate_domains=("budget", "selection"),
        independence_level="independent_binding",
        deterministic=False,
        provider_constraints=("read-only",),
    )


def _report(
    candidate: OptimizationCandidate,
    context: EvaluationContext,
    evaluator_kind: str,
    *,
    evaluator_contract: EvaluatorContract | None = None,
) -> OptimizationEvaluationReport:
    holdout = context.partition == "holdout"
    policy = resolve_statistics_policy(
        context.statistics_policy_digest,
        configured_policy=baseline_statistics_policy(),
    )
    session_ids = tuple(f"session.{index:03d}" for index in range(60))
    statistical_alpha = context.statistical_alpha
    p_value, power, lower = binary_improvement_statistics(
        60,
        len(session_ids),
        alpha=statistical_alpha,
        policy=policy,
    )
    sample = _statistical_sample(candidate, context)
    contract = evaluator_contract or (
        fixed_holdout_evaluator_contract(("budget", "selection"))
        if evaluator_kind == "fixed-holdout"
        else _evaluator_contract(evaluator_kind)
    )
    return OptimizationEvaluationReport(
        report_id=f"report.{evaluator_kind}.{candidate.candidate_id}",
        candidate_digest=candidate.candidate_digest,
        domain_contract_digest=candidate.domain_contract_digest,
        domain_adapter_id=candidate.domain_adapter_id,
        domain_adapter_version=candidate.domain_adapter_version,
        domain_adapter_digest=candidate.domain_adapter_digest,
        domain_registry_digest=candidate.domain_registry_digest,
        evaluator_kind=evaluator_kind,
        evaluator_version=contract.evaluator_version,
        evaluator_contract_digest=contract.contract_digest,
        dataset_digest=context.dataset_digest,
        partition=context.partition,
        evaluation_binding_id=context.evaluation_binding_id,
        quality_deltas={"critical_detection": 0.1},
        cost_deltas={"cost": 0},
        censoring_metrics={"unknown_or_censored": 0},
        guard_results={"protocol": True},
        comparison_session_ids=session_ids,
        hypothesis_family_digest=(
            context.hypothesis_family_digest or "sha256:hypothesis-family"
        ),
        improved_count=60,
        sample_count=len(session_ids),
        statistical_sample_digest=sample.sample_digest,
        statistics_policy_digest=policy.policy_digest,
        statistical_alpha=statistical_alpha,
        raw_p_value=p_value,
        holm_rank=1,
        holm_threshold=statistical_alpha,
        statistical_power=power,
        effect_confidence_lower=lower,
        holdout_commitment_digest=("sha256:holdout" if holdout else ""),
        holdout_test_sequence=1 if holdout else 0,
        holdout_alpha=statistical_alpha if holdout else 0,
        recommendation="finalist_eligible",
    )


class _StatisticsAuthority:
    def sample(
        self,
        candidate: OptimizationCandidate,
        context: EvaluationContext,
    ) -> OptimizationStatisticalSample:
        return _statistical_sample(candidate, context)


def _statistical_sample(
    candidate: OptimizationCandidate,
    context: EvaluationContext,
) -> OptimizationStatisticalSample:
    session_ids = tuple(f"session.{index:03d}" for index in range(60))
    return OptimizationStatisticalSample(
        candidate_digest=candidate.candidate_digest,
        dataset_digest=context.dataset_digest,
        comparison_session_ids=session_ids,
        improved_session_ids=session_ids,
        source_evidence_digests=("sha256:test-evidence",),
    )
