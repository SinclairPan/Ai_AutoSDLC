"""Codex 隔离边界探针的进程级回归测试。"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from ai_sdlc.core.stage_review.codex_isolation_probe import PROBE_PROGRAM


def _run_probe(
    tmp_path: Path,
    socket_program: str,
    *,
    target_count: int = 1,
    config_is_file: bool = False,
    runtime_patch: str = "",
    payload_patch: dict[str, Any] | None = None,
    payload_mutator: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    probe_path = tmp_path / "probe.py"
    probe_path.write_text(PROBE_PROGRAM, encoding="utf-8")
    shim_root = tmp_path / "shim"
    shim_root.mkdir()
    (shim_root / "socket.py").write_text(
        f"AF_INET = 2\nSOCK_STREAM = 1\n\n{socket_program}\n",
        encoding="utf-8",
    )

    roots: dict[str, Path] = {}
    for name in (
        "candidate",
        "peer",
        "real-home",
        "global-config",
        "runtime-read",
        "boundary-link",
        "outside",
        "output",
    ):
        root = tmp_path / name
        root.mkdir()
        (root / "sentinel.txt").write_text(name, encoding="utf-8")
        roots[name] = root

    nonce = "0" * 32
    launch_contract: dict[str, object] = {
        "schema_version": "ai-sdlc-probe-launch.v1",
        "close_fds": True,
        "pass_fds": [],
        "preexec_fd_remap": False,
        "windows_handle_list": [],
    }
    launch_contract["contract_digest"] = hashlib.sha256(
        json.dumps(
            launch_contract,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    def target(
        path: Path,
        kind: str,
        role: str,
        visibility: str = "visible",
    ) -> dict[str, object]:
        lexical = path.lstat()
        resolved = path.stat()

        def identity(value: os.stat_result) -> list[object]:
            value_kind = (
                "file"
                if stat.S_ISREG(value.st_mode)
                else "directory"
                if stat.S_ISDIR(value.st_mode)
                else "symlink"
                if stat.S_ISLNK(value.st_mode)
                else "unknown"
            )
            return [value.st_dev, value.st_ino, value_kind]

        descriptor: dict[str, object] = {
            "schema_version": "ai-sdlc-probe-target.v1",
            "path": str(path),
            "role": role,
            "nonce": nonce,
            "visibility": visibility,
            "exists": True,
            "kind": kind,
            "lexical_identity": identity(lexical),
            "resolved_identity": identity(resolved),
        }
        descriptor["descriptor_digest"] = hashlib.sha256(
            json.dumps(
                descriptor,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return descriptor

    global_config = (
        roots["global-config"] / "sentinel.txt"
        if config_is_file
        else roots["global-config"]
    )
    payload = {
        "candidate_target": target(roots["candidate"], "directory", "candidate"),
        "candidate_read_target": target(
            roots["candidate"] / "sentinel.txt",
            "file",
            "candidate-read",
        ),
        "peer_targets": [target(roots["peer"], "directory", "peer:0")],
        "peer_read_targets": [
            target(roots["peer"] / "sentinel.txt", "file", "peer-read:0")
        ],
        "real_home_target": target(
            roots["real-home"],
            "directory",
            "real-home",
        ),
        "real_home_read_target": target(
            roots["real-home"] / "sentinel.txt",
            "file",
            "real-home-read",
        ),
        "global_config_targets": [
            target(
                global_config,
                "file" if config_is_file else "directory",
                "global-config:0",
            )
        ],
        "runtime_read_target": target(
            roots["runtime-read"] / "sentinel.txt",
            "file",
            "runtime-read",
        ),
        "boundary_link_target": target(
            roots["boundary-link"],
            "directory",
            "boundary-link",
        ),
        "boundary_link_read_target": target(
            roots["boundary-link"] / "sentinel.txt",
            "file",
            "boundary-link-read",
        ),
        "network_targets": [
            [2, ["127.0.0.1", 9 + offset]] for offset in range(target_count)
        ],
        "probe_nonce": nonce,
        "sentinel_fd": -1,
        "sentinel_fd_identity": [0, 0],
        "launch_contract": launch_contract,
        "outside_target": target(
            roots["outside"],
            "directory",
            "outside",
            "hidden",
        ),
        "output_root": str(roots["output"]),
        "platform_mechanism": "linux-seccomp",
        "observed_at": "2026-07-24T00:00:00Z",
    }
    if payload_patch is not None:
        payload.update(payload_patch)
    if payload_mutator is not None:
        payload_mutator(payload)
    command_path = probe_path
    if runtime_patch:
        command_path = tmp_path / "probe-launcher.py"
        command_path.write_text(
            "\n".join(
                (
                    "import io",
                    "import json",
                    "import os",
                    "import runpy",
                    "import subprocess",
                    runtime_patch,
                    f"runpy.run_path({str(probe_path)!r}, run_name='__main__')",
                )
            ),
            encoding="utf-8",
        )
    proxy_names = {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    }
    completed = subprocess.run(
        [sys.executable, str(command_path)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env={
            **{
                key: value
                for key, value in os.environ.items()
                if key not in proxy_names
            },
            "PYTHONPATH": str(shim_root),
        },
    )

    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _network_result(evidence: dict[str, object]) -> dict[str, object]:
    boundaries = evidence["boundary_results"]
    assert isinstance(boundaries, Sequence)
    return next(
        item
        for item in boundaries
        if isinstance(item, dict) and item["action"] == "network-denied"
    )


def _boundary_result(
    evidence: dict[str, object],
    action: str,
) -> dict[str, object]:
    boundaries = evidence["boundary_results"]
    assert isinstance(boundaries, Sequence)
    return next(
        item
        for item in boundaries
        if isinstance(item, dict) and item["action"] == action
    )


def test_probe_records_socket_creation_permission_error_as_network_denial(
    tmp_path: Path,
) -> None:
    evidence = _run_probe(
        tmp_path,
        """
