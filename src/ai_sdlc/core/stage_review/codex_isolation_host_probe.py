"""不信任环境变量的内置 Codex permission-profile Host 探针。"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_sdlc.core.stage_review.binding_builders import build_host_capability_snapshot
from ai_sdlc.core.stage_review.binding_models import HostCapabilitySnapshot
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.codex_isolation_platform import platform_mechanism
from ai_sdlc.core.stage_review.codex_isolation_resolver import (
    resolve_codex_native_executable,
)
from ai_sdlc.core.stage_review.isolation_backend_identity import (
    TrustedBackendReleaseManifest,
)
from ai_sdlc.core.stage_review.isolation_backend_identity import (
    _verify_backend_runtime_identity as verify_backend_runtime_identity,
)
from ai_sdlc.core.stage_review.isolation_execution import (
    codex_permission_profile_contract,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id, utc_iso

_VERSION = re.compile(r"(?:codex-cli\s+)?(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True, slots=True)
class _ProbeEvidence:
    version: str
    help_text: str
    binary_digest: str
    release_manifest_digest: str
    runtime_identity_digest: str
    trusted_identity: bool


class CodexIsolationHostProbe:
    def __init__(
        self,
        codex_executable: str = "codex",
        *,
        release_manifest: TrustedBackendReleaseManifest | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        supplied = Path(codex_executable)
        discovered = shutil.which(codex_executable) if not supplied.is_absolute() else None
        candidate = supplied if supplied.is_absolute() else Path(discovered or codex_executable)
        self._manifest_resolved = False
        if release_manifest is not None:
            try:
                candidate = resolve_codex_native_executable(candidate, release_manifest)
                self._manifest_resolved = True
            except ValueError:
                candidate = resolve_codex_native_executable(candidate)
        else:
            candidate = resolve_codex_native_executable(candidate)
        self._executable = str(candidate.resolve(strict=False))
        initial_digest = _binary_digest(candidate)
        self._absolute_identity = candidate.is_absolute() and candidate.is_file()
        self._release_manifest = release_manifest
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cached: HostCapabilitySnapshot | None = None
        self._cached_fingerprint = ""
        self._session_id = stable_id(
            "codex-host-probe",
            platform.node(),
            str(os.getpid()),
            self._executable,
            initial_digest,
        )

    def probe(self, previous_snapshot_digest: str = "") -> HostCapabilitySnapshot:
        now = self._clock()
        probe = self._collect_evidence(now)
        contract = codex_permission_profile_contract()
        system, mechanism = platform_mechanism()
        expected_mechanism = dict(contract.platform_mechanisms).get(system)
        compatible = (
            probe.trusted_identity
            and _compatible(probe.version, probe.help_text)
            and mechanism == expected_mechanism
        )
        capabilities = ["agent_execution"] if probe.version else []
        if compatible:
            capabilities.extend(
                (
                    f"isolation.{contract.backend_id}",
                    f"network_enforcement.{contract.backend_id}",
                )
            )
        evidence, fingerprint = self._fingerprints(probe, mechanism, capabilities)
        if self._cache_valid(fingerprint, now):
            assert self._cached is not None
            return self._cached
        predecessor = (
            self._cached.snapshot_digest
            if self._cached is not None
            else previous_snapshot_digest
        )
        snapshot = build_host_capability_snapshot(
            host_adapter_id=contract.host_adapter_id,
            host_adapter_version="1.0.0",
            host_session_id=self._session_id,
            capability_ids=tuple(capabilities),
            capability_source=contract.capability_source,
            evidence_digest=evidence,
            backend_id=contract.backend_id if probe.trusted_identity else "",
            backend_contract_version=(
                contract.contract_version if probe.trusted_identity else ""
            ),
            backend_release_manifest_digest=probe.release_manifest_digest,
            backend_runtime_identity_digest=probe.runtime_identity_digest,
            previous_snapshot_digest=predecessor,
            authorization_transition="probe-confirmed",
            issued_at=utc_iso(now),
            expires_at=utc_iso(now + timedelta(minutes=2)),
        )
        self._cached = snapshot
        self._cached_fingerprint = fingerprint
        return snapshot

    def _cache_valid(self, fingerprint: str, now: datetime) -> bool:
        return bool(
            self._cached is not None
            and fingerprint == self._cached_fingerprint
            and now < parse_utc(self._cached.expires_at)
        )

    def _collect_evidence(self, now: datetime) -> _ProbeEvidence:
        version = _run(self._executable, "--version")
        help_text = _run(self._executable, "sandbox", "--help")
        binary_digest = _binary_digest(Path(self._executable))
        release_digest = ""
        runtime_digest = ""
        trusted = False
        if (
            self._release_manifest is not None
            and self._absolute_identity
            and self._manifest_resolved
        ):
            try:
                identity = verify_backend_runtime_identity(
                    self._release_manifest,
                    Path(self._executable),
                    observed_backend_version=_observed_version(version),
                    now=now,
                )
                release_digest = self._release_manifest.manifest_digest
                runtime_digest = identity.identity_digest
                trusted = True
            except (OSError, ValueError):
                trusted = False
        return _ProbeEvidence(
            version, help_text, binary_digest, release_digest, runtime_digest, trusted
        )

    def _fingerprints(
        self,
        probe: _ProbeEvidence,
        mechanism: str,
        capabilities: list[str],
    ) -> tuple[str, str]:
        evidence = canonical_digest(
            {
                "version": probe.version,
                "sandbox_help": probe.help_text,
                "platform": platform.system().lower(),
                "platform_mechanism": mechanism,
                "resolved_executable": self._executable,
                "binary_digest": probe.binary_digest,
                "trusted_identity": probe.trusted_identity,
                "release_manifest_digest": probe.release_manifest_digest,
                "runtime_identity_digest": probe.runtime_identity_digest,
            },
            CanonicalizationPolicy(),
        )
        fingerprint = canonical_digest(
            {"evidence": evidence, "capabilities": tuple(capabilities)},
            CanonicalizationPolicy(),
        )
        return evidence, fingerprint


def _run(executable: str, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            (executable, *arguments),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=_minimum_environment(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return f"{completed.stdout}\n{completed.stderr}".strip()


def _binary_digest(path: Path) -> str:
    try:
        payload = path.resolve(strict=True).read_bytes()
    except OSError:
        return ""
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _minimum_environment() -> dict[str, str]:
    allowed = ("SYSTEMROOT", "WINDIR", "TMP", "TEMP")
    return {key: os.environ[key] for key in allowed if key in os.environ}


def _compatible(version: str, help_text: str) -> bool:
    match = _VERSION.search(version)
    if match is None:
        return False
    current = tuple(map(int, match.groups()))
    return (
        current == (0, 138, 0)
        and "--permissions-profile <NAME>" in help_text
        and "--permission-profile <NAME>" not in help_text
    )


def _observed_version(value: str) -> str:
    match = _VERSION.search(value)
    return ".".join(match.groups()) if match is not None else "0.0.0"


__all__ = ["CodexIsolationHostProbe", "resolve_codex_native_executable"]
