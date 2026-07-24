from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    (
        "platform_id",
        "architecture",
        "variant",
        "package_integrity",
        "native_path",
        "native_sha",
    ),
    (
        (
            "macos",
            "arm64",
            "darwin-arm64",
            "sha512-kQyY2E25GVUUmNhHwUzg8CylA71SX/3H8Fy8h/hy6lu+uB/I5dWzwpQlcMqAFJToT8lPS6FjHX0UYOizpPle0g==",
            "vendor/aarch64-apple-darwin/bin/codex",
            "sha256:708f8e554c28bc4bb4a01270cbaea99a5f683e02ebcb0b77511880d3d3a15c5a",
        ),
        (
            "macos",
            "x64",
            "darwin-x64",
            "sha512-N7du+rxsvubFYhEgiUWN3lZyjdNdVrqwLhUrjUHsb1ZrlIakV+1alIBonURQ0lRunU8SWZ+Ncfsw5eryejooyw==",
            "vendor/x86_64-apple-darwin/bin/codex",
            "sha256:5fa956b2654f96517ff05661d65b37586993b32dc67df4a92f2fa3b1c71f734d",
        ),
        (
            "linux",
            "x64",
            "linux-x64",
            "sha512-/nLUrXRPthrSLZWtDGrn/LkREDJCz3oLoaEknYCVZVDmiMnoB3IEgBdmoLqeyor4uKOs0/AN0h/opqmJrRBf8w==",
            "vendor/x86_64-unknown-linux-musl/bin/codex",
            "sha256:ee36a80bb1116daf0b027fbbf8a12e0e772f676fa31376a3c48e461f057fdbc5",
        ),
        (
            "linux",
            "arm64",
            "linux-arm64",
            "sha512-ZMnhHRPJk+tx9PTVcjbWkNr+WniTUNDax3sQ2+WusV8XWJbdtFPA9v3kbcUqN5f5iw3Aqzgl/PvbHVJzum7cEg==",
            "vendor/aarch64-unknown-linux-musl/bin/codex",
            "sha256:4713f59cdde8ef8cce1a26119fbfd1d7fbf86fd3bf7537dc0af2bf5d74740008",
        ),
        (
            "windows",
            "x64",
            "win32-x64",
            "sha512-VbX+EgSdIAMMNwOzGOd1w+iCBq7N53JQC/11iSnNMTn7bWKTo1Ho3mp1UP7VJxvRoBVRRQxPErcvpvIuQkTwCA==",
            "vendor/x86_64-pc-windows-msvc/bin/codex.exe",
            "sha256:c2e50fa58a6fad1f5be0bbb121d4f161573d8a0f67a14a9dc31027ed0a9b5b9e",
        ),
        (
            "windows",
            "arm64",
            "win32-arm64",
            "sha512-91EdgXIKnhbBUoNe0iK8TAnBoaXoIG/YZhBi9R50mx54wWVuVCYE3dWW/p0lby4ckbBxDZ67lwR9IgIpj5mNYw==",
            "vendor/aarch64-pc-windows-msvc/bin/codex.exe",
            "sha256:70ff70aad19b940ab9c7646b98ba3e4b04fa318b002268702967d2ba5e86aef2",
        ),
    ),
)
def test_published_release_table_covers_six_exact_platform_packages(
    platform_id: str,
    architecture: str,
    variant: str,
    package_integrity: str,
    native_path: str,
    native_sha: str,
) -> None:
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        published_codex_release,
    )

    release = published_codex_release(platform_id, architecture)

    assert release is not None
    assert release.exact_backend_version == "0.138.0"
    assert release.package_name == f"@openai/codex-{variant}"
    assert release.package_version == f"0.138.0-{variant}"
    assert release.package_integrity == package_integrity
    assert release.native_relative_path == native_path
    assert release.native_sha256 == native_sha
    assert release.policy_pin_digest != release.package_integrity
    assert release.ci_attestation_verified is False


def test_published_release_table_has_no_unknown_architecture_fallback() -> None:
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        published_codex_release,
    )

    assert published_codex_release("linux", "riscv64") is None