def socket(*args, **kwargs):
    raise PermissionError(1, "Operation not permitted")
""",
    )
    network = _network_result(evidence)

    assert network["observed"] == "denied"
    assert network["os_error"] == "socket:PermissionError:1"
    assert any(
        item["operation"] == "network-denied"
        and item["os_error"] == "socket:PermissionError:1"
        for item in evidence["os_native_denials"]
    )


def test_probe_checks_file_write_access_without_creating_a_sibling(
    tmp_path: Path,
) -> None:
    evidence = _run_probe(
        tmp_path,
        """
def socket(*args, **kwargs):
    raise PermissionError(1, "Operation not permitted")
""",
        config_is_file=True,
    )
    protected_config = tmp_path / "global-config" / "sentinel.txt"
    config_boundary = next(
        item
        for item in evidence["boundary_results"]
        if item["action"] == "global-config-denied"
    )

    assert config_boundary["observed"] == "access-succeeded"
    assert protected_config.read_text(encoding="utf-8") == "global-config"
    assert not protected_config.with_name(
        protected_config.name + ".t601-write"
    ).exists()


def test_probe_accepts_only_policy_denials_for_protected_file(
    tmp_path: Path,
) -> None:
    evidence = _run_probe(
        tmp_path,
        """
def socket(*args, **kwargs):
    raise PermissionError(1, "Operation not permitted")
""",
        config_is_file=True,
        runtime_patch="""
real_io_open = io.open
real_os_open = os.open

def deny_read(path, *args, **kwargs):
    if not isinstance(path, int) and "global-config" in os.fspath(path):
        raise PermissionError(13, "Permission denied")
    return real_io_open(path, *args, **kwargs)

def deny_write(path, *args, **kwargs):
    if not isinstance(path, int) and "global-config" in os.fspath(path):
        raise PermissionError(13, "Permission denied")
    return real_os_open(path, *args, **kwargs)

io.open = deny_read
os.open = deny_write
""",
    )
    boundary = next(
        item
        for item in evidence["boundary_results"]
        if item["action"] == "global-config-denied"
    )

    assert boundary["observed"] == "denied"
    assert "read:PermissionError:13" in boundary["os_error"]
    assert "open:PermissionError:13" in boundary["os_error"]


def test_probe_accepts_read_only_filesystem_as_policy_denial(
    tmp_path: Path,
) -> None:
    evidence = _run_probe(
        tmp_path,
        """
def socket(*args, **kwargs):
    raise PermissionError(1, "Operation not permitted")
""",
        config_is_file=True,
        runtime_patch="""
