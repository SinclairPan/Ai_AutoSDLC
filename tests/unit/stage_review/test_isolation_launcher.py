from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from tests.unit.stage_review.test_codex_isolation_host_probe import (
    _native_path,
    _release,
)
from tests.unit.stage_review.test_isolation_execution import (
    _boundary_results,
    _host,
    _manifest,
)
from tests.unit.stage_review.test_provider_journal import (
    _actual_usage,
    _journal_setup,
)
from tests.unit.stage_review.test_resources import _now

from ai_sdlc.core.stage_review.provider_journal import (
    ProviderQueryResult,
    build_provider_submission,
)
from ai_sdlc.core.stage_review.provider_usage_models import metered_provider_usage


class CommandOnlyDriver:
    def __init__(self, request) -> None:
        self.provider_id = request.provider_id
        self.capabilities = request.capabilities
        self.raw_invoke_count = 0
        self.command_count = 0
        self.decode_count = 0

    def invoke(self, request):
        self.raw_invoke_count += 1
        raise AssertionError("raw invoke must never enter the reviewer chain")

    def query(self, request):
        raise AssertionError("raw query must never enter the reviewer chain")

    def build_isolated_command(self, request, permit, command_kind):
        from ai_sdlc.core.stage_review.isolation_launcher import IsolatedProviderCommand

        self.command_count += 1
        return IsolatedProviderCommand(
            argv=("provider-review", request.invocation_id),
            stdin_text="",
            command_kind=command_kind,
        )

    def decode_isolated_result(self, request, command_kind, result):
        self.decode_count += 1
        if command_kind == "query":
            return ProviderQueryResult(query_status="not_found")
        return build_provider_submission(
            request,
            provider_call_id="provider-call.isolated",
            output_payload={"decision": "PASS"},
            accounted_usage=metered_provider_usage(_actual_usage()),
        )


class RefusingCommandDriver(CommandOnlyDriver):
    def build_isolated_command(self, request, permit, command_kind):
        from ai_sdlc.core.stage_review.provider_journal_driver import (
            ProviderDriverRefused,
        )

        raise ProviderDriverRefused(
            "simulated driver refusal",
            accounted_usage=metered_provider_usage(_actual_usage()),
        )


@dataclass
class FakeTrustedBackend:
    manifest: object
    execute_count: int = 0
    probe_count: int = 0

    def probe(self, context, now):
        self.probe_count += 1
        return self.manifest

    def execute(self, command, permit):
        from ai_sdlc.core.stage_review.isolation_launcher import IsolationProcessResult

        self.execute_count += 1
        return IsolationProcessResult(
            return_code=0,
            stdout="PASS",
            stderr="",
            process_id=202,
            parent_process_id=self.manifest.parent_process_id,
            boundary_results=self.manifest.boundary_results,
            os_native_denials=self.manifest.os_native_denials,
            before_digest="sha256:before",
            after_digest="sha256:before",
            cleanup_succeeded=True,
        )


class FailingTrustedBackend(FakeTrustedBackend):
    def execute(self, command, permit):
        raise OSError("sandbox process could not start")


class StaleTrustedBackend(FakeTrustedBackend):
    def __init__(self, manifest, stale_manifest) -> None:
        super().__init__(manifest)
        self.stale_manifest = stale_manifest

    def probe(self, context, now):
        self.probe_count += 1
        if self.probe_count == 1:
            return self.manifest
        return self.stale_manifest


class JournaledFakeBackend(FakeTrustedBackend):
    def execute_journaled(self, command, permit, recorder):
        result = super().execute(command, permit)
        recorder.record_completed(replace(result, cleanup_succeeded=False))
        recorder.record_cleanup(result)
        return result


class CrashAfterCompletedBackend(FakeTrustedBackend):
    def execute_journaled(self, command, permit, recorder):
        result = super().execute(command, permit)
        recorder.record_completed(replace(result, cleanup_succeeded=False))
        raise OSError("simulated crash after command completion")


@dataclass
class IntegrityFailingBackend(FakeTrustedBackend):
    failure: str = "digest"

    def execute(self, command, permit):
        result = super().execute(command, permit)
        if self.failure == "cleanup":
            return replace(result, cleanup_succeeded=False)
        if self.failure == "boundary":
            return replace(result, boundary_results=())
        if self.failure == "process":
            return replace(result, parent_process_id=result.parent_process_id + 1)
        return replace(result, after_digest="sha256:changed")


