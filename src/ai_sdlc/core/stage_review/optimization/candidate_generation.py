"""从冻结数据与已授权归因生成有界、确定性的策略候选。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

from ai_sdlc.core.stage_review.artifact_compat import JsonValue
from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.candidate_dataset import (
    CandidateDatasetView,
)
from ai_sdlc.core.stage_review.optimization.candidate_domain_registry import (
    CandidateDomainRegistry,
)
from ai_sdlc.core.stage_review.optimization.controller_models import OptimizationEpoch
from ai_sdlc.core.stage_review.optimization.datasets import (
    DatasetPopulationEntry,
)
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationPatchOperation,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    CandidateGenerationResult,
    PipelineSnapshotResult,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import OptimizationSnapshot
from ai_sdlc.core.stage_review.registry_models import (
    CapabilityRequirementRule,
    RiskLevel,
    StageKey,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id


class LocalCandidateGenerationPort:
    def __init__(
        self,
        *,
        project_id: str,
        snapshot_source: Callable[[str], OptimizationSnapshot],
        candidate_view_source: Callable[[str], CandidateDatasetView],
        domain_registry: CandidateDomainRegistry | None = None,
    ) -> None:
        self.project_id = project_id
        self.snapshot_source = snapshot_source
        self.candidate_view_source = candidate_view_source
        if domain_registry is None:
            from ai_sdlc.core.stage_review.optimization.candidate_domain_defaults import (
                default_candidate_domain_registry,
            )

            domain_registry = default_candidate_domain_registry()
        self.domain_registry = domain_registry

    def generate(
        self,
        epoch: OptimizationEpoch,
        dataset: PipelineSnapshotResult,
        family_limit: int,
    ) -> CandidateGenerationResult:
        frozen = self.candidate_view_source(epoch.epoch_id)
        baseline = self.snapshot_source(epoch.baseline_snapshot_digest)
        if frozen.source_dataset_digest != dataset.dataset_digest:
            raise ValueError("candidate dataset lineage diverged")
        if frozen.project_id != self.project_id:
            raise ValueError("candidate project identity diverged")
        candidates = tuple(sorted(self.domain_registry.generate(baseline, frozen), key=_digest))
        return CandidateGenerationResult(
            candidates=_fair_candidate_subset(candidates, family_limit)
        )


def _digest(candidate: OptimizationCandidate) -> str:
    return candidate.candidate_digest


def selection_candidates(
    baseline: OptimizationSnapshot,
    dataset: CandidateDatasetView,
) -> tuple[OptimizationCandidate, ...]:
    session_map = {item.session_id: item for item in dataset.population}
    return _selection_candidates(
        baseline,
        dataset,
        session_map,
        dataset.attributions,
    )


def _fair_candidate_subset(
    candidates: tuple[OptimizationCandidate, ...],
    family_limit: int,
) -> tuple[OptimizationCandidate, ...]:
    domains: dict[str, dict[str, list[OptimizationCandidate]]] = {}
    for candidate in candidates:
        stratum = candidate.target_stratum_ids[0]
        strata = domains.setdefault(candidate.candidate_domain, {})
        strata.setdefault(stratum, []).append(candidate)
    queues = {
        domain: _round_robin_strata(strata) for domain, strata in domains.items()
    }
    selected: list[OptimizationCandidate] = []
    while len(selected) < family_limit and any(queues.values()):
        for domain in sorted(queues):
            values = queues[domain]
            if values and len(selected) < family_limit:
                selected.append(values.pop(0))
    return tuple(selected)


def _round_robin_strata(
    strata: dict[str, list[OptimizationCandidate]],
) -> list[OptimizationCandidate]:
    ordered: list[OptimizationCandidate] = []
    while any(strata.values()):
        for stratum in sorted(strata):
            values = strata[stratum]
            if values:
                ordered.append(values.pop(0))
    return ordered


def _selection_candidates(
    baseline: OptimizationSnapshot,
    dataset: CandidateDatasetView,
    session_map: Mapping[str, DatasetPopulationEntry],
    attributions: tuple[FindingAttribution, ...],
) -> tuple[OptimizationCandidate, ...]:
    existing = _existing_rules(baseline)
    values: list[OptimizationCandidate] = []
    for attribution in sorted(attributions, key=lambda item: item.attribution_digest):
        if attribution.candidate_domain != "selection" or not attribution.capability_id:
            continue
        entry = session_map[attribution.session_id]
        stage = cast(StageKey, entry.stage_key)
        risk = cast(RiskLevel, entry.risk_level)
        rule = CapabilityRequirementRule(
            rule_id=stable_id(
                "optimization-coverage-rule", stage, risk, attribution.capability_id
            ),
            stage_keys=(stage,),
            risk_levels=(risk,),
            capability_ids=(attribution.capability_id,),
            coverage_count=2,
        )
        if rule.rule_id in {str(item.get("rule_id", "")) for item in existing}:
            continue
        rules = tuple(
            sorted(
                (*existing, rule.model_dump(mode="json")),
                key=lambda item: str(item["rule_id"]),
            )
        )
        values.append(
            _selection_candidate(
                baseline, dataset, attribution, rules, target_stratum=f"{stage}:{risk}"
            )
        )
    return tuple(sorted(values, key=lambda item: item.candidate_digest))


def _existing_rules(snapshot: OptimizationSnapshot) -> tuple[dict[str, object], ...]:
    selection = snapshot.policy_payload.get("selection_policy")
    if not isinstance(selection, Mapping):
        raise ValueError("baseline selection policy is missing")
    raw = selection.get("capability_requirement_rules")
    if not isinstance(raw, list):
        raise ValueError("baseline capability rules are invalid")
    return tuple(dict(item) for item in raw if isinstance(item, Mapping))


def _selection_candidate(
    baseline: OptimizationSnapshot,
    dataset: CandidateDatasetView,
    attribution: FindingAttribution,
    rules: tuple[dict[str, object], ...],
    *,
    target_stratum: str,
) -> OptimizationCandidate:
    return OptimizationCandidate(
        candidate_id=stable_id(
            "optimization-candidate", baseline.snapshot_digest, attribution.attribution_digest
        ),
        candidate_domain="selection",
        base_snapshot_digest=baseline.snapshot_digest,
        patch_operations=(
            OptimizationPatchOperation(
                operation="replace",
                field_path="selection_policy.capability_requirement_rules",
                value=cast(JsonValue, list(rules)),
            ),
        ),
        expected_effect="restore attributed capability coverage",
        rollback_target=baseline.snapshot_digest,
        generator_identity="generator.deterministic-selection-v1",
        generator_provider_id="provider.local-deterministic",
        attribution_digests=(attribution.attribution_digest,),
        target_stratum_ids=(target_stratum,),
        dataset_partition_refs=("train",),
        estimated_provider_calls=0,
        estimated_tokens=0,
        estimated_cost=0,
        estimated_active_wall_clock=0,
        evidence_refs=tuple(
            sorted(
                {
                    attribution.attribution_evidence_digest,
                    attribution.finding_event_digest,
                    attribution.source_evidence_digest,
                    dataset.source_dataset_digest,
                    dataset.view_digest,
                }
            )
        ),
    )
