"""先承诺统计预算，再读取 Holdout 标签的本地确定性评估端口。"""

from __future__ import annotations

from collections.abc import Callable

from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.candidate_domain_registry import (
    CandidateDomainRegistry,
)
from ai_sdlc.core.stage_review.optimization.controller_models import OptimizationEpoch
from ai_sdlc.core.stage_review.optimization.datasets import OptimizationDatasetSnapshot
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
)
from ai_sdlc.core.stage_review.optimization.pipeline_effects import (
    _epoch_fencing_identity as epoch_fencing_identity,
)
from ai_sdlc.core.stage_review.optimization.pipeline_effects import (
    commit_effect,
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

    def evaluate(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        authorize_effect: Callable[[], None],
    ) -> OptimizationEvaluationReport:
        dataset = self.dataset_source(epoch.epoch_id)
        fencing_epoch, claim_digest = epoch_fencing_identity(authorize_effect)
        request = _query_request(
            epoch,
            candidate,
            dataset,
            epoch_fencing_epoch=fencing_epoch,
            epoch_claim_digest=claim_digest,
        )
        commitment = commit_effect(authorize_effect, lambda: self.store.commit(request))
        return build_partition_report(
            candidate,
            dataset=dataset,
            attributions=self.attribution_source(),
            context=_PartitionReportContext(
                partition="holdout",
                evaluator_kind="fixed-holdout",
                evaluator_version="1.0.0",
                evaluation_binding_id="evaluation-binding.local-holdout-v1",
                holdout_commitment_digest=commitment.commitment_digest,
                holdout_test_sequence=commitment.test_sequence,
                holdout_alpha=commitment.alpha_i,
            ),
            domain_registry=self.domain_registry,
        )


def _query_request(
    epoch: OptimizationEpoch,
    candidate: OptimizationCandidate,
    dataset: OptimizationDatasetSnapshot,
    *,
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
        provider_query_idempotency_key=stable_id(
            "local-holdout-evaluation", hypothesis
        ),
        epoch_lease_fencing_epoch=epoch_fencing_epoch,
        epoch_lease_claim_digest=epoch_claim_digest,
    )
