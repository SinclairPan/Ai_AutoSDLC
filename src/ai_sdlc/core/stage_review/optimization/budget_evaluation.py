"""以冻结资源事实回放 Budget Candidate 的耗尽恢复能力。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.datasets import (
    DatasetPopulationEntry,
    OptimizationDatasetSnapshot,
)
from ai_sdlc.core.stage_review.optimization.models import OptimizationCandidate

_RESOURCE_FIELDS = {
    "hard_provider_calls": "provider_calls",
    "hard_review_passes": "review_passes",
    "hard_tokens": "tokens",
    "hard_wall_clock": "active_wall_clock",
    "maximum_slots": "slots",
}


def _budget_recovered_sessions(
    candidate: OptimizationCandidate,
    dataset: OptimizationDatasetSnapshot,
    session_ids: tuple[str, ...],
) -> tuple[str, ...]:
    limits = _candidate_limits(candidate)
    targets = set(candidate.target_stratum_ids)
    population = {item.session_id: item for item in dataset.population}
    return tuple(
        sorted(
            session_id
            for session_id in session_ids
            if (entry := population[session_id]).terminal_outcome
            == "hard_budget_exhausted"
            and _stratum(entry) in targets
            and _usage_fits(entry, limits)
        )
    )


def _budget_report_metrics(
    candidate: OptimizationCandidate,
    dataset: OptimizationDatasetSnapshot,
    session_ids: tuple[str, ...],
    improved: tuple[str, ...],
) -> dict[str, object]:
    count = len(session_ids)
    authorized = (
        bool(candidate.metric_evidence_digests)
        and dataset.dataset_digest in candidate.evidence_refs
        and not candidate.attribution_digests
    )
    return {
        "quality_deltas": {
            "critical_detection": 0,
            "budget_exhaustion_recovery": len(improved) / count,
        },
        "cost_deltas": {"estimated_cost": candidate.estimated_cost},
        "censoring_metrics": {
            "unknown_or_censored": dataset.unknown_or_censored_rate
        },
        "guard_results": {
            "metric_evidence_authorized": authorized,
            "partition_nonempty": bool(session_ids),
            "partition_isolated": dataset.leakage_check_passed,
            "quality_observed": bool(improved),
        },
    }


def _candidate_limits(candidate: OptimizationCandidate) -> dict[str, float]:
    values: dict[str, float] = {}
    for operation in candidate.patch_operations:
        name = operation.field_path.rsplit(".", 1)[-1]
        if name not in _RESOURCE_FIELDS or isinstance(operation.value, bool):
            raise SharedStateIntegrityError("budget candidate field is unsupported")
        if not isinstance(operation.value, (int, float)):
            raise SharedStateIntegrityError("budget candidate limit is invalid")
        values[_RESOURCE_FIELDS[name]] = float(operation.value)
    if set(values) != set(_RESOURCE_FIELDS.values()):
        raise SharedStateIntegrityError("budget candidate limit set is incomplete")
    return values


def _usage_fits(
    entry: DatasetPopulationEntry, limits: dict[str, float]
) -> bool:
    return all(
        float(getattr(entry.resource_usage, name)) < limit
        for name, limit in limits.items()
    )


def _stratum(entry: DatasetPopulationEntry) -> str:
    return ":".join(
        (
            entry.stage_key,
            entry.risk_level,
            entry.candidate_size_bucket,
            "+".join(entry.provider_ids),
        )
    )
