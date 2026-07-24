from __future__ import annotations

import re

import pytest

from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.candidate_dataset import (
    _build_candidate_dataset_view as build_candidate_dataset_view,
)
from ai_sdlc.core.stage_review.optimization.candidate_domain_registry import (
    CandidateDomainAdapterBundle,
    CandidateDomainContract,
    CandidateDomainRegistry,
)
from ai_sdlc.core.stage_review.optimization.candidate_domain_semantics import (
    apply_registered_patch,
    attribution_improved_sessions,
    attribution_report_metrics,
    critical_detection_improved,
    standard_promotion_guard,
    stratum_shadow_matcher,
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
    _baseline_evaluator_contract as baseline_evaluator_contract,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_optimization_snapshot as baseline_optimization_snapshot,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_usage_estimate_policy as baseline_usage_estimate_policy,
)
from ai_sdlc.core.stage_review.optimization.evaluators import EvaluationContext
from ai_sdlc.core.stage_review.optimization.local_evaluation import (
    LocalCandidateEvaluator,
)
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationPatchOperation,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    PipelineSnapshotResult,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import OptimizationSnapshot


def test_new_domain_registers_without_core_branch_and_applies_safely() -> None:
    baseline = _baseline()
    dataset = _dataset(baseline.snapshot_digest)
    attribution = _attribution("custom_policy")
    registry = _registry(("custom_policy",), _custom_generator)
    port = LocalCandidateGenerationPort(
        project_id=dataset.project_id,
        snapshot_source=lambda _: baseline,
        candidate_view_source=lambda _: build_candidate_dataset_view(
            dataset,
            (attribution,),
        ),
        domain_registry=registry,
    )

    candidate = port.generate(
        _epoch(baseline.snapshot_digest),
        PipelineSnapshotResult(
            dataset_digest=dataset.dataset_digest,
            evaluable_session_count=1,
        ),
        family_limit=8,
    ).candidates[0]
    snapshot = CandidatePolicyApplier(registry).apply(
        candidate,
        base_snapshot=baseline,
        attributions=(attribution,),
        evaluation_report_digests=("sha256:report",),
        created_at="2026-07-23T00:00:00Z",
    )
    report = LocalCandidateEvaluator(
        dataset_source=lambda _: dataset,
        attribution_source=lambda: (attribution,),
        domain_registry=registry,
    ).evaluate(
        candidate,
        EvaluationContext(
            dataset_digest=dataset.dataset_digest,
            partition="train",
            evaluation_binding_id="evaluation-binding.custom",
            evaluation_provider_id="provider.local-evaluator",
            provider_capabilities=("local-read-only",),
            resource_reservation_digest="sha256:reservation",
        ),
        baseline_evaluator_contract(registry.domain_ids),
    )

    assert snapshot.policy_payload["custom_policy"] == {"enabled": True}
    assert candidate.domain_adapter_id == "candidate-domain.custom_policy"
    assert candidate.domain_registry_digest == registry.snapshot_digest
    assert report.domain_adapter_digest == candidate.domain_adapter_digest
    assert report.quality_deltas["critical_detection"] == 1


def test_domain_registration_rejects_incomplete_lifecycle_bundle() -> None:
    with pytest.raises(ValueError, match="lifecycle adapter is incomplete"):
        CandidateDomainAdapterBundle(
            adapter_id="candidate-domain.incomplete",
            adapter_version="1.0.0",
            generator=_custom_generator,
        )


def test_family_limit_round_robins_domain_and_stratum_groups() -> None:
    baseline = _baseline()
    dataset = _dataset(baseline.snapshot_digest)
    attributions = (
        _attribution("alpha", suffix="alpha"),
        _attribution("beta", suffix="beta"),
    )
    registry = _registry(("alpha", "beta"), _many_candidates)
    port = LocalCandidateGenerationPort(
        project_id=dataset.project_id,
        snapshot_source=lambda _: baseline,
        candidate_view_source=lambda _: build_candidate_dataset_view(
            dataset,
            attributions,
        ),
        domain_registry=registry,
    )

    result = port.generate(
        _epoch(baseline.snapshot_digest),
        PipelineSnapshotResult(
            dataset_digest=dataset.dataset_digest,
            evaluable_session_count=1,
        ),
        family_limit=2,
    )

    assert {item.candidate_domain for item in result.candidates} == {"alpha", "beta"}


def _registry(
    domains: tuple[str, ...],
    generator,
) -> CandidateDomainRegistry:
    registry = CandidateDomainRegistry()
    for domain in domains:
        registry.register(
            CandidateDomainContract(
                domain_id=domain,
                contract_version="1.0.0",
                lineage_kind="attribution",
                authorized_field_patterns=(re.escape(f"{domain}.enabled"),),
            ),
            CandidateDomainAdapterBundle(
                adapter_id=f"candidate-domain.{domain}",
                adapter_version="1.0.0",
                generator=lambda baseline, dataset, current=domain: generator(
                    baseline,
                    dataset,
                    current,
                ),
                payload_validator=lambda payload, _baseline, current=domain: (
                    _validate_custom(payload, current)
                ),
                patch_applier=apply_registered_patch,
                improvement_evaluator=attribution_improved_sessions,
                report_metrics=attribution_report_metrics,
                shadow_matcher=stratum_shadow_matcher,
                shadow_comparator=critical_detection_improved,
                promotion_guard=standard_promotion_guard,
            ),
        )
    return registry.freeze()


def _custom_generator(
    baseline: OptimizationSnapshot,
    dataset,
    domain: str,
) -> tuple[OptimizationCandidate, ...]:
    return (_candidate(baseline, dataset, domain, domain),)


