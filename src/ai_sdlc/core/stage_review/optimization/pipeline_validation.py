"""Optimization Pipeline 的跨工件血缘验证。"""

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.controller_models import OptimizationEpoch
from ai_sdlc.core.stage_review.optimization.models import OptimizationCandidate
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    PipelinePromotionPackage,
)


def _verify_promotion_package(
    epoch: OptimizationEpoch,
    candidate: OptimizationCandidate,
    reports: tuple[str, ...],
    package: PipelinePromotionPackage,
) -> None:
    expected = (
        package.decision.baseline_snapshot_digest == epoch.baseline_snapshot_digest,
        package.decision.candidate_digest == candidate.candidate_digest,
        package.decision.evaluation_report_digests == reports,
        package.decision.challenger_snapshot_digest == package.snapshot.snapshot_digest,
        package.snapshot.candidate_digest == candidate.candidate_digest,
        package.snapshot.evaluation_report_digests == reports,
    )
    if not all(expected):
        raise SharedStateIntegrityError("promotion package lineage diverged")
