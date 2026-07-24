"""Repo Write Lease 的版本化请求、快照、事件与投影。"""

from __future__ import annotations

from math import isfinite
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.canonical import normalize_repo_path
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id

_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
RepoWriteLeaseStateKind = Literal["active", "released", "expired", "reconciled"]
RepoWriteLeaseEventKind = Literal["acquired", "renewed", "released", "expired", "reconciled"]


class RepoWriteLeaseRequest(BaseModel):
    model_config = _CONFIG

    worktree_identity: str
    stage_review_session_id: str
    protected_path_set: tuple[str, ...]
    lease_owner: str
    idempotency_key: str
    lease_seconds: float = Field(gt=0)

    @field_validator(
        "worktree_identity",
        "stage_review_session_id",
        "lease_owner",
        "idempotency_key",
    )
    @classmethod
    def _require_identity(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("repo write lease identity is invalid")
        return value

    @field_validator("protected_path_set")
    @classmethod
    def _normalize_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({normalize_repo_path(value) for value in values}))
        if not normalized:
            raise ValueError("repo write lease requires protected paths")
        return normalized

    @field_validator("lease_seconds")
    @classmethod
    def _finite_duration(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("repo write lease duration must be finite")
        return value


class RepoWriteLease(ArtifactCompatibility):
    model_config = _CONFIG

    schema_version: Literal["repo-write-lease.v1"] = "repo-write-lease.v1"
    lease_id: str
    project_id: str
    worktree_identity: str
    stage_review_session_id: str
    protected_path_set: tuple[str, ...]
    lease_owner: str
    fencing_epoch: int = Field(ge=1)
    expected_revision: int = Field(ge=0)
    revision: int = Field(ge=1)
    state: RepoWriteLeaseStateKind
    acquired_at: str
    expires_at: str
    renewed_at: str
    idempotency_key: str
    previous_lease_digest: str = ""
    lease_digest: str = ""

    @field_validator(
        "lease_id",
        "project_id",
        "worktree_identity",
        "stage_review_session_id",
        "lease_owner",
        "idempotency_key",
    )
    @classmethod
    def _require_identity(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("repo write lease identity is invalid")
        return value

    @field_validator("protected_path_set")
    @classmethod
    def _canonical_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({normalize_repo_path(value) for value in values}))
        if normalized != values or not normalized:
            raise ValueError("repo write lease paths are not canonical")
        return values

    @model_validator(mode="after")
    def _verify_lease(self) -> Self:
        acquired = parse_utc(self.acquired_at)
        expires = parse_utc(self.expires_at)
        renewed = parse_utc(self.renewed_at)
        if expires <= acquired or renewed < acquired:
            raise ValueError("repo write lease timestamps are invalid")
        if self.revision == 1 and self.previous_lease_digest:
            raise ValueError("initial repo write lease cannot have a predecessor")
        if self.revision > 1 and not self.previous_lease_digest:
            raise ValueError("updated repo write lease requires a predecessor")
        expected_id = stable_id(
            "repo-write-lease",
            self.project_id,
            str(self.fencing_epoch),
            self.idempotency_key,
        )
        if self.lease_id != expected_id:
            raise ValueError("repo write lease identity is inconsistent")
        return fill_artifact_digest(self, "lease_digest")


class RepoWriteLeaseEvent(ArtifactCompatibility):
    model_config = _CONFIG

    schema_version: Literal["repo-write-lease-event.v1"] = (
        "repo-write-lease-event.v1"
    )
    sequence: int = Field(ge=1)
    event_id: str
    event_kind: RepoWriteLeaseEventKind
    previous_event_digest: str = ""
    occurred_at: str
    lease: RepoWriteLease
    event_digest: str = ""

    @field_validator("event_id")
    @classmethod
    def _require_event_id(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("repo write lease event identity is invalid")
        return value

    @model_validator(mode="after")
    def _verify_event(self) -> Self:
        parse_utc(self.occurred_at)
        if self.sequence == 1 and self.previous_event_digest:
            raise ValueError("initial lease event cannot have a predecessor")
        if self.sequence > 1 and not self.previous_event_digest:
            raise ValueError("lease event predecessor is required")
        expected_id = stable_id(
            "repo-write-lease-event",
            self.lease.lease_id,
            str(self.lease.revision),
            self.event_kind,
        )
        if self.event_id != expected_id:
            raise ValueError("repo write lease event identity is inconsistent")
        return fill_artifact_digest(self, "event_digest")


class RepoWriteLeaseState(BaseModel):
    model_config = _CONFIG

    head_sequence: int = Field(ge=0)
    head_digest: str = ""
    max_fencing_epoch: int = Field(ge=0)
    active_leases: tuple[RepoWriteLease, ...] = ()
    state_digest: str = ""

    @model_validator(mode="after")
    def _verify_state(self) -> Self:
        ordered = tuple(sorted(self.active_leases, key=lambda item: item.lease_id))
        if ordered != self.active_leases or any(
            lease.state != "active" for lease in self.active_leases
        ):
            raise ValueError("repo write lease active projection is invalid")
        return fill_artifact_digest(self, "state_digest")
