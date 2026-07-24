from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.optimization.candidate_dataset import (
    CandidateDatasetView,
)
from ai_sdlc.core.stage_review.optimization.candidate_dataset import (
    _build_candidate_dataset_view as build_candidate_dataset_view,
)
from ai_sdlc.core.stage_review.optimization.candidate_generation import (
    LocalCandidateGenerationPort,
)
from ai_sdlc.core.stage_review.optimization.candidate_policy import (
    CandidatePolicyApplier,
)
from ai_sdlc.core.stage_review.optimization.controller_models import OptimizationEpoch
from ai_sdlc.core.stage_review.optimization.datasets import (
    DatasetPopulationEntry,
    OptimizationDatasetSnapshot,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_optimization_snapshot as baseline_optimization_snapshot,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_usage_estimate_policy as baseline_usage_estimate_policy,
)
from ai_sdlc.core.stage_review.optimization.evaluators import (
    EvaluationContext,
    EvaluatorContract,
)
from ai_sdlc.core.stage_review.optimization.local_evaluation import (
    LocalCandidateEvaluator,
)
from ai_sdlc.core.stage_review.optimization.local_shadow import (
    LocalProspectiveShadowPort,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBinding,
    CommittedSessionBindingStore,
    OptimizationObservationStore,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    _build_terminal_observation as build_terminal_observation,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    PipelineSnapshotResult,
)
from ai_sdlc.core.stage_review.optimization.pipeline_effects import allow_effect
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
from ai_sdlc.core.stage_review.optimization.statistics import (
    _apply_holm_bonferroni as apply_holm_bonferroni,
)
from ai_sdlc.core.stage_review.provider_usage_models import metered_provider_usage
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts


def test_budget_exhaustion_generates_five_bounded_policy_adjustments() -> None:
    baseline = baseline_optimization_snapshot("project.shared")
    view = _view(baseline.snapshot_digest)
    port = LocalCandidateGenerationPort(
        project_id="project.shared",
        snapshot_source=lambda _: baseline,
        candidate_view_source=lambda _: view,
    )

    candidates = port.generate(
        _epoch(baseline.snapshot_digest),
        PipelineSnapshotResult(
            dataset_digest=view.source_dataset_digest,
            evaluable_session_count=20,
        ),
        family_limit=8,
    ).candidates

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.candidate_domain == "budget"
    assert candidate.attribution_digests == ()
    assert candidate.metric_evidence_digests == (view.view_digest,)
    assert tuple(item.field_path for item in candidate.patch_operations) == (
        "budget_policy.medium.hard_provider_calls",
        "budget_policy.medium.hard_review_passes",
        "budget_policy.medium.hard_tokens",
        "budget_policy.medium.hard_wall_clock",
        "budget_policy.medium.maximum_slots",
    )

    snapshot = CandidatePolicyApplier().apply(
        candidate,
        base_snapshot=baseline,
        attributions=(),
        evaluation_report_digests=("sha256:report",),
        created_at="2026-07-22T00:00:00Z",
    )
    policy = snapshot.policy_payload["budget_policy"]["medium"]
    assert policy["maximum_slots"] == 5
    assert policy["hard_provider_calls"] == 30
    assert policy["hard_review_passes"] == 10
    assert policy["hard_tokens"] == 1_250_000
    assert policy["hard_wall_clock"] == 6_750


def test_budget_candidate_is_not_generated_from_cost_only_successes() -> None:
    baseline = baseline_optimization_snapshot("project.shared")
    view = _view(baseline.snapshot_digest, terminal_outcome="consumed")
    candidates = LocalCandidateGenerationPort(
        project_id="project.shared",
        snapshot_source=lambda _: baseline,
        candidate_view_source=lambda _: view,
    ).generate(
        _epoch(baseline.snapshot_digest),
        PipelineSnapshotResult(
            dataset_digest=view.source_dataset_digest,
            evaluable_session_count=20,
        ),
        family_limit=8,
    ).candidates

    assert candidates == ()