def test_launcher_consumes_fresh_permit_and_never_calls_raw_driver(
    tmp_path: Path,
) -> None:
    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    host = _host(execution)
    backend = FakeTrustedBackend(
        _manifest(execution, host_snapshot_digest=host.snapshot_digest)
    )
    launcher = launcher_api.ReviewerIsolationLauncher(
        tmp_path,
        registry=execution.TrustedIsolationBackendRegistry.default(),
        backend=backend,
    )
    raw = CommandOnlyDriver(request)
    context = _context(launcher_api, request, host, tmp_path)
    wrapped = launcher.prepare_driver(raw, context=context, now=_now())
    assert wrapped is not None

    first = wrapped.invoke(request)
    second = wrapped.invoke(request)

    assert first.output_payload == second.output_payload == {"decision": "PASS"}
    assert raw.raw_invoke_count == 0
    assert raw.command_count == backend.execute_count == 2
    receipts = launcher.receipts()
    assert len(receipts) == 2
    assert all(item.command_started for item in receipts)
    assert len({item.permit_digest for item in receipts}) == 2
    receipt_digests = {item.receipt_digest for item in receipts}
    assert first.isolation_receipt_digest in receipt_digests
    assert second.isolation_receipt_digest in receipt_digests
    assert first.isolation_receipt_digest != second.isolation_receipt_digest
    assert all(
        item.manifest_digest == backend.manifest.manifest_digest for item in receipts
    )
    from ai_sdlc.core.stage_review.certificate_receipt_store import (
        FilesystemReviewReceiptArtifactStore,
    )

    authority = FilesystemReviewReceiptArtifactStore(
        tmp_path,
        project_id=request.project_id,
    )
    stored = authority.resolve_isolation_receipt(receipts[0].receipt_digest)
    assert stored == receipts[0]
    assert authority.resolve_isolation_permit(stored.permit_digest).permit_digest == (
        stored.permit_digest
    )


@pytest.mark.parametrize(
    ("failure", "reason"),
    (
        ("digest", "isolation.protected-state-changed"),
        ("cleanup", "isolation.execution-cleanup-failed"),
        ("boundary", "isolation.execution-lineage-mismatch"),
        ("process", "isolation.execution-lineage-mismatch"),
    ),
)
def test_execution_integrity_failure_persists_receipt_before_decode(
    tmp_path: Path,
    failure: str,
    reason: str,
) -> None:
    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    host = _host(execution)
    backend = IntegrityFailingBackend(
        _manifest(execution, host_snapshot_digest=host.snapshot_digest),
        failure=failure,
    )
    launcher = launcher_api.ReviewerIsolationLauncher(
        tmp_path,
        registry=execution.TrustedIsolationBackendRegistry.default(),
        backend=backend,
    )
    driver = CommandOnlyDriver(request)
    wrapped = launcher.prepare_driver(
        driver,
        context=_context(launcher_api, request, host, tmp_path),
        now=_now(),
    )
    assert wrapped is not None

    with pytest.raises(launcher_api.IsolationCommandRefused):
        wrapped.invoke(request)

    assert driver.decode_count == 0
    receipt = launcher.receipts()[-1]
    assert receipt.command_started is True
    assert receipt.reason_id == reason


def test_stale_backend_after_prepare_is_a_journaled_refusal(
    tmp_path: Path,
) -> None:
    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    host = _host(execution)
    backend = StaleTrustedBackend(
        _manifest(execution, host_snapshot_digest=host.snapshot_digest),
        _manifest(
            execution,
            host_snapshot_digest=host.snapshot_digest,
            backend_version="0.137.0",
        ),
    )
    launcher = launcher_api.ReviewerIsolationLauncher(
        tmp_path,
        registry=execution.TrustedIsolationBackendRegistry.default(),
        backend=backend,
    )
    driver = CommandOnlyDriver(request)
    wrapped = launcher.prepare_driver(
        driver,
        context=_context(launcher_api, request, host, tmp_path),
        now=_now(),
    )
    assert wrapped is not None

    with pytest.raises(launcher_api.IsolationCommandRefused):
        wrapped.invoke(request)

    assert driver.command_count == backend.execute_count == 0
    receipt = launcher.receipts()[-1]
    assert receipt.command_started is False
    assert receipt.reason_id == "isolation.backend-stale"


