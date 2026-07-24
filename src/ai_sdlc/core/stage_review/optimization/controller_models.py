"""离线优化 Constitution、Trigger、Epoch 与维护结果合同。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.registry_versions import (
    require_machine_id,
    require_version,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts

EpochState = Literal[
    "queued",
    "snapshotting",
    "generating",
    "replaying",
    "holdout_evaluating",
    "shadow_observing",
    "evaluating",
    "promoting",
    "pausing",
    "paused",
    "retry_wait",
    "safety_pending",
    "promoted",
    "no_change",
    "failed",
]
MaintenanceResultCode = Literal[
    "not_ready",
    "advanced",
    "paused",
    "promoted",
    "no_change",
    "failed",
]


class MaintenanceBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    maximum_provider_calls: int = Field(default=2, ge=1, le=2)
    maximum_tokens: int = Field(default=100_000, ge=1, le=100_000)
    maximum_cost: float = Field(default=2, gt=0, le=2)
    maximum_active_wall_clock: float = Field(default=300, gt=0, le=300)
    maximum_parallelism: int = Field(default=1, ge=1, le=1)


class OptimizationConstitution(ArtifactCompatibility):
    schema_version: Literal["optimization-constitution.v1"] = (
        "optimization-constitution.v1"
    )
    artifact_kind: Literal["optimization-constitution"] = "optimization-constitution"
    constitution_version: str
    epoch_budget_policy_digest: str
    attribution_policy_digest: str
    evaluator_registry_digest: str
    auto_promotion_policy_digest: str
    storage_policy_digest: str
    candidate_domain_registry_digest: str
    minimum_created_sessions: int = Field(default=30, ge=1)
    minimum_evaluable_sessions: int = Field(default=20, ge=1)
    holdout_ratio: float = Field(default=0.2, gt=0, lt=1)
    minimum_holdout_sessions: int = Field(default=10, ge=1)
    minimum_shadow_sessions: int = Field(default=10, ge=1)
    minimum_shadow_days: int = Field(default=14, ge=1)
    candidate_family_limit: int = Field(default=8, ge=1)
    no_change_new_session_cooldown: int = Field(default=10, ge=1)
    promotion_new_session_cooldown: int = Field(default=10, ge=1)
    promotion_day_cooldown: int = Field(default=7, ge=1)
    familywise_alpha: float = Field(default=0.05, gt=0, lt=1)
    constitution_digest: str = ""

    @field_validator("constitution_version")
    @classmethod
    def _version_is_supported(cls, value: str) -> str:
        return require_version(value)

    @model_validator(mode="after")
    def _verify_constitution(self) -> Self:
        if not self.candidate_domain_registry_digest.strip():
            raise ValueError("candidate domain registry digest is required")
        if self.minimum_evaluable_sessions > self.minimum_created_sessions:
            raise ValueError("evaluable baseline cannot exceed created baseline")
        return fill_artifact_digest(self, "constitution_digest")


class OptimizationTriggerEvent(ArtifactCompatibility):
    schema_version: Literal["optimization-trigger-event.v1"] = (
        "optimization-trigger-event.v1"
    )
    artifact_kind: Literal["optimization-trigger-event"] = "optimization-trigger-event"
    trigger_id: str
    project_id: str
    session_sequence_high_watermark: int = Field(ge=0)
    trigger_fingerprint: str
    constitution_digest: str
    baseline_snapshot_digest: str
    candidate_domain_registry_digest: str
    trigger_facts: tuple[str, ...]
    trigger_fact_digests: tuple[str, ...] = ()
    new_session_count: int = Field(ge=0)
    triggered: bool
    trigger_digest: str = ""

    @field_validator("trigger_id", "project_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "optimization trigger identity")

    @model_validator(mode="after")
    def _verify_trigger(self) -> Self:
        if self.trigger_facts != tuple(sorted(set(self.trigger_facts))):
            raise ValueError("optimization trigger facts must be canonical")
        if self.trigger_fact_digests != tuple(
            sorted(set(self.trigger_fact_digests))
        ):
            raise ValueError("optimization trigger fact digests must be canonical")
        return fill_artifact_digest(self, "trigger_digest")


class OptimizationEpoch(ArtifactCompatibility):
    schema_version: Literal["optimization-epoch.v1"] = "optimization-epoch.v1"
    artifact_kind: Literal["optimization-epoch"] = "optimization-epoch"
    epoch_id: str
    project_id: str
    trigger_fingerprint: str
    trigger_digest: str
    constitution_digest: str
    baseline_snapshot_digest: str
    candidate_domain_registry_digest: str
    session_sequence_high_watermark: int = Field(ge=0)
    new_session_count: int = Field(ge=0)
    state: EpochState
    revision: int = Field(ge=1)
    previous_epoch_digest: str = ""
    reservation_id: str = ""
    reservation_fencing_token: int = Field(default=0, ge=0)
    dataset_digest: str = ""
    finalist_candidate_digest: str = ""
    failure_reason: str = ""
    resume_state: EpochState | None = None
    lease_fencing_epoch: int = Field(default=0, ge=0)
    started_at: str = ""
    terminal_at: str = ""
    cumulative_usage: ResourceAmounts = Field(default_factory=ResourceAmounts)
    epoch_digest: str = ""

    @field_validator("epoch_id", "project_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "optimization epoch identity")

    @model_validator(mode="after")
    def _verify_epoch(self) -> Self:
        if not self.candidate_domain_registry_digest.strip():
            raise ValueError("optimization epoch requires candidate domain registry")
        if self.revision == 1 and self.previous_epoch_digest:
            raise ValueError("initial epoch cannot have previous digest")
        if self.revision > 1 and not self.previous_epoch_digest:
            raise ValueError("advanced epoch requires previous digest")
        if self.started_at:
            parse_utc(self.started_at)
        if self.terminal_at:
            parse_utc(self.terminal_at)
        return fill_artifact_digest(self, "epoch_digest")


class OptimizationEpochLeaseClaim(ArtifactCompatibility):
    schema_version: Literal["optimization-epoch-lease-claim.v1"] = (
        "optimization-epoch-lease-claim.v1"
    )
    artifact_kind: Literal["optimization-epoch-lease-claim"] = (
        "optimization-epoch-lease-claim"
    )
    epoch_id: str
    owner_id: str
    fencing_epoch: int = Field(ge=1)
    acquired_at: str
    expires_at: str
    previous_claim_digest: str = ""
    claim_digest: str = ""

    @field_validator("epoch_id", "owner_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "optimization lease identity")

    @model_validator(mode="after")
    def _verify_claim(self) -> Self:
        if parse_utc(self.expires_at) <= parse_utc(self.acquired_at):
            raise ValueError("optimization lease expiry must follow acquisition")
        return fill_artifact_digest(self, "claim_digest")


class OptimizationEpochLeaseRelease(ArtifactCompatibility):
    schema_version: Literal["optimization-epoch-lease-release.v1"] = (
        "optimization-epoch-lease-release.v1"
    )
    artifact_kind: Literal["optimization-epoch-lease-release"] = (
        "optimization-epoch-lease-release"
    )
    release_id: str
    epoch_id: str
    owner_id: str
    fencing_epoch: int = Field(ge=1)
    claim_digest: str
    released_at: str
    release_digest: str = ""

    @field_validator("release_id", "epoch_id", "owner_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "optimization lease release identity")

    @model_validator(mode="after")
    def _verify_release(self) -> Self:
        parse_utc(self.released_at)
        return fill_artifact_digest(self, "release_digest")


class OptimizationStepResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    next_state: EpochState
    reason: str = ""
    dataset_digest: str = ""
    finalist_candidate_digest: str = ""


class OptimizationMaintenanceResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result_code: MaintenanceResultCode
    epoch: OptimizationEpoch | None = None
    reason: str = ""
