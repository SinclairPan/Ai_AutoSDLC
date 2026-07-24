"""隔离 Backend 的可信发布清单与每次运行时二进制复验。"""

from __future__ import annotations

import hashlib
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import ConfigDict, model_validator

from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.contracts import StageReviewArtifactModel
from ai_sdlc.core.stage_review.resource_builders import stable_id, utc_iso

_RUNTIME = frozenset({"created_at", "created_by", "ai_sdlc_version", "verified_at"})


class TrustedBackendReleaseManifest(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["trusted-backend-release-manifest"] = (
        "trusted-backend-release-manifest"
    )
    release_id: str
    backend_id: str
    contract_version: str
    exact_backend_version: str
    ecosystem: Literal["npm"]
    package_name: str
    package_version: str
    platform_id: Literal["linux", "macos", "windows"]
    architecture: str
    package_integrity: str
    shim_resolver_id: str
    native_relative_path: str
    native_sha256: str
    profile_digest: str
    policy_pin_digest: str
    ci_attestation_subject: str
    ci_attestation_workflow_ref: str
    ci_attestation_digest: str = ""
    ci_attestation_verified: bool = False
    revocation_metadata_digest: str
    revoked: bool
    manifest_digest: str

    @model_validator(mode="after")
    def _verify_manifest(self) -> Self:
        values = (
            self.release_id,
            self.backend_id,
            self.contract_version,
            self.exact_backend_version,
            self.package_name,
            self.package_version,
            self.architecture,
            self.package_integrity,
            self.shim_resolver_id,
            self.native_relative_path,
            self.native_sha256,
            self.profile_digest,
            self.policy_pin_digest,
            self.ci_attestation_subject,
            self.ci_attestation_workflow_ref,
            self.revocation_metadata_digest,
        )
        if any(not item.strip() or item != item.strip() for item in values):
            raise ValueError("trusted backend release identity is invalid")
        if not self.package_integrity.startswith("sha512"):
            raise ValueError("trusted backend package integrity must be SHA-512")
        if not _valid_sha256(self.native_sha256):
            raise ValueError("trusted backend native digest is invalid")
        if not self.package_version.startswith(f"{self.exact_backend_version}-"):
            raise ValueError("trusted backend platform package version is invalid")
        if not _valid_sha256(self.policy_pin_digest):
            raise ValueError("trusted backend policy pin digest is invalid")
        if self.ci_attestation_verified != bool(self.ci_attestation_digest):
            raise ValueError("trusted backend CI attestation state is inconsistent")
        if self.ci_attestation_digest and not _valid_sha256(self.ci_attestation_digest):
            raise ValueError("trusted backend CI attestation digest is invalid")
        if not _valid_sha256(self.revocation_metadata_digest):
            raise ValueError("trusted backend revocation metadata is invalid")
        if Path(self.native_relative_path).is_absolute():
            raise ValueError("trusted backend native path must be relative")
        if self.manifest_digest != _digest(self, "manifest_digest"):
            raise ValueError("trusted backend release manifest digest is invalid")
        return self


class VerifiedBackendRuntimeIdentity(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["verified-backend-runtime-identity"] = (
        "verified-backend-runtime-identity"
    )
    identity_id: str
    release_manifest_digest: str
    backend_id: str
    contract_version: str
    exact_backend_version: str
    platform_id: Literal["linux", "macos", "windows"]
    architecture: str
    resolved_native_path: str
    native_sha256: str
    verified_at: str
    identity_digest: str

    @model_validator(mode="after")
    def _verify_identity(self) -> Self:
        if not Path(self.resolved_native_path).is_absolute():
            raise ValueError("verified backend path must be absolute")
        if self.identity_digest != _digest(self, "identity_digest"):
            raise ValueError("verified backend runtime identity digest is invalid")
        return self


class ProtectedCIAttestation(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["protected-ci-attestation"] = "protected-ci-attestation"
    release_manifest_digest: str
    subject: str
    workflow_ref: str
    run_identity_digest: str
    attestation_digest: str

    @model_validator(mode="after")
    def _verify_attestation(self) -> Self:
        if not _valid_sha256(self.run_identity_digest):
            raise ValueError("protected CI run identity digest is invalid")
        if self.attestation_digest != _digest(self, "attestation_digest"):
            raise ValueError("protected CI attestation digest is invalid")
        return self


def _build_trusted_backend_release_manifest(
    **values: object,
) -> TrustedBackendReleaseManifest:
    release_id = stable_id(
        "trusted-backend-release",
        str(values["backend_id"]),
        str(values["exact_backend_version"]),
        str(values["platform_id"]),
        str(values["architecture"]),
    )
    canonical = {**values, "release_id": release_id}
    draft = TrustedBackendReleaseManifest.model_construct(
        **canonical,  # type: ignore[arg-type]
        manifest_digest="",
    )
    return TrustedBackendReleaseManifest.model_validate(
        {**canonical, "manifest_digest": _digest(draft, "manifest_digest")}
    )


def _bind_protected_ci_attestation(
    release: TrustedBackendReleaseManifest,
    attestation: ProtectedCIAttestation,
) -> TrustedBackendReleaseManifest:
    authority = ProtectedCIAttestation.model_validate(
        attestation.model_dump(mode="json")
    )
    if (
        authority.release_manifest_digest != release.manifest_digest
        or authority.subject != release.ci_attestation_subject
        or authority.workflow_ref != release.ci_attestation_workflow_ref
    ):
        raise ValueError("protected CI attestation lineage is invalid")
    values = release.model_dump(mode="json", exclude={"manifest_digest"})
    values["ci_attestation_digest"] = authority.attestation_digest
    values["ci_attestation_verified"] = True
    return _build_trusted_backend_release_manifest(**values)


def _build_protected_ci_attestation(**values: object) -> ProtectedCIAttestation:
    draft = ProtectedCIAttestation.model_construct(
        **values,  # type: ignore[arg-type]
        attestation_digest="",
    )
    return ProtectedCIAttestation.model_validate(
        {**values, "attestation_digest": _digest(draft, "attestation_digest")}
    )


def _verify_backend_runtime_identity(
    release: TrustedBackendReleaseManifest,
    executable: Path,
    *,
    observed_backend_version: str,
    now: datetime | None = None,
) -> VerifiedBackendRuntimeIdentity:
    trusted = TrustedBackendReleaseManifest.model_validate(
        release.model_dump(mode="json")
    )
    if not trusted.ci_attestation_verified:
        raise ValueError("trusted backend protected CI attestation is missing")
    if trusted.revoked:
        raise ValueError("trusted backend release is revoked")
    if observed_backend_version != trusted.exact_backend_version:
        raise ValueError("trusted backend exact version does not match")
    current_platform, current_arch = _host_identity()
    if (trusted.platform_id, trusted.architecture) != (
        current_platform,
        current_arch,
    ):
        raise ValueError("trusted backend platform identity does not match")
    resolved = executable.resolve(strict=True)
    digest = _binary_sha256(resolved)
    if digest != trusted.native_sha256:
        raise ValueError("trusted backend native binary does not match")
    values = {
        "identity_id": stable_id(
            "verified-backend-runtime",
            trusted.manifest_digest,
            str(resolved),
            digest,
        ),
        "release_manifest_digest": trusted.manifest_digest,
        "backend_id": trusted.backend_id,
        "contract_version": trusted.contract_version,
        "exact_backend_version": trusted.exact_backend_version,
        "platform_id": trusted.platform_id,
        "architecture": trusted.architecture,
        "resolved_native_path": str(resolved),
        "native_sha256": digest,
        "verified_at": utc_iso(now or datetime.now(UTC)),
    }
    draft = VerifiedBackendRuntimeIdentity.model_construct(
        **values,  # type: ignore[arg-type]
        identity_digest="",
    )
    return VerifiedBackendRuntimeIdentity.model_validate(
        {**values, "identity_digest": _digest(draft, "identity_digest")}
    )


def _host_identity() -> tuple[str, str]:
    system = platform.system().lower()
    supported_platforms = {
        "darwin": "macos",
        "linux": "linux",
        "windows": "windows",
    }
    if system not in supported_platforms:
        raise ValueError(f"unsupported host operating system: {system or 'unknown'}")
    platform_id = supported_platforms[system]
    architecture = platform.machine().lower()
    architecture = {
        "amd64": "x64",
        "x86_64": "x64",
        "aarch64": "arm64",
    }.get(architecture, architecture)
    return platform_id, architecture


def _host_backend_platform() -> tuple[str, str]:
    return _host_identity()


def _binary_sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _valid_sha256(value: str) -> bool:
    if not value.startswith("sha256:") or len(value) != 71:
        return False
    try:
        int(value[7:], 16)
    except ValueError:
        return False
    return True


def _digest(value: object, field: str) -> str:
    return canonical_digest(
        value,
        CanonicalizationPolicy(excluded_fields=_RUNTIME | {field}),
    )
