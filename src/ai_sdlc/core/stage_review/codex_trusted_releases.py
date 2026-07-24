"""Codex 0.138.0 官方 npm 发布物的版本化 exact allowlist。"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import re
from collections.abc import Mapping

from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.isolation_backend_identity import (
    ProtectedCIAttestation,
    TrustedBackendReleaseManifest,
)
from ai_sdlc.core.stage_review.isolation_backend_identity import (
    _bind_protected_ci_attestation as bind_protected_ci_attestation,
)
from ai_sdlc.core.stage_review.isolation_backend_identity import (
    _build_protected_ci_attestation as build_protected_ci_attestation,
)
from ai_sdlc.core.stage_review.isolation_backend_identity import (
    _build_trusted_backend_release_manifest as build_trusted_backend_release_manifest,
)
from ai_sdlc.core.stage_review.isolation_backend_identity import (
    _host_backend_platform as host_backend_platform,
)

_CODEX_VERSION = "0.138.0"
_SLSA_PROVENANCE_TYPE = "https://slsa.dev/provenance/v1"
_IN_TOTO_PAYLOAD_TYPE = "application/vnd.in-toto+json"
_IN_TOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
_RELEASE_REPOSITORY = "https://github.com/openai/codex"
_RELEASE_WORKFLOW_PATH = ".github/workflows/rust-release.yml"
_RELEASE_REF = "refs/tags/rust-v0.138.0"
_GITHUB_HOSTED_BUILDER = "https://github.com/actions/runner/github-hosted"
_RELEASE_WORKFLOW_REF = f"{_RELEASE_REPOSITORY}/{_RELEASE_WORKFLOW_PATH}@{_RELEASE_REF}"

_RELEASES = {
    ("macos", "x64"): (
        "darwin-x64",
        "sha512-N7du+rxsvubFYhEgiUWN3lZyjdNdVrqwLhUrjUHsb1ZrlIakV+1alIBonURQ0lRunU8SWZ+Ncfsw5eryejooyw==",
        "vendor/x86_64-apple-darwin/bin/codex",
        "sha256:5fa956b2654f96517ff05661d65b37586993b32dc67df4a92f2fa3b1c71f734d",
    ),
    ("macos", "arm64"): (
        "darwin-arm64",
        "sha512-kQyY2E25GVUUmNhHwUzg8CylA71SX/3H8Fy8h/hy6lu+uB/I5dWzwpQlcMqAFJToT8lPS6FjHX0UYOizpPle0g==",
        "vendor/aarch64-apple-darwin/bin/codex",
        "sha256:708f8e554c28bc4bb4a01270cbaea99a5f683e02ebcb0b77511880d3d3a15c5a",
    ),
    ("linux", "x64"): (
        "linux-x64",
        "sha512-/nLUrXRPthrSLZWtDGrn/LkREDJCz3oLoaEknYCVZVDmiMnoB3IEgBdmoLqeyor4uKOs0/AN0h/opqmJrRBf8w==",
        "vendor/x86_64-unknown-linux-musl/bin/codex",
        "sha256:ee36a80bb1116daf0b027fbbf8a12e0e772f676fa31376a3c48e461f057fdbc5",
    ),
    ("linux", "arm64"): (
        "linux-arm64",
        "sha512-ZMnhHRPJk+tx9PTVcjbWkNr+WniTUNDax3sQ2+WusV8XWJbdtFPA9v3kbcUqN5f5iw3Aqzgl/PvbHVJzum7cEg==",
        "vendor/aarch64-unknown-linux-musl/bin/codex",
        "sha256:4713f59cdde8ef8cce1a26119fbfd1d7fbf86fd3bf7537dc0af2bf5d74740008",
    ),
    ("windows", "x64"): (
        "win32-x64",
        "sha512-VbX+EgSdIAMMNwOzGOd1w+iCBq7N53JQC/11iSnNMTn7bWKTo1Ho3mp1UP7VJxvRoBVRRQxPErcvpvIuQkTwCA==",
        "vendor/x86_64-pc-windows-msvc/bin/codex.exe",
        "sha256:c2e50fa58a6fad1f5be0bbb121d4f161573d8a0f67a14a9dc31027ed0a9b5b9e",
    ),
    ("windows", "arm64"): (
        "win32-arm64",
        "sha512-91EdgXIKnhbBUoNe0iK8TAnBoaXoIG/YZhBi9R50mx54wWVuVCYE3dWW/p0lby4ckbBxDZ67lwR9IgIpj5mNYw==",
        "vendor/aarch64-pc-windows-msvc/bin/codex.exe",
        "sha256:70ff70aad19b940ab9c7646b98ba3e4b04fa318b002268702967d2ba5e86aef2",
    ),
}

_OFFICIAL_NPM_PROVENANCE = {
    (_CODEX_VERSION, "macos", "x64"): (
        "sha256:6e77f2b56b7675d21d6a56a2453f83f2c492bb013c41d32e8badc34c722a1f1d"
    ),
    (_CODEX_VERSION, "macos", "arm64"): (
        "sha256:6fd2951d33771346f1ce2e3381ea827258b8683c178e672bed82c4e7976549f3"
    ),
    (_CODEX_VERSION, "linux", "x64"): (
        "sha256:64a53dbb72227453c3620d58a43ab6e6934148d830d0b8a6a9f6c21c80f059f6"
    ),
    (_CODEX_VERSION, "linux", "arm64"): (
        "sha256:4a9f9c9f5cd2fbe54fef6c01687b5d6d67905dd36cc521a0aca5199669073313"
    ),
    (_CODEX_VERSION, "windows", "x64"): (
        "sha256:63efebef91fd7d78520816820c1524cccdabd1c3d9c0e6db0d133f8e7efb3a9a"
    ),
    (_CODEX_VERSION, "windows", "arm64"): (
        "sha256:8b1bff908ffd40468ebf5d0d0ce757fc8c73fe845eaf2cdd09eb0c22ea610c5b"
    ),
}


def published_codex_release(
    platform_id: str | None = None,
    architecture: str | None = None,
) -> TrustedBackendReleaseManifest | None:
    host_platform, host_architecture = host_backend_platform()
    key = (platform_id or host_platform, architecture or host_architecture)
    values = _RELEASES.get(key)
    if values is None:
        return None
    package_variant, package_integrity, native_path, native_sha256 = values
    return build_trusted_backend_release_manifest(
        backend_id="codex.permission-profile",
        contract_version="2026-07-01",
        exact_backend_version=_CODEX_VERSION,
        ecosystem="npm",
        package_name=f"@openai/codex-{package_variant}",
        package_version=f"{_CODEX_VERSION}-{package_variant}",
        platform_id=key[0],
        architecture=key[1],
        package_integrity=package_integrity,
        shim_resolver_id="codex-npm-layout.v1",
        native_relative_path=native_path,
        native_sha256=native_sha256,
        profile_digest="sha256:5d004878289b0d9ffef465573162f72617251685378f7913334d069b491dbcd9",
        policy_pin_digest="sha256:1c6f31075b2fd0f10b8db04946bd8ccf63e388c5e4f33ef2c69e995c8d68db34",
        ci_attestation_subject=(
            f"pkg:npm/%40openai/codex@{_CODEX_VERSION}-{package_variant}"
        ),
        ci_attestation_workflow_ref=_RELEASE_WORKFLOW_REF,
        ci_attestation_digest="",
        ci_attestation_verified=False,
        revocation_metadata_digest="sha256:7d2843148886a44f696453b778b3b2043b5bb3ae8d852814d462df9e39c4e5cc",
        revoked=False,
    )


def _published_codex_attestation(
    platform_id: str | None = None,
    architecture: str | None = None,
) -> ProtectedCIAttestation | None:
    release = published_codex_release(platform_id, architecture)
    if release is None:
        return None
    run_identity_digest = _OFFICIAL_NPM_PROVENANCE.get(
        (release.exact_backend_version, release.platform_id, release.architecture)
    )
    if run_identity_digest is None:
        return None
    return build_protected_ci_attestation(
        release_manifest_digest=release.manifest_digest,
        subject=release.ci_attestation_subject,
        workflow_ref=release.ci_attestation_workflow_ref,
        run_identity_digest=run_identity_digest,
    )


def _trusted_published_codex_release(
    platform_id: str | None = None,
    architecture: str | None = None,
    *,
    registry_attestations: object | None = None,
) -> TrustedBackendReleaseManifest | None:
    release = published_codex_release(platform_id, architecture)
    expected = _published_codex_attestation(platform_id, architecture)
    if release is None or expected is None:
        return None
    if registry_attestations is None:
        return None
    authority = _verify_published_codex_npm_attestations(
        registry_attestations,
        platform_id,
        architecture,
    )
    if authority != expected:
        raise ValueError("official Codex provenance attestation is invalid")
    return bind_protected_ci_attestation(release, authority)


def _trusted_published_codex_release_digests() -> tuple[str, ...]:
    manifests = []
    for platform_id, architecture in sorted(_RELEASES):
        release = published_codex_release(platform_id, architecture)
        attestation = _published_codex_attestation(platform_id, architecture)
        if release is None or attestation is None:
            raise ValueError("published Codex release allowlist is incomplete")
        manifests.append(
            bind_protected_ci_attestation(release, attestation).manifest_digest
        )
    return tuple(sorted(manifests))


def _trusted_published_codex_release_digest(
    platform_id: str | None = None,
    architecture: str | None = None,
) -> str:
    release = published_codex_release(platform_id, architecture)
    attestation = _published_codex_attestation(platform_id, architecture)
    if release is None or attestation is None:
        raise ValueError("published Codex release allowlist is incomplete")
    return bind_protected_ci_attestation(release, attestation).manifest_digest


def verify_published_codex_npm_attestations(
    registry_attestations: object,
    platform_id: str | None = None,
    architecture: str | None = None,
) -> ProtectedCIAttestation:
    """校验官方 Codex npm provenance，作为 CI 使用的稳定公开入口。"""

    return _verify_published_codex_npm_attestations(
        registry_attestations,
        platform_id,
        architecture,
    )


def _verify_published_codex_npm_attestations(
    registry_attestations: object,
    platform_id: str | None = None,
    architecture: str | None = None,
) -> ProtectedCIAttestation:
    release = published_codex_release(platform_id, architecture)
    expected = _published_codex_attestation(platform_id, architecture)
    if release is None or expected is None:
        raise ValueError("published Codex provenance is unavailable")
    entry = _select_slsa_provenance(registry_attestations)
    bundle = _mapping(entry.get("bundle"), "SLSA provenance bundle")
    envelope = _mapping(bundle.get("dsseEnvelope"), "DSSE envelope")
    observed_digest = canonical_digest(
        {
            "predicateType": entry.get("predicateType"),
            "dsseEnvelope": envelope,
        },
        CanonicalizationPolicy(),
    )
    if not hmac.compare_digest(observed_digest, expected.run_identity_digest):
        raise ValueError("published Codex provenance digest does not match")
    statement = _decode_dsse_statement(bundle)
    _verify_dsse_lineage(statement, release)
    return expected


def _select_slsa_provenance(value: object) -> Mapping[str, object]:
    root = _mapping(value, "npm registry attestations")
    attestations = root.get("attestations")
    if not isinstance(attestations, list):
        raise ValueError("npm registry attestations are invalid")
    matches = []
    for value in attestations:
        entry = _mapping(value, "npm registry attestation")
        if entry.get("predicateType") == _SLSA_PROVENANCE_TYPE:
            matches.append(entry)
    if len(matches) != 1:
        raise ValueError("npm registry SLSA provenance is ambiguous or missing")
    return matches[0]


def _decode_dsse_statement(bundle: Mapping[str, object]) -> Mapping[str, object]:
    envelope = _mapping(bundle.get("dsseEnvelope"), "DSSE envelope")
    if envelope.get("payloadType") != _IN_TOTO_PAYLOAD_TYPE:
        raise ValueError("DSSE payload type is invalid")
    payload = envelope.get("payload")
    if not isinstance(payload, str) or not payload:
        raise ValueError("DSSE payload is invalid")
    try:
        decoded = base64.b64decode(payload, validate=True).decode("utf-8")
        statement = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("DSSE payload is invalid") from exc
    return _mapping(statement, "DSSE payload")


def _verify_dsse_lineage(
    statement: Mapping[str, object],
    release: TrustedBackendReleaseManifest,
) -> None:
    subjects = statement.get("subject")
    if not isinstance(subjects, list) or len(subjects) != 1:
        raise ValueError("published Codex DSSE lineage is invalid")
    subject = _mapping(subjects[0], "published Codex DSSE subject")
    digest = _mapping(subject.get("digest"), "published Codex DSSE digest")
    workflow = _nested_mapping(
        statement, "predicate", "buildDefinition", "externalParameters", "workflow"
    )
    builder = _nested_mapping(statement, "predicate", "runDetails", "builder")
    metadata = _nested_mapping(statement, "predicate", "runDetails", "metadata")
    invocation = metadata.get("invocationId")
    checks = (
        statement.get("_type") == _IN_TOTO_STATEMENT_TYPE,
        statement.get("predicateType") == _SLSA_PROVENANCE_TYPE,
        subject.get("name") == release.ci_attestation_subject,
        digest.get("sha512") == _package_sha512_hex(release.package_integrity),
        workflow.get("repository") == _RELEASE_REPOSITORY,
        workflow.get("path") == _RELEASE_WORKFLOW_PATH,
        workflow.get("ref") == _RELEASE_REF,
        builder.get("id") == _GITHUB_HOSTED_BUILDER,
        isinstance(invocation, str) and _valid_invocation_url(invocation),
    )
    if not all(checks):
        raise ValueError("published Codex DSSE lineage is invalid")


def _nested_mapping(value: object, *keys: str) -> Mapping[str, object]:
    current = _mapping(value, "published Codex DSSE lineage")
    for key in keys:
        current = _mapping(current.get(key), "published Codex DSSE lineage")
    return current


def _package_sha512_hex(package_integrity: str) -> str:
    if not package_integrity.startswith("sha512-"):
        raise ValueError("published Codex DSSE lineage is invalid")
    try:
        return base64.b64decode(package_integrity[7:], validate=True).hex()
    except binascii.Error as exc:
        raise ValueError("published Codex DSSE lineage is invalid") from exc


def _valid_invocation_url(value: str) -> bool:
    pattern = re.compile(
        r"https://github\.com/openai/codex/actions/runs/[1-9][0-9]*/attempts/[1-9][0-9]*"
    )
    return pattern.fullmatch(value) is not None


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is invalid")
    return value
