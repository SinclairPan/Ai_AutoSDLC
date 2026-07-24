"""Codex permission-profile 的平台命令、探针与 profile 生成。"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import socket
import stat
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ai_sdlc.core.stage_review.codex_isolation_platform import (
    platform_mechanism,
    wrap_platform_sandbox,
)
from ai_sdlc.core.stage_review.codex_isolation_probe import (
    DIRECTORY_SENTINEL_NAME,
    PROBE_PROGRAM,
)
from ai_sdlc.core.stage_review.codex_isolation_runner import CHILD_WRAPPER_PROGRAM
from ai_sdlc.core.stage_review.isolation_launcher import IsolationLaunchContext
from ai_sdlc.core.stage_review.isolation_models import (
    IsolationBoundaryResult,
    IsolationNativeDenial,
)
from ai_sdlc.core.stage_review.resource_builders import utc_iso

_PROXY_VARIABLES = {
    "ALL_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "all_proxy",
    "https_proxy",
    "http_proxy",
    "no_proxy",
}
_CHILD_WRAPPER_NAME = "ai-sdlc-child-wrapper.py"
_BOUNDARY_PROBE_NAME = "ai-sdlc-boundary-probe.py"
_PROBE_TARGET_SCHEMA = "ai-sdlc-probe-target.v1"


@dataclass(frozen=True, slots=True)
class ProbeLaunchEnvelope:
    schema_version: str = "ai-sdlc-probe-launch.v1"
    close_fds: bool = True
    pass_fds: tuple[int, ...] = ()
    preexec_fd_remap: bool = False
    windows_handle_list: tuple[int, ...] = ()

    def payload(self) -> dict[str, object]:
        contract: dict[str, object] = {
            "schema_version": self.schema_version,
            "close_fds": self.close_fds,
            "pass_fds": list(self.pass_fds),
            "preexec_fd_remap": self.preexec_fd_remap,
            "windows_handle_list": list(self.windows_handle_list),
        }
        contract["contract_digest"] = _canonical_payload_digest(contract)
        return contract


_PROBE_LAUNCH_ENVELOPE = ProbeLaunchEnvelope()


@dataclass(frozen=True, slots=True)
class SandboxRun:
    return_code: int
    stdout: str
    stderr: str
    process_id: int
    bootstrap_cleanup_succeeded: bool = True


def write_profile(context: IsolationLaunchContext) -> tuple[Path, Path]:
    run_root = Path(context.normalized_run_root).resolve()
    controller_root = Path(context.controller_config_root).resolve()
    nonce = os.urandom(16).hex()
    config_root = controller_root / nonce
    bootstrap_root = run_root / f".ai-sdlc-bootstrap-{nonce}"
    disposable_home = Path(context.disposable_home_root).resolve()
    child_roots = (
        Path(context.disposable_config_root),
        Path(context.disposable_credential_root),
        Path(context.output_root),
    )
    for path in (run_root, controller_root, disposable_home, *child_roots):
        path.mkdir(parents=True, exist_ok=True)
    (run_root / "tmp").mkdir(parents=True, exist_ok=True)
    _prepare_boundary_link(context)
    config_created = False
    bootstrap_created = False
    try:
        config_root.mkdir(exist_ok=False)
        config_created = True
        bootstrap_root.mkdir(exist_ok=False)
        bootstrap_created = True
        _write_sandbox_helpers(bootstrap_root)
        _write_profile_config(context, config_root, bootstrap_root)
    except BaseException as exc:
        rolled_back = True
        if bootstrap_created:
            rolled_back = _remove_created_directory(bootstrap_root) and rolled_back
        if config_created:
            rolled_back = _remove_created_directory(config_root) and rolled_back
        if not rolled_back:
            raise RuntimeError("sandbox profile preparation rollback failed") from exc
        raise
    return config_root, disposable_home


def profile_text(
    context: IsolationLaunchContext,
    *,
    trusted_read_paths: tuple[Path, ...] = (),
) -> str:
    trusted_read_roots = tuple(
        str(path.resolve(strict=False)) for path in trusted_read_paths
    )
    read_roots = (
        context.candidate_root,
        *context.runtime_read_roots,
        *trusted_read_roots,
    )
    _reject_equal_config_and_read_roots(context.protected_config_roots, read_roots)
    rules = [(":root", "deny"), (":minimal", "read")]
    rules.extend(_writable_rules(context))
    rules.append((context.candidate_root, "read"))
    rules.extend((path, "deny") for path in context.peer_output_roots)
    rules.append((context.protected_home_root, "deny"))
    rules.extend(
        (path, "deny")
        for path in _independent_protected_config_roots(
            context,
            read_roots=read_roots,
        )
    )
    rules.append((context.controller_config_root, "deny"))
    # 可信运行时在 CI 中可能位于受保护 HOME 下；它们经过版本和摘要绑定，
    # 必须在宽泛 HOME deny 之后以更具体规则恢复只读可见性。
    rules.extend((path, "read") for path in context.runtime_read_roots)
    rules.extend((path, "read") for path in trusted_read_roots)
    rows = ['default_permissions = "ai-sdlc-reviewer"', ""]
    rows.extend(
        (
            "[permissions.ai-sdlc-reviewer]",
            'description = "AI-SDLC Reviewer single-command isolation"',
            "",
            "[permissions.ai-sdlc-reviewer.filesystem]",
        )
    )
    rows.extend(f"{json.dumps(path)} = {json.dumps(mode)}" for path, mode in rules)
    rows.extend(("", "[permissions.ai-sdlc-reviewer.network]", "enabled = false"))
    return "\n".join(rows) + "\n"


def _independent_protected_config_roots(
    context: IsolationLaunchContext,
    *,
    read_roots: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """去除已被更宽 deny 覆盖的子路径，避免 bubblewrap 在只读父目录造挂载点。"""
    covered = [context.protected_home_root]
    independent: list[str] = []
    ordered = sorted(
        set(context.protected_config_roots),
        key=lambda value: (len(Path(value).parts), value),
    )
    for value in ordered:
        covered_by_deny = any(
            _deny_path_covers(parent, value) for parent in covered
        )
        reopened_by_read = any(
            _read_path_may_cover(parent, value) for parent in read_roots
        )
        if covered_by_deny and not reopened_by_read:
            continue
        independent.append(value)
        covered.append(value)
    return tuple(independent)


def _deny_path_covers(parent: str, child: str) -> bool:
    lexical_parent = Path(os.path.abspath(parent))
    lexical_child = Path(os.path.abspath(child))
    resolved_parent = Path(parent).resolve(strict=False)
    resolved_child = Path(child).resolve(strict=False)
    return lexical_child.is_relative_to(
        lexical_parent
    ) and resolved_child.is_relative_to(resolved_parent)


def _read_path_may_cover(parent: str, child: str) -> bool:
    lexical_parent = Path(os.path.abspath(parent))
    lexical_child = Path(os.path.abspath(child))
    resolved_parent = Path(parent).resolve(strict=False)
    resolved_child = Path(child).resolve(strict=False)
    return lexical_child.is_relative_to(
        lexical_parent
    ) or resolved_child.is_relative_to(resolved_parent)


def _reject_equal_config_and_read_roots(
    protected_config_roots: tuple[str, ...],
    read_roots: tuple[str, ...],
) -> None:
    for protected in protected_config_roots:
        lexical_protected = Path(os.path.abspath(protected))
        resolved_protected = Path(protected).resolve(strict=False)
        for readable in read_roots:
            lexical_readable = Path(os.path.abspath(readable))
            resolved_readable = Path(readable).resolve(strict=False)
            if (
                lexical_protected == lexical_readable
                or resolved_protected == resolved_readable
            ):
                raise ValueError("protected config and read root are equal")


def _writable_rules(context: IsolationLaunchContext) -> list[tuple[str, str]]:
    return [
        (context.normalized_run_root, "write"),
        (context.output_root, "write"),
        (context.disposable_home_root, "write"),
        (context.disposable_config_root, "write"),
        (context.disposable_credential_root, "write"),
    ]


@contextmanager
def _controlled_listeners() -> Iterator[tuple[list[list[object]], int]]:
    listeners: list[socket.socket] = []
    targets: list[list[object]] = []
    read_fd, write_fd = os.pipe()
    try:
        for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
            listener = socket.socket(family, socket.SOCK_STREAM)
            listener.bind((host, 0))
            listener.listen(1)
            listeners.append(listener)
            targets.append([family, list(listener.getsockname())])
            _prove_host_connectivity(listener, family)
            if family == socket.AF_INET:
                targets.append([family, ["localhost", listener.getsockname()[1]]])
        yield targets, read_fd
    finally:
        os.close(read_fd)
        os.close(write_fd)
        for listener in listeners:
            listener.close()


def _prove_host_connectivity(
    listener: socket.socket,
    family: socket.AddressFamily,
) -> None:
    client = socket.socket(family, socket.SOCK_STREAM)
    client.settimeout(1)
    try:
        client.connect(listener.getsockname())
        accepted, _ = listener.accept()
        accepted.close()
    finally:
        client.close()


def run_boundary_probe(
    executable: str,
    context: IsolationLaunchContext,
    config_root: Path,
    disposable_home: Path,
) -> SandboxRun:
    try:
        with _controlled_listeners() as (targets, sentinel_fd):
            launch_envelope = _PROBE_LAUNCH_ENVELOPE
            payload = _probe_payload(
                context,
                targets,
                sentinel_fd,
                launch_envelope=launch_envelope,
            )
            bootstrap_root = _bootstrap_root(context, config_root)
            completed = run_sandbox(
                executable,
                context,
                config_root,
                disposable_home,
                (_sandbox_python(), str(bootstrap_root / _BOUNDARY_PROBE_NAME)),
                json.dumps(payload),
                launch_envelope=launch_envelope,
            )
            if not _probe_targets_unchanged(
                payload,
                sentinel_fd,
                launch_envelope,
            ):
                return SandboxRun(
                    1,
                    "",
                    "probe target identity changed during calibration",
                    completed.process_id,
                    bootstrap_cleanup_succeeded=(completed.bootstrap_cleanup_succeeded),
                )
            return completed
    except (OSError, ValueError) as exc:
        return SandboxRun(1, "", str(exc), os.getpid())


def run_sandbox(
    executable: str,
    context: IsolationLaunchContext,
    config_root: Path,
    disposable_home: Path,
    argv: tuple[str, ...],
    stdin_text: str,
    *,
    launch_envelope: ProbeLaunchEnvelope = _PROBE_LAUNCH_ENVELOPE,
) -> SandboxRun:
    bootstrap_root = _bootstrap_root(context, config_root)
    spec_path: Path | None = None
    completed: SandboxRun | None = None
    try:
        spec_path = _write_invocation_spec(bootstrap_root, context, argv)
        _write_profile_config(context, config_root, bootstrap_root)
        wrapped = _child_wrapper_command(bootstrap_root, spec_path)
        codex = sandbox_command(executable, context.normalized_run_root, wrapped)
        writable = tuple(path for path, _ in _writable_rules(context))
        command = wrap_platform_sandbox(codex, writable)
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=context.normalized_run_root,
            env=_sandbox_environment(config_root, disposable_home, context),
            **_popen_launch_kwargs(launch_envelope),
        )
        try:
            stdout, stderr = process.communicate(stdin_text, timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            stderr = f"{stderr}\nisolation command timed out"
        completed = SandboxRun(process.returncode, stdout, stderr, process.pid)
    finally:
        bootstrap_cleaned = _cleanup_bootstrap(context, bootstrap_root)
    if completed is None:
        raise RuntimeError("sandbox command did not produce a process result")
    return SandboxRun(
        completed.return_code,
        completed.stdout,
        completed.stderr,
        completed.process_id,
        bootstrap_cleanup_succeeded=bootstrap_cleaned,
    )


def sandbox_command(
    executable: str,
    run_root: str,
    argv: tuple[str, ...],
) -> tuple[str, ...]:
    return (
        executable,
        "sandbox",
        "--permissions-profile",
        "ai-sdlc-reviewer",
        "-C",
        run_root,
        "--",
        *argv,
    )


def _popen_launch_kwargs(
    envelope: ProbeLaunchEnvelope,
) -> dict[str, object]:
    if (
        envelope.schema_version != "ai-sdlc-probe-launch.v1"
        or not envelope.close_fds
        or envelope.pass_fds
        or envelope.preexec_fd_remap
        or envelope.windows_handle_list
    ):
        raise ValueError("sandbox launch envelope is not supported")
    return {"close_fds": envelope.close_fds}


def _sandbox_environment(
    config_root: Path,
    home: Path,
    context: IsolationLaunchContext,
) -> dict[str, str]:
    allowed = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TMP", "TEMP"}
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    temp_root = str(Path(context.normalized_run_root) / "tmp")
    environment.update(
        {
            "CODEX_HOME": str(config_root),
            "HOME": str(home),
            "TMP": temp_root,
            "TEMP": temp_root,
            "TMPDIR": temp_root,
        }
    )
    environment["USERPROFILE"] = str(home)
    return {
        key: value for key, value in environment.items() if key not in _PROXY_VARIABLES
    }


def _child_wrapper_command(
    config_root: Path,
    spec_path: Path,
) -> tuple[str, ...]:
    return (
        _sandbox_python(),
        str(config_root / _CHILD_WRAPPER_NAME),
        str(spec_path),
    )


def _write_invocation_spec(
    config_root: Path,
    context: IsolationLaunchContext,
    argv: tuple[str, ...],
) -> Path:
    temp_root = str(Path(context.normalized_run_root) / "tmp")
    child_environment = {
        "CODEX_HOME": context.disposable_config_root,
        "HOME": context.disposable_home_root,
        "USERPROFILE": context.disposable_home_root,
        "AI_SDLC_CREDENTIAL_ROOT": context.disposable_credential_root,
        "AI_SDLC_OUTPUT_ROOT": context.output_root,
        "XDG_CONFIG_HOME": context.disposable_config_root,
        "GIT_CONFIG_GLOBAL": str(Path(context.disposable_config_root) / "gitconfig"),
        "TMP": temp_root,
        "TEMP": temp_root,
        "TMPDIR": temp_root,
    }
    spec_path = config_root / f"invocation-{os.urandom(16).hex()}.json"
    spec_path.write_text(
        json.dumps(
            {
                "argv": argv,
                "environment": child_environment,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return spec_path


def _write_sandbox_helpers(bootstrap_root: Path) -> None:
    (bootstrap_root / _CHILD_WRAPPER_NAME).write_text(
        CHILD_WRAPPER_PROGRAM,
        encoding="utf-8",
    )
    (bootstrap_root / _BOUNDARY_PROBE_NAME).write_text(
        PROBE_PROGRAM,
        encoding="utf-8",
    )


def _write_profile_config(
    context: IsolationLaunchContext,
    config_root: Path,
    bootstrap_root: Path,
) -> None:
    (config_root / "config.toml").write_text(
        profile_text(context, trusted_read_paths=(bootstrap_root,)),
        encoding="utf-8",
    )


def _bootstrap_root(context: IsolationLaunchContext, config_root: Path) -> Path:
    run_root = Path(context.normalized_run_root).resolve(strict=False)
    bootstrap_root = (run_root / f".ai-sdlc-bootstrap-{config_root.name}").resolve(
        strict=False
    )
    if bootstrap_root.parent != run_root:
        raise ValueError("sandbox bootstrap root escapes the reviewer run root")
    return bootstrap_root


def _cleanup_bootstrap(
    context: IsolationLaunchContext,
    bootstrap_root: Path,
) -> bool:
    run_root = Path(context.normalized_run_root).resolve(strict=False)
    if bootstrap_root.parent.resolve(
        strict=False
    ) != run_root or not bootstrap_root.name.startswith(".ai-sdlc-bootstrap-"):
        return False
    return _remove_created_directory(bootstrap_root)


def _remove_created_directory(path: Path) -> bool:
    try:
        shutil.rmtree(path, ignore_errors=False)
    except OSError:
        return False
    return not path.exists()


def sandbox_python_executable() -> str:
    """优先使用可用的系统解释器，否则使用已纳入 profile 的真实解释器。"""
    current = platform.system().lower()
    candidates = (
        (Path("/Library/Developer/CommandLineTools/usr/bin/python3"),)
        if current == "darwin"
        else (Path("/usr/bin/python3"),)
        if current == "linux"
        else ()
    )
    for system_python in candidates:
        if _python_available(system_python):
            return str(system_python.resolve())
    return str(Path(sys.executable).resolve())


def _sandbox_python() -> str:
    return sandbox_python_executable()


def _python_available(executable: Path) -> bool:
    if not executable.is_file():
        return False
    try:
        completed = subprocess.run(
            (str(executable), "-c", "pass"),
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _probe_payload(
    context: IsolationLaunchContext,
    targets: list[list[object]],
    sentinel_fd: int,
    *,
    launch_envelope: ProbeLaunchEnvelope = _PROBE_LAUNCH_ENVELOPE,
) -> dict[str, object]:
    sentinel = os.fstat(sentinel_fd)
    probe_root = Path(context.normalized_run_root).resolve(strict=False).parent
    nonce = os.urandom(16).hex()
    candidate = _probe_target(
        context.candidate_root,
        probe_root,
        role="candidate",
        nonce=nonce,
    )
    peers = tuple(
        _probe_target(value, probe_root, role=f"peer:{index}", nonce=nonce)
        for index, value in enumerate(context.peer_output_roots)
    )
    real_home = _probe_target(
        context.protected_home_root,
        probe_root,
        role="real-home",
        nonce=nonce,
    )
    global_configs = tuple(
        _probe_target(
            value,
            probe_root,
            role=f"global-config:{index}",
            nonce=nonce,
        )
        for index, value in enumerate(context.protected_config_roots)
    )
    runtime_read = probe_root / "trusted-runtime"
    boundary_link = Path(context.normalized_run_root) / "boundary-link"
    outside = Path(context.normalized_run_root).parent / "outside"
    return {
        "candidate_target": candidate,
        "candidate_read_target": _probe_sentinel_target(
            context.candidate_root,
            probe_root,
            role="candidate-read",
            nonce=nonce,
        ),
        "peer_targets": peers,
        "peer_read_targets": tuple(
            _probe_sentinel_target(
                value,
                probe_root,
                role=f"peer-read:{index}",
                nonce=nonce,
            )
            for index, value in enumerate(context.peer_output_roots)
        ),
        "real_home_target": real_home,
        "real_home_read_target": _probe_sentinel_target(
            context.protected_home_root,
            probe_root,
            role="real-home-read",
            nonce=nonce,
        ),
        "global_config_targets": global_configs,
        "runtime_read_target": _probe_sentinel_target(
            runtime_read,
            probe_root,
            role="runtime-read",
            nonce=nonce,
        ),
        "run_root": context.normalized_run_root,
        "output_root": context.output_root,
        "boundary_link_target": _probe_target(
            boundary_link,
            probe_root,
            role="boundary-link",
            nonce=nonce,
        ),
        "boundary_link_read_target": _probe_target(
            boundary_link / DIRECTORY_SENTINEL_NAME,
            probe_root,
            role="boundary-link-read",
            nonce=nonce,
        ),
        "outside_target": _probe_target(
            outside,
            probe_root,
            role="outside",
            nonce=nonce,
            visibility="hidden",
        ),
        "network_targets": targets,
        "probe_nonce": nonce,
        "sentinel_fd": sentinel_fd,
        "sentinel_fd_identity": [sentinel.st_dev, sentinel.st_ino],
        "launch_contract": launch_envelope.payload(),
        "platform_mechanism": platform_mechanism()[1],
        "observed_at": utc_iso(datetime.now().astimezone()),
    }


def _probe_sentinel_target(
    value: str,
    probe_root: Path,
    *,
    role: str,
    nonce: str,
) -> dict[str, object]:
    return _probe_target(
        Path(value) / DIRECTORY_SENTINEL_NAME,
        probe_root,
        role=role,
        nonce=nonce,
    )


def _probe_target(
    value: str | Path,
    probe_root: Path,
    *,
    role: str,
    nonce: str,
    visibility: str = "visible",
) -> dict[str, object]:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("probe target must be absolute")
    lexical = path.absolute()
    resolved = path.resolve(strict=False)
    if not lexical.is_relative_to(probe_root) or not resolved.is_relative_to(
        probe_root
    ):
        raise ValueError("probe target escapes canonical calibration root")
    snapshot = _target_snapshot(path)
    descriptor: dict[str, object] = {
        "schema_version": _PROBE_TARGET_SCHEMA,
        "path": str(lexical),
        "role": role,
        "nonce": nonce,
        "visibility": visibility,
        **snapshot,
    }
    descriptor["descriptor_digest"] = _descriptor_digest(descriptor)
    return descriptor


def _target_snapshot(path: Path) -> dict[str, object]:
    try:
        lexical = path.lstat()
        resolved = path.stat()
    except OSError:
        return {
            "exists": False,
            "kind": "unknown",
            "lexical_identity": None,
            "resolved_identity": None,
        }
    return {
        "exists": True,
        "kind": _stat_kind(resolved.st_mode),
        "lexical_identity": [
            lexical.st_dev,
            lexical.st_ino,
            _stat_kind(lexical.st_mode),
        ],
        "resolved_identity": [
            resolved.st_dev,
            resolved.st_ino,
            _stat_kind(resolved.st_mode),
        ],
    }


def _stat_kind(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "unknown"


def _descriptor_digest(descriptor: dict[str, object]) -> str:
    canonical = {
        key: value for key, value in descriptor.items() if key != "descriptor_digest"
    }
    return _canonical_payload_digest(canonical)


def _canonical_payload_digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _probe_targets_unchanged(
    payload: dict[str, object],
    sentinel_fd: int,
    launch_envelope: ProbeLaunchEnvelope = _PROBE_LAUNCH_ENVELOPE,
) -> bool:
    descriptors = [
        payload["candidate_target"],
        payload["candidate_read_target"],
        *payload["peer_targets"],
        *payload["peer_read_targets"],
        payload["real_home_target"],
        payload["real_home_read_target"],
        *payload["global_config_targets"],
        payload["runtime_read_target"],
        payload["boundary_link_target"],
        payload["boundary_link_read_target"],
        payload["outside_target"],
    ]
    try:
        sentinel = os.fstat(sentinel_fd)
    except OSError:
        return False
    if payload.get("sentinel_fd") != sentinel_fd:
        return False
    if [sentinel.st_dev, sentinel.st_ino] != payload["sentinel_fd_identity"]:
        return False
    if payload.get("launch_contract") != launch_envelope.payload():
        return False
    for value in descriptors:
        if not isinstance(value, dict):
            return False
        if value.get("descriptor_digest") != _descriptor_digest(value):
            return False
        actual = _target_snapshot(Path(str(value.get("path", ""))))
        expected = {
            "exists": value.get("exists"),
            "kind": value.get("kind"),
            "lexical_identity": value.get("lexical_identity"),
            "resolved_identity": value.get("resolved_identity"),
        }
        if actual != expected:
            return False
    return True


def decode_probe(
    run: SandboxRun,
) -> tuple[tuple[IsolationBoundaryResult, ...], tuple[IsolationNativeDenial, ...]]:
    if run.return_code != 0:
        return (), ()
    try:
        payload = json.loads(run.stdout.strip().splitlines()[-1])
        values = tuple(
            IsolationBoundaryResult.model_validate(item)
            for item in payload["boundary_results"]
        )
        denials = tuple(
            IsolationNativeDenial.model_validate(item)
            for item in payload["os_native_denials"]
        )
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return (), ()
    ordered = tuple(sorted(values, key=lambda item: item.action))

    def denial_key(item: IsolationNativeDenial) -> tuple[str, str, str, str]:
        return item.mechanism, item.operation, item.target, item.observed_at

    return ordered, tuple(sorted(denials, key=denial_key))


def _prepare_boundary_link(context: IsolationLaunchContext) -> None:
    link = Path(context.normalized_run_root) / "boundary-link"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(Path(context.peer_output_roots[0]), target_is_directory=True)


_profile_text = profile_text
_sandbox_command = sandbox_command


__all__ = [
    "SandboxRun",
    "decode_probe",
    "platform_mechanism",
    "profile_text",
    "run_boundary_probe",
    "run_sandbox",
    "sandbox_command",
    "sandbox_python_executable",
    "write_profile",
]
