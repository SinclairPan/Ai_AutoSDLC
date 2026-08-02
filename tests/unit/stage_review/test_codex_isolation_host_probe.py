from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


def test_builtin_host_probe_grants_capability_only_from_cli_contract(
    monkeypatch, tmp_path
) -> None:
    from ai_sdlc.core.stage_review import codex_isolation_host_probe as host_probe_api
    from ai_sdlc.core.stage_review.codex_isolation_host_probe import (
        CodexIsolationHostProbe,
    )

    calls: list[tuple[str, ...]] = []

    def fake_run(command, **kwargs):
        calls.append(tuple(command))
        stdout = (
            "codex-cli 0.138.0"
            if command[-1] == "--version"
            else "--permissions-profile <NAME>"
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(host_probe_api, "platform_mechanism", lambda: ("macos", "seatbelt"))
    executable = _native_path(tmp_path)
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"trusted-codex-binary")
    digest = f"sha256:{hashlib.sha256(executable.read_bytes()).hexdigest()}"
    snapshot = CodexIsolationHostProbe(
        str(executable),
        release_manifest=_release(executable, digest),
    ).probe()

    assert "isolation.codex.permission-profile" in snapshot.capability_ids
    assert "network_enforcement.codex.permission-profile" in snapshot.capability_ids
    assert any(call[-2:] == ("sandbox", "--help") for call in calls)


def test_environment_cannot_upgrade_old_codex_host_probe(monkeypatch) -> None:
    from ai_sdlc.core.stage_review.codex_isolation_host_probe import (
        CodexIsolationHostProbe,
    )

    monkeypatch.setenv("AI_SDLC_ISOLATION_ENFORCED", "1")

    def fake_run(command, **kwargs):
        stdout = "codex-cli 0.137.0" if command[-1] == "--version" else ""
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    snapshot = CodexIsolationHostProbe().probe()

    assert "isolation.codex.permission-profile" not in snapshot.capability_ids
    assert snapshot.capability_source == "builtin-subprocess-probe"


def test_manifest_mismatch_falls_back_to_untrusted_version_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from ai_sdlc.core.stage_review.codex_isolation_host_probe import (
        CodexIsolationHostProbe,
    )

    executable = tmp_path / "ordinary-install" / "codex"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"ordinary-codex-0.137")
    pinned = _native_path(tmp_path / "pinned")
    digest = f"sha256:{hashlib.sha256(b'pinned-codex-0.138').hexdigest()}"

    def fake_run(command, **kwargs):
        stdout = "codex-cli 0.137.0" if command[-1] == "--version" else ""
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    snapshot = CodexIsolationHostProbe(
        str(executable),
        release_manifest=_release(pinned, digest),
    ).probe()

    assert snapshot.capability_ids == ("agent_execution",)
    assert snapshot.backend_id == ""
    assert snapshot.backend_release_manifest_digest == ""
    assert snapshot.backend_runtime_identity_digest == ""


