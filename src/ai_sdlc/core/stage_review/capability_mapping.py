"""Optimization Snapshot 中可发布的 Capability Registry 映射。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)


class CapabilityMappingPolicy(ArtifactCompatibility):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["capability-mapping-policy.v1"] = (
        "capability-mapping-policy.v1"
    )
    artifact_kind: Literal["capability-mapping-policy"] = (
        "capability-mapping-policy"
    )
    registry_digest: str
    policy_digest: str = ""

    @model_validator(mode="after")
    def _verify_policy(self) -> Self:
        if not self.registry_digest.strip():
            raise ValueError("capability mapping registry digest is missing")
        return fill_artifact_digest(self, "policy_digest")


__all__ = ["CapabilityMappingPolicy"]