def test_execution_observation_is_completed_before_cleanup_and_recovers_closed(
    tmp_path: Path,
) -> None:
    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    host = _host(execution)
    manifest = _manifest(execution, host_snapshot_digest=host.snapshot_digest)
    context = _context(launcher_api, request, host, tmp_path)
    launcher = launcher_api.ReviewerIsolationLauncher(
        tmp_path,
        registry=execution.TrustedIsolationBackendRegistry.default(),
        backend=JournaledFakeBackend(manifest),
    )
    wrapped = launcher.prepare_driver(
        CommandOnlyDriver(request),
        context=context,
        now=_now(),
    )
    assert wrapped is not None

    wrapped.invoke(request)

    observations = launcher.execution_observations()
    assert {item.stage for item in observations} == {"completed", "cleaned"}
    by_stage = {item.stage: item for item in observations}
    assert (
        by_stage["cleaned"].previous_observation_digest
        == by_stage["completed"].observation_digest
    )

    crash_root = tmp_path / "crash"
    crashed = launcher_api.ReviewerIsolationLauncher(
        crash_root,
        registry=execution.TrustedIsolationBackendRegistry.default(),
        backend=CrashAfterCompletedBackend(manifest),
    )
    wrapped = crashed.prepare_driver(
        CommandOnlyDriver(request),
        context=context,
        now=_now(),
    )
    assert wrapped is not None
    with pytest.raises(launcher_api.IsolationCommandRefused):
        wrapped.invoke(request)

    assert tuple(item.stage for item in crashed.execution_observations()) == (
        "completed",
    )
    assert (
        crashed.prepare_driver(
            CommandOnlyDriver(request),
            context=context,
            now=_now(),
        )
        is None
    )
    assert "isolation.execution-recovery-required" in {
        item.reason_id for item in crashed.receipts()
    }


def test_backend_calibration_probe_never_targets_real_context_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.core.stage_review import codex_isolation_backend as backend_api
    from ai_sdlc.core.stage_review.codex_isolation_backend import (
        CodexPermissionProfileBackend,
    )
    from ai_sdlc.core.stage_review.codex_isolation_boundary import SandboxRun

    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    host = _host(execution)
    context = _context(launcher_api, request, host, tmp_path)
    real_targets = _seed_context_canaries(context)
    before = {path: path.read_bytes() for path in real_targets}
    executable = _native_path(tmp_path)
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"trusted-codex")
    digest = f"sha256:{hashlib.sha256(executable.read_bytes()).hexdigest()}"
    backend = CodexPermissionProfileBackend(
        str(executable),
        release_manifest=_release(executable, digest),
    )
    monkeypatch.setattr(backend, "_version", lambda: "0.138.0")
    recorded = []

    def fail_open_probe(executable, probe_context, config_root, home):
        recorded.append(probe_context)
        _pollute_probe_targets(probe_context)
        return SandboxRun(0, "", "", 202)

    monkeypatch.setattr(backend_api, "run_boundary_probe", fail_open_probe)
    monkeypatch.setattr(
        backend_api,
        "decode_probe",
        lambda run: (_boundary_results(execution), ()),
    )

    manifest = backend.probe(context, _now())

    assert manifest.cleanup_succeeded is True
    assert {path: path.read_bytes() for path in real_targets} == before
    assert recorded
    assert all("calibration-probe" in value.normalized_run_root for value in recorded)
    for value in recorded:
        probe_root = Path(value.normalized_run_root).parent
        attack_targets = (
            value.candidate_root,
            *value.peer_output_roots,
            value.protected_home_root,
            *value.protected_config_roots,
            value.disposable_home_root,
            value.disposable_config_root,
            value.controller_config_root,
            str(Path(value.normalized_run_root) / "boundary-link"),
        )
        assert all(
            Path(target).resolve(strict=False).is_relative_to(probe_root)
            for target in attack_targets
        )
    assert not tuple(
        (Path(context.normalized_run_root).parent).glob("calibration-probe-*")
    )


def test_unproven_preflight_records_receipt_without_starting_command(
    tmp_path: Path,
) -> None:
    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    host = _host(execution)
    backend = FakeTrustedBackend(
        _manifest(
            execution,
            host_snapshot_digest=host.snapshot_digest,
            backend_version="0.137.0",
        )
    )
    launcher = launcher_api.ReviewerIsolationLauncher(
        tmp_path,
        registry=execution.TrustedIsolationBackendRegistry.default(),
        backend=backend,
    )
    raw = CommandOnlyDriver(request)

    wrapped = launcher.prepare_driver(
        raw,
        context=_context(launcher_api, request, host, tmp_path),
        now=_now(),
    )

    assert wrapped is None
    assert raw.command_count == backend.execute_count == 0
    receipt = launcher.receipts()[-1]
    assert receipt.command_started is False
    assert receipt.reason_id == "isolation.backend-unproven"


