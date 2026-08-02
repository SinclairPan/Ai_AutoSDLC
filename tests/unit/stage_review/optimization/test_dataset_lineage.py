from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.datasets import (
    DatasetPolicy,
    freeze_optimization_dataset,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_usage_estimate_policy as baseline_usage_estimate_policy,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBinding,
    OptimizationSessionObservation,
)
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts


def test_dataset_captures_content_addressed_execution_and_label_lineage() -> None:
    attribution = _attribution()
    snapshot = freeze_optimization_dataset(
        project_id="project.optimization",
        bindings=(_binding(),),
        observations=(_observation("created", 1), _observation("consumed", 2)),
        epoch_started_at="2026-07-22T12:00:00Z",
        session_sequence_high_watermark=1,
        trigger_fingerprint="sha256:trigger.lineage",
        constitution_digest="sha256:constitution.1",
        baseline_snapshot_digest="sha256:baseline.1",
        holdout_generation_id="holdout-generation.lineage",
        policy=DatasetPolicy(holdout_ratio=0.2, minimum_holdout_size=1),
        usage_policy_source=lambda _: baseline_usage_estimate_policy(),
        attributions=(attribution,),
    )

    entry = snapshot.population[0]
    assert entry.binding_digests == ("sha256:binding.1",)
    assert entry.role_profile_ids == ("role.security",)
    assert entry.resource_usage.cost == 1
    assert snapshot.finding_event_digests == ("sha256:finding.1",)
    assert snapshot.finding_attribution_digests == (attribution.attribution_digest,)
    assert snapshot.label_source_digests == ("sha256:label.1",)


def _binding() -> CommittedSessionBinding:
    return CommittedSessionBinding(
        project_id="project.optimization",
        session_id="session.1",
        initial_candidate_digest="candidate.1",
        stage_key="implementation",
        risk_level="high",
        candidate_size_bucket="medium",
        provider_ids=("provider.codex",),
        binding_set_digest="sha256:binding-set.1",
        role_profile_ids=("role.security",),
        reviewer_slot_ids=("slot.blocking.1",),
        capability_ids=("security-review",),
        binding_digests=("sha256:binding.1",),
        resource_reservation_digest="sha256:reservation.1",
        active_snapshot_digest="sha256:baseline.1",
        control_sequence=1,
        control_event_digest="sha256:control.1",
        committed_at="2026-07-21T10:00:00Z",
    )


def _observation(kind: str, sequence: int) -> OptimizationSessionObservation:
    terminal = kind == "consumed"
    return OptimizationSessionObservation(
        observation_id=f"observation.session.1.{sequence}",
        project_id="project.optimization",
        session_id="session.1",
        initial_candidate_digest="candidate.1",
        sequence=sequence,
        observation_kind=kind,
        occurred_at=f"2026-07-21T10:0{sequence}:00Z",
        stage_key="implementation",
        risk_level="high",
        candidate_size_bucket="medium",
        provider_ids=("provider.codex",),
        active_snapshot_digest="sha256:baseline.1",
        terminal_reason=kind if terminal else "",
        finding_event_digests=("sha256:finding.1",) if terminal else (),
        risk_profile_digest="sha256:risk.1" if terminal else "",
        cohort_id="cohort.1" if terminal else "",
        finding_ledger_digest="sha256:ledger.1" if terminal else "",
        convergence_outcome_digest="sha256:convergence.1" if terminal else "",
        label_source_digests=("sha256:label.1",) if terminal else (),
        resource_usage=ResourceAmounts(cost=1 if terminal else 0),
    )


def _attribution() -> FindingAttribution:
    return FindingAttribution(
        attribution_id="attribution.1",
        project_id="project.optimization",
        session_id="session.1",
        finding_key="finding.1",
        finding_event_digest="sha256:finding.1",
        attribution_evidence_digest="sha256:attribution-evidence.1",
        source_evidence_digest="sha256:evidence.1",
        original_candidate_digest="candidate.1",
        discovery_candidate_digest="candidate.2",
        initial_cohort_id="cohort.1",
        discovery_cohort_id="cohort.2",
        capability_coverage_digest="sha256:coverage.1",
        capability_id="security-review",
        role_profile_id="role.security",
        provider_binding_digest="sha256:binding.1",
        attribution_engine_version="attribution-engine.v1",
        policy_digest="sha256:policy.1",
        primary_cause_id="panel_selection_gap",
        candidate_domain="selection",
        confidence=1,
        status="candidate_authorized",
        reason_code="candidate_domain_authorized",
    )
