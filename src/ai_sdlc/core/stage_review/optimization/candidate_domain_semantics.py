"""内置 Candidate Domain 生命周期语义；由注册表组合，不由流水线分支。"""

from __future__ import annotations

from collections.abc import Callable

from ai_sdlc.core.stage_review.artifact_compat import JsonValue
from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.budget_evaluation import (
    _budget_recovered_sessions as budget_recovered_sessions,
)
from ai_sdlc.core.stage_review.optimization.budget_evaluation import (
    _budget_report_metrics as budget_report_metrics,
)
from ai_sdlc.core.stage_review.optimization.candidate_policy import (
    _apply_operation as apply_operation,
)
from ai_sdlc.core.stage_review.optimization.datasets import (
    DatasetPopulationEntry,
    OptimizationDatasetSnapshot,
)
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationEvaluationReport,
    OptimizationPatchOperation,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBinding,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    PipelineShadowResult,
)
from ai_sdlc.core.stage_review.optimization.policy_evaluation import (
    _capability_mapping_applies as capability_mapping_applies,
)
from ai_sdlc.core.stage_review.optimization.policy_evaluation import (
    _role_profile_applies as role_profile_applies,
)
from ai_sdlc.core.stage_review.optimization.policy_evaluation import (
    _selection_applies as selection_applies,
)
from ai_sdlc.core.stage_review.optimization.policy_evaluation import (
    domain_attribution_applies,
    policy_improved_sessions,
)
from ai_sdlc.core.stage_review.optimization.shadow_observations import (
    OptimizationShadowObservation,
)
from ai_sdlc.core.stage_review.optimization.shadow_targeting import (
    matches_selection_candidate,
    matches_stratum_candidate,
)

Applicability = Callable[
    [
        OptimizationCandidate,
        FindingAttribution,
        DatasetPopulationEntry,
        tuple[FindingAttribution, ...],
    ],
    bool,
]


def apply_registered_patch(
    payload: dict[str, JsonValue],
    operations: tuple[OptimizationPatchOperation, ...],
) -> None:
    for operation in operations:
        apply_operation(payload, operation)


def budget_improved_sessions(
    candidate: OptimizationCandidate,
    dataset: OptimizationDatasetSnapshot,
    session_ids: tuple[str, ...],
    sources: tuple[FindingAttribution, ...],
    all_attributions: tuple[FindingAttribution, ...],
) -> tuple[str, ...]:
    del sources, all_attributions
    return budget_recovered_sessions(candidate, dataset, session_ids)


def selection_improved_sessions(
    candidate: OptimizationCandidate,
    dataset: OptimizationDatasetSnapshot,
    session_ids: tuple[str, ...],
    sources: tuple[FindingAttribution, ...],
    all_attributions: tuple[FindingAttribution, ...],
) -> tuple[str, ...]:
    return _policy_improved(
        candidate,
        dataset,
        session_ids,
        sources,
        all_attributions,
        _selection_applicability,
    )


def role_profile_improved_sessions(
    candidate: OptimizationCandidate,
    dataset: OptimizationDatasetSnapshot,
    session_ids: tuple[str, ...],
    sources: tuple[FindingAttribution, ...],
    all_attributions: tuple[FindingAttribution, ...],
) -> tuple[str, ...]:
    return _policy_improved(
        candidate,
        dataset,
        session_ids,
        sources,
        all_attributions,
        _role_profile_applicability,
    )


def capability_mapping_improved_sessions(
    candidate: OptimizationCandidate,
    dataset: OptimizationDatasetSnapshot,
    session_ids: tuple[str, ...],
    sources: tuple[FindingAttribution, ...],
    all_attributions: tuple[FindingAttribution, ...],
) -> tuple[str, ...]:
    return _policy_improved(
        candidate,
        dataset,
        session_ids,
        sources,
        all_attributions,
        _capability_mapping_applicability,
    )


def attribution_improved_sessions(
    candidate: OptimizationCandidate,
    dataset: OptimizationDatasetSnapshot,
    session_ids: tuple[str, ...],
    sources: tuple[FindingAttribution, ...],
    all_attributions: tuple[FindingAttribution, ...],
) -> tuple[str, ...]:
    return _policy_improved(
        candidate,
        dataset,
        session_ids,
        sources,
        all_attributions,
        domain_attribution_applies,
    )


