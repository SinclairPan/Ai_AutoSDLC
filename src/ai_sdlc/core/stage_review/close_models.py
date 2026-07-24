"""阶段关闭 Claim、事件、Receipt、上下文与投影合同。"""

from __future__ import annotations

from math import isfinite
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    JsonValue,
    fill_artifact_digest,
    freeze_json_mapping,
    validate_json_mapping,
)
from ai_sdlc.core.stage_review.canonical import normalize_repo_path
from ai_sdlc.core.stage_review.certificate_models import (
    StageCloseCertificate,
    StageCloseCertificateRequest,
)
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id

_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
CloseEventKind = Literal[
    "prepared",
    "close_written",
    "reconciled",
    "committed",
    "aborted",
]


class CloseArtifactContract(ArtifactCompatibility):
    model_config = _CONFIG

    schema_version: Literal["close-artifact-contract.v1"] = (
        "close-artifact-contract.v1"
    )
    artifact_path: str
    payload: dict[str, JsonValue]
    content_contract_digest: str = ""

    @field_validator("artifact_path")
    @classmethod
    def _normalize_path(cls, value: str) -> str:
        return normalize_repo_path(value)

    @field_validator("payload", mode="before")
    @classmethod
    def _validate_payload(cls, value: object) -> object:
        validate_json_mapping(value)
        return value

    @model_validator(mode="after")
    def _freeze_and_digest(self) -> Self:
        object.__setattr__(self, "payload", freeze_json_mapping(self.payload))
        return fill_artifact_digest(self, "content_contract_digest")


