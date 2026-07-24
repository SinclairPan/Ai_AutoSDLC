"""Reviewer 隔离 Manifest、Permit 与 Receipt 不可变合同。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, field_validator, model_validator

from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.contracts import StageReviewArtifactModel
from ai_sdlc.core.stage_review.resource_builders import parse_utc

IsolationPlatform = Literal["windows", "macos", "linux"]
IsolationCommandKind = Literal["invoke", "query", "refusal"]
IsolationObservationStage = Literal["completed", "cleaned", "cleanup_failed"]


def _digest(value: object, field: str) -> str:
    return canonical_digest(
        value,
        CanonicalizationPolicy(excluded_fields=frozenset({field})),
    )


class IsolationBoundaryResult(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["isolation-boundary-result"] = (
        "isolation-boundary-result"
    )
    action: str
    target_kind: str
    expected: str
    observed: str
    os_error: str
    blocked_before_side_effect: bool
    before_digest: str
    after_digest: str

    @model_validator(mode="after")
    def _verify_boundary(self) -> Self:
        _require_text(
            self.action,
            self.target_kind,
            self.expected,
            self.observed,
            self.before_digest,
            self.after_digest,
        )
        return self


class IsolationNativeDenial(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["isolation-native-denial"] = "isolation-native-denial"
    mechanism: str
    operation: str
    target: str
    os_error: str
    observed_at: str

    @field_validator("observed_at")
    @classmethod
    def _timestamp_is_utc(cls, value: str) -> str:
        parse_utc(value)
        return value

    @model_validator(mode="after")
    def _verify_denial(self) -> Self:
        _require_text(
            self.mechanism,
            self.operation,
            self.target,
            self.os_error,
        )
        return self


class IsolationEvidenceManifest(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["isolation-evidence-manifest"] = (
        "isolation-evidence-manifest"
    )
    backend_id: str
    contract_version: str
    backend_version: str
    backend_instance_id: str
    backend_epoch: str
    allocation_digest: str
    assignment_digest: str
    candidate_digest: str
    layout_digest: str
    platform: IsolationPlatform
    platform_mechanism: str
    host_snapshot_digest: str
    release_manifest_digest: str = ""
    runtime_identity_digest: str = ""
    policy_digest: str
    filesystem_policy_digest: str
    network_policy_digest: str
    process_id: int
    parent_process_id: int
    boundary_results: tuple[IsolationBoundaryResult, ...]
    os_native_denials: tuple[IsolationNativeDenial, ...]
    cleanup_succeeded: bool
    issued_at: str
    expires_at: str
    manifest_digest: str

    @field_validator("issued_at", "expires_at")
    @classmethod
    def _timestamp_is_utc(cls, value: str) -> str:
        parse_utc(value)
        return value

    @model_validator(mode="after")
    def _verify_manifest(self) -> Self:
        _require_text(
            self.backend_id,
            self.contract_version,
            self.backend_version,
            self.backend_instance_id,
            self.backend_epoch,
            self.allocation_digest,
            self.assignment_digest,
            self.candidate_digest,
            self.layout_digest,
            self.platform_mechanism,
            self.host_snapshot_digest,
            self.policy_digest,
            self.filesystem_policy_digest,
            self.network_policy_digest,
        )
        _require_boundary_results(self.boundary_results)
        _require_native_denials(self.os_native_denials)
        if self.process_id <= 0 or self.parent_process_id <= 0:
            raise ValueError("isolation manifest process lineage is invalid")
        if parse_utc(self.expires_at) <= parse_utc(self.issued_at):
            raise ValueError("isolation manifest expiry is invalid")
        if self.manifest_digest != _digest(self, "manifest_digest"):
            raise ValueError("isolation manifest digest does not match content")
        return self


class IsolationExecutionPermit(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["isolation-execution-permit"] = (
        "isolation-execution-permit"
    )
    permit_id: str
    allocation_digest: str
    assignment_digest: str
    candidate_digest: str
    host_snapshot_digest: str
    backend_id: str
    contract_version: str
    backend_version: str
    backend_instance_id: str
    backend_epoch: str
    normalized_run_root: str
    layout_digest: str
    filesystem_policy_digest: str
    network_policy_digest: str
    manifest_digest: str
    release_manifest_digest: str = ""
    runtime_identity_digest: str = ""
    issued_at: str
    expires_at: str
    nonce: str
    permit_digest: str

    @field_validator("issued_at", "expires_at")
    @classmethod
    def _timestamp_is_utc(cls, value: str) -> str:
        parse_utc(value)
        return value

    @model_validator(mode="after")
    def _verify_permit(self) -> Self:
        _require_text(
            self.permit_id,
            self.allocation_digest,
            self.assignment_digest,
            self.candidate_digest,
            self.host_snapshot_digest,
            self.backend_id,
            self.contract_version,
            self.backend_version,
            self.backend_instance_id,
            self.backend_epoch,
            self.normalized_run_root,
            self.layout_digest,
            self.filesystem_policy_digest,
            self.network_policy_digest,
            self.manifest_digest,
            self.nonce,
        )
        if parse_utc(self.expires_at) <= parse_utc(self.issued_at):
            raise ValueError("isolation permit expiry is invalid")
        if self.permit_digest != _digest(self, "permit_digest"):
            raise ValueError("isolation permit digest does not match content")
        return self


class IsolationExecutionReceipt(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["isolation-execution-receipt"] = (
        "isolation-execution-receipt"
    )
    receipt_id: str
    permit_digest: str
    manifest_digest: str
    release_manifest_digest: str = ""
    runtime_identity_digest: str = ""
    allocation_digest: str
    assignment_digest: str
    candidate_digest: str
    host_snapshot_digest: str
    backend_id: str
    backend_version: str
    backend_instance_id: str
    backend_epoch: str
    layout_digest: str
    command_kind: IsolationCommandKind
    command_started: bool
    process_id: int
    parent_process_id: int
    boundary_results: tuple[IsolationBoundaryResult, ...]
    os_native_denials: tuple[IsolationNativeDenial, ...]
    before_digest: str
    after_digest: str
    cleanup_succeeded: bool
    reason_id: str
    recorded_at: str
    receipt_digest: str

    @field_validator("recorded_at")
    @classmethod
    def _timestamp_is_utc(cls, value: str) -> str:
        parse_utc(value)
        return value

    @model_validator(mode="after")
    def _verify_receipt(self) -> Self:
        _require_text(
            self.receipt_id,
            self.permit_digest,
            self.manifest_digest,
            self.allocation_digest,
            self.assignment_digest,
            self.candidate_digest,
            self.host_snapshot_digest,
            self.backend_id,
            self.backend_version,
            self.backend_instance_id,
            self.backend_epoch,
            self.layout_digest,
            self.reason_id,
        )
        _require_boundary_results(self.boundary_results)
        _require_native_denials(self.os_native_denials)
        if self.command_started and self.process_id <= 0:
            raise ValueError("started isolation receipt requires process lineage")
        if not self.command_started and self.process_id != 0:
            raise ValueError("refused isolation receipt cannot claim a process")
        if self.receipt_digest != _digest(self, "receipt_digest"):
            raise ValueError("isolation receipt digest does not match content")
        return self


class IsolationExecutionObservation(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["isolation-execution-observation"] = (
        "isolation-execution-observation"
    )
    observation_id: str
    permit_digest: str
    manifest_digest: str
    release_manifest_digest: str = ""
    runtime_identity_digest: str = ""
    assignment_digest: str
    candidate_digest: str
    stage: IsolationObservationStage
    previous_observation_digest: str
    process_id: int
    parent_process_id: int
    before_digest: str
    after_digest: str
    cleanup_succeeded: bool
    recorded_at: str
    observation_digest: str

    @field_validator("recorded_at")
    @classmethod
    def _observation_timestamp_is_utc(cls, value: str) -> str:
        parse_utc(value)
        return value

    @model_validator(mode="after")
    def _verify_observation(self) -> Self:
        _require_text(
            self.observation_id,
            self.permit_digest,
            self.manifest_digest,
            self.assignment_digest,
            self.candidate_digest,
            self.before_digest,
            self.after_digest,
        )
        if self.process_id <= 0 or self.parent_process_id <= 0:
            raise ValueError("isolation observation process lineage is invalid")
        if (self.stage == "completed") == bool(self.previous_observation_digest):
            raise ValueError("isolation observation predecessor is invalid")
        expected_cleanup = self.stage == "cleaned"
        if self.cleanup_succeeded != expected_cleanup:
            raise ValueError("isolation observation cleanup state is invalid")
        if self.observation_digest != _digest(self, "observation_digest"):
            raise ValueError("isolation observation digest does not match content")
        return self


def _manifest_digest(value: object) -> str:
    return _digest(value, "manifest_digest")


def _permit_digest(value: object) -> str:
    return _digest(value, "permit_digest")


def _receipt_digest(value: object) -> str:
    return _digest(value, "receipt_digest")


def _observation_digest(value: object) -> str:
    return _digest(value, "observation_digest")


def _require_text(*values: str) -> None:
    if any(not value.strip() or value != value.strip() for value in values):
        raise ValueError("isolation identity cannot be empty or padded")


def _require_boundary_results(values: tuple[IsolationBoundaryResult, ...]) -> None:
    actions = tuple(item.action for item in values)
    if actions != tuple(sorted(set(actions))):
        raise ValueError("isolation boundary results are not canonical")


def _require_native_denials(values: tuple[IsolationNativeDenial, ...]) -> None:
    keys = tuple(
        (item.mechanism, item.operation, item.target, item.observed_at)
        for item in values
    )
    if keys != tuple(sorted(set(keys))):
        raise ValueError("isolation native denials are not canonical")