def test_detected_only_runs_controlled_sentinel_persists_then_cleans(
    tmp_path: Path,
) -> None:
    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    host = _host(execution)
    backend = FakeTrustedBackend(
        _manifest(execution, host_snapshot_digest=host.snapshot_digest)
    )
    launcher = launcher_api.ReviewerIsolationLauncher(
        tmp_path,
        registry=execution.TrustedIsolationBackendRegistry.default(),
        backend=backend,
    )
    raw = CommandOnlyDriver(request)
    context = replace(
        _context(launcher_api, request, host, tmp_path),
        adapter_grade="detected_only",
    )

    wrapped = launcher.prepare_driver(raw, context=context, now=_now())

    assert wrapped is None
    assert raw.command_count == backend.execute_count == 0
    evidence = launcher.detected_only_evidence()
    assert tuple(item.stage for item in evidence) == ("polluted", "cleaned")
    assert evidence[-1].previous_evidence_digest == evidence[0].evidence_digest
    assert evidence[-1].cleanup_succeeded is True
    assert evidence[-1].untrusted_command_started is False
    assert not Path(evidence[-1].sentinel_root).exists()
    receipt = launcher.receipts()[-1]
    assert receipt.command_started is False
    assert receipt.reason_id == "isolation.backend-detected-only"


def test_detected_only_cleans_sentinel_when_evidence_persistence_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    host = _host(execution)
    backend = FakeTrustedBackend(
        _manifest(execution, host_snapshot_digest=host.snapshot_digest)
    )
    launcher = launcher_api.ReviewerIsolationLauncher(
        tmp_path,
        registry=execution.TrustedIsolationBackendRegistry.default(),
        backend=backend,
    )
    context = replace(
        _context(launcher_api, request, host, tmp_path),
        adapter_grade="detected_only",
    )
    monkeypatch.setattr(
        launcher._detected_only,
        "_persist",
        lambda evidence, sequence: (_ for _ in ()).throw(OSError("disk full")),
    )

    wrapped = launcher.prepare_driver(
        CommandOnlyDriver(request),
        context=context,
        now=_now(),
    )

    sentinel_parent = Path(context.normalized_run_root) / "detected-only"
    assert wrapped is None
    assert not sentinel_parent.exists() or not tuple(sentinel_parent.iterdir())
    assert (
        launcher.receipts()[-1].reason_id == "isolation.detected-only-sentinel-failed"
    )


def test_codex_sandbox_command_uses_plural_permissions_profile_contract() -> None:
    from ai_sdlc.core.stage_review.codex_isolation_backend import _sandbox_command

    command = _sandbox_command("codex", "/trusted/run", ("provider", "review"))

    assert "--permissions-profile" in command
    assert "--permission-profile" not in command


def test_codex_sandbox_uses_short_read_only_invocation_spec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.core.stage_review import codex_isolation_boundary as boundary

    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    context = _context(launcher_api, request, _host(execution), tmp_path)
    _seed_context_canaries(context)
    config_root, disposable_home = boundary.write_profile(context)
    captured: dict[str, object] = {}

    class _FakeProcess:
        returncode = 0
        pid = 31415

        def __init__(self, command, **kwargs) -> None:
            captured["command"] = command
            captured["kwargs"] = kwargs
            bootstrap_roots = tuple(
                Path(context.normalized_run_root).glob(".ai-sdlc-bootstrap-*")
            )
            assert len(bootstrap_roots) == 1
            bootstrap_root = bootstrap_roots[0]
            specs = tuple(bootstrap_root.glob("invocation-*.json"))
            assert len(specs) == 1
            captured["bootstrap_root"] = bootstrap_root
            captured["spec_path"] = specs[0]
            captured["spec"] = json.loads(specs[0].read_text(encoding="utf-8"))
            captured["profile"] = (config_root / "config.toml").read_text(
                encoding="utf-8"
            )

        def communicate(self, stdin_text, timeout):
            captured["stdin"] = stdin_text
            captured["timeout"] = timeout
            return "ok", ""

    monkeypatch.setattr(boundary, "_sandbox_python", lambda: "/usr/bin/python3")
    monkeypatch.setattr(boundary.subprocess, "Popen", _FakeProcess)
    long_argument = "x" * 50_000

    result = boundary.run_sandbox(
        "codex",
        context,
        config_root,
        disposable_home,
        ("provider-review", long_argument),
        "request-body",
    )

    command = captured["command"]
    assert isinstance(command, tuple)
    assert long_argument not in command
    assert max(map(len, command)) < 1_024
    bootstrap_root = captured["bootstrap_root"]
    assert isinstance(bootstrap_root, Path)
    assert not bootstrap_root.is_relative_to(config_root)
    assert str(bootstrap_root / "ai-sdlc-child-wrapper.py") in command
    assert captured["spec"] == {
        "argv": ["provider-review", long_argument],
        "environment": {
            "AI_SDLC_CREDENTIAL_ROOT": context.disposable_credential_root,
            "AI_SDLC_OUTPUT_ROOT": context.output_root,
            "CODEX_HOME": context.disposable_config_root,
            "GIT_CONFIG_GLOBAL": str(
                Path(context.disposable_config_root) / "gitconfig"
            ),
            "HOME": context.disposable_home_root,
            "TEMP": str(Path(context.normalized_run_root) / "tmp"),
            "TMP": str(Path(context.normalized_run_root) / "tmp"),
            "TMPDIR": str(Path(context.normalized_run_root) / "tmp"),
            "USERPROFILE": context.disposable_home_root,
            "XDG_CONFIG_HOME": context.disposable_config_root,
        },
    }
    spec_path = captured["spec_path"]
    assert isinstance(spec_path, Path)
    assert f'{json.dumps(str(bootstrap_root))} = "read"' in captured["profile"]
    assert (
        captured["profile"].index(
            f'{json.dumps(context.normalized_run_root)} = "write"'
        )
        < captured["profile"].index(
            f'{json.dumps(str(bootstrap_root))} = "read"'
        )
    )
    assert captured["stdin"] == "request-body"
    assert result.return_code == 0
    assert not spec_path.exists()
    assert not bootstrap_root.exists()


