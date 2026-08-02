"""使用冻结分区和确认归因执行确定性 Champion-Challenger 比较。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.candidate_domain_registry import (
    CandidateDomainRegistry,
)
from ai_sdlc.core.stage_review.optimization.datasets import OptimizationDatasetSnapshot
from ai_sdlc.core.stage_review.optimization.evaluators import (
    EvaluationContext,
    EvaluatorContract,
    component_runtime_identity,
)
from ai_sdlc.core.stage_review.optimization.models import (
    CandidatePartition,
    OptimizationCandidate,
    OptimizationEvaluationReport,
    OptimizationStatisticalSample,
    OptimizationStatisticsPolicy,
)
from ai_sdlc.core.stage_review.optimization.statistics import (
    _binary_improvement_statistics as binary_improvement_statistics,
)
from ai_sdlc.core.stage_review.optimization.statistics import (
    baseline_statistics_policy,
    resolve_statistics_policy,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id


@dataclass(frozen=True, slots=True)
class _PartitionReportContext:
    partition: CandidatePartition
    evaluator_kind: str
    evaluator_version: str
    evaluator_contract_digest: str
    evaluation_binding_id: str
    holdout_commitment_digest: str = ""
    holdout_test_sequence: int = 0
    holdout_alpha: float = 0
    hypothesis_family_digest: str = ""
    statistics_policy_digest: str = ""
    statistical_alpha: float = 0


class LocalCandidateEvaluator:
    def __init__(
        self,
        *,
        dataset_source: Callable[[str], OptimizationDatasetSnapshot],
        attribution_source: Callable[[], tuple[FindingAttribution, ...]],
        domain_registry: CandidateDomainRegistry | None = None,
        statistics_policy: OptimizationStatisticsPolicy | None = None,
    ) -> None:
        self.dataset_source = dataset_source
        self.attribution_source = attribution_source
        self.domain_registry = domain_registry or _default_domain_registry()
        self.statistics_policy = statistics_policy or baseline_statistics_policy()

    def runtime_identity(self) -> dict[str, object]:
        return {
            "dataset_source": component_runtime_identity(
                self.dataset_source
            ),
            "attribution_source": component_runtime_identity(
                self.attribution_source
            ),
            "domain_registry_digest": self.domain_registry.snapshot_digest,
            "statistics_policy_digest": self.statistics_policy.policy_digest,
        }

    def evaluate(
        self,
        candidate: OptimizationCandidate,
        context: EvaluationContext,
        contract: EvaluatorContract,
    ) -> OptimizationEvaluationReport:
        dataset = self.dataset_source_by_digest(context.dataset_digest)
        policy = resolve_statistics_policy(
            context.statistics_policy_digest,
            configured_policy=self.statistics_policy,
        )
        return _build_partition_report(
            candidate,
            dataset=dataset,
            attributions=self.attribution_source(),
            context=_PartitionReportContext(
                partition=context.partition,
                evaluator_kind=contract.evaluator_kind,
                evaluator_version=contract.evaluator_version,
                evaluator_contract_digest=contract.contract_digest,
                evaluation_binding_id=context.evaluation_binding_id,
                hypothesis_family_digest=context.hypothesis_family_digest,
                statistics_policy_digest=context.statistics_policy_digest,
                statistical_alpha=context.statistical_alpha,
            ),
            domain_registry=self.domain_registry,
            statistics_policy=policy,
        )

    def dataset_source_by_digest(self, digest: str) -> OptimizationDatasetSnapshot:
        dataset = self.dataset_source(digest)
        if dataset.dataset_digest != digest:
            raise ValueError("evaluation dataset lineage diverged")
        return dataset


class LocalEvaluationStatisticsAuthority:
    """独立于 evaluator adapter，从冻结数据集重建统计样本。"""

    def __init__(
        self,
        *,
        dataset_source: Callable[[str], OptimizationDatasetSnapshot],
        attribution_source: Callable[[], tuple[FindingAttribution, ...]],
        domain_registry: CandidateDomainRegistry | None = None,
    ) -> None:
        self.dataset_source = dataset_source
        self.attribution_source = attribution_source
        self.domain_registry = domain_registry or _default_domain_registry()

    def runtime_identity(self) -> dict[str, object]:
        return {
            "dataset_source": component_runtime_identity(
                self.dataset_source
            ),
            "attribution_source": component_runtime_identity(
                self.attribution_source
            ),
            "domain_registry_digest": self.domain_registry.snapshot_digest,
        }

    def sample(
        self,
        candidate: OptimizationCandidate,
        context: EvaluationContext,
    ) -> OptimizationStatisticalSample:
        dataset = self.dataset_source(context.dataset_digest)
        if dataset.dataset_digest != context.dataset_digest:
            raise ValueError("statistics authority dataset lineage diverged")
        attributions = self.attribution_source()
        trusted = _authorized_attributions(candidate, attributions)
        session_ids = dataset.partition_assignment[context.partition]
        improved = self.domain_registry.improved_sessions(
            candidate,
            dataset,
            session_ids,
            trusted,
            attributions,
        )
        return _statistical_sample(
            candidate,
            dataset,
            session_ids,
            improved,
            trusted,
        )


def _build_partition_report(
    candidate: OptimizationCandidate, *,
    dataset: OptimizationDatasetSnapshot,
    attributions: tuple[FindingAttribution, ...],
    context: _PartitionReportContext,
    domain_registry: CandidateDomainRegistry | None = None,
    statistics_policy: OptimizationStatisticsPolicy | None = None,
) -> OptimizationEvaluationReport:
    domains = domain_registry or _default_domain_registry()
    partition = context.partition
    session_ids = dataset.partition_assignment[partition]
    trusted = _authorized_attributions(candidate, attributions)
    improved = domains.improved_sessions(
        candidate, dataset, session_ids, trusted, attributions
    )
    policy = statistics_policy or baseline_statistics_policy()
    if (
        context.statistics_policy_digest
        and context.statistics_policy_digest != policy.policy_digest
    ):
        raise ValueError("evaluation statistics policy lineage diverged")
    sample = _statistical_sample(
        candidate,
        dataset,
        session_ids,
        improved,
        trusted,
    )
    threshold = context.holdout_alpha if partition == "holdout" else 0
    statistical_alpha = (
        context.holdout_alpha
        if partition == "holdout"
        else context.statistical_alpha or policy.shadow_alpha
    )
    p_value, power, lower = _improvement_statistics(
        improved,
        session_ids,
        alpha=statistical_alpha,
        policy=policy,
    )
    eligible = _finalist_eligible(
        partition,
        p_value,
        threshold,
        power,
        lower,
        policy=policy,
    )
    return OptimizationEvaluationReport.model_validate({
        "report_id": stable_id(
            "optimization-evaluation", candidate.candidate_digest, partition
        ),
        "candidate_digest": candidate.candidate_digest,
        "evaluator_kind": context.evaluator_kind,
        "evaluator_version": context.evaluator_version,
        "evaluator_contract_digest": context.evaluator_contract_digest,
        "dataset_digest": dataset.dataset_digest,
        "partition": partition,
        "evaluation_binding_id": context.evaluation_binding_id,
        "domain_contract_digest": candidate.domain_contract_digest,
        "domain_adapter_id": candidate.domain_adapter_id,
        "domain_adapter_version": candidate.domain_adapter_version,
        "domain_adapter_digest": candidate.domain_adapter_digest,
        "domain_registry_digest": candidate.domain_registry_digest,
        **domains.evaluation_metrics(
            candidate, dataset, session_ids, improved, trusted
        ),
        "comparison_session_ids": tuple(sorted(session_ids)),
        "hypothesis_family_digest": (
            context.hypothesis_family_digest
            or _hypothesis_family(candidate, dataset, trusted, context.evaluator_kind)
        ),
        "improved_count": len(improved),
        "sample_count": len(session_ids),
        "statistical_sample_digest": sample.sample_digest,
        "statistics_policy_digest": policy.policy_digest,
        "statistical_alpha": statistical_alpha,
        "raw_p_value": p_value,
        "holm_rank": context.holdout_test_sequence if partition == "holdout" else 0,
        "holm_threshold": threshold,
        "statistical_power": power,
        "effect_confidence_lower": lower,
        "holdout_commitment_digest": context.holdout_commitment_digest,
        "holdout_test_sequence": context.holdout_test_sequence,
        "holdout_alpha": context.holdout_alpha,
        "recommendation": "finalist_eligible" if eligible else "no_change",
    })


def _statistical_sample(
    candidate: OptimizationCandidate,
    dataset: OptimizationDatasetSnapshot,
    session_ids: tuple[str, ...],
    improved: tuple[str, ...],
    attributions: tuple[FindingAttribution, ...],
) -> OptimizationStatisticalSample:
    sources = {
        dataset.data_integrity_digest,
        *candidate.metric_evidence_digests,
        *(item.attribution_digest for item in attributions),
    }
    return OptimizationStatisticalSample(
        candidate_digest=candidate.candidate_digest,
        dataset_digest=dataset.dataset_digest,
        comparison_session_ids=tuple(sorted(session_ids)),
        improved_session_ids=tuple(sorted(improved)),
        source_evidence_digests=tuple(sorted(sources)),
    )


def _improvement_statistics(
    improved: tuple[str, ...],
    session_ids: tuple[str, ...],
    *,
    alpha: float,
    policy: OptimizationStatisticsPolicy,
) -> tuple[float, float, float]:
    return binary_improvement_statistics(
        len(improved),
        len(session_ids),
        alpha=alpha,
        policy=policy,
    )


def _finalist_eligible(
    partition: CandidatePartition,
    p_value: float,
    threshold: float,
    power: float,
    lower: float,
    *,
    policy: OptimizationStatisticsPolicy,
) -> bool:
    return (
        partition == "holdout"
        and p_value <= threshold
        and power >= policy.minimum_statistical_power
        and lower > 0
    )


def _default_domain_registry() -> CandidateDomainRegistry:
    from ai_sdlc.core.stage_review.optimization.candidate_domain_defaults import (
        default_candidate_domain_registry,
    )

    return default_candidate_domain_registry()


def _authorized_attributions(
    candidate: OptimizationCandidate,
    attributions: tuple[FindingAttribution, ...],
) -> tuple[FindingAttribution, ...]:
    by_digest = {item.attribution_digest: item for item in attributions}
    try:
        values = tuple(by_digest[digest] for digest in candidate.attribution_digests)
    except KeyError as exc:
        raise ValueError("candidate attribution is unavailable") from exc
    if any(
        item.status != "candidate_authorized"
        or item.candidate_domain != candidate.candidate_domain
        for item in values
    ):
        raise ValueError("candidate attribution is not authorized")
    return values


def _hypothesis_family(
    candidate: OptimizationCandidate,
    dataset: OptimizationDatasetSnapshot,
    sources: tuple[FindingAttribution, ...],
    evaluator_kind: str,
) -> str:
    return canonical_digest(
        {
            "candidate_domain": candidate.candidate_domain,
            "target_stratum_ids": candidate.target_stratum_ids,
            "dataset_digest": dataset.dataset_digest,
            "evaluator_kind": evaluator_kind,
            "source_causes": sorted(item.primary_cause_id for item in sources),
        },
        CanonicalizationPolicy(),
    )
