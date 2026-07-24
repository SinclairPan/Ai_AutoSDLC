"""有界优化存储的 Policy、Record、Index、Checkpoint 与 Manifest。"""

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


class StoragePressureError(RuntimeError):
    """写入超出 steady-state 或专用 Reserve。"""


class SegmentIndexLookupIncompleteError(RuntimeError):
    """索引预算不足，不能把未查完误判为不存在。"""


class OptimizationStoragePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    maximum_total_bytes: int = Field(default=1024**3, gt=0)
    minimum_free_bytes: int = Field(default=2 * 1024**3, ge=0)
    minimum_free_ratio: float = Field(default=0.1, ge=0, le=1)
    critical_recovery_reserve_bytes: int = Field(default=48 * 1024**2, ge=0)
    session_binding_reserve_bytes: int = Field(default=16 * 1024**2, ge=0)
    maintenance_reclamation_reserve_bytes: int = Field(
        default=128 * 1024**2, ge=0
    )
    safety_bundle_max_bytes: int = Field(default=1024**2, gt=0)
    maximum_segment_records: int = Field(default=10_000, gt=0)
    maximum_segment_bytes: int = Field(default=64 * 1024**2, gt=0)
    maximum_index_scan_items: int = Field(default=10_000, gt=0)
    maximum_index_scan_seconds: float = Field(default=0.5, gt=0)

    @model_validator(mode="after")
    def _verify_reserves(self) -> Self:
        reserves = (
            self.critical_recovery_reserve_bytes
            + self.session_binding_reserve_bytes
            + self.maintenance_reclamation_reserve_bytes
        )
        if reserves >= self.maximum_total_bytes:
            raise ValueError("storage reserves must leave steady-state capacity")
        if self.safety_bundle_max_bytes > (
            self.critical_recovery_reserve_bytes
            + self.session_binding_reserve_bytes
        ):
            raise ValueError("safety bundle exceeds safety reserves")
        return self


class OptimizationStorageRecord(ArtifactCompatibility):
    schema_version: Literal["optimization-storage-record.v1"] = (
        "optimization-storage-record.v1"
    )
    artifact_kind: Literal["optimization-storage-record"] = (
        "optimization-storage-record"
    )
    project_id: str
    stream_kind: str
    sequence: int = Field(ge=1)
    previous_record_digest: str
    payload: dict[str, JsonValue]
    keys: dict[str, str]
    record_digest: str = ""

    @field_validator("payload", mode="before")
    @classmethod
    def _freeze_payload(cls, value: object) -> dict[str, JsonValue]:
        if not isinstance(value, Mapping):
            raise ValueError("storage record payload must be a mapping")
        return cast(dict[str, JsonValue], freeze_json_mapping(value))

    @field_validator("keys")
    @classmethod
    def _keys_are_stable(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or not item.strip() for key, item in value.items()):
            raise ValueError("storage record lookup key is invalid")
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def _verify_record(self) -> Self:
        if self.sequence == 1 and self.previous_record_digest:
            raise ValueError("first stream record cannot have predecessor")
        if self.sequence > 1 and not self.previous_record_digest:
            raise ValueError("stream record predecessor is required")
        return fill_artifact_digest(self, "record_digest")


class SegmentIndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key_kind: str
    key_digest: str
    sequence: int = Field(ge=1)
    record_offset: int = Field(ge=0)
    record_digest: str


class OptimizationSegmentIndex(ArtifactCompatibility):
    schema_version: Literal["optimization-segment-index.v1"] = (
        "optimization-segment-index.v1"
    )
    artifact_kind: Literal["optimization-segment-index"] = (
        "optimization-segment-index"
    )
    project_id: str
    stream_kind: str
    first_sequence: int = Field(ge=1)
    last_sequence: int = Field(ge=1)
    segment_digest: str
    entries: tuple[SegmentIndexEntry, ...]
    index_digest: str = ""

    @model_validator(mode="after")
    def _verify_index(self) -> Self:
        if self.last_sequence < self.first_sequence:
            raise ValueError("segment index range is invalid")
        return fill_artifact_digest(self, "index_digest")


class OptimizationSegmentDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stream_kind: str
    first_sequence: int = Field(ge=1)
    last_sequence: int = Field(ge=1)
    record_count: int = Field(ge=1)
    previous_head_digest: str
    head_digest: str
    segment_relative_path: str
    segment_digest: str
    index_relative_path: str
    index_digest: str


class StreamCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stream_kind: str
    compacted_through_sequence: int = Field(ge=0)
    head_digest: str
    segment_digests: tuple[str, ...]
    index_digests: tuple[str, ...]


class OptimizationStorageCheckpoint(ArtifactCompatibility):
    schema_version: Literal["optimization-storage-checkpoint.v1"] = (
        "optimization-storage-checkpoint.v1"
    )
    artifact_kind: Literal["optimization-storage-checkpoint"] = (
        "optimization-storage-checkpoint"
    )
    project_id: str
    sequence: int = Field(ge=1)
    previous_checkpoint_digest: str = ""
    streams: tuple[StreamCheckpoint, ...]
    commit_fencing_high_watermark: int = Field(ge=1)
    commit_claim_digest: str
    checkpoint_digest: str = ""

    @model_validator(mode="after")
    def _verify_checkpoint(self) -> Self:
        names = tuple(item.stream_kind for item in self.streams)
        if names != tuple(sorted(set(names))):
            raise ValueError("checkpoint streams must be canonical")
        return fill_artifact_digest(self, "checkpoint_digest")


class OptimizationStorageManifest(ArtifactCompatibility):
    schema_version: Literal["optimization-storage-manifest.v1"] = (
        "optimization-storage-manifest.v1"
    )
    artifact_kind: Literal["optimization-storage-manifest"] = (
        "optimization-storage-manifest"
    )
    project_id: str
    revision: int = Field(ge=0)
    previous_manifest_digest: str = ""
    checkpoint_digest: str = ""
    segments: tuple[OptimizationSegmentDescriptor, ...] = ()
    commit_fencing_high_watermark: int = Field(default=0, ge=0)
    commit_claim_digest: str = ""
    manifest_digest: str = ""

    @model_validator(mode="after")
    def _verify_manifest(self) -> Self:
        if self.revision == 0 and (
            self.previous_manifest_digest
            or self.checkpoint_digest
            or self.segments
            or self.commit_fencing_high_watermark
            or self.commit_claim_digest
        ):
            raise ValueError("initial storage manifest must be empty")
        if self.revision > 0 and (
            not self.checkpoint_digest
            or not self.commit_claim_digest
            or self.commit_fencing_high_watermark < 1
        ):
            raise ValueError("committed storage manifest is incomplete")
        return fill_artifact_digest(self, "manifest_digest")