@pytest.mark.parametrize("failure_target", ("helpers", "profile"))
def test_codex_profile_preparation_rolls_back_private_and_bootstrap_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_target: str,
) -> None:
    from ai_sdlc.core.stage_review import codex_isolation_boundary as boundary

    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    context = _context(launcher_api, request, _host(execution), tmp_path)
    canaries = _seed_context_canaries(context)
    nonce_bytes = b"\xab" * 16
    nonce = nonce_bytes.hex()
    config_root = Path(context.controller_config_root) / nonce
    bootstrap_root = (
        Path(context.normalized_run_root) / f".ai-sdlc-bootstrap-{nonce}"
    )

    def _fail_helper_write(root: Path) -> None:
        (root / "partial-helper.py").write_text("partial", encoding="utf-8")
        raise OSError("helper write failed")

    def _fail_profile_write(*args, **kwargs) -> None:
        del args, kwargs
        raise OSError("profile write failed")

    monkeypatch.setattr(boundary.os, "urandom", lambda size: nonce_bytes[:size])
    if failure_target == "helpers":
        monkeypatch.setattr(boundary, "_write_sandbox_helpers", _fail_helper_write)
    else:
        monkeypatch.setattr(boundary, "_write_profile_config", _fail_profile_write)

    with pytest.raises(OSError, match="write failed"):
        boundary.write_profile(context)

    assert not config_root.exists()
    assert not bootstrap_root.exists()
    assert canaries[-1].read_text(encoding="utf-8") == "canary-4"


def test_codex_sandbox_cleans_bootstrap_when_invocation_spec_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.core.stage_review import codex_isolation_boundary as boundary

    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    context = _context(launcher_api, request, _host(execution), tmp_path)
    _seed_context_canaries(context)
    config_root, disposable_home = boundary.write_profile(context)
    bootstrap_roots = tuple(
        Path(context.normalized_run_root).glob(".ai-sdlc-bootstrap-*")
    )
    assert len(bootstrap_roots) == 1
    bootstrap_root = bootstrap_roots[0]

    def _fail_spec_write(bootstrap, supplied_context, argv):
        del supplied_context, argv
        (bootstrap / "partial-invocation.json").write_text(
            "partial",
            encoding="utf-8",
        )
        raise OSError("disk full")

    monkeypatch.setattr(boundary, "_write_invocation_spec", _fail_spec_write)

    with pytest.raises(OSError, match="disk full"):
        boundary.run_sandbox(
            "codex",
            context,
            config_root,
            disposable_home,
            ("provider-review",),
            "request-body",
        )

    assert not bootstrap_root.exists()


def test_codex_sandbox_cleans_bootstrap_when_process_start_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.core.stage_review import codex_isolation_boundary as boundary

    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    context = _context(launcher_api, request, _host(execution), tmp_path)
    _seed_context_canaries(context)
    config_root, disposable_home = boundary.write_profile(context)
    bootstrap_roots = tuple(
        Path(context.normalized_run_root).glob(".ai-sdlc-bootstrap-*")
    )
    assert len(bootstrap_roots) == 1
    bootstrap_root = bootstrap_roots[0]

    def _fail_process_start(*args, **kwargs):
        del args, kwargs
        raise OSError("process start failed")

    monkeypatch.setattr(boundary, "_sandbox_python", lambda: "/usr/bin/python3")
    monkeypatch.setattr(boundary.subprocess, "Popen", _fail_process_start)

    with pytest.raises(OSError, match="process start failed"):
        boundary.run_sandbox(
            "codex",
            context,
            config_root,
            disposable_home,
            ("provider-review",),
            "request-body",
        )

    assert not bootstrap_root.exists()


