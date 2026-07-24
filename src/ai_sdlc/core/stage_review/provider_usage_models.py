"""Provider 预算结算值及其计量或估算依据。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.resource_models import (
    ResourceAmounts,
    is_complete_provider_actual_usage,
)

_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
UsageSource = Literal["metered", "estimated"]


class ProviderUsageEstimatePolicy(BaseModel):
    model_config = _CONFIG

    schema_version: Literal["provider-usage-estimate-policy.v1"] = (
        "provider-usage-estimate-policy.v1"
    )
    policy_id: str
    version: str
    characters_per_token: float = Field(gt=0)
    estimated_cost_per_token: float = Field(gt=0)
    policy_digest: str

    @model_validator(mode="after")
    def _verify_policy(self) -> Self:
        if any(
            not value.strip() or value != value.strip()
            for value in (self.policy_id, self.version)
        ):
            raise ValueError("provider usage estimate policy identity is invalid")
        expected = canonical_digest(
            self.model_dump(mode="json", exclude={"policy_digest"}),
            CanonicalizationPolicy(),
        )
        if self.policy_digest != expected:
            raise ValueError("provider usage estimate policy digest is invalid")
        return self


class ProviderUsageBasis(BaseModel):
    model_config = _CONFIG

    schema_version: Literal["provider-usage-basis.v1"] = "provider-usage-basis.v1"
    token_source: UsageSource
    cost_source: UsageSource
    active_wall_clock_source: UsageSource
    estimation_policy_id: str = ""
    estimation_policy_version: str = ""
    estimation_policy_digest: str = ""
    input_characters: int = Field(default=0, ge=0)
    output_characters: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _verify_basis(self) -> Self:
        estimated = "estimated" in (
            self.token_source,
            self.cost_source,
            self.active_wall_clock_source,
        )
        policy = (
            self.estimation_policy_id,
            self.estimation_policy_version,
            self.estimation_policy_digest,
        )
        if estimated != all(value.strip() for value in policy):
            raise ValueError("provider usage estimation policy lineage is incomplete")
        if estimated != (self.input_characters + self.output_characters > 0):
            raise ValueError("provider usage estimation inputs are incomplete")
        return self


class AccountedProviderUsage(BaseModel):
    """用于预算结算的用量；Basis 明确区分实测值与可比估算值。"""

    model_config = _CONFIG

    schema_version: Literal["accounted-provider-usage.v1"] = (
        "accounted-provider-usage.v1"
    )
    amounts: ResourceAmounts
    basis: ProviderUsageBasis

    @model_validator(mode="after")
    def _verify_amounts(self) -> Self:
        if not is_complete_provider_actual_usage(self.amounts):
            raise ValueError("provider accounted usage is incomplete")
        return self


def build_usage_estimate_policy(
    *,
    policy_id: str,
    version: str,
    characters_per_token: float,
    estimated_cost_per_token: float,
) -> ProviderUsageEstimatePolicy:
    values = {
        "schema_version": "provider-usage-estimate-policy.v1",
        "policy_id": policy_id,
        "version": version,
        "characters_per_token": float(characters_per_token),
        "estimated_cost_per_token": float(estimated_cost_per_token),
    }
    return ProviderUsageEstimatePolicy(
        schema_version="provider-usage-estimate-policy.v1",
        policy_id=policy_id,
        version=version,
        characters_per_token=float(characters_per_token),
        estimated_cost_per_token=float(estimated_cost_per_token),
        policy_digest=canonical_digest(values, CanonicalizationPolicy()),
    )


def metered_provider_usage(amounts: ResourceAmounts) -> AccountedProviderUsage:
    return AccountedProviderUsage(
        amounts=amounts,
        basis=ProviderUsageBasis(
            token_source="metered",
            cost_source="metered",
            active_wall_clock_source="metered",
        ),
    )


def accounted_usage_from_payload(payload: object) -> AccountedProviderUsage | None:
    try:
        return AccountedProviderUsage.model_validate(payload)
    except ValueError:
        return None


__all__ = [
    "AccountedProviderUsage",
    "ProviderUsageBasis",
    "ProviderUsageEstimatePolicy",
    "accounted_usage_from_payload",
    "build_usage_estimate_policy",
    "metered_provider_usage",
]
