from __future__ import annotations

import pytest

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.candidate_dataset import (
    _build_candidate_dataset_view as build_candidate_dataset_view,
)
from ai_sdlc.core.stage_review.optimization.candidate_domain_defaults import (
    default_candidate_domain_registry,
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
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationPatchOperation,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    PipelineSnapshotResult,
)


def test_candidate_schema_rejects_prefix_only_field_authorization() -> None:
    baseline = baseline_optimization_snapshot("project.shared")
    attribution = _attribution("session.train", "sha256:eligible")
    candidate = _candidate(
        OptimizationPatchOperation(
            operation="replace",
            field_path="selection_policy.maximum_slots",
            value=2,
        ),
        baseline_digest=baseline.snapshot_digest,
        attribution_digest=attribution.attribution_digest,
    )

    with pytest.raises(ValueError, match="not authorized"):
        CandidatePolicyApplier().apply(
            candidate,
            base_snapshot=baseline,
            attributions=(attribution,),
            evaluation_report_digests=("sha256:report",),
            created_at="2026-07-22T00:00:00Z",
        )


def test_generator_uses_only_train_authorized_attribution() -> None:
    dataset = _dataset()
    eligible = _attribution("session.train", "sha256:eligible")
    validation = _attribution("session.validation", "sha256:validation")
    held_out = _attribution("session.holdout", "sha256:held-out")
    port = LocalCandidateGenerationPort(
        project_id="project.shared",
        snapshot_source=lambda _: baseline_optimization_snapshot("project.shared"),
        candidate_view_source=lambda _: build_candidate_dataset_view(
            dataset, (eligible, validation, held_out)
        ),
    )

    result = port.generate(
        _epoch(),
        PipelineSnapshotResult(
            dataset_digest=dataset.dataset_digest,
            evaluable_session_count=2,
        ),
        family_limit=8,
    )

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.attribution_digests == (eligible.attribution_digest,)
    assert candidate.patch_operations[0].field_path == (
        "selection_policy.capability_requirement_rules"
    )
    rules = candidate.patch_operations[0].value
    assert isinstance(rules, list)
    assert rules[0]["stage_keys"] == ["implementation"]
    assert rules[0]["risk_levels"] == ["high"]
    assert rules[0]["capability_ids"] == ["capability.security"]


def test_candidate_view_physically_excludes_validation_and_holdout() -> None:
    dataset = _dataset()
    eligible = _attribution("session.train", "sha256:eligible")
    validation = _attribution("session.validation", "sha256:validation")
    held_out = _attribution("session.holdout", "sha256:held-out")

    view = build_candidate_dataset_view(dataset, (eligible, validation, held_out))

    payload = view.model_dump(mode="json")
    assert tuple(item.session_id for item in view.population) == ("session.train",)
    assert view.attributions == (eligible,)
    assert "session.validation" not in repr(payload)
    assert "session.holdout" not in repr(payload)


def test_applier_validates_lineage_and_builds_challenger_snapshot() -> None:
    baseline = baseline_optimization_snapshot("project.shared")
    attribution = _attribution("session.train", "sha256:eligible")
    candidate = _candidate(
        OptimizationPatchOperation(
            operation="replace",
            field_path="selection_policy.capability_requirement_rules",
            value=[
                {
                    "rule_id": "optimization.coverage.security",
                    "stage_keys": ["implementation"],
                    "risk_levels": ["high"],
                    "capability_ids": ["capability.security"],
                    "coverage_count": 2,
                }
            ],
        ),
        baseline_digest=baseline.snapshot_digest,
        attribution_digest=attribution.attribution_digest,
    )
    applier = CandidatePolicyApplier()

    snapshot = applier.apply(
        candidate,
        base_snapshot=baseline,
        attributions=(attribution,),
        evaluation_report_digests=("sha256:report",),
        created_at="2026-07-22T00:00:00Z",
    )

    rules = snapshot.policy_payload["selection_policy"][
        "capability_requirement_rules"
    ]
    assert rules[0]["coverage_count"] == 2
    assert snapshot.parent_snapshot_digest == baseline.snapshot_digest
    assert snapshot.stable_fallback_digest == baseline.snapshot_digest
    assert snapshot.candidate_digest == candidate.candidate_digest

    with pytest.raises(SharedStateIntegrityError, match="attribution"):
        applier.apply(
            candidate,
            base_snapshot=baseline,
            attributions=(),
            evaluation_report_digests=("sha256:report",),
            created_at="2026-07-22T00:00:00Z",
        )