def test_codex_sandbox_cleans_bootstrap_after_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.core.stage_review import codex_isolation_boundary as boundary

    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    context = _context(launcher_api, request, _host(execution), tmp_path)
    _seed_context_canaries(context)
    config_root, disposable_home = boundary.write_profile(context)
    bootstrap_roots = tuple(
        Path(context.normalized_run_root).glob(".ai-sdlc-bootstrap-*")
    )
    assert len(bootstrap_roots) == 1
    bootstrap_root = bootstrap_roots[0]

    class _TimedOutProcess:
        returncode = -9
        pid = 27182

        def __init__(self, command, **kwargs) -> None:
            del kwargs
            self.command = command
            self.communication_count = 0
            self.killed = False

        def communicate(self, stdin_text=None, timeout=None):
            del stdin_text
            self.communication_count += 1
            if self.communication_count == 1:
                raise subprocess.TimeoutExpired(self.command, timeout)
            assert self.killed is True
            return "", "terminated"

        def kill(self) -> None:
            self.killed = True

    monkeypatch.setattr(boundary, "_sandbox_python", lambda: "/usr/bin/python3")
    monkeypatch.setattr(boundary.subprocess, "Popen", _TimedOutProcess)

    result = boundary.run_sandbox(
        "codex",
        context,
        config_root,
        disposable_home,
        ("provider-review",),
        "request-body",
    )

    assert result.return_code == -9
    assert "isolation command timed out" in result.stderr
    assert result.bootstrap_cleanup_succeeded is True
    assert not bootstrap_root.exists()


def test_codex_sandbox_surfaces_bootstrap_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.core.stage_review import codex_isolation_boundary as boundary

    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    context = _context(launcher_api, request, _host(execution), tmp_path)
    _seed_context_canaries(context)
    config_root, disposable_home = boundary.write_profile(context)

    class _CompletedProcess:
        returncode = 0
        pid = 16180

        def __init__(self, command, **kwargs) -> None:
            del command, kwargs

        def communicate(self, stdin_text=None, timeout=None):
            del stdin_text, timeout
            return "ok", ""

    monkeypatch.setattr(boundary, "_sandbox_python", lambda: "/usr/bin/python3")
    monkeypatch.setattr(boundary.subprocess, "Popen", _CompletedProcess)
    monkeypatch.setattr(boundary, "_cleanup_bootstrap", lambda context, root: False)

    result = boundary.run_sandbox(
        "codex",
        context,
        config_root,
        disposable_home,
        ("provider-review",),
        "request-body",
    )

    assert result.return_code == 0
    assert result.bootstrap_cleanup_succeeded is False


def test_codex_bootstrap_cleanup_rejects_symlink_escape(tmp_path: Path) -> None:
    from ai_sdlc.core.stage_review import codex_isolation_boundary as boundary

    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    context = _context(launcher_api, request, _host(execution), tmp_path)
    run_root = Path(context.normalized_run_root)
    run_root.mkdir(parents=True)
    outside = tmp_path / "outside-bootstrap"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("protected", encoding="utf-8")
    linked_bootstrap = run_root / ".ai-sdlc-bootstrap-forged"
    linked_bootstrap.symlink_to(outside, target_is_directory=True)

    assert boundary._cleanup_bootstrap(context, linked_bootstrap) is False
    assert linked_bootstrap.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "protected"


def test_boundary_probe_moves_program_and_payload_off_command_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.core.stage_review import codex_isolation_boundary as boundary
    from ai_sdlc.core.stage_review.codex_isolation_probe import PROBE_PROGRAM

    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    context = _context(launcher_api, request, _host(execution), tmp_path)
    _seed_context_canaries(context)
    config_root, disposable_home = boundary.write_profile(context)
    captured: dict[str, object] = {}

    def _capture(executable, supplied, config, home, argv, stdin_text):
        captured["argv"] = argv
        captured["payload"] = json.loads(stdin_text)
        return boundary.SandboxRun(0, "{}", "", 2718)

    monkeypatch.setattr(boundary, "run_sandbox", _capture)

    result = boundary.run_boundary_probe(
        "codex",
        context,
        config_root,
        disposable_home,
    )

    assert result.return_code == 0
    assert PROBE_PROGRAM not in captured["argv"]
    probe_path = Path(captured["argv"][-1])
    assert probe_path.name == "ai-sdlc-boundary-probe.py"
    assert probe_path.is_relative_to(Path(context.normalized_run_root))
    assert not probe_path.is_relative_to(config_root)
    assert captured["payload"]["candidate_root"] == context.candidate_root


