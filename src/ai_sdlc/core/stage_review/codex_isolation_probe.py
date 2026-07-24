"""Codex permission profile 内执行的无依赖恶意边界探针。"""

from __future__ import annotations

PROBE_PROGRAM = r"""
import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

cfg = json.load(sys.stdin)

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

def read_one(target):
    path = Path(target)
    try:
        if path.is_file():
            path.read_bytes()
        else:
            next(item for item in path.rglob("*") if item.is_file()).read_bytes()
        return True, ""
    except (OSError, StopIteration) as exc:
        return False, f"{type(exc).__name__}:{getattr(exc, 'errno', '')}"

def write_one(target):
    path = Path(target)
    try:
        destination = path.with_name(path.name + ".t601-write") if path.is_file() else path / "t601-write.txt"
        destination.write_text("x", encoding="utf-8")
        return True, ""
    except OSError as exc:
        return False, f"{type(exc).__name__}:{exc.errno}"

def denied_write(action, target):
    before = digest(target)
    wrote, error = write_one(target)
    observed = "write-succeeded" if wrote else "denied"
    return result(action, target, "denied", observed, error, before, digest(target))

def denied_read_write(action, targets):
    before = hashlib.sha256(json.dumps([digest(item) for item in targets]).encode()).hexdigest()
    errors = []
    denied = True
    for target in targets:
        read, read_error = read_one(target)
        wrote, write_error = write_one(target)
        denied = denied and not read and not wrote
        errors.extend((read_error, write_error))
    after = hashlib.sha256(json.dumps([digest(item) for item in targets]).encode()).hexdigest()
    return result(action, "|".join(targets), "denied", "denied" if denied else "access-succeeded", ";".join(errors), before, after)

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
    successes = 0
    for family, address in cfg["network_targets"]:
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        try:
            sock.connect(tuple(address))
            successes += 1
        except OSError as exc:
            errors.append(f"{type(exc).__name__}:{exc.errno}")
        finally:
            sock.close()
    observed = "denied" if successes == 0 and len(errors) == len(cfg["network_targets"]) else "connect-succeeded"
    return result("network-denied", "controlled-ipv4-direct-ip-ipv6-direct-ip-localhost", "denied", observed, ";".join(errors), "network", "network")

results = []
candidate = cfg["candidate_root"]
candidate_read, _ = read_one(candidate)
candidate_write = denied_write("candidate-read-only", candidate)
if not candidate_read:
    candidate_write["observed"] = "candidate-read-denied"
    candidate_write["blocked_before_side_effect"] = False
results.append(candidate_write)
results.append(denied_read_write("peer-output-denied", cfg["peer_roots"]))
results.append(denied_read_write("real-home-denied", [cfg["real_home"]]))
results.append(denied_read_write("global-config-denied", cfg["global_configs"]))
results.append(denied_read_write("symlink-boundary-denied", [cfg["boundary_link"]]))
results.append(network_denied())

child_before = digest(candidate)
child = subprocess.run([sys.executable, "-c", "from pathlib import Path; Path(r'%s/t601-child.txt').write_text('x')" % candidate], capture_output=True, text=True)
child_after = digest(candidate)
child_observed = "denied" if child.returncode != 0 and child_before == child_after else "write-succeeded"
results.append(result("child-process-contained", candidate, "denied", child_observed, child.stderr[-240:], child_before, child_after))

proxy_names = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"]
proxy_present = [name for name in proxy_names if os.environ.get(name)]
try:
    sentinel = os.fstat(int(cfg["sentinel_fd"]))
    handle_open = [sentinel.st_dev, sentinel.st_ino] == cfg["sentinel_fd_identity"]
except OSError:
    handle_open = False
observed = "denied" if not proxy_present and not handle_open else "inherited"
results.append(result("handles-filtered", "environment-and-handles", "denied", observed, ",".join(proxy_present), "filtered", "filtered"))
results.append(denied_write("run-root-disposable", cfg["outside_root"]))
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

__all__ = ["PROBE_PROGRAM"]
