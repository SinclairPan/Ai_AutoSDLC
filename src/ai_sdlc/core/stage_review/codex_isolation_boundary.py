"""Codex permission-profile 的平台命令、探针与 profile 生成。"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
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
from ai_sdlc.core.stage_review.codex_isolation_probe import PROBE_PROGRAM
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
    rules = [(":root", "deny"), (":minimal", "read")]
    rules.extend(_writable_rules(context))
    rules.append((context.candidate_root, "read"))
    rules.extend((path, "deny") for path in context.peer_output_roots)
    rules.append((context.protected_home_root, "deny"))
    rules.extend((path, "deny") for path in context.protected_config_roots)
    rules.append((context.controller_config_root, "deny"))
    # 可信运行时在 CI 中可能位于受保护 HOME 下；它们经过版本和摘要绑定，
    # 必须在宽泛 HOME deny 之后以更具体规则恢复只读可见性。
    rules.extend((path, "read") for path in context.runtime_read_roots)
    rules.extend(
        (str(path.resolve(strict=False)), "read") for path in trusted_read_paths
    )
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
            payload = _probe_payload(context, targets, sentinel_fd)
            bootstrap_root = _bootstrap_root(context, config_root)
            return run_sandbox(
                executable,
                context,
                config_root,
                disposable_home,
                (_sandbox_python(), str(bootstrap_root / _BOUNDARY_PROBE_NAME)),
                json.dumps(payload),
            )
    except OSError as exc:
        return SandboxRun(1, "", str(exc), os.getpid())


def run_sandbox(
    executable: str,
    context: IsolationLaunchContext,
    config_root: Path,
    disposable_home: Path,
    argv: tuple[str, ...],
    stdin_text: str,
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
            close_fds=True,
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
    bootstrap_root = (
        run_root / f".ai-sdlc-bootstrap-{config_root.name}"
    ).resolve(strict=False)
    if bootstrap_root.parent != run_root:
        raise ValueError("sandbox bootstrap root escapes the reviewer run root")
    return bootstrap_root


def _cleanup_bootstrap(
    context: IsolationLaunchContext,
    bootstrap_root: Path,
) -> bool:
    run_root = Path(context.normalized_run_root).resolve(strict=False)
    if (
        bootstrap_root.parent.resolve(strict=False) != run_root
        or not bootstrap_root.name.startswith(".ai-sdlc-bootstrap-")
    ):
        return False
    return _remove_created_directory(bootstrap_root)


def _remove_created_directory(path: Path) -> bool:
    try:
        shutil.rmtree(path, ignore_errors=False)
    except OSError:
        return False
    return not path.exists()


def _sandbox_python() -> str:
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
) -> dict[str, object]:
    sentinel = os.fstat(sentinel_fd)
    return {
        "candidate_root": context.candidate_root,
        "peer_roots": context.peer_output_roots,
        "real_home": context.protected_home_root,
        "global_configs": context.protected_config_roots,
        "run_root": context.normalized_run_root,
        "output_root": context.output_root,
        "boundary_link": str(Path(context.normalized_run_root) / "boundary-link"),
        "outside_root": str(Path(context.normalized_run_root).parent / "outside"),
        "network_targets": targets,
        "sentinel_fd": sentinel_fd,
        "sentinel_fd_identity": [sentinel.st_dev, sentinel.st_ino],
        "platform_mechanism": platform_mechanism()[1],
        "observed_at": utc_iso(datetime.now().astimezone()),
    }


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
    "write_profile",
]