@pytest.mark.parametrize(
    ("platform_id", "architecture", "variant", "run_identity_digest"),
    (
        (
            "macos",
            "x64",
            "darwin-x64",
            "sha256:6e77f2b56b7675d21d6a56a2453f83f2c492bb013c41d32e8badc34c722a1f1d",
        ),
        (
            "macos",
            "arm64",
            "darwin-arm64",
            "sha256:6fd2951d33771346f1ce2e3381ea827258b8683c178e672bed82c4e7976549f3",
        ),
        (
            "linux",
            "x64",
            "linux-x64",
            "sha256:64a53dbb72227453c3620d58a43ab6e6934148d830d0b8a6a9f6c21c80f059f6",
        ),
        (
            "linux",
            "arm64",
            "linux-arm64",
            "sha256:4a9f9c9f5cd2fbe54fef6c01687b5d6d67905dd36cc521a0aca5199669073313",
        ),
        (
            "windows",
            "x64",
            "win32-x64",
            "sha256:63efebef91fd7d78520816820c1524cccdabd1c3d9c0e6db0d133f8e7efb3a9a",
        ),
        (
            "windows",
            "arm64",
            "win32-arm64",
            "sha256:8b1bff908ffd40468ebf5d0d0ce757fc8c73fe845eaf2cdd09eb0c22ea610c5b",
        ),
    ),
)
def test_official_provenance_expectation_covers_published_release(
    platform_id: str,
    architecture: str,
    variant: str,
    run_identity_digest: str,
) -> None:
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        _published_codex_attestation as published_codex_attestation,
    )
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        published_codex_release,
    )

    base = published_codex_release(platform_id, architecture)
    attestation = published_codex_attestation(platform_id, architecture)
    assert attestation is not None

    assert base is not None
    subject = f"pkg:npm/%40openai/codex@0.138.0-{variant}"
    workflow_ref = (
        "https://github.com/openai/codex/.github/workflows/"
        "rust-release.yml@refs/tags/rust-v0.138.0"
    )
    assert base.ci_attestation_verified is False
    assert base.ci_attestation_digest == ""
    assert base.ci_attestation_subject == subject
    assert base.ci_attestation_workflow_ref == workflow_ref
    assert attestation.release_manifest_digest == base.manifest_digest
    assert attestation.subject == subject
    assert attestation.workflow_ref == workflow_ref
    assert attestation.run_identity_digest == run_identity_digest


def test_published_release_without_measured_attestation_is_not_trusted() -> None:
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        _trusted_published_codex_release as trusted_published_codex_release,
    )

    assert trusted_published_codex_release("linux", "x64") is None


def test_trusted_published_release_rejects_codex_0137(tmp_path: Path) -> None:
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        _published_codex_attestation as published_codex_attestation,
    )
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        published_codex_release,
    )
    from ai_sdlc.core.stage_review.isolation_backend_identity import (
        _bind_protected_ci_attestation as bind_protected_ci_attestation,
    )
    from ai_sdlc.core.stage_review.isolation_backend_identity import (
        _verify_backend_runtime_identity as verify_backend_runtime_identity,
    )

    base = published_codex_release()
    attestation = published_codex_attestation()
    assert base is not None
    assert attestation is not None
    release = bind_protected_ci_attestation(base, attestation)
    executable = tmp_path / "codex"
    executable.write_bytes(b"not-reached-for-version-mismatch")

    with pytest.raises(ValueError, match="exact version"):
        verify_backend_runtime_identity(
            release,
            executable,
            observed_backend_version="0.137.0",
        )


def test_unknown_host_operating_system_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.core.stage_review import isolation_backend_identity as identity_api

    monkeypatch.setattr(identity_api.platform, "system", lambda: "FreeBSD")
    monkeypatch.setattr(identity_api.platform, "machine", lambda: "x86_64")

    with pytest.raises(ValueError, match="unsupported host operating system"):
        identity_api._host_backend_platform()


