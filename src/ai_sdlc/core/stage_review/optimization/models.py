"""唯一 OptimizationCandidate 与 EvaluationReport Schema。"""

from __future__ import annotations

from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    JsonValue,
    fill_artifact_digest,
    freeze_json_mapping,
)
from ai_sdlc.core.stage_review.registry_versions import require_machine_id

CandidateDomain = str
CandidatePartition = Literal["train", "validation", "holdout", "prospective_shadow"]


class OptimizationPatchOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    operation: Literal["add", "replace", "remove"]
    field_path: str
    value: JsonValue = None

    @field_validator("field_path")
    @classmethod
    def _path_is_present(cls, value: str) -> str:
        if not value.strip() or value.startswith(".") or value.endswith("."):
            raise ValueError("optimization patch field path is invalid")
        return value.strip()

    @field_validator("value", mode="after")
    @classmethod
    def _value_is_frozen(cls, value: JsonValue) -> JsonValue:
        return cast(JsonValue, freeze_json_mapping({"value": value})["value"])

    @model_validator(mode="after")
    def _remove_has_no_value(self) -> Self:
        if self.operation == "remove" and self.value is not None:
            raise ValueError("remove patch cannot carry a replacement value")
        return self


class OptimizationCandidate(ArtifactCompatibility):
    schema_version: Literal["optimization-candidate.v1"] = "optimization-candidate.v1"
    artifact_kind: Literal["optimization-candidate"] = "optimization-candidate"
    candidate_id: str
    candidate_domain: CandidateDomain
    domain_contract_digest: str = ""
    domain_adapter_id: str = ""
    domain_adapter_version: str = ""
    domain_adapter_digest: str = ""
    domain_registry_digest: str = ""
    base_snapshot_digest: str
    patch_operations: tuple[OptimizationPatchOperation, ...]
    expected_effect: str
    rollback_target: str
    generator_identity: str
    generator_provider_id: str
    attribution_digests: tuple[str, ...]
    metric_evidence_digests: tuple[str, ...] = ()
    target_stratum_ids: tuple[str, ...]
    dataset_partition_refs: tuple[CandidatePartition, ...]
    estimated_provider_calls: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    estimated_cost: float = Field(ge=0)
    estimated_active_wall_clock: float = Field(ge=0)
    evidence_refs: tuple[str, ...]
    candidate_digest: str = ""

    @field_validator("candidate_id", "generator_identity", "candidate_domain")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "optimization candidate identity")

    @field_validator(
        "attribution_digests",
        "metric_evidence_digests",
        "target_stratum_ids",
        "dataset_partition_refs",
        "evidence_refs",
    )
    @classmethod
    def _sets_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("optimization candidate set must be sorted and unique")
        return value

    @model_validator(mode="after")
    def _verify_candidate(self) -> Self:
        if not self.patch_operations or not self.target_stratum_ids:
            raise ValueError("optimization candidate requires a patch")
        if self.dataset_partition_refs != ("train",):
            raise ValueError("optimization candidate may reference train only")
        evidence_kinds = (
            bool(self.metric_evidence_digests),
            bool(self.attribution_digests),
        )
        if evidence_kinds[0] == evidence_kinds[1]:
            raise ValueError("optimization candidate requires one evidence lineage")
        fields = tuple(item.field_path for item in self.patch_operations)
        if fields != tuple(sorted(set(fields))):
            raise ValueError("optimization patch fields must be sorted and unique")
        domain_binding = (
            self.domain_contract_digest,
            self.domain_adapter_id,
            self.domain_adapter_version,
            self.domain_adapter_digest,
            self.domain_registry_digest,
        )
        if any(domain_binding) and not all(domain_binding):
            raise ValueError("candidate domain adapter binding is incomplete")
        return fill_artifact_digest(self, "candidate_digest")
class OptimizationEvaluationReport(ArtifactCompatibility):
    schema_version: Literal["optimization-evaluation-report.v1"] = (
        "optimization-evaluation-report.v1"
    )
    artifact_kind: Literal["optimization-evaluation-report"] = (
        "optimization-evaluation-report"
    )
    report_id: str
    candidate_digest: str
    domain_contract_digest: str
    domain_adapter_id: str
    domain_adapter_version: str
    domain_adapter_digest: str
    domain_registry_digest: str
    evaluator_kind: str
    evaluator_version: str
    dataset_digest: str
    partition: CandidatePartition
    evaluation_binding_id: str
    quality_deltas: dict[str, float]
    cost_deltas: dict[str, float]
    censoring_metrics: dict[str, float]
    guard_results: dict[str, bool]
    comparison_session_ids: tuple[str, ...] = ()
    hypothesis_family_digest: str = ""
    raw_p_value: float = Field(default=1, ge=0, le=1)
    holm_rank: int = Field(default=0, ge=0)
    holm_threshold: float = Field(default=0, ge=0, le=1)
    statistical_power: float = Field(default=0, ge=0, le=1)
    effect_confidence_lower: float = Field(default=0, ge=-1, le=1)
    holdout_commitment_digest: str = ""
    holdout_test_sequence: int = Field(default=0, ge=0)
    holdout_alpha: float = Field(default=0, ge=0, le=1)
    recommendation: Literal["reject", "no_change", "finalist_eligible"]
    report_digest: str = ""

    @field_validator("report_id", "evaluator_kind", "evaluation_binding_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "optimization evaluation identity")

    @field_validator("comparison_session_ids")
    @classmethod
    def _sessions_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("evaluation sessions must be sorted and unique")
        return value

    @model_validator(mode="after")
    def _verify_report(self) -> Self:
        domain_binding = (
            self.domain_contract_digest,
            self.domain_adapter_id,
            self.domain_adapter_version,
            self.domain_adapter_digest,
            self.domain_registry_digest,
        )
        if not all(value.strip() for value in domain_binding):
            raise ValueError("evaluation domain adapter binding is incomplete")
        if not self.guard_results:
            raise ValueError("optimization evaluation requires guard results")
        if self.recommendation == "finalist_eligible" and not all(
            self.guard_results.values()
        ):
            raise ValueError("failed guard cannot recommend a finalist")
        if self.partition == "holdout":
            holdout = (
                self.holdout_commitment_digest,
                self.holdout_test_sequence > 0,
                self.holdout_alpha > 0,
            )
            if not all(holdout):
                raise ValueError("holdout evaluation commitment is incomplete")
        if self.recommendation == "finalist_eligible" and (
            not self.comparison_session_ids
            or not self.hypothesis_family_digest
            or self.raw_p_value > self.holm_threshold
            or self.statistical_power < 0.8
            or self.effect_confidence_lower <= 0
        ):
            raise ValueError("finalist statistical evidence is insufficient")
        return fill_artifact_digest(self, "report_digest")
