"""生成会被真实运行时消费的治理策略候选。"""

from __future__ import annotations

from collections.abc import Mapping

from ai_sdlc.core.stage_review.binding_models import IndependenceGrade
from ai_sdlc.core.stage_review.binding_policy import BindingPolicy
from ai_sdlc.core.stage_review.capability_mapping import CapabilityMappingPolicy
from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.candidate_dataset import (
    CandidateDatasetView,
)
from ai_sdlc.core.stage_review.optimization.datasets import DatasetPopulationEntry
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationPatchOperation,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import OptimizationSnapshot
from ai_sdlc.core.stage_review.registry import default_registry_bundle
from ai_sdlc.core.stage_review.registry_models import ReviewerRoleModule
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.role_profiles import RoleProfilePolicy, role_profile_id

_GRADES: tuple[IndependenceGrade, ...] = (
    "session_independent",
    "provider_independent",
    "model_diversity_proven",
)


def binding_candidates(
    baseline: OptimizationSnapshot,
    dataset: CandidateDatasetView,
) -> tuple[OptimizationCandidate, ...]:
    policy = _binding_policy(baseline)
    next_grade = _next_grade(policy.minimum_blocking_independence_grade)
    if next_grade is None:
        return ()
    population = {item.session_id: item for item in dataset.population}
    values = (
        _binding_candidate(baseline, dataset, item, population[item.session_id], next_grade)
        for item in dataset.attributions
        if item.candidate_domain == "binding"
        and item.primary_cause_id == "provider_quality_gap"
        and item.session_id in population
    )
    return tuple(sorted(values, key=lambda item: item.candidate_digest))


def role_profile_candidates(
    baseline: OptimizationSnapshot,
    dataset: CandidateDatasetView,
) -> tuple[OptimizationCandidate, ...]:
    modules = default_registry_bundle().role_modules
    profiles = _role_profiles(baseline)
    population = {item.session_id: item for item in dataset.population}
    values: list[OptimizationCandidate] = []
    for item in dataset.attributions:
        if item.candidate_domain != "role_profile" or item.session_id not in population:
            continue
        composition = _profile_composition(item.role_profile_id, profiles, modules)
        alternative = _alternative_module(item.capability_id, composition, modules)
        if composition is None or alternative is None:
            continue
        composed = tuple(sorted({*composition, alternative.module_digest}))
        values.append(
            _role_candidate(
                baseline,
                dataset,
                item,
                population[item.session_id],
                profiles,
                composition,
                composed,
            )
        )
    return tuple(sorted(values, key=lambda item: item.candidate_digest))


def capability_mapping_candidates(
    baseline: OptimizationSnapshot,
    dataset: CandidateDatasetView,
) -> tuple[OptimizationCandidate, ...]:
    current = CapabilityMappingPolicy.model_validate(
        baseline.policy_payload.get("capability_mapping")
    )
    packaged = default_registry_bundle().registry.registry_digest
    if current.registry_digest == packaged:
        return ()
    population = {item.session_id: item for item in dataset.population}
    values = (
        _capability_mapping_candidate(
            baseline, dataset, item, population[item.session_id], packaged
        )
        for item in dataset.attributions
        if item.candidate_domain == "capability_mapping"
        and item.session_id in population
    )
    return tuple(sorted(values, key=lambda item: item.candidate_digest))


def _binding_candidate(
    baseline: OptimizationSnapshot,
    dataset: CandidateDatasetView,
    attribution: FindingAttribution,
    entry: DatasetPopulationEntry,
    grade: IndependenceGrade,
) -> OptimizationCandidate:
    stratum = _stratum(entry)
    return OptimizationCandidate(
        candidate_id=stable_id(
            "optimization-binding-candidate",
            baseline.snapshot_digest,
            attribution.attribution_digest,
        ),
        candidate_domain="binding",
        base_snapshot_digest=baseline.snapshot_digest,
        patch_operations=(
            OptimizationPatchOperation(
                operation="replace",
                field_path="binding_policy.minimum_blocking_independence_grade",
                value=grade,
            ),
        ),
        expected_effect="raise independent provider evidence for blocking reviewers",
        rollback_target=baseline.snapshot_digest,
        generator_identity="generator.deterministic-binding-v1",
        generator_provider_id="provider.local-deterministic",
        attribution_digests=(attribution.attribution_digest,),
        target_stratum_ids=(stratum,),
        dataset_partition_refs=("train",),
        estimated_provider_calls=0,
        estimated_tokens=0,
        estimated_cost=0,
        estimated_active_wall_clock=0,
        evidence_refs=tuple(
            sorted(
                {
                    attribution.attribution_evidence_digest,
                    attribution.provider_binding_digest,
                    attribution.source_evidence_digest,
                    dataset.source_dataset_digest,
                    dataset.view_digest,
                }
            )
        ),
    )


