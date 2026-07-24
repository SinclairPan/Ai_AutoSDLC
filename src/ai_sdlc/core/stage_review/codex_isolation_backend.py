"""Codex 0.138+ permission profile 的跨平台 Reviewer 隔离 Backend。"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from ai_sdlc.core.stage_review.codex_isolation_boundary import (
    SandboxRun,
    decode_probe,
    profile_text,
    run_boundary_probe,
    run_sandbox,
    sandbox_command,
    write_profile,
)
from ai_sdlc.core.stage_review.codex_isolation_platform import platform_mechanism
from ai_sdlc.core.stage_review.codex_isolation_policy import (
    PreparedIsolationPolicy,
    cleanup_transient,
)
from ai_sdlc.core.stage_review.codex_isolation_policy import (
    _calibration_context as calibration_context,
)
from ai_sdlc.core.stage_review.codex_isolation_policy import (
    _cleanup_calibration_root as cleanup_calibration_root,
)
from ai_sdlc.core.stage_review.codex_isolation_policy import (
    _cleanup_controller as cleanup_controller,
)
from ai_sdlc.core.stage_review.codex_isolation_policy import (
    _context_paths_valid as context_paths_valid,
)
from ai_sdlc.core.stage_review.codex_isolation_policy import (
    _permit_matches as permit_matches,
)
from ai_sdlc.core.stage_review.codex_isolation_policy import (
    _policy_digests as policy_digests,
)
from ai_sdlc.core.stage_review.codex_isolation_policy import (
    _policy_payload as policy_payload,
)
from ai_sdlc.core.stage_review.codex_isolation_policy import (
    _protected_digest as protected_digest,
)
from ai_sdlc.core.stage_review.codex_isolation_policy import (
    _seed_calibration_targets as seed_calibration_targets,
)
from ai_sdlc.core.stage_review.codex_isolation_policy import (
    _with_backend_runtime as with_backend_runtime,
)
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
    IsolationBoundaryResult,
    IsolationNativeDenial,
    build_isolation_evidence_manifest,
    codex_permission_profile_contract,
)
from ai_sdlc.core.stage_review.isolation_launcher import (
    IsolatedProviderCommand,
    IsolationExecutionRecorder,
    IsolationLaunchContext,
    IsolationProcessResult,
)
from ai_sdlc.core.stage_review.isolation_models import (
    IsolationEvidenceManifest,
    IsolationExecutionPermit,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id, utc_iso

_profile_text = profile_text
_sandbox_command = sandbox_command
_cleanup_transient = cleanup_transient

_VERSION = re.compile(r"(?:codex-cli\s+)?(\d+\.\d+\.\d+)")


@dataclass(frozen=True, slots=True)
class _CalibrationEvidence:
    boundaries: tuple[IsolationBoundaryResult, ...]
    denials: tuple[IsolationNativeDenial, ...]
    process_id: int
    cleanup_succeeded: bool
    mechanism_verified: bool


class CodexPermissionProfileBackend:
    def __init__(
        self,
        codex_executable: str = "codex",
        *,
        release_manifest: TrustedBackendReleaseManifest | None = None,
    ) -> None:
        executable = resolve_codex_native_executable(
            Path(codex_executable), release_manifest
        )
        self._executable = str(executable)
        self._release_manifest = release_manifest
        self._epoch = secrets.token_hex(16)
        self._prepared: dict[str, PreparedIsolationPolicy] = {}

    def probe(
        self,
        context: IsolationLaunchContext,
        now: datetime,
    ) -> IsolationEvidenceManifest:
        context = with_backend_runtime(context, Path(self._executable))
        version = self._version()
        runtime_identity_digest = self._runtime_identity(version, now)
        calibration = _calibration_evidence(
            self._executable,
            context,
            enabled=bool(runtime_identity_digest),
        )
        digests = policy_digests(policy_payload(context))
        manifest = _manifest(
            context,
            version,
            digests,
            calibration.boundaries,
            calibration.denials,
            calibration.cleanup_succeeded,
            calibration.process_id,
            self._epoch,
            now,
            release_manifest_digest=(
                self._release_manifest.manifest_digest
                if self._release_manifest is not None
                else ""
            ),
            runtime_identity_digest=runtime_identity_digest,
        )
        if calibration.mechanism_verified and calibration.cleanup_succeeded:
            self._prepared[manifest.manifest_digest] = PreparedIsolationPolicy(
                context=context,
                manifest=manifest,
                runtime_identity_digest=runtime_identity_digest,
            )
        return manifest

    def execute(
        self,
        command: IsolatedProviderCommand,
        permit: IsolationExecutionPermit,
    ) -> IsolationProcessResult:
        return self._execute(command, permit, recorder=None)

    def execute_journaled(
        self,
        command: IsolatedProviderCommand,
        permit: IsolationExecutionPermit,
        recorder: IsolationExecutionRecorder,
    ) -> IsolationProcessResult:
        return self._execute(command, permit, recorder=recorder)

    def _execute(
        self,
        command: IsolatedProviderCommand,
        permit: IsolationExecutionPermit,
        *,
        recorder: IsolationExecutionRecorder | None,
    ) -> IsolationProcessResult:
        prepared = self._prepared.get(permit.manifest_digest)
        if prepared is None or not permit_matches(prepared, permit):
            raise RuntimeError("isolation backend permit is not active")
        current_identity = self._runtime_identity(
            self._version(),
            datetime.now().astimezone(),
        )
        if not current_identity or current_identity != prepared.runtime_identity_digest:
            raise RuntimeError("isolation backend runtime identity changed")
        before = protected_digest(prepared.context)
        controller: Path | None = None
        completed: IsolationProcessResult | None = None
        try:
            controller, disposable_home = write_profile(prepared.context)
            run = run_sandbox(
                self._executable,
                prepared.context,
                controller,
                disposable_home,
                command.argv,
                command.stdin_text,
            )
            completed = _process_result(prepared, run, before)
            if recorder is not None:
                recorder.record_completed(completed)
        finally:
            cleaned = cleanup_transient(prepared.context)
            if controller is not None:
                cleaned = cleanup_controller(controller) and cleaned
            if completed is not None:
                cleaned = completed.cleanup_succeeded and cleaned
            if completed is not None and recorder is not None:
                recorder.record_cleanup(
                    replace(completed, cleanup_succeeded=cleaned)
                )
        if completed is None:
            raise RuntimeError("isolation command did not produce completion evidence")
        return replace(completed, cleanup_succeeded=cleaned)

    def _version(self) -> str:
        try:
            completed = subprocess.run(
                (self._executable, "--version"),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return "0.0.0"
        match = _VERSION.search(f"{completed.stdout}\n{completed.stderr}")
        return match.group(1) if match else "0.0.0"

    def _runtime_identity(self, version: str, now: datetime) -> str:
        if self._release_manifest is not None:
            contract = codex_permission_profile_contract()
            if (
                self._release_manifest.backend_id != contract.backend_id
                or self._release_manifest.contract_version
                != contract.contract_version
            ):
                return ""
            try:
                identity = verify_backend_runtime_identity(
                    self._release_manifest,
                    Path(self._executable),
                    observed_backend_version=version,
                    now=now,
                )
            except (OSError, ValueError):
                return ""
            return identity.identity_digest
        return ""


def _calibration_evidence(
    executable: str,
    context: IsolationLaunchContext,
    *,
    enabled: bool,
) -> _CalibrationEvidence:
    if not enabled or not context_paths_valid(context):
        return _CalibrationEvidence((), (), os.getpid(), False, False)
    probe_context = calibration_context(context)
    probe_root = Path(probe_context.normalized_run_root).parent
    boundaries: tuple[IsolationBoundaryResult, ...] = ()
    denials: tuple[IsolationNativeDenial, ...] = ()
    process_id = os.getpid()
    mechanism_verified = False
    try:
        seed_calibration_targets(probe_context)
        prepared = write_profile(probe_context)
        run = run_boundary_probe(
            executable, probe_context, prepared[0], prepared[1]
        )
        process_id = run.process_id
        boundaries, denials = decode_probe(run)
        mechanism_verified = bool(boundaries)
    except (OSError, subprocess.SubprocessError):
        mechanism_verified = False
    finally:
        cleanup_succeeded = cleanup_calibration_root(probe_root)
    return _CalibrationEvidence(
        boundaries, denials, process_id, cleanup_succeeded, mechanism_verified
    )


def _process_result(
    prepared: PreparedIsolationPolicy,
    run: SandboxRun,
    before: str,
) -> IsolationProcessResult:
    return IsolationProcessResult(
        return_code=run.return_code,
        stdout=run.stdout,
        stderr=run.stderr,
        process_id=run.process_id,
        parent_process_id=os.getpid(),
        boundary_results=prepared.manifest.boundary_results,
        os_native_denials=prepared.manifest.os_native_denials,
        before_digest=before,
        after_digest=protected_digest(prepared.context),
        cleanup_succeeded=run.bootstrap_cleanup_succeeded,
    )


def _manifest(
    context: IsolationLaunchContext,
    version: str,
    digests: tuple[str, str, str],
    boundaries: tuple[IsolationBoundaryResult, ...],
    denials: tuple[IsolationNativeDenial, ...],
    cleanup_succeeded: bool,
    process_id: int,
    epoch: str,
    now: datetime,
    *,
    release_manifest_digest: str,
    runtime_identity_digest: str,
) -> IsolationEvidenceManifest:
    contract = codex_permission_profile_contract()
    system, mechanism = platform_mechanism()
    return build_isolation_evidence_manifest(
        backend_id=contract.backend_id,
        contract_version=contract.contract_version,
        backend_version=version,
        backend_instance_id=stable_id("codex-isolation-backend", version, system),
        backend_epoch=epoch,
        allocation_digest=context.allocation_digest,
        assignment_digest=context.assignment_digest,
        candidate_digest=context.candidate_digest,
        layout_digest=context.layout_digest,
        platform=system,
        platform_mechanism=mechanism,
        host_snapshot_digest=context.host_snapshot.snapshot_digest,
        release_manifest_digest=release_manifest_digest,
        runtime_identity_digest=runtime_identity_digest,
        policy_digest=digests[0],
        filesystem_policy_digest=digests[1],
        network_policy_digest=digests[2],
        process_id=max(process_id, 1),
        parent_process_id=max(os.getpid(), 1),
        boundary_results=boundaries,
        os_native_denials=denials,
        cleanup_succeeded=cleanup_succeeded,
        issued_at=utc_iso(now),
        expires_at=utc_iso(now + timedelta(minutes=2)),
    )