real_io_open = io.open
real_os_open = os.open

def deny_read(path, *args, **kwargs):
    if not isinstance(path, int) and "global-config" in os.fspath(path):
        raise PermissionError(13, "Permission denied")
    return real_io_open(path, *args, **kwargs)

def deny_write(path, *args, **kwargs):
    if not isinstance(path, int) and "global-config" in os.fspath(path):
        raise OSError(30, "Read-only file system")
    return real_os_open(path, *args, **kwargs)

io.open = deny_read
os.open = deny_write
""",
    )
    boundary = next(
        item
        for item in evidence["boundary_results"]
        if item["action"] == "global-config-denied"
    )

    assert boundary["observed"] == "denied"
    assert "open:OSError:30" in boundary["os_error"]


@pytest.mark.parametrize("error_number", [2, 5, 12, 20, 24])
def test_probe_treats_non_policy_file_open_errors_as_probe_failure(
    tmp_path: Path,
    error_number: int,
) -> None:
    evidence = _run_probe(
        tmp_path,
        """
def socket(*args, **kwargs):
    raise PermissionError(1, "Operation not permitted")
""",
        config_is_file=True,
        runtime_patch=f"""
real_io_open = io.open
real_os_open = os.open

def deny_read(path, *args, **kwargs):
    if not isinstance(path, int) and "global-config" in os.fspath(path):
        raise PermissionError(13, "Permission denied")
    return real_io_open(path, *args, **kwargs)

def fail_write(path, *args, **kwargs):
    if not isinstance(path, int) and "global-config" in os.fspath(path):
        raise OSError({error_number}, "probe fault")
    return real_os_open(path, *args, **kwargs)

io.open = deny_read
os.open = fail_write
""",
    )
    boundary = next(
        item
        for item in evidence["boundary_results"]
        if item["action"] == "global-config-denied"
    )

    assert boundary["observed"] == "probe-failed"
    assert boundary["os_error"].endswith(f":{error_number}")


def test_probe_accepts_hidden_outside_enoent_as_native_denial(
    tmp_path: Path,
) -> None:
    evidence = _run_probe(
        tmp_path,
        """
def socket(*args, **kwargs):
    raise PermissionError(1, "Operation not permitted")
""",
        runtime_patch="""
real_os_open = os.open

def hide_outside(path, *args, **kwargs):
    if not isinstance(path, int) and "outside" in os.fspath(path):
        raise FileNotFoundError(2, "hidden by native sandbox")
    return real_os_open(path, *args, **kwargs)

os.open = hide_outside
""",
    )

    boundary = _boundary_result(evidence, "run-root-disposable")
    assert boundary["observed"] == "denied"
    assert boundary["os_error"] == "open:FileNotFoundError:2"
    assert boundary["blocked_before_side_effect"] is True


def test_probe_keeps_file_access_succeeded_when_close_fails(
    tmp_path: Path,
) -> None:
    evidence = _run_probe(
        tmp_path,
        """
def socket(*args, **kwargs):
    raise PermissionError(1, "Operation not permitted")
""",
        config_is_file=True,
        runtime_patch="""
real_io_open = io.open
real_os_open = os.open
real_os_close = os.close
protected_descriptors = set()

def deny_read(path, *args, **kwargs):
    if not isinstance(path, int) and "global-config" in os.fspath(path):
        raise PermissionError(13, "Permission denied")
    return real_io_open(path, *args, **kwargs)

def track_open(path, *args, **kwargs):
    descriptor = real_os_open(path, *args, **kwargs)
    if not isinstance(path, int) and "global-config" in os.fspath(path):
        protected_descriptors.add(descriptor)
    return descriptor

def fail_close(descriptor):
    if descriptor in protected_descriptors:
        protected_descriptors.remove(descriptor)
        raise OSError(9, "close fault")
    return real_os_close(descriptor)

io.open = deny_read
os.open = track_open
os.close = fail_close
""",
    )
    boundary = next(
        item
        for item in evidence["boundary_results"]
        if item["action"] == "global-config-denied"
    )

    assert boundary["observed"] == "access-succeeded"
    assert "close:OSError:9" in boundary["os_error"]


@pytest.mark.parametrize(
    "descriptor_patch",
    [
        {"exists": False},
        {"kind": "unknown"},
    ],
)
def test_probe_rejects_missing_or_unknown_host_target(
    tmp_path: Path,
    descriptor_patch: dict[str, object],
) -> None:
    protected_config = tmp_path / "global-config" / "sentinel.txt"
    descriptor = {
        "path": str(protected_config),
        "kind": "file",
        "exists": True,
        **descriptor_patch,
    }
    evidence = _run_probe(
        tmp_path,
        """
