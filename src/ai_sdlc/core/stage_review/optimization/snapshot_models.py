"""Optimization Snapshot、Control Event 与 Session Freeze 合同。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    JsonValue,
    fill_artifact_digest,
    freeze_json_mapping,
)
from ai_sdlc.core.stage_review.registry_versions import require_machine_id
from ai_sdlc.core.stage_review.resource_builders import parse_utc

SnapshotControlEventKind = Literal[
    "promotion", "stability", "revocation", "rollback", "session_binding"
]


class OptimizationSnapshot(ArtifactCompatibility):
    schema_version: Literal["optimization-snapshot.v1"] = "optimization-snapshot.v1"
    artifact_kind: Literal["optimization-snapshot"] = "optimization-snapshot"
    snapshot_id: str
    project_id: str
    parent_snapshot_digest: str = ""
    stable_fallback_digest: str = ""
    candidate_digest: str = ""
    evaluation_report_digests: tuple[str, ...] = ()
    policy_payload: dict[str, JsonValue]
    created_at: str
    is_baseline: bool = False
    snapshot_digest: str = ""

    @field_validator("snapshot_id", "project_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "optimization snapshot identity")

    @field_validator("policy_payload", mode="before")
    @classmethod
    def _freeze_payload(cls, value: object) -> dict[str, JsonValue]:
        if not isinstance(value, Mapping):
            raise ValueError("optimization policy payload must be a mapping")
        return cast(dict[str, JsonValue], freeze_json_mapping(value))

    @model_validator(mode="after")
    def _verify_snapshot(self) -> Self:
        parse_utc(self.created_at)
        if self.is_baseline and (
            self.parent_snapshot_digest
            or self.stable_fallback_digest
            or self.candidate_digest
            or self.evaluation_report_digests
        ):
            raise ValueError("baseline snapshot cannot depend on a challenger")
        if not self.is_baseline and (
            not self.parent_snapshot_digest
            or not self.stable_fallback_digest
            or not self.candidate_digest
            or not self.evaluation_report_digests
        ):
            raise ValueError("challenger snapshot lineage is incomplete")
        return fill_artifact_digest(self, "snapshot_digest")


class ActiveOptimizationPointer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    head_sequence: int = Field(ge=0)
    head_digest: str
    pointer_revision: int = Field(ge=0)
    active_snapshot_digest: str
    stable_fallback_digest: str
    revocation_generation: int = Field(ge=0)
    revoked_snapshot_digests: tuple[str, ...]
    revoked_set_digest: str
    session_binding_sequence: int = Field(ge=0)
    control_digest: str


class SnapshotSelectionToken(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    head_sequence: int
    head_digest: str
    pointer_revision: int
    revocation_generation: int
    active_snapshot_digest: str
    stable_fallback_digest: str
    revoked_snapshot_digests: tuple[str, ...]
    control_digest: str


class SnapshotControlEvent(ArtifactCompatibility):
    schema_version: Literal["snapshot-control-event.v1"] = "snapshot-control-event.v1"
    artifact_kind: Literal["snapshot-control-event"] = "snapshot-control-event"
    project_id: str
    sequence: int = Field(ge=1)
    event_kind: SnapshotControlEventKind
    operation_id: str
    previous_event_digest: str
    previous_control_digest: str
    next_control_digest: str
    effect_digest: str
    target_snapshot_digest: str = ""
    revoked_snapshot_digest: str = ""
    session_id: str = ""
    reason: str = ""
    pointer_revision: int = Field(ge=0)
    revocation_generation: int = Field(ge=0)
    session_binding_sequence: int = Field(ge=0)
    commit_fencing_epoch: int = Field(ge=1)
    commit_claim_digest: str
    event_digest: str = ""

    @field_validator("project_id", "operation_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "snapshot control identity")

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        if not self.commit_claim_digest:
            raise ValueError("snapshot control fencing claim is required")
        return fill_artifact_digest(self, "event_digest")


class SnapshotRevocationOperation(ArtifactCompatibility):
    schema_version: Literal["snapshot-revocation-operation.v1"] = (
        "snapshot-revocation-operation.v1"
    )
    artifact_kind: Literal["snapshot-revocation-operation"] = (
        "snapshot-revocation-operation"
    )
    operation_id: str
    project_id: str
    revoked_snapshot_digest: str
    reason: str
    operation_digest: str = ""

    @field_validator("operation_id", "project_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "snapshot revocation identity")

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        return fill_artifact_digest(self, "operation_digest")


class SessionSnapshotBindingOperation(ArtifactCompatibility):
    schema_version: Literal["session-snapshot-binding-operation.v1"] = (
        "session-snapshot-binding-operation.v1"
    )
    artifact_kind: Literal["session-snapshot-binding-operation"] = (
        "session-snapshot-binding-operation"
    )
    operation_id: str
    project_id: str
    session_id: str
    initial_candidate_digest: str
    stage_key: str
    risk_level: str
    candidate_size_bucket: str
    provider_ids: tuple[str, ...]
    binding_set_digest: str = ""
    role_profile_ids: tuple[str, ...] = ()
    reviewer_slot_ids: tuple[str, ...] = ()
    capability_ids: tuple[str, ...] = ()
    binding_digests: tuple[str, ...] = ()
    resource_reservation_digest: str = ""
    risk_profile_digest: str = ""
    created_at: str
    target_snapshot_digest: str
    expected_head_sequence: int = Field(ge=0)
    expected_head_digest: str
    expected_pointer_revision: int = Field(ge=0)
    expected_revocation_generation: int = Field(ge=0)
    operation_digest: str = ""

    @field_validator("operation_id", "project_id", "session_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "session snapshot binding identity")

    @model_validator(mode="after")
    def _verify_operation(self) -> Self:
        parse_utc(self.created_at)
        groups = (
            self.provider_ids,
            self.role_profile_ids,
            self.reviewer_slot_ids,
            self.capability_ids,
            self.binding_digests,
        )
        if any(group != tuple(sorted(set(group))) for group in groups):
            raise ValueError("session binding lineage sets must be canonical")
        return fill_artifact_digest(self, "operation_digest")
