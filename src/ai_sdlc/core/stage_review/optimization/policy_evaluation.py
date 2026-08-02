"""按 Candidate 的真实策略语义匹配历史与未来可比 Session。"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ai_sdlc.core.stage_review.capability_mapping import CapabilityMappingPolicy
from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.datasets import (
    DatasetPopulationEntry,
    OptimizationDatasetSnapshot,
)
from ai_sdlc.core.stage_review.optimization.models import OptimizationCandidate
from ai_sdlc.core.stage_review.registry import default_registry_bundle
from ai_sdlc.core.stage_review.role_profiles import RoleProfilePolicy


def policy_improved_sessions(
    candidate: OptimizationCandidate,
    dataset: OptimizationDatasetSnapshot,
    session_ids: tuple[str, ...],
    sources: tuple[FindingAttribution, ...],
    all_attributions: tuple[FindingAttribution, ...],
    applicability: Callable[
        [OptimizationCandidate, FindingAttribution, DatasetPopulationEntry, tuple[FindingAttribution, ...]],
        bool,
    ],
) -> tuple[str, ...]:
    population = {item.session_id: item for item in dataset.population}
    signatures = _source_signatures(sources, population)
    matches = {
        item.session_id
        for item in all_attributions
        if item.session_id in session_ids
        and item.session_id in population
        and item.status == "candidate_authorized"
        and _signature(item, population[item.session_id]) in signatures
        and applicability(candidate, item, population[item.session_id], sources)
    }
    return tuple(sorted(matches))


def domain_attribution_applies(
    candidate: OptimizationCandidate,
    attribution: FindingAttribution,
    entry: DatasetPopulationEntry,
    sources: tuple[FindingAttribution, ...],
) -> bool:
    del entry, sources
    return attribution.candidate_domain == candidate.candidate_domain


def _selection_applies(
    candidate: OptimizationCandidate,
    attribution: FindingAttribution,
    entry: DatasetPopulationEntry,
) -> bool:
    value = _patch_value(
        candidate, "selection_policy.capability_requirement_rules"
    )
    if not isinstance(value, list):
        return False
    return any(
        isinstance(rule, Mapping)
        and entry.stage_key in _strings(rule.get("stage_keys"))
        and entry.risk_level in _strings(rule.get("risk_levels"))
        and attribution.capability_id in _strings(rule.get("capability_ids"))
        for rule in value
    )


def _role_profile_applies(
    candidate: OptimizationCandidate,
    attribution: FindingAttribution,
    sources: tuple[FindingAttribution, ...],
) -> bool:
    payload = _role_policy_payload(candidate)
    if payload is None:
        return False
    policy = RoleProfilePolicy.model_validate(payload)
    modules = default_registry_bundle().role_modules
    by_digest = {item.module_digest: item for item in modules}
    source_roles = {item.role_profile_id for item in sources}
    return any(
        len(composition) > 1
        and attribution.capability_id
        in {
            capability
            for digest in composition
            for capability in by_digest[digest].capability_ids
        }
        and bool(source_roles & {by_digest[digest].module_id for digest in composition})
        for composition in policy.compositions
    )


def _capability_mapping_applies(
    candidate: OptimizationCandidate,
    attribution: FindingAttribution,
) -> bool:
    value = _patch_value(candidate, "capability_mapping.registry_digest")
    bundle = default_registry_bundle()
    policy = CapabilityMappingPolicy(registry_digest=str(value))
    capabilities = {item.capability_id for item in bundle.registry.capabilities}
    return (
        policy.registry_digest == bundle.registry.registry_digest
        and attribution.capability_id in capabilities
    )


def _role_policy_payload(candidate: OptimizationCandidate) -> dict[str, object] | None:
    value = _patch_value(candidate, "role_profiles.compositions")
    if not isinstance(value, list):
        return None
    modules = default_registry_bundle().role_modules
    return {
        "module_digests": sorted(item.module_digest for item in modules),
        "compositions": value,
    }


def _patch_value(candidate: OptimizationCandidate, field_path: str) -> object:
    return next(
        (
            item.value
            for item in candidate.patch_operations
            if item.field_path == field_path
        ),
        None,
    )


def _source_signatures(
    sources: tuple[FindingAttribution, ...],
    population: dict[str, DatasetPopulationEntry],
) -> set[tuple[str, str, str, str]]:
    return {
        _signature(item, population[item.session_id])
        for item in sources
        if item.session_id in population
    }


def _signature(
    attribution: FindingAttribution,
    entry: DatasetPopulationEntry,
) -> tuple[str, str, str, str]:
    return (
        attribution.primary_cause_id,
        attribution.capability_id,
        entry.stage_key,
        entry.risk_level,
    )


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value)


__all__ = [
    "domain_attribution_applies",
    "policy_improved_sessions",
]