def socket(*args, **kwargs):
    raise PermissionError(1, "Operation not permitted")
""",
        config_is_file=True,
        payload_patch={"global_config_targets": [descriptor]},
    )

    assert (
        next(
            item["observed"]
            for item in evidence["boundary_results"]
            if item["action"] == "global-config-denied"
        )
        == "probe-failed"
    )


def test_probe_rejects_descriptor_digest_tampering(tmp_path: Path) -> None:
    def tamper(payload: dict[str, object]) -> None:
        target = payload["outside_target"]
        assert isinstance(target, dict)
        target["nonce"] = "f" * 32

    evidence = _run_probe(
        tmp_path,
        """
def socket(*args, **kwargs):
    raise PermissionError(1, "Operation not permitted")
""",
        payload_mutator=tamper,
    )

    assert (
        _boundary_result(evidence, "run-root-disposable")["observed"] == "probe-failed"
    )


def test_probe_rejects_target_role_swap(tmp_path: Path) -> None:
    def swap(payload: dict[str, object]) -> None:
        payload["candidate_target"] = payload["outside_target"]

    evidence = _run_probe(
        tmp_path,
        """
def socket(*args, **kwargs):
    raise PermissionError(1, "Operation not permitted")
""",
        payload_mutator=swap,
    )

    assert (
        _boundary_result(evidence, "candidate-read-only")["observed"] == "probe-failed"
    )


def test_probe_requires_runtime_read_canary_for_global_config_denial(
    tmp_path: Path,
) -> None:
    def remove_runtime_canary(payload: dict[str, object]) -> None:
        target = payload["runtime_read_target"]
        assert isinstance(target, dict)
        Path(str(target["path"])).unlink()

    evidence = _run_probe(
        tmp_path,
        """
def socket(*args, **kwargs):
    raise PermissionError(1, "Operation not permitted")
""",
        payload_mutator=remove_runtime_canary,
    )

    assert (
        _boundary_result(evidence, "global-config-denied")["observed"]
        == "runtime-read-probe-failed"
    )


def test_probe_rejects_empty_protected_target_set(tmp_path: Path) -> None:
    evidence = _run_probe(
        tmp_path,
        """
def socket(*args, **kwargs):
    raise PermissionError(1, "Operation not permitted")
""",
        payload_patch={"peer_targets": [], "peer_read_targets": []},
    )

    assert (
        next(
            item["observed"]
            for item in evidence["boundary_results"]
            if item["action"] == "peer-output-denied"
        )
        == "probe-failed"
    )


def test_child_containment_passes_quoted_candidate_path_as_argv(
    tmp_path: Path,
) -> None:
    quoted_root = tmp_path / "project with ' quote"
    quoted_root.mkdir()
    evidence = _run_probe(
        quoted_root,
        """
def socket(*args, **kwargs):
    raise PermissionError(1, "Operation not permitted")
""",
    )

    assert (
        _boundary_result(evidence, "child-process-contained")["observed"]
        == "write-succeeded"
    )
    assert (quoted_root / "candidate" / "t601-child.txt").read_text() == "x"


@pytest.mark.parametrize(
    "runtime_patch",
    [
        """
class FakeCompleted:
    returncode = 1
    stdout = ""
    stderr = "resource fault"

subprocess.run = lambda *args, **kwargs: FakeCompleted()
""",
        """
def fail_start(*args, **kwargs):
    raise OSError(12, "process start resource fault")

subprocess.run = fail_start
""",
        """
class FakeCompleted:
    returncode = 0
    stdout = "not-json"
    stderr = ""

subprocess.run = lambda *args, **kwargs: FakeCompleted()
""",
        """
class FakeCompleted:
    returncode = 0
    stdout = json.dumps({
        "protocol": "ai-sdlc-child-boundary.v1",
        "target": "TARGET_PATH",
        "state": [],
        "errno": "13",
        "error": 13,
    })
    stderr = ""

