"""Provider 不可用事实的可信、限时、不可变证明。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, field_validator, model_validator

from ai_sdlc.core.stage_review.binding_digests import provider_availability_digest
from ai_sdlc.core.stage_review.contracts import StageReviewArtifactModel
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id


class ProviderAvailabilityAttestation(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["provider-availability-attestation"] = (
        "provider-availability-attestation"
    )
    attestation_id: str
    plan_digest: str
    previous_binding_set_digest: str
    unavailable_provider_ids: tuple[str, ...]
    source_journal_event_digests: tuple[str, ...]
    attestor_id: str
    attestor_version: str
    evidence_digest: str
    issued_at: str
    expires_at: str
    attestation_digest: str

    @field_validator("issued_at", "expires_at")
    @classmethod
    def _timestamp_is_utc(cls, value: str) -> str:
        parse_utc(value)
        return value

    @model_validator(mode="after")
    def _verify_attestation(self) -> Self:
        _require_sorted_unique(
            self.unavailable_provider_ids,
            self.source_journal_event_digests,
        )
        if not self.unavailable_provider_ids or not self.source_journal_event_digests:
            raise ValueError("provider availability evidence is incomplete")
        if parse_utc(self.expires_at) <= parse_utc(self.issued_at):
            raise ValueError("provider availability expiry is invalid")
        expected_id = stable_id(
            "provider-availability",
            self.plan_digest,
            self.previous_binding_set_digest,
            self.evidence_digest,
        )
        if self.attestation_id != expected_id:
            raise ValueError("provider availability identity is invalid")
        if self.attestation_digest != provider_availability_digest(self):
            raise ValueError("provider availability digest does not match content")
        return self


def _require_sorted_unique(*values: tuple[str, ...]) -> None:
    if any(items != tuple(sorted(set(items))) for items in values):
        raise ValueError("provider availability collection is not canonical")
