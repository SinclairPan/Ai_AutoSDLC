"""固定离线优化流水线的阶段端口与不可变结果。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_sdlc.core.stage_review.optimization.controller_models import OptimizationEpoch
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationEvaluationReport,
)
from ai_sdlc.core.stage_review.optimization.promotion import AutoPromotionDecision
from ai_sdlc.core.stage_review.optimization.snapshot_models import OptimizationSnapshot


class PipelineSnapshotResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_digest: str
    evaluable_session_count: int = Field(ge=0)


class CandidateGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidates: tuple[OptimizationCandidate, ...]


class PipelineReplayResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reports: tuple[OptimizationEvaluationReport, ...]
    finalist_candidate_digest: str = ""


class PipelineHoldoutResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report: OptimizationEvaluationReport


class ShadowComparisonMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    critical_detection_delta: float
    late_critical_delta: float
    reviewer_coverage_leak_delta: float
    false_positive_delta: float
    reversal_delta: float
    stage_reopen_delta: float
    needs_user_delta: float
    blocked_delta: float
    timeout_delta: float
    abandon_delta: float
    hard_budget_exhausted_delta: float
    unknown_or_censored_delta: float


class PipelineShadowResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    complete: bool
    evidence_digest: str = ""
    reason: str = ""
    session_ids: tuple[str, ...] = ()
    observation_days: int = Field(default=0, ge=0)
    quality_confidence_lower: float = Field(default=0, ge=-1, le=1)
    metrics: ShadowComparisonMetrics | None = None
    guard_results: dict[str, bool] = Field(default_factory=dict)
    evaluation_binding_id: str = ""

    @model_validator(mode="after")
    def _verify_completeness(self) -> PipelineShadowResult:
        evidence = (
            self.evidence_digest,
            self.session_ids,
            self.metrics,
            self.guard_results,
            self.evaluation_binding_id,
        )
        if self.complete and (self.reason or not all(evidence)):
            raise ValueError("complete shadow result requires full evidence")
        if not self.complete and not self.reason:
            raise ValueError("incomplete shadow result requires a reason")
        return self


class PipelinePromotionPackage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: AutoPromotionDecision
    snapshot: OptimizationSnapshot


class PipelinePublicationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    control_event_digest: str


class DatasetSnapshotPort(Protocol):
    def freeze(
        self, epoch: OptimizationEpoch, authorize_effect: Callable[[], None]
    ) -> PipelineSnapshotResult: ...


class CandidateGenerationPort(Protocol):
    def generate(
        self,
        epoch: OptimizationEpoch,
        dataset: PipelineSnapshotResult,
        family_limit: int,
    ) -> CandidateGenerationResult: ...


class HoldoutEvaluationPort(Protocol):
    def evaluate(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        authorize_effect: Callable[[], None],
    ) -> OptimizationEvaluationReport: ...


class ShadowObservationPort(Protocol):
    def observe(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        authorize_effect: Callable[[], None],
        maximum_provider_calls: int = 0,
    ) -> PipelineShadowResult: ...


class PromotionEvaluationPort(Protocol):
    def evaluate(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        reports: tuple[OptimizationEvaluationReport, ...],
        shadow: PipelineShadowResult,
    ) -> PipelinePromotionPackage: ...


class SnapshotPublicationPort(Protocol):
    def promote(
        self,
        package: PipelinePromotionPackage,
        authorize_effect: Callable[[], None],
    ) -> str: ...