class StageCloseContext(ArtifactCompatibility):
    model_config = _CONFIG

    schema_version: Literal["stage-close-context.v1"] = "stage-close-context.v1"
    certificate: StageCloseCertificate
    certificate_request: StageCloseCertificateRequest
    close_artifact: CloseArtifactContract
    worktree_identity: str
    lease_owner: str
    lease_seconds: float = Field(gt=0)
    final_resource_reservation_digest: str = ""
    resource_reconciliation_digest: str = ""
    fencing_epoch: int = Field(default=0, ge=0)
    context_digest: str = ""

    @field_validator("worktree_identity", "lease_owner")
    @classmethod
    def _require_identity(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("stage close context identity is invalid")
        return value

    @field_validator("lease_seconds")
    @classmethod
    def _finite_duration(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("stage close lease duration must be finite")
        return value

    @model_validator(mode="after")
    def _bind_certificate(self) -> Self:
        certificate = self.certificate
        request = self.certificate_request
        expected = (
            certificate.scope == request.intent.scope,
            certificate.command_id == request.intent.command_id,
            certificate.close_intent_digest == request.intent.close_intent_digest,
            certificate.candidate_manifest_digest
            == request.evidence.candidate_manifest_digest,
            certificate.evidence_digest == request.evidence.evidence_digest,
            certificate.protected_path_set == request.evidence.protected_path_set,
        )
        if not all(expected):
            raise ValueError("stage close context certificate binding is invalid")
        bindings = (
            (
                "final_resource_reservation_digest",
                certificate.final_resource_reservation_digest,
            ),
            (
                "resource_reconciliation_digest",
                certificate.resource_reconciliation_digest,
            ),
            ("fencing_epoch", certificate.resource_fencing_epoch),
        )
        for field, value in bindings:
            current = getattr(self, field)
            if current not in ("", 0) and current != value:
                raise ValueError("stage close context resource binding is invalid")
            object.__setattr__(self, field, value)
        return fill_artifact_digest(self, "context_digest")


class CloseConsumptionClaim(ArtifactCompatibility):
    model_config = _CONFIG

    schema_version: Literal["close-consumption-claim.v1"] = (
        "close-consumption-claim.v1"
    )
    claim_id: str
    scope: FindingScope
    certificate_id: str
    certificate_digest: str
    certificate_revision: int = Field(ge=1)
    session_start_revision: int = Field(ge=1)
    command_id: str
    idempotency_key: str
    close_intent_digest: str
    candidate_manifest_digest: str
    protected_path_set: tuple[str, ...] = ()
    artifact_path: str
    content_contract_digest: str
    worktree_identity: str
    final_resource_reservation_digest: str
    resource_reconciliation_digest: str
    fencing_epoch: int = Field(ge=1)
    prepared_at: str
    claim_digest: str = ""

    @field_validator(
        "claim_id",
        "certificate_id",
        "certificate_digest",
        "command_id",
        "idempotency_key",
        "close_intent_digest",
        "candidate_manifest_digest",
        "content_contract_digest",
        "worktree_identity",
        "final_resource_reservation_digest",
        "resource_reconciliation_digest",
    )
    @classmethod
    def _require_binding(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("close consumption claim binding is invalid")
        return value

    @field_validator("artifact_path")
    @classmethod
    def _normalize_path(cls, value: str) -> str:
        return normalize_repo_path(value)

    @field_validator("protected_path_set")
    @classmethod
    def _canonical_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({normalize_repo_path(path) for path in value}))
        if normalized != value:
            raise ValueError("close claim protected paths must be canonical")
        return normalized

    @model_validator(mode="after")
    def _verify_claim(self) -> Self:
        parse_utc(self.prepared_at)
        if self.compatibility_mode == "strict" and not self.protected_path_set:
            raise ValueError("close claim requires protected paths")
        expected_id = stable_id("close-consumption-claim", self.certificate_id)
        if self.claim_id != expected_id:
            raise ValueError("close consumption claim identity is inconsistent")
        return fill_artifact_digest(self, "claim_digest")


class CloseConsumptionEvent(ArtifactCompatibility):
    model_config = _CONFIG

    schema_version: Literal["close-consumption-event.v1"] = (
        "close-consumption-event.v1"
    )
    sequence: int = Field(ge=1)
    event_id: str
    event_kind: CloseEventKind
    claim_id: str
    claim_digest: str
    previous_event_digest: str = ""
    close_intent_digest: str
    artifact_path: str
    content_contract_digest: str
    close_artifact_digest: str | None = None
    resource_reconciliation_digest: str
    receipt_digest: str = ""
    governance_decision_digest: str = ""
    occurred_at: str
    event_digest: str = ""

    @field_validator(
        "event_id",
        "claim_id",
        "claim_digest",
        "close_intent_digest",
        "content_contract_digest",
        "resource_reconciliation_digest",
    )
    @classmethod
    def _require_binding(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("close consumption event binding is invalid")
        return value

    @field_validator("artifact_path")
    @classmethod
    def _normalize_path(cls, value: str) -> str:
        return normalize_repo_path(value)

    @model_validator(mode="after")
    def _verify_event(self) -> Self:
        parse_utc(self.occurred_at)
        if self.sequence == 1 and self.previous_event_digest:
            raise ValueError("prepared event cannot have a predecessor")
        if self.sequence > 1 and not self.previous_event_digest:
            raise ValueError("close event predecessor is required")
        _validate_event_fields(self)
        expected_id = stable_id(
            "close-consumption-event",
            self.claim_digest,
            self.event_kind,
        )
        if self.event_id != expected_id:
            raise ValueError("close consumption event identity is inconsistent")
        return fill_artifact_digest(self, "event_digest")


class StageCloseConsumptionReceipt(ArtifactCompatibility):
    model_config = _CONFIG

    schema_version: Literal["stage-close-consumption-receipt.v1"] = (
        "stage-close-consumption-receipt.v1"
    )
    receipt_id: str
    claim_id: str
    claim_digest: str
    certificate_id: str
    certificate_digest: str
    command_id: str
    close_intent_digest: str
    close_artifact_digest: str
    reconciled_event_digest: str
    final_resource_reservation_digest: str
    resource_reconciliation_digest: str
    fencing_epoch: int = Field(ge=1)
    committed_at: str
    receipt_digest: str = ""

    @field_validator(
        "receipt_id",
        "claim_id",
        "claim_digest",
        "certificate_id",
        "certificate_digest",
        "command_id",
        "close_intent_digest",
        "close_artifact_digest",
        "reconciled_event_digest",
        "final_resource_reservation_digest",
        "resource_reconciliation_digest",
    )
    @classmethod
    def _require_binding(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("stage close receipt binding is invalid")
        return value

    @model_validator(mode="after")
    def _verify_receipt(self) -> Self:
        parse_utc(self.committed_at)
        expected_id = stable_id("stage-close-consumption-receipt", self.claim_digest)
        if self.receipt_id != expected_id:
            raise ValueError("stage close receipt identity is inconsistent")
        return fill_artifact_digest(self, "receipt_digest")


class CloseConsumptionState(BaseModel):
    model_config = _CONFIG

    schema_version: Literal["close-consumption-state.v1"] = (
        "close-consumption-state.v1"
    )
    claim_id: str
    claim_digest: str
    certificate_id: str
    consumed_by_command_id: str
    status: Literal["consuming", "closed", "aborted"]
    revision: int = Field(ge=0)
    event_kinds: tuple[CloseEventKind, ...]
    head_event_digest: str
    close_artifact_digest: str = ""
    receipt_digest: str = ""
    closed: bool
    state_digest: str = ""

    @field_validator(
        "claim_id",
        "claim_digest",
        "certificate_id",
        "consumed_by_command_id",
    )
    @classmethod
    def _require_identity(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("close consumption state identity is invalid")
        return value

    @model_validator(mode="after")
    def _verify_state(self) -> Self:
        if self.closed != (self.status == "closed"):
            raise ValueError("close consumption state closed flag is inconsistent")
        return fill_artifact_digest(self, "state_digest")


class StageCloseAuthorization(BaseModel):
    model_config = _CONFIG

    status: Literal["closed", "needs_user"]
    claim: CloseConsumptionClaim
    receipt: StageCloseConsumptionReceipt | None
    state: CloseConsumptionState

    @model_validator(mode="after")
    def _verify_result(self) -> Self:
        if (self.status == "closed") != (self.receipt is not None and self.state.closed):
            raise ValueError("stage close authorization result is inconsistent")
        return self


def _validate_event_fields(event: CloseConsumptionEvent) -> None:
    has_artifact = bool(event.close_artifact_digest)
    if event.event_kind == "prepared" and has_artifact:
        raise ValueError("prepared close event cannot bind an artifact digest")
    if (
        event.event_kind in {"close_written", "reconciled", "committed"}
        and not has_artifact
    ):
        raise ValueError("close event requires an artifact digest")
    if event.event_kind == "committed" and not event.receipt_digest:
        raise ValueError("committed close event requires a receipt")
    if event.event_kind != "committed" and event.receipt_digest:
        raise ValueError("only committed close event can bind a receipt")
    if event.event_kind == "aborted" and not event.governance_decision_digest:
        raise ValueError("aborted close event requires governance evidence")
    if event.event_kind != "aborted" and event.governance_decision_digest:
        raise ValueError("only aborted close event can bind governance evidence")