def test_fake_path_codex_cannot_grant_trusted_capability(
    monkeypatch, tmp_path
) -> None:
    from ai_sdlc.core.stage_review.codex_isolation_host_probe import (
        CodexIsolationHostProbe,
    )

    fake = tmp_path / "codex"
    fake.write_text("fake", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path))

    def fake_run(command, **kwargs):
        stdout = (
            "codex-cli 0.138.0"
            if command[-1] == "--version"
            else "--permissions-profile <NAME>"
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    snapshot = CodexIsolationHostProbe().probe()

    assert "isolation.codex.permission-profile" not in snapshot.capability_ids


def test_probe_reuses_snapshot_until_capability_changes_or_expires(
    monkeypatch, tmp_path
) -> None:
    from ai_sdlc.core.stage_review import codex_isolation_host_probe as host_probe_api
    from ai_sdlc.core.stage_review.codex_isolation_host_probe import (
        CodexIsolationHostProbe,
    )

    current = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    executable = _native_path(tmp_path)
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"trusted-codex-binary")
    digest = f"sha256:{hashlib.sha256(executable.read_bytes()).hexdigest()}"

    def fake_run(command, **kwargs):
        stdout = (
            "codex-cli 0.138.0"
            if command[-1] == "--version"
            else "--permissions-profile <NAME>"
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(host_probe_api, "platform_mechanism", lambda: ("macos", "seatbelt"))
    probe = CodexIsolationHostProbe(
        str(executable),
        release_manifest=_release(executable, digest),
        clock=lambda: current,
    )

    first = probe.probe()
    unchanged = probe.probe()
    current += timedelta(minutes=3)
    renewed = probe.probe()
    executable.write_bytes(b"changed-codex-binary")
    changed = probe.probe()

    assert unchanged is first
    assert renewed.snapshot_digest != first.snapshot_digest
    assert renewed.previous_snapshot_digest == first.snapshot_digest
    assert changed.snapshot_digest != renewed.snapshot_digest
    assert changed.previous_snapshot_digest == renewed.snapshot_digest
    assert "isolation.codex.permission-profile" not in changed.capability_ids


def _release(executable, digest):
    from ai_sdlc.core.stage_review.isolation_backend_identity import (
        _build_trusted_backend_release_manifest as build_trusted_backend_release_manifest,
    )
    from ai_sdlc.core.stage_review.isolation_backend_identity import (
        _host_backend_platform as host_backend_platform,
    )

    platform_id, architecture = host_backend_platform()
    prefix, _ = _platform_variant()
    variant = f"{prefix}-{architecture}"
    return build_trusted_backend_release_manifest(
        backend_id="codex.permission-profile",
        contract_version="2026-07-01",
        exact_backend_version="0.138.0",
        ecosystem="npm",
        package_name=f"@openai/codex-{variant}",
        package_version=f"0.138.0-{variant}",
        platform_id=platform_id,
        architecture=architecture,
        package_integrity="sha512:test",
        shim_resolver_id="codex-npm-layout.v1",
        native_relative_path=executable.name,
        native_sha256=digest,
        profile_digest="sha256:profile",
        policy_pin_digest=f"sha256:{'a' * 64}",
        ci_attestation_subject="test-subject",
        ci_attestation_workflow_ref="test-ref",
        ci_attestation_digest=f"sha256:{'c' * 64}",
        ci_attestation_verified=True,
        revocation_metadata_digest=f"sha256:{'b' * 64}",
        revoked=False,
    )


def _native_path(root: Path) -> Path:
    platform_id, architecture = _platform_variant()
    return (
        root
        / "node_modules"
        / "@openai"
        / f"codex-{platform_id}-{architecture}"
        / "codex"
    )


def _platform_variant() -> tuple[str, str]:
    from ai_sdlc.core.stage_review.isolation_backend_identity import (
        _host_backend_platform as host_backend_platform,
    )

    platform_id, architecture = host_backend_platform()
    prefix = "darwin" if platform_id == "macos" else (
        "win32" if platform_id == "windows" else "linux"
    )
    return prefix, architecture

@pytest.mark.parametrize("shim_name", ("codex.cmd", "codex.ps1", "codex.js"))
def test_manifest_resolver_maps_windows_shims_to_exact_native(
    tmp_path: Path,
    shim_name: str,
) -> None:
    from ai_sdlc.core.stage_review.codex_isolation_host_probe import (
        resolve_codex_native_executable,
    )
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        published_codex_release,
    )

    release = published_codex_release("windows", "x64")
    assert release is not None
    node_modules = tmp_path / "node_modules"
    native = node_modules / "@openai" / "codex-win32-x64" / release.native_relative_path
    native.parent.mkdir(parents=True)
    native.write_bytes(b"native")
    if shim_name == "codex.js":
        shim = node_modules / "@openai" / "codex" / "bin" / shim_name
    else:
        shim = tmp_path / "bin" / shim_name
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text("shim", encoding="utf-8")

    assert resolve_codex_native_executable(shim, release) == native.resolve()


def test_manifest_resolver_maps_symlink_and_rejects_unknown_resolver(
    tmp_path: Path,
) -> None:
    from ai_sdlc.core.stage_review.codex_isolation_host_probe import (
        resolve_codex_native_executable,
    )
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        published_codex_release,
    )

    release = published_codex_release("linux", "x64")
    assert release is not None
    node_modules = tmp_path / "node_modules"
    native = node_modules / "@openai" / "codex-linux-x64" / release.native_relative_path
    native.parent.mkdir(parents=True)
    native.write_bytes(b"native")
    script = node_modules / "@openai" / "codex" / "bin" / "codex.js"
    script.parent.mkdir(parents=True)
    script.write_text("shim", encoding="utf-8")
    shim = tmp_path / "bin" / "codex"
    shim.parent.mkdir(parents=True)
    shim.symlink_to(script)

    assert resolve_codex_native_executable(shim, release) == native.resolve()
    with pytest.raises(ValueError, match="not trusted"):
        resolve_codex_native_executable(
            shim,
            release.model_copy(update={"shim_resolver_id": "unknown"}),
        )