def _candidate(
    operation: OptimizationPatchOperation,
    *,
    baseline_digest: str = "sha256:baseline",
    attribution_digest: str = "sha256:attribution",
) -> OptimizationCandidate:
    return OptimizationCandidate(
        candidate_id="candidate.selection-security",
        candidate_domain="selection",
        **default_candidate_domain_registry().candidate_binding("selection"),
        base_snapshot_digest=baseline_digest,
        patch_operations=(operation,),
        expected_effect="restore security capability coverage",
        rollback_target=baseline_digest,
        generator_identity="generator.deterministic-selection-v1",
        generator_provider_id="provider.local-deterministic",
        attribution_digests=(attribution_digest,),
        target_stratum_ids=("implementation:high",),
        dataset_partition_refs=("train",),
        estimated_provider_calls=0,
        estimated_tokens=0,
        estimated_cost=0,
        estimated_active_wall_clock=0,
        evidence_refs=("sha256:evidence",),
    )


def _attribution(session_id: str, digest: str) -> FindingAttribution:
    return FindingAttribution(
        attribution_id=f"attribution.{session_id}",
        project_id="project.shared",
        session_id=session_id,
        finding_key=f"finding.{session_id}",
        finding_event_digest=f"sha256:event.{session_id}",
        attribution_evidence_digest=f"sha256:input.{session_id}",
        source_evidence_digest=digest,
        original_candidate_digest="sha256:original",
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


def _dataset() -> OptimizationDatasetSnapshot:
    baseline_digest = baseline_optimization_snapshot("project.shared").snapshot_digest
    usage_policy = baseline_usage_estimate_policy()
    entries = (
        _entry("session.train", baseline_digest),
        _entry("session.validation", baseline_digest),
        _entry("session.holdout", baseline_digest),
    )
    return OptimizationDatasetSnapshot(
        project_id="project.shared",
        epoch_started_at="2026-07-22T00:00:00Z",
        session_sequence_high_watermark=30,
        trigger_fingerprint="sha256:trigger",
        constitution_digest="sha256:constitution",
        baseline_snapshot_digest=baseline_digest,
        comparison_usage_estimation_policy_version=usage_policy.version,
        comparison_usage_estimation_policy_digest=usage_policy.policy_digest,
        holdout_generation_id="holdout-generation.test",
        population=entries,
        session_population_ids=(
            "session.holdout",
            "session.train",
            "session.validation",
        ),
        evaluable_session_ids=(
            "session.holdout",
            "session.train",
            "session.validation",
        ),
        censoring_reasons={},
        partition_assignment={
            "train": ("session.train",),
            "validation": ("session.validation",),
            "holdout": ("session.holdout",),
            "prospective_shadow": (),
        },
        unknown_or_censored_rate=0,
        leakage_check_passed=True,
        data_integrity_digest=canonical_digest(entries, CanonicalizationPolicy()),
    )


def _entry(session_id: str, baseline_digest: str) -> DatasetPopulationEntry:
    usage_policy = baseline_usage_estimate_policy()
    return DatasetPopulationEntry(
        session_id=session_id,
        initial_candidate_digest="sha256:candidate",
        stage_key="implementation",
        risk_level="high",
        candidate_size_bucket="medium",
        provider_ids=("provider.codex",),
        active_snapshot_digest=baseline_digest,
        usage_estimation_policy_version=usage_policy.version,
        usage_estimation_policy_digest=usage_policy.policy_digest,
        control_sequence=1 if session_id.endswith("train") else 2,
        committed_at="2026-07-21T00:00:00Z",
        evaluable=True,
        terminal_outcome="consumed",
        observation_digests=(f"sha256:observation.{session_id}",),
    )


def _epoch() -> OptimizationEpoch:
    return OptimizationEpoch(
        epoch_id="optimization-epoch.candidate",
        project_id="project.shared",
        trigger_fingerprint="sha256:trigger",
        trigger_digest="sha256:trigger-event",
        constitution_digest="sha256:constitution",
        baseline_snapshot_digest=baseline_optimization_snapshot(
            "project.shared"
        ).snapshot_digest,
        candidate_domain_registry_digest="sha256:registry",
        session_sequence_high_watermark=30,
        new_session_count=30,
        state="generating",
        revision=1,
    )
