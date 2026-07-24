"""迟到归因与收敛比较的紧凑值对象。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class LateCriticalFinding(BaseModel):
    model_config = _MODEL_CONFIG

    finding_key: str
    original_candidate_digest: str
    discovery_candidate_digest: str
    initial_cohort_id: str
    discovery_cohort_id: str
    evidence_bundle_digest: str
    confirmation_result: str


class ReviewerCoverageLeak(BaseModel):
    model_config = _MODEL_CONFIG

    finding_key: str
    capability_id: str
    role_contract_digest: str
    binding_digest: str
    evidence_bundle_digest: str
    candidate_digest: str
    initial_cohort_id: str
    discovery_cohort_id: str
    plan_digest: str
    binding_set_digest: str
    engine_version: str
    confirmation_result: str
    capability_coverage_digest: str


class FindingAttributionInput(BaseModel):
    model_config = _MODEL_CONFIG

    finding_key: str
    original_candidate_digest: str
    discovery_candidate_digest: str
    initial_cohort_id: str
    discovery_cohort_id: str
    evidence_bundle_digest: str
    capability_id: str
    role_contract_digest: str
    binding_digest: str
    resolver_version: str
    engine_version: str
    confirmation_result: str
    capability_coverage_digest: str
    role_profile_id: str
    provider_binding_digest: str


class FindingCloseability(BaseModel):
    model_config = _MODEL_CONFIG

    closeable: bool
    reason_ids: tuple[str, ...]
    unresolved_finding_keys: tuple[str, ...]


class ProgressSnapshot(ArtifactCompatibility):
    model_config = _MODEL_CONFIG

    schema_version: Literal["progress-snapshot.v1"] = "progress-snapshot.v1"
    comparison_policy_digest: str
    p0_open: int = Field(ge=0)
    required_test_failures: int = Field(ge=0)
    integrity_failures: int = Field(ge=0)
    reopened_or_regressed: int = Field(ge=0)
    p1_open: int = Field(ge=0)
    unreviewed_change: int = Field(ge=0)
    provider_calls: int = Field(ge=0)
    tokens: int = Field(ge=0)
    estimated_cost: float = Field(ge=0)
    active_execution_seconds: float = Field(ge=0)
    snapshot_digest: str = ""

    @model_validator(mode="after")
    def _validate_digest(self) -> ProgressSnapshot:
        return fill_artifact_digest(self, "snapshot_digest")


class ProgressComparison(BaseModel):
    model_config = _MODEL_CONFIG

    outcome: Literal["improved", "same", "regressed", "uncomparable"]
    decisive_dimension: str | None = None
