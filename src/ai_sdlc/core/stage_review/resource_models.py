"""ResourceGovernor 的预算、Reservation、事件与投影合同。"""

from __future__ import annotations

from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.contracts import RiskSeverity, StageReviewArtifactModel

ResourcePool = Literal["foreground", "offline_optimization"]
ReservationState = Literal[
    "admission",
    "final",
    "expired",
    "released",
    "reconciled",
]
BudgetPressure = Literal[
    "within",
    "soft_limit_reached",
    "hard_limit_reached",
]
ReservationResultCode = Literal[
    "reserved",
    "finalized",
    "expanded",
    "renewed",
    "released",
    "recorded",
    "reconciled",
    "authorized",
    "settled",
    "capacity_exhausted",
    "requirement_exceeds_admission",
    "hard_limit_exceeded",
    "invalid_reservation",
    "not_final",
    "stale_fencing",
    "lease_expired",
    "cas_conflict",
    "lock_unavailable",
    "invalid_input",
    "state_corrupt",
]
ResourceEventKind = Literal[
    "admission_reserved",
    "admission_reused",
    "reservation_finalized",
    "reservation_expanded",
    "reservation_renewed",
    "reservation_released",
    "provider_call_authorized",
    "provider_call_settled",
    "provider_call_reconciled",
    "budget_grant_reconciled",
    "usage_recorded",
    "reservation_expired",
    "reservation_reconciled",
]


class ResourceAmounts(BaseModel):
    """可预留和单调计量的独立资源维度。"""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    COUNT_FIELDS: ClassVar[tuple[str, ...]] = (
        "slots",
        "provider_calls",
        "review_passes",
        "tokens",
        "parallelism",
        "role_replans",
        "provider_retries",
        "binding_attempts",
    )
    FLOAT_FIELDS: ClassVar[tuple[str, ...]] = ("cost", "active_wall_clock")
    ALL_FIELDS: ClassVar[tuple[str, ...]] = COUNT_FIELDS + FLOAT_FIELDS

    slots: int = Field(default=0, ge=0)
    provider_calls: int = Field(default=0, ge=0)
    review_passes: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    cost: float = Field(default=0, ge=0)
    active_wall_clock: float = Field(default=0, ge=0)
    parallelism: int = Field(default=0, ge=0)
    role_replans: int = Field(default=0, ge=0)
    provider_retries: int = Field(default=0, ge=0)
    binding_attempts: int = Field(default=0, ge=0)

    def __add__(self, other: ResourceAmounts) -> ResourceAmounts:
        return ResourceAmounts.model_validate(
            {
                name: getattr(self, name) + getattr(other, name)
                for name in self.ALL_FIELDS
            }
        )

    def scaled(self, multiplier: int) -> ResourceAmounts:
        if multiplier < 0:
            raise ValueError("resource multiplier cannot be negative")
        return ResourceAmounts.model_validate(
            {name: getattr(self, name) * multiplier for name in self.ALL_FIELDS}
        )

    def fits_within(self, limit: ResourceAmounts) -> bool:
        return all(
            getattr(self, name) <= getattr(limit, name) for name in self.ALL_FIELDS
        )

    def any_positive(self) -> bool:
        return any(getattr(self, name) > 0 for name in self.ALL_FIELDS)


def is_complete_provider_actual_usage(amounts: ResourceAmounts) -> bool:
    """Provider 实际结算必须保留调用、成本和活跃执行计量。"""

    return (
        amounts.provider_calls == 1
        and amounts.tokens > 0
        and amounts.cost > 0
        and amounts.active_wall_clock > 0
        and amounts.parallelism == 0
    )


class ResourceSoftLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    slots: float = Field(default=0, ge=0)
    provider_calls: float = Field(default=0, ge=0)
    review_passes: float = Field(default=0, ge=0)
    tokens: float = Field(default=0, ge=0)
    cost: float = Field(default=0, ge=0)
    active_wall_clock: float = Field(default=0, ge=0)
    parallelism: float = Field(default=0, ge=0)
    role_replans: float = Field(default=0, ge=0)
    provider_retries: float = Field(default=0, ge=0)
    binding_attempts: float = Field(default=0, ge=0)


class BudgetEnvelope(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    artifact_kind: Literal["reviewer-budget-envelope"] = "reviewer-budget-envelope"
    project_id: str
    work_item_id: str
    stage_review_session_id: str
    risk_level: RiskSeverity
    pool: ResourcePool = "foreground"
    budget_policy_digest: str
    budget_policy_version: str
    hard_limits: ResourceAmounts
    soft_limits: ResourceSoftLimits
    admission_requirement: ResourceAmounts
    envelope_digest: str

    @field_validator(
        "project_id",
        "work_item_id",
        "stage_review_session_id",
        "budget_policy_digest",
        "budget_policy_version",
    )
    @classmethod
    def _identity_is_present(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("budget envelope identity cannot be empty")
        return value

    @model_validator(mode="after")
    def _verify_envelope(self) -> Self:
        from ai_sdlc.core.stage_review.resource_digests import budget_envelope_digest

        if self.admission_requirement != self.hard_limits:
            raise ValueError("admission requirement must reserve the hard envelope")
        expected_soft = ResourceSoftLimits.model_validate(
            {
                name: getattr(self.hard_limits, name) * 0.8
                for name in ResourceAmounts.ALL_FIELDS
            }
        )
        if self.soft_limits != expected_soft:
            raise ValueError("soft limits must equal eighty percent of hard limits")
        if self.envelope_digest != budget_envelope_digest(self):
            raise ValueError("budget envelope digest does not match content")
        return self


class ResourceGovernorConfig(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    artifact_kind: Literal["resource-governor-config"] = "resource-governor-config"
    project_id: str
    foreground_capacity: ResourceAmounts
    offline_optimization_capacity: ResourceAmounts = Field(
        default_factory=ResourceAmounts
    )
    config_digest: str

    @field_validator("project_id")
    @classmethod
    def _project_is_present(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("resource governor project identity cannot be empty")
        return value

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        from ai_sdlc.core.stage_review.resource_digests import resource_config_digest

        if self.config_digest != resource_config_digest(self):
            raise ValueError("resource governor config digest does not match content")
        return self