def test_exact_release_manifest_verifies_runtime_and_rejects_binary_change(
    tmp_path: Path,
) -> None:
    from ai_sdlc.core.stage_review.isolation_backend_identity import (
        _build_trusted_backend_release_manifest as build_trusted_backend_release_manifest,
    )
    from ai_sdlc.core.stage_review.isolation_backend_identity import (
        _verify_backend_runtime_identity as verify_backend_runtime_identity,
    )

    executable = tmp_path / "codex"
    executable.write_bytes(b"published-native-binary")
    digest = f"sha256:{hashlib.sha256(executable.read_bytes()).hexdigest()}"
    release = build_trusted_backend_release_manifest(
        backend_id="codex.permission-profile",
        contract_version="2026-07-01",
        exact_backend_version="0.138.0",
        ecosystem="npm",
        package_name=f"@openai/codex-{_package_variant()}",
        package_version=f"0.138.0-{_package_variant()}",
        platform_id=_platform_id(),
        architecture=_architecture_id(),
        package_integrity="sha512:published-package-integrity",
        shim_resolver_id="codex-npm-layout.v1",
        native_relative_path="vendor/native/codex",
        native_sha256=digest,
        profile_digest="sha256:profile",
        policy_pin_digest=f"sha256:{'a' * 64}",
        ci_attestation_subject="@openai/codex-test@0.138.0",
        ci_attestation_workflow_ref="workflow:test",
        ci_attestation_digest=f"sha256:{'c' * 64}",
        ci_attestation_verified=True,
        revocation_metadata_digest=f"sha256:{'b' * 64}",
        revoked=False,
    )

    identity = verify_backend_runtime_identity(
        release,
        executable,
        observed_backend_version="0.138.0",
    )

    assert identity.release_manifest_digest == release.manifest_digest
    assert identity.native_sha256 == digest
    executable.write_bytes(b"changed")
    with pytest.raises(ValueError, match="native binary"):
        verify_backend_runtime_identity(
            release,
            executable,
            observed_backend_version="0.138.0",
        )


def test_release_verification_is_exact_and_factory_selection_has_no_fallback(
    tmp_path: Path,
) -> None:
    from ai_sdlc.core.stage_review.isolation_backend_factory import (
        IsolationBackendBundleFactoryRegistry,
    )
    from ai_sdlc.core.stage_review.isolation_backend_identity import (
        _build_trusted_backend_release_manifest as build_trusted_backend_release_manifest,
    )
    from ai_sdlc.core.stage_review.isolation_backend_identity import (
        _verify_backend_runtime_identity as verify_backend_runtime_identity,
    )

    executable = tmp_path / "codex"
    executable.write_bytes(b"published-native-binary")
    digest = f"sha256:{hashlib.sha256(executable.read_bytes()).hexdigest()}"
    release = build_trusted_backend_release_manifest(
        backend_id="codex.permission-profile",
        contract_version="2026-07-01",
        exact_backend_version="0.138.0",
        ecosystem="npm",
        package_name=f"@openai/codex-{_package_variant()}",
        package_version=f"0.138.0-{_package_variant()}",
        platform_id=_platform_id(),
        architecture=_architecture_id(),
        package_integrity="sha512:published-package-integrity",
        shim_resolver_id="codex-npm-layout.v1",
        native_relative_path="vendor/native/codex",
        native_sha256=digest,
        profile_digest="sha256:profile",
        policy_pin_digest=f"sha256:{'a' * 64}",
        ci_attestation_subject="@openai/codex-test@0.138.0",
        ci_attestation_workflow_ref="workflow:test",
        ci_attestation_digest=f"sha256:{'c' * 64}",
        ci_attestation_verified=True,
        revocation_metadata_digest=f"sha256:{'b' * 64}",
        revoked=False,
    )
    with pytest.raises(ValueError, match="exact version"):
        verify_backend_runtime_identity(
            release,
            executable,
            observed_backend_version="0.139.0",
        )
    identity = verify_backend_runtime_identity(
        release,
        executable,
        observed_backend_version="0.138.0",
    )
    registry = IsolationBackendBundleFactoryRegistry()
    registry.register(
        backend_id=release.backend_id,
        contract_version=release.contract_version,
        platform_id=release.platform_id,
        architecture=release.architecture,
        factory=lambda runtime: ("bundle", runtime.identity_digest),
    )

    assert registry.create(release, identity)[0] == "bundle"
    with pytest.raises(ValueError, match="not registered"):
        registry.create(
            release.model_copy(update={"architecture": "unknown-arch"}),
            identity,
        )
    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            backend_id=release.backend_id,
            contract_version=release.contract_version,
            platform_id=release.platform_id,
            architecture=release.architecture,
            factory=lambda runtime: runtime,
        )


def _platform_id() -> str:
    from ai_sdlc.core.stage_review.isolation_backend_identity import (
        _host_backend_platform as host_backend_platform,
    )

    return host_backend_platform()[0]


def _architecture_id() -> str:
    from ai_sdlc.core.stage_review.isolation_backend_identity import (
        _host_backend_platform as host_backend_platform,
    )

    return host_backend_platform()[1]


def _package_variant() -> str:
    platform_id, architecture = _platform_id(), _architecture_id()
    prefix = (
        "darwin"
        if platform_id == "macos"
        else ("win32" if platform_id == "windows" else "linux")
    )
    return f"{prefix}-{architecture}"
