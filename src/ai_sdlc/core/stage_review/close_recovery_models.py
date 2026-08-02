"""人工中止后的互斥恢复请求、决策与新证书结果。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.certificate_models import StageCloseCertificate
from ai_sdlc.core.stage_review.close_models import CloseConsumptionClaim
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id

_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
CloseRecoveryKind = Literal["supersede_session", "authorize_new_certificate"]


class StageCloseRecoveryRequest(BaseModel):
    model_config = _CONFIG

    actor_id: str
    idempotency_key: str
    recovery_kind: CloseRecoveryKind
    new_command_id: str = ""
    reason_code: str
    reason: str

    @field_validator("actor_id", "idempotency_key", "reason_code", "reason")
    @classmethod
    def _require_text(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("stage close recovery request field is invalid")
        return value

    @model_validator(mode="after")
    def _validate_target(self) -> Self:
        requires_command = self.recovery_kind == "authorize_new_certificate"
        if requires_command != bool(self.new_command_id.strip()):
            raise ValueError("stage close recovery command binding is invalid")
        if self.new_command_id and self.new_command_id != self.new_command_id.strip():
            raise ValueError("stage close recovery command binding is invalid")
        return self


class StageCloseRecoveryDecision(ArtifactCompatibility):
    model_config = _CONFIG

    schema_version: Literal["stage-close-recovery-decision.v1"] = (
        "stage-close-recovery-decision.v1"
    )
    decision_id: str
    scope: FindingScope
    aborted_claim_id: str
    aborted_claim_digest: str
    aborted_certificate_id: str
    aborted_certificate_digest: str
    aborted_session_revision: int = Field(ge=1)
    aborted_session_digest: str
    recovery_kind: CloseRecoveryKind
    new_command_id: str = ""
    actor_id: str
    idempotency_key: str
    reason_code: str
    reason: str
    authority_id: str
    authority_binding_digest: str
    decided_at: str
    decision_digest: str = ""

    @field_validator(
        "decision_id",
        "aborted_claim_id",
        "aborted_claim_digest",
        "aborted_certificate_id",
        "aborted_certificate_digest",
        "aborted_session_digest",
        "actor_id",
        "idempotency_key",
        "reason_code",
        "reason",
        "authority_id",
        "authority_binding_digest",
    )
    @classmethod
    def _require_binding(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("stage close recovery decision binding is invalid")
        return value

    @model_validator(mode="after")
    def _validate_decision(self) -> Self:
        parse_utc(self.decided_at)
        expected = stable_id(
            "stage-close-recovery-decision",
            self.aborted_claim_digest,
        )
        if self.decision_id != expected:
            raise ValueError("stage close recovery decision identity is invalid")
        requires_command = self.recovery_kind == "authorize_new_certificate"
        if requires_command != bool(self.new_command_id):
            raise ValueError("stage close recovery decision target is invalid")
        return fill_artifact_digest(self, "decision_digest")


class StageCloseReauthorization(BaseModel):
    model_config = _CONFIG

    status: Literal["authorized"] = "authorized"
    decision: StageCloseRecoveryDecision
    certificate: StageCloseCertificate
    claim: CloseConsumptionClaim

    @model_validator(mode="after")
    def _validate_lineage(self) -> Self:
        checks = (
            self.decision.recovery_kind == "authorize_new_certificate",
            self.decision.new_command_id == self.certificate.command_id,
            self.certificate.certificate_id == self.claim.certificate_id,
            self.certificate.certificate_digest == self.claim.certificate_digest,
            self.certificate.command_id == self.claim.command_id,
        )
        if not all(checks):
            raise ValueError("stage close reauthorization lineage is invalid")
        return self
