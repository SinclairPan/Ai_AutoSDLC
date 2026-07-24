from __future__ import annotations

from pathlib import Path

from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.candidate_dataset import (
    _build_candidate_dataset_view as build_candidate_dataset_view,
)
from ai_sdlc.core.stage_review.optimization.candidate_generation import (
    LocalCandidateGenerationPort,
)
from ai_sdlc.core.stage_review.optimization.controller_models import OptimizationEpoch
from ai_sdlc.core.stage_review.optimization.datasets import (
    DatasetPopulationEntry,
    OptimizationDatasetSnapshot,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_foreground_capacity as baseline_foreground_capacity,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_optimization_snapshot as baseline_optimization_snapshot,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_usage_estimate_policy as baseline_usage_estimate_policy,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    baseline_auto_promotion_policy,
    baseline_offline_capacity,
)
from ai_sdlc.core.stage_review.optimization.evaluators import (
    EvaluationContext,
    EvaluatorContract,
)
from ai_sdlc.core.stage_review.optimization.holdout_store import HoldoutCommitmentStore
from ai_sdlc.core.stage_review.optimization.local_evaluation import (
    LocalCandidateEvaluator,
)
from ai_sdlc.core.stage_review.optimization.local_holdout import (
    LocalHoldoutEvaluationPort,
)
from ai_sdlc.core.stage_review.optimization.local_promotion import (
    LocalPromotionEvaluationPort,
)
from ai_sdlc.core.stage_review.optimization.local_shadow import (
    LocalProspectiveShadowPort,
)
from ai_sdlc.core.stage_review.optimization.models import OptimizationCandidate
from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBinding,
    CommittedSessionBindingStore,
    OptimizationObservationStore,
    OptimizationSessionObservation,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    _build_terminal_observation as build_terminal_observation,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    PipelineSnapshotResult,
)
from ai_sdlc.core.stage_review.optimization.pipeline_effects import allow_effect
from ai_sdlc.core.stage_review.optimization.product_pipeline import (
    SnapshotPublication,
)
from ai_sdlc.core.stage_review.optimization.promotion import AutoPromotionGate
from ai_sdlc.core.stage_review.optimization.shadow import (
    OptimizationShadowAssignmentStore,
)
from ai_sdlc.core.stage_review.optimization.shadow_observations import (
    OptimizationShadowObservationStore,
    ShadowOutcome,
)
from ai_sdlc.core.stage_review.optimization.shadow_observations import (
    _build_shadow_observation as build_shadow_observation,
)
from ai_sdlc.core.stage_review.optimization.snapshot_monitor import (
    reconcile_active_snapshot,
)
from ai_sdlc.core.stage_review.optimization.snapshots import SnapshotControlService
from ai_sdlc.core.stage_review.optimization.statistics import (
    _apply_holm_bonferroni as apply_holm_bonferroni,
)
from ai_sdlc.core.stage_review.provider_usage_models import metered_provider_usage
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.resources import ResourceGovernor


def test_attribution_to_active_snapshot_product_flow(tmp_path: Path) -> None:
    baseline, epoch, candidate, attributions, reports = _historical_evidence(tmp_path)
    shadow = _shadow_result(tmp_path, epoch, candidate)
    governor = ResourceGovernor(
        tmp_path,
        project_id=epoch.project_id,
        foreground_capacity=baseline_foreground_capacity(),
        offline_optimization_capacity=baseline_offline_capacity(),
    )
    snapshots = SnapshotControlService(
        tmp_path,
        project_id=epoch.project_id,
        baseline_snapshot=baseline,
        resource_governor=governor,
    )
    package = LocalPromotionEvaluationPort(
        snapshot_source=lambda _: baseline,
        attribution_source=lambda: attributions,
        gate=AutoPromotionGate(baseline_auto_promotion_policy()),
        resource_capacity=baseline_offline_capacity(),
        clock=lambda: "2026-08-06T00:00:00Z",
    ).evaluate(epoch, candidate, reports, shadow)

    event_digest = SnapshotPublication(snapshots).promote(package, allow_effect)

    assert package.decision.approved is True
    assert event_digest
    assert snapshots.resolve_snapshot().active_snapshot_digest == (
        package.snapshot.snapshot_digest
    )


def test_promoted_snapshot_is_automatically_marked_stable(tmp_path: Path) -> None:
    snapshots, package, observations = _promoted_snapshot(tmp_path)
    for index in range(10):
        observations.append(
            _active_observation(
                package.snapshot.snapshot_digest,
                index,
                terminal_reason="consumed",
            )
        )

    result = reconcile_active_snapshot(
        snapshots,
        observations,
        clock=lambda: "2026-08-21T00:00:00Z",
    )

    token = snapshots.resolve_snapshot()
    assert result == "marked_stable"
    assert token.stable_fallback_digest == package.snapshot.snapshot_digest


