"""激活评估引用的不可变隔离与质量来源记录。"""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import ConfigDict, field_validator, model_validator

from ai_sdlc.core.stage_review.activation_models import (
    ActivationProbeEvidence,
    IsolationPlatformEvidence,
)
from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.registry_versions import (
    normalize_text_set,
    require_machine_id,
)


class ActivationIsolationSourceRecord(ArtifactCompatibility):
    schema_version: Literal["activation-isolation-source-record.v1"] = (
        "activation-isolation-source-record.v1"
    )
    artifact_kind: Literal["activation-isolation-source-record"] = (
        "activation-isolation-source-record"
    )
    project_id: str
    evidence: IsolationPlatformEvidence
    source_attestation_digests: tuple[str, ...]
    import_receipt_digest: str
    record_digest: str = ""

    @field_validator("project_id")
    @classmethod
    def _project_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "activation source project_id")

    @field_validator("source_attestation_digests", mode="before")
    @classmethod
    def _normalize_sources(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_text_set(value))

    @model_validator(mode="after")
    def _verify_record(self) -> Self:
        if not self.source_attestation_digests:
            raise ValueError("activation isolation source attestations are missing")
        digests = (
            *self.source_attestation_digests,
            self.evidence.evidence_digest,
            self.import_receipt_digest,
        )
        if any(not _valid_sha256(item) for item in digests):
            raise ValueError("activation isolation source digest is invalid")
        return fill_artifact_digest(self, "record_digest")


class ActivationProbeSourceRecord(ArtifactCompatibility):
    schema_version: Literal["activation-probe-source-record.v1"] = (
        "activation-probe-source-record.v1"
    )
    artifact_kind: Literal["activation-probe-source-record"] = (
        "activation-probe-source-record"
    )
    project_id: str
    evidence: ActivationProbeEvidence
    source_artifact_digests: tuple[str, ...]
    import_receipt_digest: str
    record_digest: str = ""

    @field_validator("project_id")
    @classmethod
    def _project_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "activation source project_id")

    @field_validator("source_artifact_digests", mode="before")
    @classmethod
    def _normalize_sources(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_text_set(value))

    @model_validator(mode="after")
    def _verify_record(self) -> Self:
        if not self.source_artifact_digests:
            raise ValueError("activation probe source artifacts are missing")
        digests = (*self.source_artifact_digests, self.import_receipt_digest)
        if any(not _valid_sha256(item) for item in digests):
            raise ValueError("activation probe source digest is invalid")
        return fill_artifact_digest(self, "record_digest")


class ActivationEvidencePackage(ArtifactCompatibility):
    schema_version: Literal["activation-evidence-package.v2"] = (
        "activation-evidence-package.v2"
    )
    artifact_kind: Literal["activation-evidence-package"] = (
        "activation-evidence-package"
    )
    project_id: str
    repository: str
    tested_commit: str
    signer_workflow: str
    evidence_purpose: Literal["stage-gate-activation"] = "stage-gate-activation"
    isolation_matrix: tuple[IsolationPlatformEvidence, ...]
    probes: ActivationProbeEvidence
    source_artifact_digests: tuple[str, ...]
    package_digest: str = ""

    @field_validator("project_id")
    @classmethod
    def _package_project_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "activation package project_id")

    @field_validator("source_artifact_digests", mode="before")
    @classmethod
    def _normalize_artifacts(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_text_set(value))

    @model_validator(mode="after")
    def _verify_package(self) -> Self:
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository) is None:
            raise ValueError("activation evidence repository is invalid")
        if re.fullmatch(r"[0-9a-f]{40}", self.tested_commit) is None:
            raise ValueError("activation evidence tested commit is invalid")
        prefix = f"{self.repository}/.github/workflows/"
        if not self.signer_workflow.startswith(prefix):
            raise ValueError("activation evidence signer workflow is invalid")
        platforms = tuple(item.platform_id for item in self.isolation_matrix)
        if len(platforms) != len(set(platforms)) or not platforms:
            raise ValueError("activation evidence isolation matrix is invalid")
        if not self.source_artifact_digests or any(
            not _valid_sha256(item) for item in self.source_artifact_digests
        ):
            raise ValueError("activation evidence source artifact is invalid")
        return fill_artifact_digest(self, "package_digest")


class ActivationEvidenceImportReceipt(ArtifactCompatibility):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["activation-evidence-import-receipt.v1"] = (
        "activation-evidence-import-receipt.v1"
    )
    artifact_kind: Literal["activation-evidence-import-receipt"] = (
        "activation-evidence-import-receipt"
    )
    project_id: str
    repository: str
    tested_commit: str
    signer_workflow: str
    evidence_purpose: str
    activation_policy_digest: str
    package_digest: str
    artifact_path: str
    artifact_digest: str
    bundle_path: str
    bundle_digest: str
    verification_output_digest: str
    receipt_digest: str = ""

    @model_validator(mode="after")
    def _verify_receipt(self) -> Self:
        identity = (
            self.project_id,
            self.repository,
            self.signer_workflow,
            self.evidence_purpose,
            self.activation_policy_digest,
        )
        paths = (self.artifact_path, self.bundle_path)
        digests = (
            self.package_digest,
            self.artifact_digest,
            self.bundle_digest,
            self.verification_output_digest,
        )
        if any(not item.strip() or item != item.strip() for item in identity):
            raise ValueError("activation evidence import identity is invalid")
        if any(not item or item.startswith(("/", "\\")) or ".." in item for item in paths):
            raise ValueError("activation evidence import path is invalid")
        if re.fullmatch(r"[0-9a-f]{40}", self.tested_commit) is None:
            raise ValueError("activation evidence import commit is invalid")
        if any(not _valid_sha256(item) for item in digests):
            raise ValueError("activation evidence import digest is invalid")
        return fill_artifact_digest(self, "receipt_digest")


def _valid_sha256(value: str) -> bool:
    if not value.startswith("sha256:") or len(value) != 71:
        return False
    try:
        int(value[7:], 16)
    except ValueError:
        return False
    return True