def fake_run(argv, *args, **kwargs):
    completed = FakeCompleted()
    completed.stdout = completed.stdout.replace("TARGET_PATH", argv[-1])
    return completed

subprocess.run = fake_run
""",
        """
class FakeCompleted:
    returncode = 0
    stdout = json.dumps({
        "protocol": "ai-sdlc-child-boundary.v1",
        "target": "wrong-target",
        "state": "denied",
        "errno": 13,
        "error": "open:PermissionError:13",
    })
    stderr = ""

subprocess.run = lambda *args, **kwargs: FakeCompleted()
""",
        """
class FakeCompleted:
    returncode = 0
    stdout = json.dumps({
        "protocol": "ai-sdlc-child-boundary.v1",
        "target": "TARGET_PATH",
        "state": "denied",
        "errno": 24,
        "error": "open:OSError:24",
    })
    stderr = ""

def fake_run(argv, *args, **kwargs):
    completed = FakeCompleted()
    completed.stdout = completed.stdout.replace("TARGET_PATH", argv[-1])
    return completed

subprocess.run = fake_run
""",
    ],
)
def test_child_containment_rejects_process_and_protocol_faults(
    tmp_path: Path,
    runtime_patch: str,
) -> None:
    evidence = _run_probe(
        tmp_path,
        """
def socket(*args, **kwargs):
    raise PermissionError(1, "Operation not permitted")
""",
        runtime_patch=runtime_patch,
    )

    boundary = _boundary_result(evidence, "child-process-contained")
    assert boundary["observed"] == "probe-failed"
    assert boundary["blocked_before_side_effect"] is False


def test_handles_probe_accepts_bad_file_descriptor_as_filtered(
    tmp_path: Path,
) -> None:
    evidence = _run_probe(
        tmp_path,
        """
def socket(*args, **kwargs):
    raise PermissionError(1, "Operation not permitted")
""",
    )

    boundary = _boundary_result(evidence, "handles-filtered")
    assert boundary["observed"] == "denied"
    assert boundary["os_error"] == "fstat:OSError:9"
    assert boundary["blocked_before_side_effect"] is True


def test_handles_probe_treats_descriptor_identity_mismatch_as_filtered_reuse(
    tmp_path: Path,
) -> None:
    evidence = _run_probe(
        tmp_path,
        """
def socket(*args, **kwargs):
    raise PermissionError(1, "Operation not permitted")
""",
        runtime_patch="""
real_fstat = os.fstat

class WrongIdentity:
    st_dev = 7
    st_ino = 11

def mismatched_fstat(descriptor):
    if descriptor == -1:
        return WrongIdentity()
    return real_fstat(descriptor)

os.fstat = mismatched_fstat
""",
    )

    boundary = _boundary_result(evidence, "handles-filtered")
    assert boundary["observed"] == "denied"
    assert boundary["os_error"] == "fstat:identity-mismatch"
    assert boundary["blocked_before_side_effect"] is True


def test_handles_probe_rejects_launch_contract_drift(
    tmp_path: Path,
) -> None:
    evidence = _run_probe(
        tmp_path,
        """
def socket(*args, **kwargs):
    raise PermissionError(1, "Operation not permitted")
""",
        payload_patch={
            "launch_contract": {
                "schema_version": "ai-sdlc-probe-launch.v1",
                "close_fds": False,
                "pass_fds": [],
                "preexec_fd_remap": False,
                "windows_handle_list": [],
            }
        },
    )

    boundary = _boundary_result(evidence, "handles-filtered")
    assert boundary["observed"] == "probe-failed"
    assert boundary["os_error"] == "launch-contract:invalid"


@pytest.mark.parametrize("error_number", [5, 12])
def test_handles_probe_accepts_only_bad_file_descriptor_as_filtered(
    tmp_path: Path,
    error_number: int,
) -> None:
    evidence = _run_probe(
        tmp_path,
        """
def socket(*args, **kwargs):
    raise PermissionError(1, "Operation not permitted")
""",
        runtime_patch=f"""
real_fstat = os.fstat

def fail_fstat(descriptor):
    if descriptor == -1:
        raise OSError({error_number}, "fstat probe fault")
    return real_fstat(descriptor)