def test_critical_active_snapshot_signal_revokes_and_rolls_back(
    tmp_path: Path,
) -> None:
    snapshots, package, observations = _promoted_snapshot(tmp_path)
    observations.append(
        _active_observation(
            package.snapshot.snapshot_digest,
            1,
            terminal_reason="false_certificate",
            kind="integrity_failure",
        )
    )

    result = reconcile_active_snapshot(
        snapshots,
        observations,
        clock=lambda: "2026-08-07T00:00:00Z",
    )

    token = snapshots.resolve_snapshot()
    assert result == "revoked_and_rolled_back"
    assert package.snapshot.snapshot_digest in token.revoked_snapshot_digests
    assert token.active_snapshot_digest == package.snapshot.stable_fallback_digest


def test_promotion_rejects_projected_resource_constitution_overrun(
    tmp_path: Path,
) -> None:
    baseline, epoch, candidate, attributions, reports = _historical_evidence(tmp_path)
    shadow = _shadow_result(tmp_path, epoch, candidate)
    capacity = baseline_offline_capacity()
    exhausted = epoch.model_copy(
        update={
            "cumulative_usage": ResourceAmounts(cost=capacity.cost + 1),
            "epoch_digest": "",
        }
    )
    package = LocalPromotionEvaluationPort(
        snapshot_source=lambda _: baseline,
        attribution_source=lambda: attributions,
        gate=AutoPromotionGate(baseline_auto_promotion_policy()),
        resource_capacity=capacity,
        clock=lambda: "2026-08-06T00:00:00Z",
    ).evaluate(exhausted, candidate, reports, shadow)

    assert package.decision.approved is False
    assert "resource_bounds" in package.decision.failed_guards


def test_promotion_rejects_adverse_shadow_rates(tmp_path: Path) -> None:
    baseline, epoch, candidate, attributions, reports = _historical_evidence(tmp_path)
    shadow = _shadow_result(tmp_path, epoch, candidate, adverse_outcome=True)
    package = LocalPromotionEvaluationPort(
        snapshot_source=lambda _: baseline,
        attribution_source=lambda: attributions,
        gate=AutoPromotionGate(baseline_auto_promotion_policy()),
        resource_capacity=baseline_offline_capacity(),
        clock=lambda: "2026-08-06T00:00:00Z",
    ).evaluate(epoch, candidate, reports, shadow)

    assert package.decision.approved is False
    assert "false_positive_non_regression" in package.decision.failed_guards
    assert "needs_user_non_regression" in package.decision.failed_guards


def test_shadow_assignment_is_not_evidence_without_independent_observation(
    tmp_path: Path,
) -> None:
    _, epoch, candidate, _, _ = _historical_evidence(tmp_path)

    shadow = _shadow_result(
        tmp_path, epoch, candidate, include_observations=False
    )

    assert shadow.complete is False
    assert shadow.reason == "shadow_observations_pending"


def _promoted_snapshot(tmp_path: Path):
    baseline, epoch, candidate, attributions, reports = _historical_evidence(tmp_path)
    shadow = _shadow_result(tmp_path, epoch, candidate)
    governor = ResourceGovernor(
        tmp_path,
        project_id=epoch.project_id,
        foreground_capacity=baseline_foreground_capacity(),
        offline_optimization_capacity=baseline_offline_capacity(),
    )
    snapshots = SnapshotControlService(
        tmp_path,
        project_id=epoch.project_id,
        baseline_snapshot=baseline,
        resource_governor=governor,
    )
    package = LocalPromotionEvaluationPort(
        snapshot_source=lambda _: baseline,
        attribution_source=lambda: attributions,
        gate=AutoPromotionGate(baseline_auto_promotion_policy()),
        resource_capacity=baseline_offline_capacity(),
        clock=lambda: "2026-08-06T00:00:00Z",
    ).evaluate(epoch, candidate, reports, shadow)
    SnapshotPublication(snapshots).promote(package, allow_effect)
    observations = OptimizationObservationStore(
        tmp_path,
        project_id=epoch.project_id,
    )
    return snapshots, package, observations


def _active_observation(
    snapshot_digest: str,
    index: int,
    *,
    terminal_reason: str,
    kind: str = "consumed",
) -> OptimizationSessionObservation:
    return OptimizationSessionObservation(
        observation_id=f"observation.active-{index:02d}",
        project_id="project.product-flow",
        session_id=f"session.active-{index:02d}",
        initial_candidate_digest=f"sha256:active-candidate.{index}",
        sequence=200 + index,
        observation_kind=kind,
        occurred_at="2026-08-07T00:00:00Z",
        stage_key="implementation",
        risk_level="high",
        candidate_size_bucket="medium",
        provider_ids=("provider.codex",),
        active_snapshot_digest=snapshot_digest,
        terminal_reason=terminal_reason,
    )


