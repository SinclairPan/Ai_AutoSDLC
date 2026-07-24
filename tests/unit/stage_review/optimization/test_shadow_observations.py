from pathlib import Path

import pytest

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_usage_estimate_policy as baseline_usage_estimate_policy,
)
from ai_sdlc.core.stage_review.optimization.shadow import (
    OptimizationShadowAssignment,
    OptimizationShadowAssignmentStore,
    ShadowSessionInput,
)
from ai_sdlc.core.stage_review.optimization.shadow_observations import (
    OptimizationShadowObservationStore,
    ShadowOutcome,
)
from ai_sdlc.core.stage_review.optimization.shadow_observations import (
    _build_shadow_observation as build_shadow_observation,
)
from ai_sdlc.core.stage_review.provider_usage_models import metered_provider_usage
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts


def test_shadow_observation_is_immutable_and_binds_provider_lineage(
    tmp_path: Path,
) -> None:
    policy = baseline_usage_estimate_policy()
    assignment = OptimizationShadowAssignmentStore(
        tmp_path, project_id="project.shared"
    ).assign(
        epoch_id="optimization-epoch.001",
        finalist_candidate_digest="sha256:finalist",
        session=ShadowSessionInput(
            session_id="session.031",
            session_sequence=31,
            initial_candidate_digest="sha256:initial",
            risk_profile_digest="sha256:risk",
            visible_evidence_digest="sha256:evidence",
            active_baseline_result_digest="sha256:baseline-result",
            baseline_snapshot_digest="sha256:baseline-snapshot",
            usage_estimation_policy_version=policy.version,
            usage_estimation_policy_digest=policy.policy_digest,
        ),
        epoch_session_sequence_high_watermark=30,
    )
    store = OptimizationShadowObservationStore(
        tmp_path, project_id="project.shared"
    )
    observation = build_shadow_observation(
        assignment,
        baseline=ShadowOutcome(terminal_outcome="consumed"),
        challenger=ShadowOutcome(
            critical_detected=True,
            terminal_outcome="consumed",
        ),
        evaluation_binding_id="evaluation-binding.independent",
        evaluation_provider_id="provider.independent",
        provider_invocation_id="provider-invocation.001",
        provider_submission_digest="sha256:submission",
        accounted_usage=_accounted_usage(),
        validation_digest="sha256:validation",
        resource_settlement_event_digest="sha256:settlement",
        label_source_digests=("sha256:label",),
        observed_at="2026-07-23T00:00:00Z",
    )

    assert store.append(observation) == observation
    assert store.read_assignment(assignment.assignment_id) == observation
    divergent = observation.model_copy(
        update={
            "challenger": ShadowOutcome(
                false_positive=True,
                terminal_outcome="consumed",
            ),
            "observation_digest": "",
        }
    )
    with pytest.raises(SharedStateIntegrityError, match="other content"):
        store.append(divergent)


def test_complete_shadow_observation_requires_authority_labels() -> None:
    with pytest.raises(ValueError, match="label source"):
        build_shadow_observation(
            _assignment_fixture(),
            baseline=ShadowOutcome(terminal_outcome="consumed"),
            challenger=ShadowOutcome(terminal_outcome="consumed"),
            evaluation_binding_id="evaluation-binding.independent",
            evaluation_provider_id="provider.independent",
            provider_invocation_id="provider-invocation.001",
            provider_submission_digest="sha256:submission",
            accounted_usage=_accounted_usage(),
            validation_digest="sha256:validation",
            resource_settlement_event_digest="sha256:settlement",
            label_source_digests=(),
            observed_at="2026-07-23T00:00:00Z",
        )


def _assignment_fixture() -> OptimizationShadowAssignment:
    policy = baseline_usage_estimate_policy()
    return OptimizationShadowAssignment(
        assignment_id="optimization-shadow.fixture",
        project_id="project.shared",
        epoch_id="optimization-epoch.001",
        finalist_candidate_digest="sha256:finalist",
        session_id="session.031",
        session_sequence=31,
        initial_candidate_digest="sha256:initial",
        risk_profile_digest="sha256:risk",
        visible_evidence_digest="sha256:evidence",
        active_baseline_result_digest="sha256:baseline-result",
        baseline_snapshot_digest="sha256:baseline-snapshot",
        usage_estimation_policy_version=policy.version,
        usage_estimation_policy_digest=policy.policy_digest,
    )


def _accounted_usage():
    return metered_provider_usage(
        ResourceAmounts(
            provider_calls=1,
            review_passes=1,
            tokens=1,
            cost=0.01,
            active_wall_clock=0.1,
        )
    )
