"""Permanent Release Truth 的冻结输入与三个 canonical 工件合同。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _require_identity(value: str) -> str:
    if not value or value != value.strip():
        raise ValueError("release truth identity is invalid")
    return value


def _require_digest(value: str) -> str:
    if _DIGEST.fullmatch(value) is None:
        raise ValueError("release truth digest is invalid")
    return value


def _require_git_sha(value: str) -> str:
    if _GIT_SHA.fullmatch(value) is None:
        raise ValueError("release truth git SHA is invalid")
    return value


def _require_utc_timestamp(value: str) -> str:
    if not value.endswith("Z"):
        raise ValueError("release truth timestamp must use canonical UTC")
    datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    return value


class ReleaseAssetBinding(BaseModel):
    """一个最终 Release 资产的不可变名称与摘要绑定。"""

    model_config = _MODEL_CONFIG

    name: str
    digest: str
    size_bytes: int = Field(ge=1)
    platform: str

    _identity = field_validator("name", "platform")(_require_identity)
    _digest = field_validator("digest")(_require_digest)


class RequiredGateBinding(BaseModel):
    """从 GitHub required check 权威读取的一条精确 Gate 结论。"""

    model_config = _MODEL_CONFIG

    name: str
    conclusion: str
    required: bool
    protected: bool
    authority_repository: str
    workflow_ref: str
    workflow_run_id: int = Field(ge=1)
    workflow_run_attempt: int = Field(ge=1)
    head_sha: str
    completed_at: str
    valid_until: str
    evidence_digest: str

    _identity = field_validator(
        "name", "conclusion", "authority_repository", "workflow_ref"
    )(_require_identity)
    _sha = field_validator("head_sha")(_require_git_sha)
    _timestamps = field_validator("completed_at", "valid_until")(
        _require_utc_timestamp
    )
    _digest = field_validator("evidence_digest")(_require_digest)


class ReleaseCandidateSnapshot(BaseModel):
    """Proof 构建与 Publish CAS 共享的冻结 GitHub 候选快照。"""

    model_config = _MODEL_CONFIG

    repository: str
    draft_release_id: int = Field(ge=1)
    draft_release_updated_at: str
    draft: bool
    tag_name: str
    tag_object_sha: str
    commit_sha: str
    tree_sha: str
    required_policy_digest: str
    required_gate_names: tuple[str, ...]
    required_gates: tuple[RequiredGateBinding, ...]
    workflow_run_id: int = Field(ge=1)
    workflow_run_attempt: int = Field(ge=1)
    expected_assets: tuple[ReleaseAssetBinding, ...]
    assets: tuple[ReleaseAssetBinding, ...]
    release_settings_digest: str
    publish_workflow_ref: str
    evidence_cutoff_at: str

    _identity = field_validator("repository", "tag_name", "publish_workflow_ref")(
        _require_identity
    )
    _shas = field_validator("tag_object_sha", "commit_sha", "tree_sha")(
        _require_git_sha
    )
    _digests = field_validator("required_policy_digest", "release_settings_digest")(
        _require_digest
    )
    _timestamps = field_validator("draft_release_updated_at", "evidence_cutoff_at")(
        _require_utc_timestamp
    )


class ReleaseSatisfactionProof(ArtifactCompatibility):
    """精确候选在证据截止点满足全部发布条件的内容寻址证明。"""

    model_config = _MODEL_CONFIG

    schema_version: Literal["release-satisfaction-proof.v1"] = (
        "release-satisfaction-proof.v1"
    )
    repository: str
    draft_release_id: int = Field(ge=1)
    draft_release_updated_at: str
    tag_name: str
    tag_object_sha: str
    commit_sha: str
    tree_sha: str
    required_policy_digest: str
    required_gates: tuple[RequiredGateBinding, ...]
    workflow_run_id: int = Field(ge=1)
    workflow_run_attempt: int = Field(ge=1)
    assets: tuple[ReleaseAssetBinding, ...]
    release_settings_digest: str
    publish_workflow_ref: str
    evidence_cutoff_at: str
    proof_digest: str = ""

    _identity = field_validator("repository", "tag_name", "publish_workflow_ref")(
        _require_identity
    )
    _shas = field_validator("tag_object_sha", "commit_sha", "tree_sha")(
        _require_git_sha
    )
    _digests = field_validator("required_policy_digest", "release_settings_digest")(
        _require_digest
    )
    _timestamps = field_validator("draft_release_updated_at", "evidence_cutoff_at")(
        _require_utc_timestamp
    )

    @model_validator(mode="after")
    def _validate_proof(self) -> Self:
        return fill_artifact_digest(self, "proof_digest")


class PublishedReleaseSnapshot(BaseModel):
    """GitHub Published Release 与公开 evidence generation 的权威快照。"""

    model_config = _MODEL_CONFIG

    repository: str
    github_release_id: int = Field(ge=1)
    github_release_url: str
    tag_name: str
    commit_sha: str
    tree_sha: str
    published: bool
    draft: bool
    immutable: bool
    release_attestation_verified: bool
    release_attestation_digest: str
    assets: tuple[ReleaseAssetBinding, ...]
    revocation_generation: int = Field(ge=0)

    _identity = field_validator(
        "repository", "github_release_url", "tag_name"
    )(_require_identity)
    _shas = field_validator("commit_sha", "tree_sha")(_require_git_sha)
    _digest = field_validator("release_attestation_digest")(_require_digest)


class ReleaseCertificate(ArtifactCompatibility):
    """GitHub 发布、不可变与资产 attestation 全部一致后的 generation-0 证书。"""

    model_config = _MODEL_CONFIG

    schema_version: Literal["release-certificate.v1"] = "release-certificate.v1"
    repository: str
    github_release_id: int = Field(ge=1)
    github_release_url: str
    tag_name: str
    commit_sha: str
    tree_sha: str
    proof_digest: str
    release_attestation_digest: str
    assets: tuple[ReleaseAssetBinding, ...]
    immutable: Literal[True] = True
    revocation_generation: Literal[0] = 0
    issued_at: str
    certificate_digest: str = ""

    _identity = field_validator(
        "repository", "github_release_url", "tag_name"
    )(_require_identity)
    _shas = field_validator("commit_sha", "tree_sha")(_require_git_sha)
    _digests = field_validator("proof_digest", "release_attestation_digest")(
        _require_digest
    )
    _timestamp = field_validator("issued_at")(_require_utc_timestamp)

    @model_validator(mode="after")
    def _validate_certificate(self) -> Self:
        names = tuple(asset.name for asset in self.assets)
        if not names or names != tuple(sorted(set(names))):
            raise ValueError("release certificate assets are not canonical")
        return fill_artifact_digest(self, "certificate_digest")


class RevocationSignal(BaseModel):
    """可重放为 Receipt 的后验安全信号；自身不是持久化 artifact kind。"""

    model_config = _MODEL_CONFIG

    reason_code: str
    evidence_digest: str
    work_item_id: str
    observed_at: str

    _identity = field_validator("reason_code", "work_item_id")(_require_identity)
    _digest = field_validator("evidence_digest")(_require_digest)
    _timestamp = field_validator("observed_at")(_require_utc_timestamp)


class ReleaseRevocationReceipt(ArtifactCompatibility):
    """追加式、单调 generation 的唯一撤销线性化工件。"""

    model_config = _MODEL_CONFIG

    schema_version: Literal["release-revocation-receipt.v1"] = (
        "release-revocation-receipt.v1"
    )
    repository: str
    tag_name: str
    certificate_digest: str
    generation: int = Field(ge=1)
    predecessor_receipt_digest: str
    reason_code: str
    evidence_digest: str
    work_item_id: str
    observed_at: str
    receipt_digest: str = ""

    _identity = field_validator(
        "repository", "tag_name", "reason_code", "work_item_id"
    )(_require_identity)
    _digests = field_validator("certificate_digest", "evidence_digest")(
        _require_digest
    )
    _timestamp = field_validator("observed_at")(_require_utc_timestamp)

    @field_validator("predecessor_receipt_digest")
    @classmethod
    def _validate_predecessor_digest(cls, value: str) -> str:
        if value:
            return _require_digest(value)
        return value

    @model_validator(mode="after")
    def _validate_receipt(self) -> Self:
        if self.generation == 1 and self.predecessor_receipt_digest:
            raise ValueError("generation 1 receipt must not have a predecessor")
        if self.generation > 1 and not self.predecessor_receipt_digest:
            raise ValueError("later receipt requires predecessor digest")
        return fill_artifact_digest(self, "receipt_digest")


class ReleaseTrustDecision(BaseModel):
    """从 GitHub、Certificate 与完整 Receipt 链派生的只读推荐判定。"""

    model_config = _MODEL_CONFIG

    status: Literal["trusted", "untrusted", "unknown"]
    reason_code: str
    certificate_digest: str = ""
    revocation_generation: int = Field(ge=0)
    observed_at: str

    _reason = field_validator("reason_code")(_require_identity)
    _timestamp = field_validator("observed_at")(_require_utc_timestamp)


__all__ = [
    "ReleaseAssetBinding",
    "ReleaseCandidateSnapshot",
    "ReleaseCertificate",
    "ReleaseRevocationReceipt",
    "ReleaseSatisfactionProof",
    "ReleaseTrustDecision",
    "PublishedReleaseSnapshot",
    "RequiredGateBinding",
    "RevocationSignal",
]