def _historical_evidence(tmp_path: Path):
    project_id = "project.product-flow"
    baseline = baseline_optimization_snapshot(project_id)
    dataset = _dataset(project_id, baseline.snapshot_digest)
    attributions = tuple(
        _attribution(
            project_id,
            entry.session_id,
            original_candidate_digest=entry.initial_candidate_digest,
        )
        for entry in dataset.population
    )
    epoch = _epoch(project_id, baseline.snapshot_digest, dataset.dataset_digest)
    candidate = LocalCandidateGenerationPort(
        project_id=project_id,
        snapshot_source=lambda _: baseline,
        candidate_view_source=lambda _: build_candidate_dataset_view(
            dataset, attributions
        ),
    ).generate(
        epoch,
        PipelineSnapshotResult(
            dataset_digest=dataset.dataset_digest,
            evaluable_session_count=30,
        ),
        family_limit=8,
    ).candidates[0]

    evaluator = LocalCandidateEvaluator(
        dataset_source=lambda _: dataset,
        attribution_source=lambda: attributions,
    )
    replay = evaluator.evaluate(
        candidate,
        EvaluationContext(
            dataset_digest=dataset.dataset_digest,
            partition="validation",
            evaluation_binding_id="evaluation-binding.population",
            evaluation_provider_id="provider.local-evaluator",
            provider_capabilities=("local-read-only", "read-only"),
            resource_reservation_digest="sha256:reservation",
        ),
        _contract(),
    )
    replay = apply_holm_bonferroni((replay,))[0]
    holdout = LocalHoldoutEvaluationPort(
        store=HoldoutCommitmentStore(
            tmp_path, project_id=project_id, familywise_alpha=0.05
        ),
        dataset_source=lambda _: dataset,
        attribution_source=lambda: attributions,
    ).evaluate(epoch, candidate, allow_effect)
    return baseline, epoch, candidate, attributions, (replay, holdout)


def _shadow_result(
    root: Path,
    epoch: OptimizationEpoch,
    candidate: OptimizationCandidate,
    *,
    include_observations: bool = True,
    adverse_outcome: bool = False,
):
    bindings = CommittedSessionBindingStore(root, project_id=epoch.project_id)
    observations = OptimizationObservationStore(root, project_id=epoch.project_id)
    assignments = OptimizationShadowAssignmentStore(
        root, project_id=epoch.project_id
    )
    shadow_observations = OptimizationShadowObservationStore(
        root, project_id=epoch.project_id
    )
    for index in range(10):
        binding = CommittedSessionBinding(
            project_id=epoch.project_id,
            session_id=f"session.shadow-{index:02d}",
            initial_candidate_digest=f"sha256:shadow-candidate.{index}",
            stage_key="implementation",
            risk_level="high",
            candidate_size_bucket="medium",
            provider_ids=("provider.codex",),
            active_snapshot_digest=epoch.baseline_snapshot_digest,
            control_sequence=31 + index,
            control_event_digest=f"sha256:control.{index}",
            committed_at="2026-07-23T00:00:00Z",
        )
        bindings.append(binding)
        observations.append(
            build_terminal_observation(
                binding,
                "consumed",
                sequence=100 + index,
                occurred_at="2026-07-24T00:00:00Z",
                terminal_reason="consumed",
            )
        )
    port = LocalProspectiveShadowPort(
        assignments=assignments,
        bindings=bindings,
        observations=observations,
        shadow_observations=shadow_observations,
        clock=lambda: "2026-08-06T00:00:00Z",
        minimum_sessions=10,
        minimum_days=14,
        usage_policy_source=lambda _: baseline_usage_estimate_policy(),
    )
    pending = port.observe(epoch, candidate, allow_effect)
    if include_observations:
        for index in range(10):
            assignment = assignments.read_session(f"session.shadow-{index:02d}")
            assert assignment is not None
            shadow_observations.append(
                build_shadow_observation(
                    assignment,
                    baseline=ShadowOutcome(terminal_outcome="consumed"),
                    challenger=ShadowOutcome(
                        critical_detected=True,
                        false_positive=adverse_outcome,
                        terminal_outcome=(
                            "needs_user" if adverse_outcome else "consumed"
                        ),
                    ),
                    evaluation_binding_id="evaluation-binding.shadow-independent",
                    evaluation_provider_id="provider.shadow-independent",
                    provider_invocation_id=f"provider-invocation.shadow-{index}",
                    provider_submission_digest=f"sha256:submission.{index}",
                    accounted_usage=metered_provider_usage(
                        ResourceAmounts(
                            provider_calls=1,
                            review_passes=1,
                            tokens=1,
                            cost=0.01,
                            active_wall_clock=0.1,
                        )
                    ),
                    validation_digest=f"sha256:validation.{index}",
                    resource_settlement_event_digest=f"sha256:settlement.{index}",
                    label_source_digests=(f"sha256:label.{index}",),
                    observed_at="2026-08-06T00:00:00Z",
                )
            )
        return port.observe(epoch, candidate, allow_effect)
    return pending


