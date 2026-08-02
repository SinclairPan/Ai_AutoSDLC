"""固定离线优化流水线的阶段端口与不可变结果。"""

from __future__ import annotations

from collections.abc import Callable
from math import isclose
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.optimization.controller_models import OptimizationEpoch
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationEvaluationReport,
)
from ai_sdlc.core.stage_review.optimization.promotion import (
    AutoPromotionDecision,
    AutoPromotionEvidence,
)
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


class PipelineShadowResult(ArtifactCompatibility):
    schema_version: Literal["pipeline-shadow-result.v1"] = (
        "pipeline-shadow-result.v1"
    )
    artifact_kind: Literal["pipeline-shadow-result"] = "pipeline-shadow-result"

    complete: bool
    evidence_digest: str = ""
    reason: str = ""
    session_ids: tuple[str, ...] = ()
    observation_days: int = Field(default=0, ge=0)
    quality_confidence_lower: float = Field(default=0, ge=-1, le=1)
    metrics: ShadowComparisonMetrics | None = None
    guard_results: dict[str, bool] = Field(default_factory=dict)
    evaluation_binding_id: str = ""
    improved_count: int = Field(default=0, ge=0)
    sample_count: int = Field(default=0, ge=0)
    statistics_policy_digest: str = ""
    statistical_alpha: float = Field(default=0, ge=0, le=1)
    statistical_power: float = Field(default=0, ge=0, le=1)
    shadow_result_digest: str = ""

    @model_validator(mode="after")
    def _verify_completeness(self) -> PipelineShadowResult:
        evidence = (
            self.evidence_digest,
            self.session_ids,
            self.metrics,
            self.guard_results,
            self.evaluation_binding_id,
            self.statistics_policy_digest,
            self.statistical_alpha > 0,
        )
        if self.complete and (self.reason or not all(evidence)):
            raise ValueError("complete shadow result requires full evidence")
        if not self.complete and not self.reason:
            raise ValueError("incomplete shadow result requires a reason")
        if self.complete:
            self._verify_statistical_binding()
        return fill_artifact_digest(self, "shadow_result_digest")

    def _verify_statistical_binding(self) -> None:
        from ai_sdlc.core.stage_review.optimization.statistics import (
            _binary_improvement_statistics,
            statistics_policy_for_digest,
        )

        if (
            self.sample_count != len(self.session_ids)
            or self.improved_count > self.sample_count
        ):
            raise ValueError("shadow statistical sample binding is invalid")
        policy = statistics_policy_for_digest(self.statistics_policy_digest)
        _, expected_power, expected_lower = _binary_improvement_statistics(
            self.improved_count,
            self.sample_count,
            alpha=self.statistical_alpha,
            policy=policy,
        )
        if not (
            isclose(
                self.statistical_power,
                expected_power,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
            and isclose(
                self.quality_confidence_lower,
                expected_lower,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
        ):
            raise ValueError("shadow statistical evidence diverged from policy")


class PipelinePromotionPackage(ArtifactCompatibility):
    schema_version: Literal["pipeline-promotion-package.v1"] = (
        "pipeline-promotion-package.v1"
    )
    artifact_kind: Literal["pipeline-promotion-package"] = (
        "pipeline-promotion-package"
    )

    epoch_id: str
    constitution_digest: str
    decision: AutoPromotionDecision
    evidence: AutoPromotionEvidence
    snapshot: OptimizationSnapshot
    package_digest: str = ""

    @model_validator(mode="after")
    def _verify_package(self) -> Self:
        if not self.epoch_id.strip() or not self.constitution_digest.strip():
            raise ValueError("promotion package epoch lineage is incomplete")
        lineage = (
            self.decision.promotion_evidence_digest == self.evidence.evidence_digest,
            self.decision.baseline_snapshot_digest
            == self.evidence.baseline_snapshot_digest,
            self.decision.challenger_snapshot_digest
            == self.evidence.challenger_snapshot_digest
            == self.snapshot.snapshot_digest,
            self.decision.candidate_digest
            == self.evidence.candidate_digest
            == self.snapshot.candidate_digest,
            self.decision.evaluation_report_digests
            == self.evidence.evaluation_report_digests
            == self.snapshot.evaluation_report_digests,
            self.decision.shadow_result_digest
            == self.evidence.shadow_result_digest
            == self.snapshot.shadow_result_digest,
        )
        if not all(lineage):
            raise ValueError("promotion package lineage is inconsistent")
        return fill_artifact_digest(self, "package_digest")


class PipelinePromotionAuthorization(ArtifactCompatibility):
    schema_version: Literal["pipeline-promotion-authorization.v1"] = (
        "pipeline-promotion-authorization.v1"
    )
    artifact_kind: Literal["pipeline-promotion-authorization"] = (
        "pipeline-promotion-authorization"
    )
    authorization_id: str
    epoch_id: str
    epoch_revision: int = Field(ge=1)
    epoch_digest: str
    constitution_digest: str
    runtime_bundle_manifest_digest: str
    epoch_fencing_epoch: int = Field(ge=1)
    epoch_claim_digest: str
    promotion_package_digest: str
    decision_digest: str
    promotion_evidence_digest: str
    snapshot_digest: str
    shadow_result_digest: str
    evaluation_report_digests: tuple[str, ...]
    evidence_root_digest: str = ""
    authorization_digest: str = ""

    @model_validator(mode="after")
    def _verify_authorization(self) -> Self:
        required = (
            self.authorization_id,
            self.epoch_id,
            self.epoch_digest,
            self.constitution_digest,
            self.runtime_bundle_manifest_digest,
            self.epoch_claim_digest,
            self.promotion_package_digest,
            self.decision_digest,
            self.promotion_evidence_digest,
            self.snapshot_digest,
            self.shadow_result_digest,
        )
        if not all(item.strip() for item in required):
            raise ValueError("promotion authorization lineage is incomplete")
        if not self.evaluation_report_digests:
            raise ValueError("promotion authorization reports are required")
        expected_root = canonical_digest(
            {
                "decision_digest": self.decision_digest,
                "promotion_evidence_digest": self.promotion_evidence_digest,
                "snapshot_digest": self.snapshot_digest,
                "shadow_result_digest": self.shadow_result_digest,
                "evaluation_report_digests": self.evaluation_report_digests,
            },
            CanonicalizationPolicy(),
        )
        if self.evidence_root_digest and self.evidence_root_digest != expected_root:
            raise ValueError("promotion authorization evidence root diverged")
        if not self.evidence_root_digest:
            object.__setattr__(self, "evidence_root_digest", expected_root)
        return fill_artifact_digest(self, "authorization_digest")


class PipelinePublicationResult(ArtifactCompatibility):
    schema_version: Literal["pipeline-publication-result.v1"] = (
        "pipeline-publication-result.v1"
    )
    artifact_kind: Literal["pipeline-publication-result"] = (
        "pipeline-publication-result"
    )
    control_event_digest: str
    operation_id: str
    promotion_package_digest: str
    decision_digest: str
    snapshot_digest: str
    shadow_result_digest: str
    evaluation_report_digests: tuple[str, ...]
    promotion_policy_digest: str
    publication_digest: str = ""

    @model_validator(mode="after")
    def _verify_publication(self) -> Self:
        required = (
            self.control_event_digest,
            self.operation_id,
            self.promotion_package_digest,
            self.decision_digest,
            self.snapshot_digest,
            self.shadow_result_digest,
            self.promotion_policy_digest,
        )
        if not all(item.strip() for item in required):
            raise ValueError("publication result lineage is incomplete")
        if not self.evaluation_report_digests:
            raise ValueError("publication result reports are required")
        return fill_artifact_digest(self, "publication_digest")


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

    def validate_cached(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        report: OptimizationEvaluationReport,
    ) -> None: ...


class ShadowObservationPort(Protocol):
    def observe(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        authorize_effect: Callable[[], None],
        maximum_provider_calls: int = 0,
    ) -> PipelineShadowResult: ...


class PromotionEvaluationPort(Protocol):
    @property
    def policy_digest(self) -> str: ...

    def evaluate(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        reports: tuple[OptimizationEvaluationReport, ...],
        shadow: PipelineShadowResult,
    ) -> PipelinePromotionPackage: ...

    def validate_cached(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        reports: tuple[OptimizationEvaluationReport, ...],
        shadow: PipelineShadowResult,
        package: PipelinePromotionPackage,
    ) -> None: ...


class SnapshotPublicationPort(Protocol):
    def promote(
        self,
        package: PipelinePromotionPackage,
        authorize_effect: Callable[[], None],
    ) -> PipelinePublicationResult: ...

    def validate_cached(
        self,
        package: PipelinePromotionPackage,
        publication: PipelinePublicationResult,
    ) -> None: ...


class PromotionAuthorizationPort(Protocol):
    def issue_promotion_authorization(
        self,
        epoch: OptimizationEpoch,
        package: PipelinePromotionPackage,
        *,
        fencing_epoch: int,
        claim_digest: str,
    ) -> PipelinePromotionAuthorization: ...

    def promotion_authorization(
        self,
        package_digest: str,
    ) -> PipelinePromotionAuthorization | None: ...

    def verify_promotion_authorization(
        self,
        receipt: PipelinePromotionAuthorization,
        package: PipelinePromotionPackage,
    ) -> None: ...