def _many_candidates(
    baseline: OptimizationSnapshot,
    dataset,
    domain: str,
) -> tuple[OptimizationCandidate, ...]:
    strata = ("stratum.a", "stratum.a", "stratum.z") if domain == "alpha" else (
        "stratum.b",
    )
    return tuple(
        _candidate(baseline, dataset, domain, f"{domain}.{index}", stratum=stratum)
        for index, stratum in enumerate(strata)
    )


def _candidate(
    baseline: OptimizationSnapshot,
    dataset,
    domain: str,
    suffix: str,
    *,
    stratum: str = "stratum.custom",
) -> OptimizationCandidate:
    attribution = next(
        item for item in dataset.attributions if item.candidate_domain == domain
    )
    return OptimizationCandidate(
        candidate_id=f"candidate.{suffix}",
        candidate_domain=domain,
        base_snapshot_digest=baseline.snapshot_digest,
        patch_operations=(
            OptimizationPatchOperation(
                operation="replace",
                field_path=f"{domain}.enabled",
                value=True,
            ),
        ),
        expected_effect="enable a registered custom domain",
        rollback_target=baseline.snapshot_digest,
        generator_identity=f"generator.{domain}",
        generator_provider_id="provider.local-deterministic",
        attribution_digests=(attribution.attribution_digest,),
        target_stratum_ids=(stratum,),
        dataset_partition_refs=("train",),
        estimated_provider_calls=0,
        estimated_tokens=0,
        estimated_cost=0,
        estimated_active_wall_clock=0,
        evidence_refs=(dataset.view_digest,),
    )


def _validate_custom(payload, domain: str) -> None:
    value = payload.get(domain)
    if value != {"enabled": True}:
        raise ValueError("custom policy payload is invalid")


def _baseline() -> OptimizationSnapshot:
    original = baseline_optimization_snapshot("project.registry")
    payload = original.model_dump(mode="json")["policy_payload"]
    for domain in ("custom_policy", "alpha", "beta"):
        payload[domain] = {"enabled": False}
    return OptimizationSnapshot(
        snapshot_id="optimization-snapshot.registry",
        project_id="project.registry",
        policy_payload=payload,
        created_at="2026-07-20T00:00:00Z",
        is_baseline=True,
    )


def _dataset(baseline_digest: str) -> OptimizationDatasetSnapshot:
    usage_policy = baseline_usage_estimate_policy()
    entry = DatasetPopulationEntry(
        session_id="session.train",
        initial_candidate_digest="sha256:candidate",
        stage_key="implementation",
        risk_level="high",
        candidate_size_bucket="medium",
        provider_ids=("provider.codex",),
        active_snapshot_digest=baseline_digest,
        usage_estimation_policy_version=usage_policy.version,
        usage_estimation_policy_digest=usage_policy.policy_digest,
        control_sequence=1,
        committed_at="2026-07-21T00:00:00Z",
        evaluable=True,
        terminal_outcome="consumed",
        observation_digests=("sha256:observation",),
    )
    return OptimizationDatasetSnapshot(
        project_id="project.registry",
        epoch_started_at="2026-07-22T00:00:00Z",
        session_sequence_high_watermark=1,
        trigger_fingerprint="sha256:trigger",
        constitution_digest="sha256:constitution",
        baseline_snapshot_digest=baseline_digest,
        comparison_usage_estimation_policy_version=usage_policy.version,
        comparison_usage_estimation_policy_digest=usage_policy.policy_digest,
        holdout_generation_id="holdout-generation.registry",
        population=(entry,),
        session_population_ids=(entry.session_id,),
        evaluable_session_ids=(entry.session_id,),
        censoring_reasons={},
        partition_assignment={
            "train": (entry.session_id,),
            "validation": (),
            "holdout": (),
            "prospective_shadow": (),
        },
        unknown_or_censored_rate=0,
        leakage_check_passed=True,
        data_integrity_digest=canonical_digest((entry,), CanonicalizationPolicy()),
    )


def _attribution(domain: str, *, suffix: str = "custom") -> FindingAttribution:
    return FindingAttribution(
        attribution_id=f"attribution.{suffix}",
        project_id="project.registry",
        session_id="session.train",
        finding_key=f"finding.{suffix}",
        finding_event_digest=f"sha256:event.{suffix}",
        attribution_evidence_digest=f"sha256:input.{suffix}",
        source_evidence_digest=f"sha256:evidence.{suffix}",
        original_candidate_digest="sha256:candidate",
        discovery_candidate_digest="sha256:discovery",
        initial_cohort_id="cohort.initial",
        discovery_cohort_id="cohort.discovery",
        capability_coverage_digest="sha256:coverage",
        capability_id="capability.custom",
        role_profile_id="role.custom",
        provider_binding_digest="sha256:binding",
        attribution_engine_version="1.0.0",
        policy_digest="sha256:policy",
        primary_cause_id="panel_selection_gap",
        candidate_domain=domain,
        confidence=1,
        status="candidate_authorized",
        reason_code="candidate_domain_authorized",
    )


def _epoch(baseline_digest: str) -> OptimizationEpoch:
    return OptimizationEpoch(
        epoch_id="optimization-epoch.registry",
        project_id="project.registry",
        trigger_fingerprint="sha256:trigger",
        trigger_digest="sha256:trigger-event",
        constitution_digest="sha256:constitution",
        baseline_snapshot_digest=baseline_digest,
        candidate_domain_registry_digest="sha256:registry",
        session_sequence_high_watermark=1,
        new_session_count=1,
        state="generating",
        revision=1,
        started_at="2026-07-22T00:00:00Z",
    )