os.fstat = fail_fstat
""",
    )

    boundary = _boundary_result(evidence, "handles-filtered")
    assert boundary["observed"] == "probe-failed"
    assert boundary["os_error"] == f"fstat:OSError:{error_number}"
    assert boundary["blocked_before_side_effect"] is False


@pytest.mark.parametrize("error_number", [24, 12, 97])
def test_probe_does_not_treat_socket_creation_fault_as_policy_denial(
    tmp_path: Path,
    error_number: int,
) -> None:
    evidence = _run_probe(
        tmp_path,
        f"""
def socket(*args, **kwargs):
    raise OSError({error_number}, "probe fault")
""",
    )
    network = _network_result(evidence)

    assert network["observed"] == "probe-failed"
    assert network["os_error"] == f"socket:OSError:{error_number}"
    assert not any(
        item["operation"] == "network-denied" for item in evidence["os_native_denials"]
    )


def test_probe_does_not_treat_settimeout_fault_as_policy_denial(
    tmp_path: Path,
) -> None:
    evidence = _run_probe(
        tmp_path,
        """
class FakeSocket:
    def settimeout(self, value):
        raise OSError(9, "probe fault")

    def close(self):
        pass

def socket(*args, **kwargs):
    return FakeSocket()
""",
    )
    network = _network_result(evidence)

    assert network["observed"] == "probe-failed"
    assert network["os_error"] == "settimeout:OSError:9"


@pytest.mark.parametrize(
    "error_number",
    [1, 13, 51, 61, 65, 101, 111, 113, 10013, 10051, 10061, 10065],
)
def test_probe_accepts_supported_connect_denial_errors(
    tmp_path: Path,
    error_number: int,
) -> None:
    evidence = _run_probe(
        tmp_path,
        f"""
class FakeSocket:
    def settimeout(self, value):
        pass

    def connect(self, address):
        raise OSError({error_number}, "policy denial")

    def close(self):
        pass

def socket(*args, **kwargs):
    return FakeSocket()
""",
    )
    network = _network_result(evidence)

    assert network["observed"] == "denied"
    assert network["os_error"].startswith("connect:")
    assert network["os_error"].endswith(f":{error_number}")


def test_probe_rejects_unknown_connect_error_as_probe_failure(
    tmp_path: Path,
) -> None:
    evidence = _run_probe(
        tmp_path,
        """
class FakeSocket:
    def settimeout(self, value):
        pass

    def connect(self, address):
        raise OSError(24, "probe fault")

    def close(self):
        pass

def socket(*args, **kwargs):
    return FakeSocket()
""",
    )

    assert _network_result(evidence)["observed"] == "probe-failed"


def test_probe_connection_success_overrides_other_policy_denials(
    tmp_path: Path,
) -> None:
    evidence = _run_probe(
        tmp_path,
        """
created = 0

class FakeSocket:
    def settimeout(self, value):
        pass

    def connect(self, address):
        pass

    def close(self):
        pass

def socket(*args, **kwargs):
    global created
    created += 1
    if created == 2:
        raise PermissionError(1, "Operation not permitted")
    return FakeSocket()
""",
        target_count=2,
    )
    network = _network_result(evidence)

    assert network["observed"] == "connect-succeeded"
    assert network["os_error"] == "socket:PermissionError:1"
    assert not any(
        item["operation"] == "network-denied" for item in evidence["os_native_denials"]
    )


@pytest.mark.parametrize(
    ("connect_program", "expected"),
    [
        ("pass", "connect-succeeded"),
        ('raise PermissionError(1, "Operation not permitted")', "probe-failed"),
    ],
)
def test_probe_close_failure_never_creates_policy_denial(
    tmp_path: Path,
    connect_program: str,
    expected: str,
) -> None:
    evidence = _run_probe(
        tmp_path,
        f"""
class FakeSocket:
    def settimeout(self, value):
        pass

    def connect(self, address):
        {connect_program}

    def close(self):
        raise OSError(9, "close fault")

def socket(*args, **kwargs):
    return FakeSocket()
""",
    )
    network = _network_result(evidence)

    assert network["observed"] == expected
    assert "close:OSError:9" in network["os_error"]
    assert not any(
        item["operation"] == "network-denied" for item in evidence["os_native_denials"]
    )