def test_posix_sandbox_prefers_system_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.core.stage_review import codex_isolation_boundary as boundary

    monkeypatch.setattr(boundary.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        boundary,
        "_python_available",
        lambda path: (
            path == Path("/Library/Developer/CommandLineTools/usr/bin/python3")
        ),
    )

    expected = Path("/Library/Developer/CommandLineTools/usr/bin/python3").resolve()
    assert boundary._sandbox_python() == str(expected)


def test_linux_backend_delegates_to_codex_native_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.core.stage_review import codex_isolation_platform as platform_backend

    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    context = _context(launcher_api, request, _host(execution), tmp_path)
    monkeypatch.setattr(platform_backend.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        platform_backend,
        "_system_bubblewrap_path",
        lambda: Path("/usr/bin/bwrap"),
    )

    codex = ("codex", "sandbox", "--permissions-profile", "reviewer")
    command = platform_backend.wrap_platform_sandbox(
        codex,
        (context.normalized_run_root, context.output_root),
    )

    assert command == codex


def test_launcher_rejects_manifest_from_swapped_runtime_layout(tmp_path: Path) -> None:
    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    host = _host(execution)
    manifest = _manifest(execution, host_snapshot_digest=host.snapshot_digest)
    swapped = execution.build_isolation_evidence_manifest(
        **{
            **manifest.model_dump(
                mode="json",
                exclude={"artifact_kind", "manifest_digest"},
            ),
            "layout_digest": "sha256:swapped-layout",
        }
    )
    launcher = launcher_api.ReviewerIsolationLauncher(
        tmp_path,
        registry=execution.TrustedIsolationBackendRegistry.default(),
        backend=FakeTrustedBackend(swapped),
    )

    wrapped = launcher.prepare_driver(
        CommandOnlyDriver(request),
        context=_context(launcher_api, request, host, tmp_path),
        now=_now(),
    )

    assert wrapped is None
    assert launcher.receipts()[-1].reason_id == "isolation.manifest-lineage-mismatch"


def test_codex_profile_denies_root_and_controller_but_allows_disposable_roots(
    tmp_path: Path,
) -> None:
    from ai_sdlc.core.stage_review.codex_isolation_backend import _profile_text

    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    context = _context(launcher_api, request, _host(execution), tmp_path)

    profile = _profile_text(context)

    assert '":root" = "deny"' in profile
    assert f'{json.dumps(context.controller_config_root)} = "deny"' in profile
    assert f'{json.dumps(context.disposable_config_root)} = "write"' in profile
    assert f'{json.dumps(context.output_root)} = "write"' in profile


def test_codex_profile_restores_measured_runtime_below_protected_home(
    tmp_path: Path,
) -> None:
    from ai_sdlc.core.stage_review.codex_isolation_backend import _profile_text

    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    context = _context(launcher_api, request, _host(execution), tmp_path)
    runtime = Path(context.protected_home_root) / "trusted-codex-runtime"
    context = replace(context, runtime_read_roots=(str(runtime),))

    profile = _profile_text(context)

    deny = f'{json.dumps(context.protected_home_root)} = "deny"'
    restore = f'{json.dumps(str(runtime))} = "read"'
    assert profile.index(deny) < profile.index(restore)


def test_linux_backend_runtime_includes_existing_musl_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.core.stage_review import codex_isolation_policy as policy

    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    context = _context(launcher_api, request, _host(execution), tmp_path)
    executable = tmp_path / "codex-runtime" / "codex"
    executable.parent.mkdir()
    executable.write_bytes(b"codex")
    loader = tmp_path / "musl" / "ld-musl-x86_64.so.1"
    loader.parent.mkdir()
    loader.write_bytes(b"loader")
    monkeypatch.setattr(policy.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        policy,
        "_LINUX_RUNTIME_READ_CANDIDATES",
        (loader,),
        raising=False,
    )

    resolved = policy._with_backend_runtime(context, executable)

    assert str(loader.resolve()) in resolved.runtime_read_roots
    assert str(loader.parent.resolve()) in resolved.runtime_read_roots


