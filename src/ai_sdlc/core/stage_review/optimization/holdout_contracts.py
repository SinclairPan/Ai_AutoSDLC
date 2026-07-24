"""一次性 Holdout 查询、承诺与执行结果合同。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderJournalResult,
    ProviderRecoveryCapabilities,
)
from ai_sdlc.core.stage_review.registry_versions import require_machine_id
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts


class HoldoutQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    epoch_id: str
    hypothesis_digest: str
    holdout_generation_id: str
    baseline_snapshot_digest: str
    finalist_candidate_digest: str
    holdout_session_ids: tuple[str, ...]
    provider_query_idempotency_key: str
    epoch_lease_fencing_epoch: int = Field(ge=1)
    epoch_lease_claim_digest: str

    @field_validator(
        "epoch_id", "holdout_generation_id", "provider_query_idempotency_key"
    )
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "holdout query identity")

    @model_validator(mode="after")
    def _verify_sessions(self) -> Self:
        if not self.holdout_session_ids or self.holdout_session_ids != tuple(
            sorted(set(self.holdout_session_ids))
        ):
            raise ValueError("holdout sessions must be canonical and non-empty")
        return self


class HoldoutQueryCommitment(ArtifactCompatibility):
    schema_version: Literal["holdout-query-commitment.v1"] = (
        "holdout-query-commitment.v1"
    )
    artifact_kind: Literal["holdout-query-commitment"] = "holdout-query-commitment"
    commitment_id: str
    project_id: str
    epoch_id: str
    idempotency_key: str
    hypothesis_digest: str
    holdout_generation_id: str
    baseline_snapshot_digest: str
    finalist_candidate_digest: str
    holdout_session_ids: tuple[str, ...]
    provider_query_idempotency_key: str
    test_sequence: int = Field(ge=1)
    alpha_i: float = Field(gt=0)
    previous_commitment_digest: str = ""
    commit_fencing_epoch: int = Field(ge=1)
    commit_claim_digest: str
    epoch_lease_fencing_epoch: int = Field(ge=1)
    epoch_lease_claim_digest: str
    commitment_digest: str = ""

    @field_validator("commitment_id", "project_id", "epoch_id", "idempotency_key")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "holdout commitment identity")

    @model_validator(mode="after")
    def _verify_commitment(self) -> Self:
        if self.test_sequence == 1 and self.previous_commitment_digest:
            raise ValueError("first holdout commitment cannot have a predecessor")
        if self.test_sequence > 1 and not self.previous_commitment_digest:
            raise ValueError("holdout commitment predecessor is required")
        if not self.commit_claim_digest or not self.epoch_lease_claim_digest:
            raise ValueError("holdout commitment fencing claim is required")
        return fill_artifact_digest(self, "commitment_digest")


class HoldoutProviderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str
    request_digest: str
    anticipated_usage: ResourceAmounts
    capabilities: ProviderRecoveryCapabilities


class HoldoutEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    commitment: HoldoutQueryCommitment
    invocation_result: ProviderJournalResult
