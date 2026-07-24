from __future__ import annotations

from ai_sdlc.core.stage_review.finding_models import FindingIdentityInput
from ai_sdlc.core.stage_review.optimization.candidate_domain_defaults import (
    default_candidate_domain_registry,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_usage_estimate_policy as baseline_usage_estimate_policy,
)
from ai_sdlc.core.stage_review.optimization.local_shadow import _complete_result
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationPatchOperation,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    OptimizationSessionObservation,
)
from ai_sdlc.core.stage_review.optimization.shadow import (
    OptimizationShadowAssignment,
)
from ai_sdlc.core.stage_review.optimization.shadow_labels import (
    labeled_shadow_outcomes,
)
from ai_sdlc.core.stage_review.optimization.shadow_observations import (
    _build_shadow_observation as build_shadow_observation,
)
from ai_sdlc.core.stage_review.provider_usage_models import metered_provider_usage
from ai_sdlc.core.stage_review.remote_review_models import (
    RemoteReviewFinding,
    RemoteReviewOutput,
)
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.session_artifact_models import CoverageDeclaration


def test_unconfirmed_shadow_finding_is_censored_not_false_positive() -> None:
    baseline, challenger, labels = labeled_shadow_outcomes(
        candidate=_candidate(),
        baseline_observation=_baseline(),
        review=_review_with_unconfirmed_p1(),
        finding_events=(),
    )

    assert baseline.critical_detected is False
    assert challenger.critical_detected is False
    assert challenger.false_positive is False
    assert challenger.unconfirmed_finding is True
    assert challenger.terminal_outcome == "unknown_or_censored"
    assert labels == tuple(
        sorted(("sha256:terminal-label", _baseline().observation_digest))
    )


def test_unconfirmed_shadow_finding_fails_authority_lineage_guard() -> None:
    assignment = _assignment()
    baseline, challenger, labels = labeled_shadow_outcomes(
        candidate=_candidate(),
        baseline_observation=_baseline(),
        review=_review_with_unconfirmed_p1(),
        finding_events=(),
    )
    observation = build_shadow_observation(
        assignment,
        baseline=baseline,
        challenger=challenger,
        evaluation_binding_id="evaluation-binding.independent",
        evaluation_provider_id="provider.independent",
        provider_invocation_id="provider-invocation.shadow-label",
        provider_submission_digest="sha256:submission",
        accounted_usage=metered_provider_usage(
            ResourceAmounts(
                provider_calls=1,
                review_passes=1,
                tokens=1,
                cost=0.01,
                active_wall_clock=0.1,
            )
        ),
        validation_digest="sha256:validation",
        resource_settlement_event_digest="sha256:settlement",
        label_source_digests=labels,
        observed_at="2026-08-06T00:00:00Z",
    )

    result = _complete_result(
        _candidate(), (assignment,), (observation,), days=14
    )

    assert result.complete is True
    assert result.guard_results["authority_label_lineage"] is False


def _candidate() -> OptimizationCandidate:
    return OptimizationCandidate(
        candidate_id="candidate.shadow-label",
        candidate_domain="selection",
        **default_candidate_domain_registry().candidate_binding("selection"),
        base_snapshot_digest="sha256:baseline-snapshot",
        patch_operations=(
            OptimizationPatchOperation(
                operation="replace",
                field_path="selection_policy.capability_requirement_rules",
                value=[],
            ),
        ),
        expected_effect="recover confirmed critical coverage",
        rollback_target="sha256:baseline-snapshot",
        generator_identity="generator.selection",
        generator_provider_id="provider.generator",
        attribution_digests=("sha256:attribution",),
        target_stratum_ids=("implementation:high",),
        dataset_partition_refs=("train",),
        estimated_provider_calls=1,
        estimated_tokens=1000,
        estimated_cost=0.5,
        estimated_active_wall_clock=30,
        evidence_refs=("sha256:evidence",),
    )


def _baseline() -> OptimizationSessionObservation:
    return OptimizationSessionObservation(
        observation_id="observation.shadow-label",
        project_id="project.shadow-label",
        session_id="session.shadow-label",
        initial_candidate_digest="sha256:initial",
        sequence=31,
        observation_kind="consumed",
        occurred_at="2026-07-24T00:00:00Z",
        stage_key="implementation",
        risk_level="high",
        candidate_size_bucket="medium",
        provider_ids=("provider.codex",),
        active_snapshot_digest="sha256:baseline-snapshot",
        terminal_reason="consumed",
        label_source_digests=("sha256:terminal-label",),
    )


def _review_with_unconfirmed_p1() -> RemoteReviewOutput:
    identity = FindingIdentityInput(
        rule_id="rule.shadow",
        category="correctness",
        asset_identity="src/example.py",
        semantic_location="example",
        failure_signature="unconfirmed failure",
    )
    return RemoteReviewOutput(
        verdict="findings",
        coverage=CoverageDeclaration(
            reviewed_area_ids=("capability.correctness",)
        ),
        findings=(
            RemoteReviewFinding(
                identity=identity,
                severity="P1",
                evidence_bundle_digest="sha256:finding-evidence",
                capability_id="capability.correctness",
            ),
        ),
        evidence_digests=("sha256:review-evidence",),
    )


def _assignment() -> OptimizationShadowAssignment:
    policy = baseline_usage_estimate_policy()
    return OptimizationShadowAssignment(
        assignment_id="optimization-shadow.shadow-label",
        project_id="project.shadow-label",
        epoch_id="optimization-epoch.shadow-label",
        finalist_candidate_digest=_candidate().candidate_digest,
        session_id="session.shadow-label",
        session_sequence=31,
        initial_candidate_digest="sha256:initial",
        risk_profile_digest="sha256:risk",
        visible_evidence_digest="sha256:visible",
        active_baseline_result_digest=_baseline().observation_digest,
        baseline_snapshot_digest="sha256:baseline-snapshot",
        usage_estimation_policy_version=policy.version,
        usage_estimation_policy_digest=policy.policy_digest,
    )
