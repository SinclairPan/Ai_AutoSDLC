"""Optimization Commit Fencing 的不可变 Claim 与压缩索引工件。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.registry_versions import require_machine_id
from ai_sdlc.core.stage_review.resource_builders import parse_utc


class OptimizationCommitLeaseClaim(ArtifactCompatibility):
    schema_version: Literal["optimization-commit-lease-claim.v1"] = (
        "optimization-commit-lease-claim.v1"
    )
    artifact_kind: Literal["optimization-commit-lease-claim"] = (
        "optimization-commit-lease-claim"
    )
    project_id: str
    owner_id: str
    scope: str
    fencing_epoch: int = Field(ge=1)
    expected_head: str
    acquired_at: str
    expires_at: str
    previous_claim_digest: str = ""
    claim_digest: str = ""

    @field_validator("project_id", "owner_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "optimization commit lease identity")

    @field_validator("scope", "expected_head")
    @classmethod
    def _value_is_present(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("optimization commit lease scope is invalid")
        return value

    @model_validator(mode="after")
    def _verify_claim(self) -> Self:
        acquired = parse_utc(self.acquired_at)
        expires = parse_utc(self.expires_at)
        duration = (expires - acquired).total_seconds()
        if duration <= 0 or duration > 2:
            raise ValueError("optimization commit lease must expire within two seconds")
        if self.fencing_epoch == 1 and self.previous_claim_digest:
            raise ValueError("first commit lease claim cannot have predecessor")
        if self.fencing_epoch > 1 and not self.previous_claim_digest:
            raise ValueError("commit lease claim predecessor is required")
        return fill_artifact_digest(self, "claim_digest")


class OptimizationCommitLeaseSegment(ArtifactCompatibility):
    schema_version: Literal["optimization-commit-lease-segment.v1"] = (
        "optimization-commit-lease-segment.v1"
    )
    artifact_kind: Literal["optimization-commit-lease-segment"] = (
        "optimization-commit-lease-segment"
    )
    first_sequence: int = Field(ge=1)
    last_sequence: int = Field(ge=1)
    first_previous_claim_digest: str
    last_claim_digest: str
    relative_path: str
    payload_digest: str
    descriptor_digest: str = ""

    @model_validator(mode="after")
    def _verify_segment(self) -> Self:
        if self.last_sequence < self.first_sequence:
            raise ValueError("commit lease segment range is invalid")
        if not self.last_claim_digest or not self.payload_digest:
            raise ValueError("commit lease segment digest is missing")
        if self.relative_path.startswith(("/", "\\")) or ".." in self.relative_path:
            raise ValueError("commit lease segment path is invalid")
        return fill_artifact_digest(self, "descriptor_digest")


class OptimizationCommitLeaseCheckpoint(ArtifactCompatibility):
    schema_version: Literal["optimization-commit-lease-checkpoint.v1"] = (
        "optimization-commit-lease-checkpoint.v1"
    )
    artifact_kind: Literal["optimization-commit-lease-checkpoint"] = (
        "optimization-commit-lease-checkpoint"
    )
    project_id: str
    compacted_through: int = Field(ge=0)
    compacted_claim_digest: str = ""
    segments: tuple[OptimizationCommitLeaseSegment, ...] = ()
    checkpoint_digest: str = ""

    @model_validator(mode="after")
    def _verify_checkpoint(self) -> Self:
        expected = 1
        previous = ""
        for segment in self.segments:
            if (
                segment.first_sequence != expected
                or segment.first_previous_claim_digest != previous
            ):
                raise ValueError("commit lease checkpoint segment chain diverged")
            expected = segment.last_sequence + 1
            previous = segment.last_claim_digest
        if self.compacted_through != expected - 1:
            raise ValueError("commit lease checkpoint range diverged")
        if self.compacted_claim_digest != previous:
            raise ValueError("commit lease checkpoint head diverged")
        return fill_artifact_digest(self, "checkpoint_digest")


__all__ = [
    "OptimizationCommitLeaseCheckpoint",
    "OptimizationCommitLeaseClaim",
    "OptimizationCommitLeaseSegment",
]
