"""Finding 初始批次与追加命令合同。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_sdlc.core.stage_review.finding_models import (
    FindingEventType,
    FindingIdentityInput,
    FindingIdentityMapping,
    FindingScope,
    LateFindingOrigin,
    Severity,
)

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class FindingInitialDraft(BaseModel):
    model_config = _MODEL_CONFIG

    identity: FindingIdentityInput
    severity: Severity
    evidence_bundle_digest: str
    actor_id: str
    slot_id: str
    capability_id: str


class FindingInitialBatchCommand(BaseModel):
    model_config = _MODEL_CONFIG

    scope: FindingScope
    command_id: str
    idempotency_key: str
    expected_revision: int = Field(ge=0)
    session_fencing_epoch: int = Field(ge=1)
    candidate_digest: str
    policy_digest: str
    plan_digest: str
    binding_set_digest: str
    initial_review_seal_digest: str
    findings: tuple[FindingInitialDraft, ...]


class FindingLineageAdvanceCommand(BaseModel):
    model_config = _MODEL_CONFIG

    scope: FindingScope
    command_id: str
    idempotency_key: str
    expected_revision: int = Field(ge=0)
    session_fencing_epoch: int = Field(ge=1)
    candidate_digest: str
    policy_digest: str
    plan_digest: str
    binding_set_digest: str
    cohort_id: str
    previous_ledger_digest: str
    session_event_digest: str
    advanced_at: str


class FindingAppendCommand(BaseModel):
    model_config = _MODEL_CONFIG

    scope: FindingScope
    command_id: str
    idempotency_key: str
    expected_revision: int = Field(ge=0)
    session_fencing_epoch: int = Field(ge=1)
    finding_key: str | None = None
    identity: FindingIdentityInput | None = None
    identity_mapping: FindingIdentityMapping | None = None
    event_type: FindingEventType
    actor_id: str
    slot_id: str
    capability_id: str
    candidate_digest: str
    policy_digest: str
    plan_digest: str
    binding_set_digest: str
    evidence_bundle_digest: str
    severity: Severity | None = None
    category: str | None = None
    late_origin: LateFindingOrigin | None = None
    regression_of: str | None = None
    remediation_batch_id: str | None = None
    waiver_id: str | None = None
    waiver_digest: str | None = None
    replacement_keys: tuple[str, ...] = ()
    macro_rebaseline_evidence_digest: str | None = None
    handoff_id: str | None = None
    handoff_resolution: Literal["accepted", "rejected"] | None = None
    target_receipt_digest: str | None = None
    target_scope: FindingScope | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> Self:
        if self.event_type == "discovered" and self.identity is None:
            raise ValueError("discovered event requires identity")
        if self.event_type != "discovered" and self.finding_key is None:
            raise ValueError("finding event requires finding_key")
        return self