def _role_candidate(
    baseline: OptimizationSnapshot,
    dataset: CandidateDatasetView,
    attribution: FindingAttribution,
    entry: DatasetPopulationEntry,
    profiles: RoleProfilePolicy,
    previous: tuple[str, ...],
    composed: tuple[str, ...],
) -> OptimizationCandidate:
    compositions = tuple(
        sorted(composed if item == previous else item for item in profiles.compositions)
    )
    return OptimizationCandidate(
        candidate_id=stable_id(
            "optimization-role-candidate",
            baseline.snapshot_digest,
            attribution.attribution_digest,
        ),
        candidate_domain="role_profile",
        base_snapshot_digest=baseline.snapshot_digest,
        patch_operations=(
            OptimizationPatchOperation(
                operation="replace",
                field_path="role_profiles.compositions",
                value=[list(item) for item in compositions],
            ),
        ),
        expected_effect="compose an independently scoped reviewer profile",
        rollback_target=baseline.snapshot_digest,
        generator_identity="generator.deterministic-role-profile-v1",
        generator_provider_id="provider.local-deterministic",
        attribution_digests=(attribution.attribution_digest,),
        target_stratum_ids=(_stratum(entry),),
        dataset_partition_refs=("train",),
        estimated_provider_calls=0,
        estimated_tokens=0,
        estimated_cost=0,
        estimated_active_wall_clock=0,
        evidence_refs=tuple(
            sorted(
                {
                    attribution.attribution_evidence_digest,
                    attribution.capability_coverage_digest,
                    attribution.source_evidence_digest,
                    dataset.source_dataset_digest,
                    dataset.view_digest,
                }
            )
        ),
    )


def _capability_mapping_candidate(
    baseline: OptimizationSnapshot,
    dataset: CandidateDatasetView,
    attribution: FindingAttribution,
    entry: DatasetPopulationEntry,
    registry_digest: str,
) -> OptimizationCandidate:
    return OptimizationCandidate(
        candidate_id=stable_id(
            "optimization-capability-mapping-candidate",
            baseline.snapshot_digest,
            attribution.attribution_digest,
        ),
        candidate_domain="capability_mapping",
        base_snapshot_digest=baseline.snapshot_digest,
        patch_operations=(OptimizationPatchOperation(
            operation="replace",
            field_path="capability_mapping.registry_digest",
            value=registry_digest,
        ),),
        expected_effect="restore the packaged governed capability registry",
        rollback_target=baseline.snapshot_digest,
        generator_identity="generator.deterministic-capability-mapping-v1",
        generator_provider_id="provider.local-deterministic",
        attribution_digests=(attribution.attribution_digest,),
        target_stratum_ids=(_stratum(entry),),
        dataset_partition_refs=("train",),
        estimated_provider_calls=0,
        estimated_tokens=0,
        estimated_cost=0,
        estimated_active_wall_clock=0,
        evidence_refs=tuple(sorted({
            attribution.attribution_evidence_digest,
            attribution.capability_coverage_digest,
            attribution.source_evidence_digest,
            dataset.source_dataset_digest,
            dataset.view_digest,
        })),
    )
def _binding_policy(snapshot: OptimizationSnapshot) -> BindingPolicy:
    raw = snapshot.policy_payload.get("binding_policy")
    if not isinstance(raw, Mapping):
        raise ValueError("baseline binding policy is missing")
    return BindingPolicy.model_validate(raw)


def _role_profiles(snapshot: OptimizationSnapshot) -> RoleProfilePolicy:
    raw = snapshot.policy_payload.get("role_profiles")
    if not isinstance(raw, Mapping):
        raise ValueError("baseline role profile policy is missing")
    return RoleProfilePolicy.model_validate(raw)


def _profile_composition(
    profile_id: str,
    policy: RoleProfilePolicy,
    modules: tuple[ReviewerRoleModule, ...],
) -> tuple[str, ...] | None:
    return next(
        (
            item
            for item in policy.compositions
            if role_profile_id(item, modules) == profile_id
        ),
        None,
    )


def _alternative_module(
    capability_id: str,
    composition: tuple[str, ...] | None,
    modules: tuple[ReviewerRoleModule, ...],
) -> ReviewerRoleModule | None:
    if composition is None:
        return None
    current = set(composition)
    values = tuple(
        item
        for item in modules
        if item.module_digest not in current and capability_id in item.capability_ids
    )
    return min(values, key=lambda item: item.module_id) if values else None


def _next_grade(value: IndependenceGrade) -> IndependenceGrade | None:
    index = _GRADES.index(value) + 1
    return None if index >= len(_GRADES) else _GRADES[index]


def _stratum(entry: DatasetPopulationEntry) -> str:
    providers = "+".join(entry.provider_ids)
    return ":".join(
        (
            entry.stage_key,
            entry.risk_level,
            entry.candidate_size_bucket,
            providers,
        )
    )


__all__ = [
    "binding_candidates",
    "capability_mapping_candidates",
    "role_profile_candidates",
]
