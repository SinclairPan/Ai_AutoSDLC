"""Optimization Pipeline 的跨工件血缘验证。"""

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.controller_models import OptimizationEpoch
from ai_sdlc.core.stage_review.optimization.models import OptimizationCandidate
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    PipelinePromotionPackage,
    PipelineShadowResult,
)


def _verify_promotion_package(
    epoch: OptimizationEpoch,
    candidate: OptimizationCandidate,
    reports: tuple[str, ...],
    shadow: PipelineShadowResult,
    package: PipelinePromotionPackage,
    *,
    expected_policy_digest: str,
) -> None:
    expected = (
        package.epoch_id == epoch.epoch_id,
        package.constitution_digest == epoch.constitution_digest,
        package.decision.policy_digest == expected_policy_digest,
        package.decision.baseline_snapshot_digest == epoch.baseline_snapshot_digest,
        package.decision.candidate_digest == candidate.candidate_digest,
        package.decision.evaluation_report_digests == reports,
        package.decision.shadow_result_digest == shadow.shadow_result_digest,
        package.decision.promotion_evidence_digest
        == package.evidence.evidence_digest,
        package.decision.challenger_snapshot_digest == package.snapshot.snapshot_digest,
        package.evidence.baseline_snapshot_digest == epoch.baseline_snapshot_digest,
        package.evidence.challenger_snapshot_digest == package.snapshot.snapshot_digest,
        package.evidence.candidate_digest == candidate.candidate_digest,
        package.evidence.evaluation_report_digests == reports,
        package.evidence.shadow_result_digest == shadow.shadow_result_digest,
        package.snapshot.project_id == epoch.project_id,
        package.snapshot.parent_snapshot_digest == epoch.baseline_snapshot_digest,
        package.snapshot.stable_fallback_digest == epoch.baseline_snapshot_digest,
        package.snapshot.candidate_digest == candidate.candidate_digest,
        package.snapshot.evaluation_report_digests == reports,
        package.snapshot.shadow_result_digest == shadow.shadow_result_digest,
    )
    if not all(expected):
        raise SharedStateIntegrityError("promotion package lineage diverged")
