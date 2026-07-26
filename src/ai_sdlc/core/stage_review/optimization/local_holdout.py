"""先承诺统计预算，再读取 Holdout 标签的本地确定性评估端口。"""

from __future__ import annotations

from collections.abc import Callable

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.candidate_domain_registry import (
    CandidateDomainRegistry,
)
from ai_sdlc.core.stage_review.optimization.controller_models import OptimizationEpoch
from ai_sdlc.core.stage_review.optimization.datasets import OptimizationDatasetSnapshot
from ai_sdlc.core.stage_review.optimization.evaluators import (
    component_runtime_identity,
    fixed_holdout_evaluator_contract,
)
from ai_sdlc.core.stage_review.optimization.holdout_contracts import HoldoutQueryRequest
from ai_sdlc.core.stage_review.optimization.holdout_store import HoldoutCommitmentStore
from ai_sdlc.core.stage_review.optimization.local_evaluation import (
    _build_partition_report as build_partition_report,
)
from ai_sdlc.core.stage_review.optimization.local_evaluation import (
    _PartitionReportContext,
)
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationEvaluationReport,
    OptimizationStatisticsPolicy,
)
from ai_sdlc.core.stage_review.optimization.pipeline_effects import (
    _epoch_fencing_identity as epoch_fencing_identity,
)
from ai_sdlc.core.stage_review.optimization.pipeline_effects import (
    commit_effect,
)
from ai_sdlc.core.stage_review.optimization.statistics import (
    baseline_statistics_policy,
    resolve_statistics_policy,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id


class LocalHoldoutEvaluationPort:
    def __init__(
        self,
        *,
        store: HoldoutCommitmentStore,
        dataset_source: Callable[[str], OptimizationDatasetSnapshot],
        attribution_source: Callable[[], tuple[FindingAttribution, ...]],
        domain_registry: CandidateDomainRegistry | None = None,
        statistics_policy: OptimizationStatisticsPolicy | None = None,
    ) -> None:
        self.store = store
        self.dataset_source = dataset_source
        self.attribution_source = attribution_source
        if domain_registry is None:
            from ai_sdlc.core.stage_review.optimization.candidate_domain_defaults import (
                default_candidate_domain_registry,
            )

            domain_registry = default_candidate_domain_registry()
        self.domain_registry = domain_registry
        self.statistics_policy = statistics_policy or baseline_statistics_policy()

    def runtime_identity(self) -> dict[str, object]:
        return {
            "store": component_runtime_identity(self.store),
            "dataset_source": component_runtime_identity(
                self.dataset_source
            ),
            "attribution_source": component_runtime_identity(
                self.attribution_source
            ),
            "domain_registry_digest": self.domain_registry.snapshot_digest,
            "statistics_policy_digest": self.statistics_policy.policy_digest,
            "build_partition_report": component_runtime_identity(
                build_partition_report
            ),
            "query_request": component_runtime_identity(_query_request),
            "partition_report_context": component_runtime_identity(
                _PartitionReportContext
            ),
            "fixed_holdout_contract": component_runtime_identity(
                fixed_holdout_evaluator_contract
            ),
            "epoch_fencing_identity": component_runtime_identity(
                epoch_fencing_identity
            ),
            "commit_effect": component_runtime_identity(commit_effect),
            "statistics_resolver": component_runtime_identity(
                resolve_statistics_policy
            ),
        }

    def evaluate(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        authorize_effect: Callable[[], None],
    ) -> OptimizationEvaluationReport:
        dataset = self.dataset_source(epoch.epoch_id)
        policy = resolve_statistics_policy(
            epoch.statistics_policy_digest,
            configured_policy=self.statistics_policy,
        )
        fencing_epoch, claim_digest = epoch_fencing_identity(authorize_effect)
        request = _query_request(
            epoch,
            candidate,
            dataset,
            statistics_policy=policy,
            epoch_fencing_epoch=fencing_epoch,
            epoch_claim_digest=claim_digest,
        )
        commitment = commit_effect(authorize_effect, lambda: self.store.commit(request))
        contract = fixed_holdout_evaluator_contract(self.domain_registry.domain_ids)
        return build_partition_report(
            candidate,
            dataset=dataset,
            attributions=self.attribution_source(),
            context=_PartitionReportContext(
                partition="holdout",
                evaluator_kind="fixed-holdout",
                evaluator_version="1.0.0",
                evaluator_contract_digest=contract.contract_digest,
                evaluation_binding_id="evaluation-binding.local-holdout-v1",
                holdout_commitment_digest=commitment.commitment_digest,
                holdout_test_sequence=commitment.test_sequence,
                holdout_alpha=commitment.alpha_i,
                statistics_policy_digest=policy.policy_digest,
                statistical_alpha=commitment.alpha_i,
            ),
            domain_registry=self.domain_registry,
            statistics_policy=policy,
        )

    def validate_cached(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        report: OptimizationEvaluationReport,
    ) -> None:
        dataset = self.dataset_source(epoch.epoch_id)
        policy = resolve_statistics_policy(
            epoch.statistics_policy_digest,
            configured_policy=self.statistics_policy,
        )
        commitment = self.store.commitment(report.holdout_commitment_digest)
        if commitment is None:
            raise SharedStateIntegrityError(
                "holdout report commitment is unavailable"
            )
        lineage = (
            dataset.dataset_digest == epoch.dataset_digest,
            commitment.project_id == epoch.project_id,
            commitment.epoch_id == epoch.epoch_id,
            commitment.baseline_snapshot_digest == epoch.baseline_snapshot_digest,
            commitment.finalist_candidate_digest == candidate.candidate_digest,
            commitment.holdout_session_ids
            == dataset.partition_assignment["holdout"],
            commitment.statistics_policy_digest == policy.policy_digest,
            commitment.commitment_digest == report.holdout_commitment_digest,
            commitment.test_sequence == report.holdout_test_sequence,
            commitment.alpha_i == report.holdout_alpha,
        )
        if not all(lineage):
            raise SharedStateIntegrityError(
                "holdout commitment lineage diverged"
            )
        contract = fixed_holdout_evaluator_contract(self.domain_registry.domain_ids)
        expected = build_partition_report(
            candidate,
            dataset=dataset,
            attributions=self.attribution_source(),
            context=_PartitionReportContext(
                partition="holdout",
                evaluator_kind=contract.evaluator_kind,
                evaluator_version=contract.evaluator_version,
                evaluator_contract_digest=contract.contract_digest,
                evaluation_binding_id="evaluation-binding.local-holdout-v1",
                holdout_commitment_digest=commitment.commitment_digest,
                holdout_test_sequence=commitment.test_sequence,
                holdout_alpha=commitment.alpha_i,
                statistics_policy_digest=policy.policy_digest,
                statistical_alpha=commitment.alpha_i,
            ),
            domain_registry=self.domain_registry,
            statistics_policy=policy,
        )
        if expected != report:
            raise SharedStateIntegrityError(
                "holdout cached report diverged from authority"
            )


def _query_request(
    epoch: OptimizationEpoch,
    candidate: OptimizationCandidate,
    dataset: OptimizationDatasetSnapshot,
    *,
    statistics_policy: OptimizationStatisticsPolicy,
    epoch_fencing_epoch: int,
    epoch_claim_digest: str,
) -> HoldoutQueryRequest:
    sessions = dataset.partition_assignment["holdout"]
    hypothesis = canonical_digest(
        {
            "baseline_snapshot_digest": epoch.baseline_snapshot_digest,
            "candidate_digest": candidate.candidate_digest,
            "dataset_digest": dataset.dataset_digest,
        },
        CanonicalizationPolicy(),
    )
    return HoldoutQueryRequest(
        epoch_id=epoch.epoch_id,
        hypothesis_digest=hypothesis,
        holdout_generation_id=dataset.holdout_generation_id,
        baseline_snapshot_digest=epoch.baseline_snapshot_digest,
        finalist_candidate_digest=candidate.candidate_digest,
        holdout_session_ids=sessions,
        statistics_policy_digest=statistics_policy.policy_digest,
        holdout_alpha_ledger_id=statistics_policy.holdout_alpha_ledger_id,
        holdout_alpha_ledger_limit=(
            statistics_policy.holdout_alpha_ledger_limit
        ),
        familywise_alpha=statistics_policy.familywise_alpha,
        provider_query_idempotency_key=stable_id(
            "local-holdout-evaluation", hypothesis
        ),
        epoch_lease_fencing_epoch=epoch_fencing_epoch,
        epoch_lease_claim_digest=epoch_claim_digest,
    )