def test_budget_replay_uses_exhaustion_outcomes_not_attribution_shortcut() -> None:
    baseline = baseline_optimization_snapshot("project.shared")
    dataset = _dataset(baseline.snapshot_digest)
    view = build_candidate_dataset_view(dataset, ())
    candidate = LocalCandidateGenerationPort(
        project_id="project.shared",
        snapshot_source=lambda _: baseline,
        candidate_view_source=lambda _: view,
    ).generate(
        _epoch(baseline.snapshot_digest),
        PipelineSnapshotResult(
            dataset_digest=dataset.dataset_digest,
            evaluable_session_count=30,
        ),
        family_limit=8,
    ).candidates[0]
    context = EvaluationContext(
        dataset_digest=dataset.dataset_digest,
        partition="validation",
        evaluation_binding_id="evaluation-binding.budget",
        evaluation_provider_id="provider.local-evaluator",
        provider_capabilities=("local-read-only", "read-only"),
        resource_reservation_digest="sha256:reservation",
    )
    report = LocalCandidateEvaluator(
        dataset_source=lambda _: dataset,
        attribution_source=lambda: (),
    ).evaluate(candidate, context, _contract())
    adjusted = apply_holm_bonferroni((report,))[0]

    assert report.quality_deltas["critical_detection"] == 0
    assert report.quality_deltas["budget_exhaustion_recovery"] == 1
    assert report.guard_results["metric_evidence_authorized"] is True
    assert adjusted.recommendation == "finalist_eligible"


