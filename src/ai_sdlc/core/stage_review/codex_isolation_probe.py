"""Codex permission profile 内执行的无依赖恶意边界探针。"""

from __future__ import annotations

DIRECTORY_SENTINEL_NAME = ".ai-sdlc-boundary-sentinel"

PROBE_PROGRAM = r"""
import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

cfg = json.load(sys.stdin)
FS_POLICY_DENIAL_ERRNOS = {1, 13, 30}
SOCKET_POLICY_DENIAL_ERRNOS = {1, 13, 10013}
CONNECT_POLICY_DENIAL_ERRNOS = {
    1, 13,
    51, 61, 65,
    101, 111, 113,
    10013, 10051, 10061, 10065,
}
CHILD_PROTOCOL = "ai-sdlc-child-boundary.v1"
TARGET_PROTOCOL = "ai-sdlc-probe-target.v1"
TARGET_KEYS = {
    "schema_version", "path", "role", "nonce", "visibility",
    "exists", "kind", "lexical_identity", "resolved_identity",
    "descriptor_digest",
}
EXPECTED_LAUNCH_CONTRACT = {
    "schema_version": "ai-sdlc-probe-launch.v1",
    "close_fds": True,
    "pass_fds": [],
    "preexec_fd_remap": False,
    "windows_handle_list": [],
}
EXPECTED_LAUNCH_CONTRACT["contract_digest"] = hashlib.sha256(
    json.dumps(
        EXPECTED_LAUNCH_CONTRACT,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()
CHILD_PROBE_PROGRAM = r'''
import json
import os
import sys

target = sys.argv[1]
destination = os.path.join(target, "t601-child.txt")
descriptor = None
error_number = None
error = ""
phase = "open"
try:
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    phase = "write"
    os.write(descriptor, b"x")
    phase = "close"
    os.close(descriptor)
    descriptor = None
    state = "allowed"
except OSError as exc:
    error_number = exc.errno
    state = (
        "denied"
        if isinstance(exc, PermissionError) or exc.errno in {1, 13, 30}
        else "probe-failed"
    )
    error = f"{phase}:{type(exc).__name__}:{exc.errno}"
    if descriptor is not None:
        try:
            os.close(descriptor)
        except OSError:
            pass
print(json.dumps({
    "protocol": "ai-sdlc-child-boundary.v1",
    "target": target,
    "state": state,
    "errno": error_number,
    "error": error,
}, sort_keys=True))
'''

def digest(value):
    target = Path(value)
    try:
        if not target.exists():
            return "missing"
        if target.is_file():
            return hashlib.sha256(target.read_bytes()).hexdigest()
        rows = []
        for item in sorted(target.rglob("*")):
            if item.is_file():
                rows.append((str(item.relative_to(target)), hashlib.sha256(item.read_bytes()).hexdigest()))
        return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    except OSError as exc:
        return "denied:" + str(exc.errno)

def result(action, target, expected, observed, error, before, after):
    return {
        "action": action,
        "target_kind": target,
        "expected": expected,
        "observed": observed,
        "os_error": error,
        "blocked_before_side_effect": expected == "denied" and observed == "denied" and before == after,
        "before_digest": "sha256:" + before,
        "after_digest": "sha256:" + after,
    }

def descriptor_digest(target):
    canonical = {
        key: value
        for key, value in target.items()
        if key != "descriptor_digest"
    }
    return hashlib.sha256(
        json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

def valid_identity(value):
    return (
        isinstance(value, list)
        and len(value) == 3
        and type(value[0]) is int
        and type(value[1]) is int
        and value[2] in {"file", "directory", "symlink", "unknown"}
    )

def valid_target(target, role, visibility="visible"):
    if not isinstance(target, dict) or set(target) != TARGET_KEYS:
        return False
    if (
        target["schema_version"] != TARGET_PROTOCOL
        or target["role"] != role
        or target["visibility"] != visibility
        or not isinstance(target["path"], str)
        or not Path(target["path"]).is_absolute()
        or not isinstance(target["nonce"], str)
        or len(target["nonce"]) != 32
        or target["nonce"] != cfg.get("probe_nonce")
        or any(character not in "0123456789abcdef" for character in target["nonce"])
        or type(target["exists"]) is not bool
        or target["kind"] not in {"file", "directory", "unknown"}
        or not isinstance(target["descriptor_digest"], str)
        or target["descriptor_digest"] != descriptor_digest(target)
    ):
        return False
    if target["exists"]:
        return (
            valid_identity(target["lexical_identity"])
            and valid_identity(target["resolved_identity"])
        )
    return (
        target["kind"] == "unknown"
        and target["lexical_identity"] is None
        and target["resolved_identity"] is None
    )

def fs_error_state(exc, *, action, target, phase):
    if isinstance(exc, PermissionError) or exc.errno in FS_POLICY_DENIAL_ERRNOS:
        return "denied"
    if (
        exc.errno == 2
        and action == "run-root-disposable"
        and phase == "open"
        and target["role"] == "outside"
        and target["visibility"] == "hidden"
    ):
        return "denied"
    return "probe-failed"

def read_one(target, role):
    if (
        not valid_target(target, role)
        or not target["exists"]
        or target["kind"] != "file"
    ):
        return "probe-failed", "target:invalid-or-missing"
    path = Path(target["path"])
    try:
        path.read_bytes()
        return "allowed", ""
    except OSError as exc:
        return (
            fs_error_state(
                exc,
                action="read-protected",
                target=target,
                phase="read",
            ),
            f"read:{type(exc).__name__}:{exc.errno}",
        )

def write_one(target, role, action, visibility="visible"):
    if (
        not valid_target(target, role, visibility)
        or not target["exists"]
        or target["kind"] not in {"file", "directory"}
    ):
        return "probe-failed", "target:invalid-or-missing"
    path = Path(target["path"])
    is_file = target["kind"] == "file"
    destination = path if is_file else path / "t601-write.txt"
    flags = os.O_WRONLY if is_file else os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(destination, flags, 0o600)
    except OSError as exc:
        return (
            fs_error_state(
                exc,
                action=action,
                target=target,
                phase="open",
            ),
            f"open:{type(exc).__name__}:{exc.errno}",
        )
    try:
        os.close(descriptor)
    except OSError as exc:
        return "allowed", f"close:{type(exc).__name__}:{exc.errno}"
    return "allowed", ""

def denied_write(action, target, role, visibility="visible"):
    path = target["path"]
    before = digest(path)
    state, error = write_one(target, role, action, visibility)
    observed = {
        "allowed": "write-succeeded",
        "denied": "denied",
        "probe-failed": "probe-failed",
    }[state]
    return result(action, path, "denied", observed, error, before, digest(path))

def denied_read_write(action, targets, read_targets, target_roles, read_roles):
    paths = [item["path"] for item in targets]
    if (
        not targets
        or len(targets) != len(read_targets)
        or len(targets) != len(target_roles)
        or len(targets) != len(read_roles)
    ):
        return result(action, "|".join(paths), "denied", "probe-failed", "target-set:invalid", "invalid", "invalid")
    before = hashlib.sha256(json.dumps([digest(item) for item in paths]).encode()).hexdigest()
    errors = []
    states = []
    for target, read_target, target_role, read_role in zip(
        targets,
        read_targets,
        target_roles,
        read_roles,
    ):
        read_state, read_error = read_one(read_target, read_role)
        write_state, write_error = write_one(
            target,
            target_role,
            action,
        )
        states.extend((read_state, write_state))
        errors.extend((read_error, write_error))
    after = hashlib.sha256(json.dumps([digest(item) for item in paths]).encode()).hexdigest()
    if "allowed" in states:
        observed = "access-succeeded"
    elif "probe-failed" in states:
        observed = "probe-failed"
    else:
        observed = "denied"
    return result(action, "|".join(paths), "denied", observed, ";".join(errors), before, after)

def allowed_write(target):
    before = digest(target)
    path = Path(target) / "t601-output-allowed.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("allowed", encoding="utf-8")
        observed, error = "allowed", ""
    except OSError as exc:
        observed, error = "denied", f"{type(exc).__name__}:{exc.errno}"
    return result("output-write-allowed", target, "allowed", observed, error, before, digest(target))

def network_denied():
    errors = []
    denials = 0
    probe_errors = 0
    successes = 0
    for family, address in cfg["network_targets"]:
        sock = None
        phase = "socket"
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            phase = "settimeout"
            sock.settimeout(0.5)
            phase = "connect"
            sock.connect(tuple(address))
            successes += 1
        except OSError as exc:
            errors.append(f"{phase}:{type(exc).__name__}:{exc.errno}")
            allowed_errnos = (
                SOCKET_POLICY_DENIAL_ERRNOS
                if phase == "socket"
                else CONNECT_POLICY_DENIAL_ERRNOS
                if phase == "connect"
                else set()
            )
            if exc.errno in allowed_errnos:
                denials += 1
            else:
                probe_errors += 1
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError as exc:
                    errors.append(f"close:{type(exc).__name__}:{exc.errno}")
                    probe_errors += 1
    target_count = len(cfg["network_targets"])
    if successes:
        observed = "connect-succeeded"
    elif probe_errors or target_count == 0 or denials != target_count:
        observed = "probe-failed"
    else:
        observed = "denied"
    return result("network-denied", "controlled-ipv4-direct-ip-ipv6-direct-ip-localhost", "denied", observed, ";".join(errors), "network", "network")

def child_containment(target):
    before = digest(target)
    try:
        child = subprocess.run(
            [sys.executable, "-c", CHILD_PROBE_PROGRAM, target],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return result(
            "child-process-contained",
            target,
            "denied",
            "probe-failed",
            f"launch:{type(exc).__name__}:{getattr(exc, 'errno', None)}",
            before,
            digest(target),
        )
    after = digest(target)
    error = f"returncode:{child.returncode}:{child.stderr[-240:]}"
    observed = "probe-failed"
    if child.returncode == 0:
        try:
            payload = json.loads(child.stdout.strip().splitlines()[-1])
        except (IndexError, TypeError, ValueError, json.JSONDecodeError):
            payload = None
            error = "protocol:invalid-json"
        required = {"protocol", "target", "state", "errno", "error"}
        if (
            isinstance(payload, dict)
            and set(payload) == required
            and payload["protocol"] == CHILD_PROTOCOL
            and payload["target"] == target
            and isinstance(payload["state"], str)
            and payload["state"] in {"allowed", "denied", "probe-failed"}
            and (
                payload["errno"] is None
                or type(payload["errno"]) is int
            )
            and isinstance(payload["error"], str)
        ):
            state = payload["state"]
            error_number = payload["errno"]
            error = payload["error"]
            if (
                state == "allowed"
                and error_number is None
                and before != after
            ):
                observed = "write-succeeded"
            elif (
                state == "denied"
                and error_number in FS_POLICY_DENIAL_ERRNOS
                and before == after
            ):
                observed = "denied"
            elif (
                state == "probe-failed"
                and error_number not in FS_POLICY_DENIAL_ERRNOS
            ):
                observed = "probe-failed"
            else:
                error = f"protocol:inconsistent:{state}:{error_number}"
        elif payload is not None:
            error = "protocol:invalid-schema-or-target"
    return result(
        "child-process-contained",
        target,
        "denied",
        observed,
        error,
        before,
        after,
    )

results = []
candidate = cfg["candidate_target"]
candidate_read, candidate_read_error = read_one(
    cfg["candidate_read_target"],
    "candidate-read",
)
candidate_write = denied_write(
    "candidate-read-only",
    candidate,
    "candidate",
)
if candidate_read != "allowed":
    candidate_write["observed"] = (
        "candidate-read-denied"
        if candidate_read == "denied"
        else "candidate-read-probe-failed"
    )
    candidate_write["os_error"] = ";".join(
        item
        for item in (candidate_read_error, candidate_write["os_error"])
        if item
    )
    candidate_write["blocked_before_side_effect"] = False
results.append(candidate_write)
results.append(denied_read_write(
    "peer-output-denied",
    cfg["peer_targets"],
    cfg["peer_read_targets"],
    [f"peer:{index}" for index in range(len(cfg["peer_targets"]))],
    [f"peer-read:{index}" for index in range(len(cfg["peer_read_targets"]))],
))
results.append(denied_read_write(
    "real-home-denied",
    [cfg["real_home_target"]],
    [cfg["real_home_read_target"]],
    ["real-home"],
    ["real-home-read"],
))
global_config_result = denied_read_write(
    "global-config-denied",
    cfg["global_config_targets"],
    cfg["global_config_targets"],
    [f"global-config:{index}" for index in range(len(cfg["global_config_targets"]))],
    [f"global-config:{index}" for index in range(len(cfg["global_config_targets"]))],
)
runtime_read, runtime_read_error = read_one(
    cfg["runtime_read_target"],
    "runtime-read",
)
if runtime_read != "allowed":
    global_config_result["observed"] = "runtime-read-probe-failed"
    global_config_result["os_error"] = ";".join(
        item
        for item in (runtime_read_error, global_config_result["os_error"])
        if item
    )
    global_config_result["blocked_before_side_effect"] = False
results.append(global_config_result)
results.append(denied_read_write(
    "symlink-boundary-denied",
    [cfg["boundary_link_target"]],
    [cfg["boundary_link_read_target"]],
    ["boundary-link"],
    ["boundary-link-read"],
))
results.append(network_denied())

candidate_path = candidate["path"]
results.append(child_containment(candidate_path))

proxy_names = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"]
proxy_present = [name for name in proxy_names if os.environ.get(name)]
if cfg.get("launch_contract") != EXPECTED_LAUNCH_CONTRACT:
    handle_state = "probe-failed"
    handle_error = "launch-contract:invalid"
else:
    try:
        sentinel = os.fstat(int(cfg["sentinel_fd"]))
        handle_state = (
            "inherited"
            if [sentinel.st_dev, sentinel.st_ino] == cfg["sentinel_fd_identity"]
            else "denied"
        )
        handle_error = (
            ""
            if handle_state == "inherited"
            else "fstat:identity-mismatch"
        )
    except OSError as exc:
        handle_state = "denied" if exc.errno == 9 else "probe-failed"
        handle_error = f"fstat:{type(exc).__name__}:{exc.errno}"
if proxy_present or handle_state == "inherited":
    observed = "inherited"
elif handle_state == "probe-failed":
    observed = "probe-failed"
else:
    observed = "denied"
handle_errors = [*proxy_present, *([handle_error] if handle_error else [])]
results.append(result("handles-filtered", "environment-and-handles", "denied", observed, ",".join(handle_errors), "filtered", "filtered"))
results.append(denied_write(
    "run-root-disposable",
    cfg["outside_target"],
    "outside",
    "hidden",
))
results.append(allowed_write(cfg["output_root"]))

denials = []
for item in results:
    if item["observed"] == "denied" and item["os_error"]:
        denials.append({
            "mechanism": cfg["platform_mechanism"],
            "operation": item["action"],
            "target": item["target_kind"],
            "os_error": item["os_error"].strip(),
            "observed_at": cfg["observed_at"],
        })
print(json.dumps({"boundary_results": results, "os_native_denials": denials}, sort_keys=True))
"""

__all__ = ["DIRECTORY_SENTINEL_NAME", "PROBE_PROGRAM"]
