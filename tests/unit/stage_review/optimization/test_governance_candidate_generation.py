from __future__ import annotations

from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.capability_mapping import CapabilityMappingPolicy
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
    _baseline_optimization_snapshot as baseline_optimization_snapshot,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_usage_estimate_policy as baseline_usage_estimate_policy,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    PipelineSnapshotResult,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import OptimizationSnapshot
from ai_sdlc.core.stage_review.registry import default_registry_bundle


def test_provider_quality_gap_generates_real_independence_policy_candidate() -> None:
    baseline = baseline_optimization_snapshot("project.governance")
    dataset = _dataset(baseline.snapshot_digest)
    attribution = _attribution("binding", "provider_quality_gap")

    candidate = _generate(baseline, dataset, attribution)

    assert candidate.candidate_domain == "binding"
    assert candidate.patch_operations[0].field_path == (
        "binding_policy.minimum_blocking_independence_grade"
    )
    assert candidate.patch_operations[0].value == "provider_independent"
    assert candidate.target_stratum_ids == (
        "implementation:high:medium:provider.codex-a+provider.codex-b",
    )


def test_reviewer_miss_generates_composed_role_profile_candidate() -> None:
    baseline = baseline_optimization_snapshot("project.governance")
    dataset = _dataset(baseline.snapshot_digest)
    attribution = _attribution("role_profile", "reviewer_execution_miss")

    candidate = _generate(baseline, dataset, attribution)

    modules = {item.module_id: item for item in default_registry_bundle().role_modules}
    value = candidate.patch_operations[0].value
    assert candidate.candidate_domain == "role_profile"
    assert candidate.patch_operations[0].field_path == "role_profiles.compositions"
    assert isinstance(value, list)
    assert sorted(
        (
            modules["role.security"].module_digest,
            modules["role.trust-boundary-integrity"].module_digest,
        )
    ) in value


def test_authorized_mapping_candidate_restores_packaged_registry() -> None:
    baseline = baseline_optimization_snapshot("project.governance")
    payload = baseline.model_dump(mode="json")["policy_payload"]
    payload["capability_mapping"] = CapabilityMappingPolicy(
        registry_digest="sha256:legacy-registry"
    ).model_dump(mode="json")
    legacy = OptimizationSnapshot(
        snapshot_id="optimization-snapshot.legacy-registry",
        project_id="project.governance",
        policy_payload=payload,
        created_at="2026-07-20T00:00:00Z",
        is_baseline=True,
    )
    dataset = _dataset(legacy.snapshot_digest)
    attribution = _attribution("capability_mapping", "future_mapping_gap")

    candidate = _generate(legacy, dataset, attribution)

    assert candidate.candidate_domain == "capability_mapping"
    assert candidate.patch_operations[0].field_path == (
        "capability_mapping.registry_digest"
    )
    assert candidate.patch_operations[0].value == (
        default_registry_bundle().registry.registry_digest
    )


def _generate(baseline, dataset, attribution):
    port = LocalCandidateGenerationPort(
        project_id=dataset.project_id,
        snapshot_source=lambda _: baseline,
        candidate_view_source=lambda _: build_candidate_dataset_view(
            dataset, (attribution,)
        ),
    )
    result = port.generate(
        _epoch(baseline.snapshot_digest),
        PipelineSnapshotResult(
            dataset_digest=dataset.dataset_digest,
            evaluable_session_count=2,
        ),
        family_limit=8,
    )
    return next(item for item in result.candidates if item.candidate_domain == attribution.candidate_domain)


def _dataset(baseline_digest: str) -> OptimizationDatasetSnapshot:
    usage_policy = baseline_usage_estimate_policy()
    entries = (_entry("session.train", baseline_digest), _entry("session.valid", baseline_digest))
    return OptimizationDatasetSnapshot(
        project_id="project.governance",
        epoch_started_at="2026-07-22T00:00:00Z",
        session_sequence_high_watermark=30,
        trigger_fingerprint="sha256:trigger",
        constitution_digest="sha256:constitution",
        baseline_snapshot_digest=baseline_digest,
        comparison_usage_estimation_policy_version=usage_policy.version,
        comparison_usage_estimation_policy_digest=usage_policy.policy_digest,
        holdout_generation_id="holdout-generation.governance",
        population=entries,
        session_population_ids=("session.train", "session.valid"),
        evaluable_session_ids=("session.train", "session.valid"),
        censoring_reasons={},
        partition_assignment={
            "train": ("session.train",),
            "validation": ("session.valid",),
            "holdout": (),
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
        provider_ids=("provider.codex-a", "provider.codex-b"),
        active_snapshot_digest=baseline_digest,
        usage_estimation_policy_version=usage_policy.version,
        usage_estimation_policy_digest=usage_policy.policy_digest,
        control_sequence=1 if session_id.endswith("train") else 2,
        committed_at="2026-07-21T00:00:00Z",
        evaluable=True,
        terminal_outcome="consumed",
        observation_digests=(f"sha256:observation.{session_id}",),
    )


def _attribution(domain: str, cause: str) -> FindingAttribution:
    return FindingAttribution(
        attribution_id="attribution.provider-gap",
        project_id="project.governance",
        session_id="session.train",
        finding_key="finding.provider-gap",
        finding_event_digest="sha256:event",
        attribution_evidence_digest="sha256:input",
        source_evidence_digest="sha256:evidence",
        original_candidate_digest="sha256:candidate",
        discovery_candidate_digest="sha256:discovery",
        initial_cohort_id="cohort.initial",
        discovery_cohort_id="cohort.discovery",
        capability_coverage_digest="sha256:coverage",
        capability_id="capability.security",
        role_profile_id="role.security",
        provider_binding_digest="sha256:binding",
        attribution_engine_version="1.0.0",
        policy_digest="sha256:policy",
        primary_cause_id=cause,
        candidate_domain=domain,
        confidence=1,
        status="candidate_authorized",
        reason_code="candidate_domain_authorized",
    )


def _epoch(baseline_digest: str) -> OptimizationEpoch:
    return OptimizationEpoch(
        epoch_id="optimization-epoch.governance",
        project_id="project.governance",
        trigger_fingerprint="sha256:trigger",
        trigger_digest="sha256:trigger-event",
        constitution_digest="sha256:constitution",
        baseline_snapshot_digest=baseline_digest,
        candidate_domain_registry_digest="sha256:registry",
        session_sequence_high_watermark=30,
        new_session_count=30,
        state="generating",
        revision=1,
    )
