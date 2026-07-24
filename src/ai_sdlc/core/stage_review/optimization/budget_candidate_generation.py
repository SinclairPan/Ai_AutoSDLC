"""从冻结终态与真实资源用量生成有界 Budget Policy 候选。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from math import ceil
from typing import cast

from ai_sdlc.core.stage_review.artifact_compat import JsonValue
from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.candidate_dataset import (
    CandidateDatasetView,
)
from ai_sdlc.core.stage_review.optimization.datasets import DatasetPopulationEntry
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationPatchOperation,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import OptimizationSnapshot
from ai_sdlc.core.stage_review.resource_builders import stable_id

_FIELDS = (
    "hard_provider_calls",
    "hard_review_passes",
    "hard_tokens",
    "hard_wall_clock",
    "maximum_slots",
)
_MINIMUM_STRATUM_SESSIONS = 10


def budget_candidates(
    baseline: OptimizationSnapshot,
    dataset: CandidateDatasetView,
) -> tuple[OptimizationCandidate, ...]:
    if baseline.snapshot_digest != dataset.baseline_snapshot_digest:
        raise SharedStateIntegrityError("budget candidate baseline lineage diverged")
    policies = _budget_policies(baseline)
    ceilings = _policy_values(policies, "high")
    values = tuple(
        candidate
        for key, entries in _strata(dataset.population).items()
        if (candidate := _candidate(dataset, policies, ceilings, key, entries))
        is not None
    )
    return tuple(sorted(values, key=lambda item: item.candidate_digest))


def _candidate(
    dataset: CandidateDatasetView,
    policies: Mapping[str, object],
    ceilings: Mapping[str, object],
    key: tuple[str, str, str, tuple[str, ...]],
    entries: tuple[DatasetPopulationEntry, ...],
) -> OptimizationCandidate | None:
    exhausted = tuple(
        item for item in entries if item.terminal_outcome == "hard_budget_exhausted"
    )
    if len(entries) < _MINIMUM_STRATUM_SESSIONS or not exhausted:
        return None
    stage, risk, size, providers = key
    current = _policy_values(policies, risk)
    replacements = _increased_values(current, ceilings)
    if not replacements:
        return None
    operations = tuple(
        OptimizationPatchOperation(
            operation="replace",
            field_path=f"budget_policy.{risk}.{name}",
            value=cast(JsonValue, value),
        )
        for name, value in sorted(replacements.items())
    )
    stratum = ":".join((stage, risk, size, "+".join(providers)))
    evidence = _evidence_refs(dataset, entries)
    return OptimizationCandidate(
        candidate_id=stable_id(
            "optimization-budget-candidate", _baseline_key(dataset), stratum
        ),
        candidate_domain="budget",
        base_snapshot_digest=dataset.baseline_snapshot_digest,
        patch_operations=operations,
        expected_effect="reduce observed hard budget exhaustion within packaged ceilings",
        rollback_target=dataset.baseline_snapshot_digest,
        generator_identity="generator.deterministic-budget-v1",
        generator_provider_id="provider.local-deterministic",
        attribution_digests=(),
        metric_evidence_digests=(dataset.view_digest,),
        target_stratum_ids=(stratum,),
        dataset_partition_refs=("train",),
        estimated_provider_calls=0,
        estimated_tokens=0,
        estimated_cost=0,
        estimated_active_wall_clock=0,
        evidence_refs=evidence,
    )


def _baseline_key(dataset: CandidateDatasetView) -> str:
    return stable_id(
        "budget-candidate-input",
        dataset.baseline_snapshot_digest,
        dataset.view_digest,
    )


def _budget_policies(snapshot: OptimizationSnapshot) -> Mapping[str, object]:
    values = snapshot.policy_payload.get("budget_policy")
    if not isinstance(values, Mapping):
        raise SharedStateIntegrityError("budget candidate policy source is missing")
    return values


def _policy_values(
    policies: Mapping[str, object], risk: str
) -> Mapping[str, object]:
    key = "high" if risk == "critical" else risk
    values = policies.get(key)
    if not isinstance(values, Mapping):
        raise SharedStateIntegrityError("budget candidate risk policy is missing")
    return values


def _increased_values(
    current: Mapping[str, object], ceilings: Mapping[str, object]
) -> dict[str, int | float]:
    replacements: dict[str, int | float] = {}
    for name in _FIELDS:
        old = _number(current.get(name))
        ceiling = _number(ceilings.get(name))
        proposed = old + 1 if name == "maximum_slots" else ceil(old * 1.25)
        replacement = min(proposed, ceiling)
        if replacement > old:
            replacements[name] = replacement
    return replacements


def _number(value: object) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SharedStateIntegrityError("budget candidate numeric policy is invalid")
    return value


def _strata(
    entries: tuple[DatasetPopulationEntry, ...],
) -> dict[tuple[str, str, str, tuple[str, ...]], tuple[DatasetPopulationEntry, ...]]:
    grouped: dict[
        tuple[str, str, str, tuple[str, ...]], list[DatasetPopulationEntry]
    ] = defaultdict(list)
    for item in entries:
        grouped[
            (
                item.stage_key,
                item.risk_level,
                item.candidate_size_bucket,
                item.provider_ids,
            )
        ].append(item)
    return {key: tuple(values) for key, values in sorted(grouped.items())}


def _evidence_refs(
    dataset: CandidateDatasetView,
    entries: tuple[DatasetPopulationEntry, ...],
) -> tuple[str, ...]:
    values = {dataset.source_dataset_digest, dataset.view_digest}
    values.update(
        digest
        for item in entries
        for digest in (*item.observation_digests, *item.label_source_digests)
    )
    return tuple(sorted(values))