def test_cleanup_removes_disposable_roots_instead_of_only_claiming_success(
    tmp_path: Path,
) -> None:
    from ai_sdlc.core.stage_review.codex_isolation_backend import _cleanup_transient

    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    context = _context(launcher_api, request, _host(execution), tmp_path)
    roots = (
        context.disposable_home_root,
        context.disposable_config_root,
        context.disposable_credential_root,
    )
    for value in roots:
        Path(value).mkdir(parents=True, exist_ok=True)
        (Path(value) / "sentinel.txt").write_text("sentinel", encoding="utf-8")

    assert _cleanup_transient(context) is True
    assert all(not Path(value).exists() for value in roots)


def test_backend_start_failure_records_refusal_before_command(tmp_path: Path) -> None:
    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    host = _host(execution)
    backend = FailingTrustedBackend(
        _manifest(execution, host_snapshot_digest=host.snapshot_digest)
    )
    launcher = launcher_api.ReviewerIsolationLauncher(
        tmp_path,
        registry=execution.TrustedIsolationBackendRegistry.default(),
        backend=backend,
    )
    wrapped = launcher.prepare_driver(
        CommandOnlyDriver(request),
        context=_context(launcher_api, request, host, tmp_path),
        now=_now(),
    )
    assert wrapped is not None

    with pytest.raises(launcher_api.IsolationCommandRefused):
        wrapped.invoke(request)

    receipt = launcher.receipts()[-1]
    assert receipt.command_started is False
    assert receipt.reason_id == "isolation.backend-execution-refused"


def test_command_build_refusal_records_receipt_after_consuming_permit(
    tmp_path: Path,
) -> None:
    execution, launcher_api = _apis()
    _, _, request = _journal_setup(tmp_path)
    host = _host(execution)
    backend = FakeTrustedBackend(
        _manifest(execution, host_snapshot_digest=host.snapshot_digest)
    )
    launcher = launcher_api.ReviewerIsolationLauncher(
        tmp_path,
        registry=execution.TrustedIsolationBackendRegistry.default(),
        backend=backend,
    )
    wrapped = launcher.prepare_driver(
        RefusingCommandDriver(request),
        context=_context(launcher_api, request, host, tmp_path),
        now=_now(),
    )
    assert wrapped is not None

    with pytest.raises(launcher_api.IsolationCommandRefused) as captured:
        wrapped.invoke(request)

    receipt = launcher.receipts()[-1]
    assert receipt.command_started is False
    assert receipt.reason_id == "isolation.command-build-refused"
    assert backend.execute_count == 0
    assert captured.value.accounted_usage == metered_provider_usage(_actual_usage())


def _apis():
    from ai_sdlc.core.stage_review import isolation_execution, isolation_launcher

    return isolation_execution, isolation_launcher


def _context(api, request, host, root: Path):
    allocation = root / "allocation"
    return api.IsolationLaunchContext(
        allocation_digest="sha256:allocation",
        assignment_digest=request.assignment_digest,
        candidate_digest=request.candidate_digest,
        host_snapshot=host,
        adapter_grade="enforced",
        normalized_run_root=str(allocation / "run"),
        layout_digest="sha256:layout",
        candidate_root=str(root / "candidate"),
        peer_output_roots=(str(root / "peer"),),
        disposable_home_root=str(allocation / "home"),
        disposable_config_root=str(allocation / "child-config"),
        disposable_credential_root=str(allocation / "credentials"),
        output_root=str(allocation / "output"),
        controller_config_root=str(root / "controller-config"),
        protected_home_root=str(root / "protected-home"),
        protected_config_roots=(str(root / "protected-home" / ".gitconfig"),),
        runtime_read_roots=(str(Path(__file__).resolve().parent),),
        selected_backend_id=host.backend_id,
        selected_contract_version=host.backend_contract_version,
        release_manifest_digest=host.backend_release_manifest_digest,
        runtime_identity_digest=host.backend_runtime_identity_digest,
    )


def _seed_context_canaries(context) -> tuple[Path, ...]:
    targets = (
        Path(context.candidate_root) / "candidate.txt",
        Path(context.peer_output_roots[0]) / "peer.txt",
        Path(context.protected_home_root) / "home.txt",
        Path(context.protected_config_roots[0]),
        Path(context.controller_config_root) / "controller.txt",
    )
    for index, path in enumerate(targets):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"canary-{index}", encoding="utf-8")
    return targets


def _pollute_probe_targets(context) -> None:
    targets = (
        Path(context.candidate_root) / "probe-write.txt",
        Path(context.peer_output_roots[0]) / "probe-write.txt",
        Path(context.protected_home_root) / "probe-write.txt",
        Path(context.protected_config_roots[0]),
        Path(context.normalized_run_root) / "boundary-link" / "probe-write.txt",
    )
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fail-open-probe", encoding="utf-8")