def _policy_improved(
    candidate: OptimizationCandidate,
    dataset: OptimizationDatasetSnapshot,
    session_ids: tuple[str, ...],
    sources: tuple[FindingAttribution, ...],
    all_attributions: tuple[FindingAttribution, ...],
    applicability: Applicability,
) -> tuple[str, ...]:
    return policy_improved_sessions(
        candidate,
        dataset,
        session_ids,
        sources,
        all_attributions,
        applicability,
    )


def _selection_applicability(
    candidate: OptimizationCandidate,
    attribution: FindingAttribution,
    entry: DatasetPopulationEntry,
    sources: tuple[FindingAttribution, ...],
) -> bool:
    del sources
    return selection_applies(candidate, attribution, entry)


def _role_profile_applicability(
    candidate: OptimizationCandidate,
    attribution: FindingAttribution,
    entry: DatasetPopulationEntry,
    sources: tuple[FindingAttribution, ...],
) -> bool:
    del entry
    return role_profile_applies(candidate, attribution, sources)


def _capability_mapping_applicability(
    candidate: OptimizationCandidate,
    attribution: FindingAttribution,
    entry: DatasetPopulationEntry,
    sources: tuple[FindingAttribution, ...],
) -> bool:
    del entry, sources
    return capability_mapping_applies(candidate, attribution)


def attribution_report_metrics(
    candidate: OptimizationCandidate,
    dataset: OptimizationDatasetSnapshot,
    session_ids: tuple[str, ...],
    improved: tuple[str, ...],
    sources: tuple[FindingAttribution, ...],
) -> dict[str, object]:
    return {
        "quality_deltas": {"critical_detection": len(improved) / len(session_ids)},
        "cost_deltas": {"estimated_cost": candidate.estimated_cost},
        "censoring_metrics": {
            "unknown_or_censored": dataset.unknown_or_censored_rate
        },
        "guard_results": {
            "attribution_authorized": bool(sources),
            "partition_nonempty": bool(session_ids),
            "partition_isolated": dataset.leakage_check_passed,
            "quality_observed": bool(improved),
        },
    }


def budget_metrics(
    candidate: OptimizationCandidate,
    dataset: OptimizationDatasetSnapshot,
    session_ids: tuple[str, ...],
    improved: tuple[str, ...],
    sources: tuple[FindingAttribution, ...],
) -> dict[str, object]:
    del sources
    return budget_report_metrics(candidate, dataset, session_ids, improved)


def critical_detection_improved(
    candidate: OptimizationCandidate,
    observation: OptimizationShadowObservation,
) -> bool:
    del candidate
    return (
        observation.challenger.critical_detected
        and not observation.baseline.critical_detected
    )


def budget_shadow_improved(
    candidate: OptimizationCandidate,
    observation: OptimizationShadowObservation,
) -> bool:
    del candidate
    return (
        observation.baseline.terminal_outcome == "hard_budget_exhausted"
        and observation.challenger.terminal_outcome != "hard_budget_exhausted"
    )


def standard_promotion_guard(
    candidate: OptimizationCandidate,
    reports: tuple[OptimizationEvaluationReport, ...],
    shadow: PipelineShadowResult,
) -> dict[str, bool]:
    return {
        "domain_adapter_lineage": bool(reports)
        and shadow.complete
        and all(
            item.candidate_digest == candidate.candidate_digest
            and item.domain_adapter_digest == candidate.domain_adapter_digest
            and item.domain_registry_digest == candidate.domain_registry_digest
            for item in reports
        )
    }


def selection_shadow_matcher(
    binding: CommittedSessionBinding,
    candidate: OptimizationCandidate,
) -> bool:
    return matches_selection_candidate(binding, candidate)


def stratum_shadow_matcher(
    binding: CommittedSessionBinding,
    candidate: OptimizationCandidate,
) -> bool:
    return matches_stratum_candidate(binding, candidate)


__all__ = [
    "apply_registered_patch",
    "attribution_improved_sessions",
    "attribution_report_metrics",
    "budget_improved_sessions",
    "budget_metrics",
    "budget_shadow_improved",
    "capability_mapping_improved_sessions",
    "critical_detection_improved",
    "role_profile_improved_sessions",
    "selection_improved_sessions",
    "selection_shadow_matcher",
    "standard_promotion_guard",
    "stratum_shadow_matcher",
]