def test_budget_shadow_compares_future_exhaustion_outcomes(tmp_path) -> None:
    baseline = baseline_optimization_snapshot("project.shared")
    dataset = _dataset(baseline.snapshot_digest)
    view = build_candidate_dataset_view(dataset, ())
    epoch = _epoch(baseline.snapshot_digest)
    candidate = LocalCandidateGenerationPort(
        project_id="project.shared",
        snapshot_source=lambda _: baseline,
        candidate_view_source=lambda _: view,
    ).generate(
        epoch,
        PipelineSnapshotResult(
            dataset_digest=dataset.dataset_digest,
            evaluable_session_count=30,
        ),
        family_limit=8,
    ).candidates[0]
    bindings = CommittedSessionBindingStore(tmp_path, project_id="project.shared")
    observations = OptimizationObservationStore(tmp_path, project_id="project.shared")
    assignments = OptimizationShadowAssignmentStore(
        tmp_path, project_id="project.shared"
    )
    shadow_observations = OptimizationShadowObservationStore(
        tmp_path, project_id="project.shared"
    )
    for index in range(10):
        binding = _future_binding(index, baseline.snapshot_digest)
        bindings.append(binding)
        observations.append(
            build_terminal_observation(
                binding,
                "hard_budget_exhausted",
                sequence=100 + index,
                occurred_at="2026-07-24T00:00:00Z",
                terminal_reason="hard_budget_exhausted",
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
    assert port.observe(epoch, candidate, allow_effect).complete is False
    for index in range(10):
        assignment = assignments.read_session(f"session.future-{index:02d}")
        assert assignment is not None
        shadow_observations.append(
            build_shadow_observation(
                assignment,
                baseline=ShadowOutcome(
                    terminal_outcome="hard_budget_exhausted"
                ),
                challenger=ShadowOutcome(terminal_outcome="consumed"),
                evaluation_binding_id="evaluation-binding.budget-shadow",
                evaluation_provider_id="provider.budget-shadow",
                provider_invocation_id=f"provider-invocation.budget-{index}",
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
    result = port.observe(epoch, candidate, allow_effect)

    assert result.complete is True
    assert result.metrics is not None
    assert result.metrics.hard_budget_exhausted_delta == -1
    assert result.quality_confidence_lower > 0


def _view(
    baseline_digest: str,
    *,
    terminal_outcome: str = "hard_budget_exhausted",
) -> CandidateDatasetView:
    return build_candidate_dataset_view(
        _dataset(baseline_digest, terminal_outcome=terminal_outcome), ()
    )


def _dataset(
    baseline_digest: str,
    *,
    terminal_outcome: str = "hard_budget_exhausted",
) -> OptimizationDatasetSnapshot:
    usage_policy = baseline_usage_estimate_policy()
    population = tuple(
        DatasetPopulationEntry(
            session_id=f"session.budget-{index:02d}",
            initial_candidate_digest=f"sha256:candidate.{index}",
            stage_key="implementation",
            risk_level="medium",
            candidate_size_bucket="medium",
            provider_ids=("provider.codex",),
            active_snapshot_digest=baseline_digest,
            usage_estimation_policy_version=usage_policy.version,
            usage_estimation_policy_digest=usage_policy.policy_digest,
            control_sequence=index + 1,
            committed_at="2026-07-21T00:00:00Z",
            evaluable=True,
            terminal_outcome=terminal_outcome,
            observation_digests=(f"sha256:observation.{index}",),
            label_source_digests=(f"sha256:label.{index}",),
            resource_usage=ResourceAmounts(
                slots=4,
                provider_calls=24,
                review_passes=8,
                tokens=1_000_000,
                cost=20,
                active_wall_clock=5_400,
            ),
        )
        for index in range(30)
    )
    ids = tuple(item.session_id for item in population)
    return OptimizationDatasetSnapshot(
        project_id="project.shared",
        epoch_started_at="2026-07-22T00:00:00Z",
        session_sequence_high_watermark=30,
        trigger_fingerprint="sha256:trigger",
        constitution_digest="sha256:constitution",
        baseline_snapshot_digest=baseline_digest,
        comparison_usage_estimation_policy_version=usage_policy.version,
        comparison_usage_estimation_policy_digest=usage_policy.policy_digest,
        holdout_generation_id="holdout-generation.budget",
        population=population,
        session_population_ids=ids,
        evaluable_session_ids=ids,
        censoring_reasons={},
        partition_assignment={
            "train": ids[:10],
            "validation": ids[10:20],
            "holdout": ids[20:],
            "prospective_shadow": (),
        },
        unknown_or_censored_rate=0,
        leakage_check_passed=True,
        data_integrity_digest=canonical_digest(population, CanonicalizationPolicy()),
    )


def _epoch(baseline_digest: str) -> OptimizationEpoch:
    return OptimizationEpoch(
        epoch_id="optimization-epoch.budget",
        project_id="project.shared",
        trigger_fingerprint="sha256:trigger",
        trigger_digest="sha256:trigger-event",
        constitution_digest="sha256:constitution",
        baseline_snapshot_digest=baseline_digest,
        candidate_domain_registry_digest="sha256:registry",
        session_sequence_high_watermark=20,
        new_session_count=20,
        state="generating",
        revision=1,
        dataset_digest="sha256:dataset",
        started_at="2026-07-22T00:00:00Z",
    )


def _future_binding(index: int, baseline_digest: str) -> CommittedSessionBinding:
    return CommittedSessionBinding(
        project_id="project.shared",
        session_id=f"session.future-{index:02d}",
        initial_candidate_digest=f"sha256:future-candidate.{index}",
        stage_key="implementation",
        risk_level="medium",
        candidate_size_bucket="medium",
        provider_ids=("provider.codex",),
        active_snapshot_digest=baseline_digest,
        control_sequence=31 + index,
        control_event_digest=f"sha256:control.{index}",
        committed_at="2026-07-23T00:00:00Z",
    )


def _contract() -> EvaluatorContract:
    return EvaluatorContract(
        evaluator_kind="population-metrics",
        evaluator_version="1.0.0",
        candidate_schema_version="optimization-candidate.v1",
        report_schema_version="optimization-evaluation-report.v1",
        allowed_partitions=("validation",),
        compatible_candidate_domains=("budget",),
        independence_level="deterministic",
        deterministic=True,
        provider_constraints=("local-read-only",),
    )
