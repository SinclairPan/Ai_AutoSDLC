"""远端 Reviewer 的规范化响应合同。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from ai_sdlc.core.stage_review.finding_models import FindingIdentityInput, Severity
from ai_sdlc.core.stage_review.provider_usage_models import AccountedProviderUsage
from ai_sdlc.core.stage_review.resource_models import (
    is_complete_provider_actual_usage,
)
from ai_sdlc.core.stage_review.session_artifact_models import CoverageDeclaration

_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class RemoteReviewFinding(BaseModel):
    model_config = _CONFIG

    identity: FindingIdentityInput
    severity: Severity
    evidence_bundle_digest: str
    capability_id: str


class RemoteReviewOutput(BaseModel):
    model_config = _CONFIG

    schema_version: Literal["remote-review.v1"] = "remote-review.v1"
    verdict: Literal["passed", "findings"]
    coverage: CoverageDeclaration
    findings: tuple[RemoteReviewFinding, ...] = ()
    evidence_digests: tuple[str, ...]

    @model_validator(mode="after")
    def _verify_output(self) -> Self:
        if (self.verdict == "findings") != bool(self.findings):
            raise ValueError("remote review verdict does not match findings")
        if self.evidence_digests != tuple(sorted(set(self.evidence_digests))):
            raise ValueError("remote review evidence must be canonical")
        return self


class RemoteReviewProviderResponse(BaseModel):
    model_config = _CONFIG

    provider_call_id: str
    review: RemoteReviewOutput
    accounted_usage: AccountedProviderUsage

    @model_validator(mode="after")
    def _verify_response(self) -> Self:
        if (
            not self.provider_call_id.strip()
            or self.provider_call_id != self.provider_call_id.strip()
        ):
            raise ValueError("remote provider call identity is invalid")
        if not is_complete_provider_actual_usage(self.accounted_usage.amounts):
            raise ValueError("remote provider usage is incomplete")
        if self.accounted_usage.amounts.review_passes != 1:
            raise ValueError("remote review must settle exactly one review pass")
        return self


__all__ = [
    "RemoteReviewFinding",
    "RemoteReviewOutput",
    "RemoteReviewProviderResponse",
]
