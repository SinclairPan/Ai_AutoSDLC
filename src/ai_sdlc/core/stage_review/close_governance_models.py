"""Stage Close 人工治理中止请求、Authority Binding 与不可变决策。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id

_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class StageCloseAbortRequest(BaseModel):
    model_config = _CONFIG

    actor_id: str
    idempotency_key: str
    reason_code: str
    reason: str

    @field_validator("actor_id", "idempotency_key", "reason_code", "reason")
    @classmethod
    def _require_text(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("stage close abort request field is invalid")
        return value


class StageCloseGovernanceAuthorityBinding(ArtifactCompatibility):
    model_config = _CONFIG

    schema_version: Literal["stage-close-governance-authority.v1"] = (
        "stage-close-governance-authority.v1"
    )
    project_id: str
    shared_state_binding_id: str
    authority_id: str
    authorized_actor_ids: tuple[str, ...]
    binding_digest: str = ""

    @model_validator(mode="after")
    def _validate_binding(self) -> Self:
        values = (
            self.project_id,
            self.shared_state_binding_id,
            self.authority_id,
        )
        if any(not item.strip() or item != item.strip() for item in values):
            raise ValueError("stage close governance authority identity is invalid")
        actors = self.authorized_actor_ids
        if not actors or actors != tuple(sorted(set(actors))):
            raise ValueError("stage close governance actors must be canonical")
        return fill_artifact_digest(self, "binding_digest")


class StageCloseGovernanceDecision(ArtifactCompatibility):
    model_config = _CONFIG

    schema_version: Literal["stage-close-governance-decision.v1"] = (
        "stage-close-governance-decision.v1"
    )
    decision_id: str
    decision_kind: Literal["abort_stage_close"] = "abort_stage_close"
    scope: FindingScope
    claim_id: str
    claim_digest: str
    certificate_id: str
    certificate_digest: str
    command_id: str
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
        "claim_id",
        "claim_digest",
        "certificate_id",
        "certificate_digest",
        "command_id",
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
            raise ValueError("stage close governance decision binding is invalid")
        return value

    @model_validator(mode="after")
    def _validate_decision(self) -> Self:
        parse_utc(self.decided_at)
        expected_id = stable_id(
            "stage-close-governance-decision",
            self.claim_digest,
        )
        if self.decision_id != expected_id:
            raise ValueError("stage close governance decision identity is invalid")
        return fill_artifact_digest(self, "decision_digest")
