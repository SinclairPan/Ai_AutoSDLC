"""阶段关闭意图、证据和一次性证书合同。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.canonical import normalize_repo_path
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class StageCloseIntent(ArtifactCompatibility):
    model_config = _MODEL_CONFIG

    schema_version: Literal["stage-close-intent.v1"] = "stage-close-intent.v1"
    scope: FindingScope
    gate_id: str
    close_kind: str
    target_status: str
    command_id: str
    idempotency_key: str
    loop_id: str
    loop_round_number: int = Field(ge=1)
    close_intent_digest: str = ""

    @field_validator(
        "gate_id",
        "close_kind",
        "target_status",
        "command_id",
        "idempotency_key",
        "loop_id",
    )
    @classmethod
    def _require_identity(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("stage close intent identity is invalid")
        return value

    @model_validator(mode="after")
    def _validate_digest(self) -> Self:
        return fill_artifact_digest(self, "close_intent_digest")


class StageCloseEvidence(ArtifactCompatibility):
    model_config = _MODEL_CONFIG

    schema_version: Literal["stage-close-evidence.v1"] = "stage-close-evidence.v1"
    candidate_manifest_digest: str
    test_evidence_digest: str
    integrity_evidence_digest: str
    protected_path_set: tuple[str, ...]
    evidence_digest: str = ""

    @field_validator(
        "candidate_manifest_digest",
        "test_evidence_digest",
        "integrity_evidence_digest",
    )
    @classmethod
    def _require_digest(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("stage close evidence digest is invalid")
        return value

    @field_validator("protected_path_set")
    @classmethod
    def _canonical_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({normalize_repo_path(path) for path in value}))
        if not normalized or normalized != value:
            raise ValueError("stage close protected paths must be canonical")
        return normalized

    @model_validator(mode="after")
    def _validate_digest(self) -> Self:
        return fill_artifact_digest(self, "evidence_digest")


class StageCloseCertificateRequest(BaseModel):
    model_config = _MODEL_CONFIG

    intent: StageCloseIntent
    evidence: StageCloseEvidence
    expected_session_revision: int = Field(ge=1)
    resource_reconciliation_digest: str

    @field_validator("resource_reconciliation_digest")
    @classmethod
    def _require_reconciliation(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("resource reconciliation digest is required")
        return value


class StageCloseCertificate(ArtifactCompatibility):
    model_config = _MODEL_CONFIG

    schema_version: Literal["stage-close-certificate.v1"] = "stage-close-certificate.v1"
    certificate_id: str
    certificate_revision: int = Field(default=1, ge=1)
    scope: FindingScope
    gate_id: str
    close_intent_digest: str
    close_kind: str
    target_status: str
    command_id: str
    work_item_id: str
    loop_id: str
    loop_round_number: int = Field(ge=1)
    stage_instance_id: str
    candidate_manifest_digest: str
    evidence_digest: str
    protected_path_set: tuple[str, ...] = ()
    task_risk_profile_digest: str
    registry_digest: str
    selection_policy_digest: str
    budget_policy_digest: str
    policy_digest: str
    optimization_snapshot_digest: str
    budget_revision: int = Field(ge=0)
    budget_grant_digests: tuple[str, ...]
    final_resource_reservation_digest: str
    resource_reconciliation_digest: str
    resource_fencing_epoch: int = Field(ge=1)
    panel_plan_digest: str
    binding_digest: str
    active_cohort_id: str
    satisfied_slot_ids: tuple[str, ...]
    required_role_coverage_proof_digest: str
    quorum_policy_digest: str
    finding_ledger_digest: str
    test_evidence_digest: str
    integrity_evidence_digest: str
    session_revision: int = Field(ge=1)
    session_digest: str
    issued_at: str
    consumable: Literal[True] = True
    certificate_digest: str = ""

    @field_validator(
        "certificate_id",
        "gate_id",
        "close_intent_digest",
        "close_kind",
        "target_status",
        "command_id",
        "work_item_id",
        "loop_id",
        "stage_instance_id",
        "candidate_manifest_digest",
        "evidence_digest",
        "task_risk_profile_digest",
        "registry_digest",
        "selection_policy_digest",
        "budget_policy_digest",
        "policy_digest",
        "optimization_snapshot_digest",
        "final_resource_reservation_digest",
        "resource_reconciliation_digest",
        "panel_plan_digest",
        "binding_digest",
        "active_cohort_id",
        "required_role_coverage_proof_digest",
        "quorum_policy_digest",
        "finding_ledger_digest",
        "test_evidence_digest",
        "integrity_evidence_digest",
        "session_digest",
    )
    @classmethod
    def _require_binding(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("stage close certificate identity or digest is invalid")
        return value

    @field_validator("protected_path_set")
    @classmethod
    def _canonical_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({normalize_repo_path(path) for path in value}))
        if normalized != value:
            raise ValueError("certificate protected paths must be canonical")
        return normalized

    @model_validator(mode="after")
    def _validate_certificate(self) -> Self:
        parse_utc(self.issued_at)
        canonical = (
            self.budget_grant_digests,
            self.satisfied_slot_ids,
        )
        if any(values != tuple(sorted(set(values))) for values in canonical):
            raise ValueError("stage close certificate set values are not canonical")
        scope_values = (
            self.scope.project_id,
            self.scope.work_item_id,
            self.scope.stage_instance_id,
            self.scope.session_id,
        )
        if any(not value.strip() or value != value.strip() for value in scope_values):
            raise ValueError("stage close certificate scope identity is invalid")
        if not self.satisfied_slot_ids:
            raise ValueError("stage close certificate requires satisfied slots")
        if self.compatibility_mode == "strict" and not self.protected_path_set:
            raise ValueError("stage close certificate requires protected paths")
        expected_id = stable_id(
            "stage-close-certificate",
            self.scope.project_id,
            self.scope.work_item_id,
            self.scope.stage_instance_id,
            self.scope.session_id,
            self.gate_id,
            self.close_kind,
            self.target_status,
            self.loop_id,
            str(self.loop_round_number),
            self.session_digest,
            self.candidate_manifest_digest,
            self.evidence_digest,
            self.resource_reconciliation_digest,
        )
        if self.certificate_id != expected_id:
            raise ValueError("stage close certificate identity is inconsistent")
        if (
            self.work_item_id != self.scope.work_item_id
            or self.stage_instance_id != self.scope.stage_instance_id
        ):
            raise ValueError("stage close certificate scope is inconsistent")
        return fill_artifact_digest(self, "certificate_digest")