def _dataset(project_id: str, baseline_digest: str) -> OptimizationDatasetSnapshot:
    usage_policy = baseline_usage_estimate_policy()
    entries = tuple(_entry(index, baseline_digest) for index in range(30))
    ids = tuple(item.session_id for item in entries)
    return OptimizationDatasetSnapshot(
        project_id=project_id,
        epoch_started_at="2026-07-22T00:00:00Z",
        session_sequence_high_watermark=30,
        trigger_fingerprint="sha256:trigger",
        constitution_digest="sha256:constitution",
        baseline_snapshot_digest=baseline_digest,
        comparison_usage_estimation_policy_version=usage_policy.version,
        comparison_usage_estimation_policy_digest=usage_policy.policy_digest,
        holdout_generation_id="holdout-generation.product-flow",
        population=entries,
        session_population_ids=ids,
        evaluable_session_ids=ids,
        censoring_reasons={},
        partition_assignment={
            "train": ids[:10],
            "validation": ids[10:20],
            "holdout": ids[20:30],
            "prospective_shadow": (),
        },
        unknown_or_censored_rate=0,
        leakage_check_passed=True,
        data_integrity_digest=canonical_digest(entries, CanonicalizationPolicy()),
    )


def _entry(index: int, baseline_digest: str) -> DatasetPopulationEntry:
    usage_policy = baseline_usage_estimate_policy()
    return DatasetPopulationEntry(
        session_id=f"session.historical-{index:02d}",
        initial_candidate_digest=f"sha256:candidate.{index}",
        stage_key="implementation",
        risk_level="high",
        candidate_size_bucket="medium",
        provider_ids=("provider.codex",),
        active_snapshot_digest=baseline_digest,
        usage_estimation_policy_version=usage_policy.version,
        usage_estimation_policy_digest=usage_policy.policy_digest,
        control_sequence=index + 1,
        committed_at="2026-07-21T00:00:00Z",
        evaluable=True,
        terminal_outcome="consumed",
        observation_digests=(f"sha256:observation.{index}",),
    )


def _attribution(
    project_id: str,
    session_id: str,
    *,
    original_candidate_digest: str = "sha256:original",
) -> FindingAttribution:
    return FindingAttribution(
        attribution_id=f"attribution.{session_id}",
        project_id=project_id,
        session_id=session_id,
        finding_key=f"finding.{session_id}",
        finding_event_digest=f"sha256:event.{session_id}",
        attribution_evidence_digest=f"sha256:input.{session_id}",
        source_evidence_digest=f"sha256:evidence.{session_id}",
        original_candidate_digest=original_candidate_digest,
        discovery_candidate_digest="sha256:discovery",
        initial_cohort_id="cohort.initial",
        discovery_cohort_id="cohort.discovery",
        capability_coverage_digest="sha256:coverage",
        capability_id="capability.security",
        role_profile_id="role.security",
        provider_binding_digest="sha256:binding",
        attribution_engine_version="1.0.0",
        policy_digest="sha256:policy",
        primary_cause_id="panel_selection_gap",
        candidate_domain="selection",
        confidence=1,
        status="candidate_authorized",
        reason_code="candidate_domain_authorized",
    )


def _epoch(
    project_id: str, baseline_digest: str, dataset_digest: str
) -> OptimizationEpoch:
    return OptimizationEpoch(
        epoch_id="optimization-epoch.product-flow",
        project_id=project_id,
        trigger_fingerprint="sha256:trigger",
        trigger_digest="sha256:trigger-event",
        constitution_digest="sha256:constitution",
        baseline_snapshot_digest=baseline_digest,
        candidate_domain_registry_digest="sha256:registry",
        session_sequence_high_watermark=30,
        new_session_count=30,
        state="replaying",
        revision=1,
        dataset_digest=dataset_digest,
        started_at="2026-07-22T00:00:00Z",
    )


def _contract() -> EvaluatorContract:
    return EvaluatorContract(
        evaluator_kind="population-metrics",
        evaluator_version="1.0.0",
        candidate_schema_version="optimization-candidate.v1",
        report_schema_version="optimization-evaluation-report.v1",
        allowed_partitions=("validation",),
        compatible_candidate_domains=("selection",),
        independence_level="deterministic",
        deterministic=True,
        provider_constraints=("local-read-only",),
    )
