"""Activation epoch 的跨进程读写租约。"""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypedDict

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    ShortFileLock,
    _clear_dead_owner,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id

_LOCAL = threading.local()
_THREAD_TOKENS: dict[int, str] = {}
_THREAD_TOKENS_LOCK = threading.Lock()
_PROCESS_START_CACHE: dict[int, str] = {}


class _LocalFenceState(TypedDict):
    readers: set[Path]
    mutation_depth: int


@contextmanager
def activation_safety_read_lease(
    root: Path,
    project_id: str,
) -> Iterator[None]:
    """允许同一激活 epoch 的产品 writer 并发，并阻止策略跨 epoch。"""

    fence_root = _fence_root(root, project_id)
    state = _local_fence_state(fence_root)
    if state["mutation_depth"]:
        yield
        return
    registry_lock = fence_root / "registry.lock"
    writer_intent = fence_root / "writer-intent.lock"
    lease_id = stable_id(
        "activation-safety-reader",
        str(os.getpid()),
        str(threading.get_ident()),
        str(time.monotonic_ns()),
    )
    lease_path = fence_root / "readers" / f"{lease_id}.json"
    deadline = time.monotonic() + 60
    while True:
        acquired = False
        with ShortFileLock(registry_lock, timeout_seconds=5):
            if writer_intent.is_file():
                _clear_stale_owner(writer_intent)
            if not writer_intent.is_file():
                acquired = create_json_exclusive(
                    lease_path,
                    _owner_payload(),
                )
        if acquired:
            break
        if time.monotonic() >= deadline:
            raise ResourceLockUnavailableError(
                "timed out waiting for activation safety read lease"
            )
        time.sleep(0.01)
    state["readers"].add(lease_path)
    try:
        yield
    finally:
        state["readers"].discard(lease_path)
        lease_path.unlink(missing_ok=True)


@contextmanager
def activation_safety_mutation_fence(
    root: Path,
    project_id: str,
) -> Iterator[None]:
    """Finding、Attribution 与 Policy promotion 的跨进程独占写租约。"""

    fence_root = _fence_root(root, project_id)
    state = _local_fence_state(fence_root)
    if state["mutation_depth"]:
        state["mutation_depth"] += 1
        try:
            yield
        finally:
            state["mutation_depth"] -= 1
        return
    if state["readers"]:
        raise ResourceLockUnavailableError(
            "activation safety mutation cannot upgrade an active read lease"
        )
    registry_lock = fence_root / "registry.lock"
    writer_intent = fence_root / "writer-intent.lock"
    deadline = time.monotonic() + 300
    intent_owned = False
    while not intent_owned:
        with ShortFileLock(registry_lock, timeout_seconds=5):
            if writer_intent.is_file():
                _clear_stale_owner(writer_intent)
            if not writer_intent.is_file():
                intent_owned = create_json_exclusive(
                    writer_intent,
                    _owner_payload(),
                )
        if intent_owned:
            break
        if time.monotonic() >= deadline:
            raise ResourceLockUnavailableError(
                "timed out waiting for activation safety mutation lease"
            )
        time.sleep(0.01)
    try:
        while True:
            readers = tuple(sorted((fence_root / "readers").glob("*.json")))
            active = []
            for path in readers:
                if path in state["readers"]:
                    continue
                if not _clear_stale_owner(path) and path.is_file():
                    active.append(path)
            if not active:
                break
            if time.monotonic() >= deadline:
                raise ResourceLockUnavailableError(
                    "timed out draining activation safety readers"
                )
            time.sleep(0.01)
        state["mutation_depth"] = 1
        yield
    finally:
        state["mutation_depth"] = 0
        if intent_owned:
            writer_intent.unlink(missing_ok=True)


def _fence_root(root: Path, project_id: str) -> Path:
    shared = resolve_canonical_shared_state(root, project_id)
    return shared / "activation-safety-fence"


def _local_fence_state(fence_root: Path) -> _LocalFenceState:
    states = getattr(_LOCAL, "fence_states", None)
    if states is None:
        states: dict[str, _LocalFenceState] = {}
        _LOCAL.fence_states = states
    key = str(fence_root.resolve())
    if key not in states:
        states[key] = {"readers": set(), "mutation_depth": 0}
    _current_thread_token()
    return states[key]


def _owner_payload() -> dict[str, object]:
    payload: dict[str, object] = {
        "pid": os.getpid(),
        "thread_id": threading.get_ident(),
        "thread_token": _current_thread_token(),
        "started_at": time.time(),
    }
    process_start = _current_process_start_identity()
    if process_start:
        payload["process_start"] = process_start
    return payload


def _current_thread_token() -> str:
    token = getattr(_LOCAL, "thread_token", "")
    if not token:
        token = stable_id(
            "activation-safety-thread",
            str(os.getpid()),
            str(threading.get_ident()),
            str(time.monotonic_ns()),
        )
        _LOCAL.thread_token = token
        with _THREAD_TOKENS_LOCK:
            _THREAD_TOKENS[threading.get_ident()] = token
    return token


def _clear_stale_owner(path: Path) -> bool:
    if _clear_dead_owner(path):
        return True
    try:
        payload = read_json_object(path)
    except (FileNotFoundError, ValueError):
        return False
    pid = int(payload.get("pid", 0) or 0)
    stale = False
    if pid == os.getpid():
        thread_id = int(payload.get("thread_id", 0) or 0)
        live_threads = {
            thread.ident
            for thread in threading.enumerate()
            if thread.ident is not None and thread.is_alive()
        }
        with _THREAD_TOKENS_LOCK:
            live_token = _THREAD_TOKENS.get(thread_id, "")
        stale = (
            thread_id not in live_threads
            or bool(payload.get("thread_token"))
            and payload.get("thread_token") != live_token
        )
    elif pid > 0 and payload.get("process_start"):
        current_start = _process_start_identity(pid)
        stale = (
            current_start is not None
            and current_start != payload.get("process_start")
        )
    if not stale:
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def _process_start_identity(pid: int) -> str | None:
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.is_file():
        try:
            fields = proc_stat.read_text(encoding="utf-8").rsplit(")", 1)[1].split()
            return f"proc:{fields[19]}"
        except (IndexError, OSError):
            return None
    if os.name == "nt":
        return _windows_process_start_identity(pid)
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            check=False,
            env={
                **os.environ,
                "LANG": "C",
                "LC_ALL": "C",
                "TZ": "UTC",
            },
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    started = result.stdout.strip()
    return f"ps:{started}" if result.returncode == 0 and started else None


def _current_process_start_identity() -> str | None:
    pid = os.getpid()
    if pid not in _PROCESS_START_CACHE:
        started = _process_start_identity(pid)
        if started:
            _PROCESS_START_CACHE[pid] = started
    return _PROCESS_START_CACHE.get(pid)


def _windows_process_start_identity(pid: int) -> str | None:
    from ctypes import wintypes

    class _FileTime(ctypes.Structure):
        _fields_ = [
            ("low", wintypes.DWORD),
            ("high", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    get_process_times = kernel32.GetProcessTimes
    get_process_times.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
    ]
    get_process_times.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handle = open_process(0x1000, False, pid)
    if not handle:
        return None
    creation = _FileTime()
    exit_time = _FileTime()
    kernel_time = _FileTime()
    user_time = _FileTime()
    try:
        if not get_process_times(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        return f"win:{creation.high << 32 | creation.low}"
    finally:
        close_handle(handle)


__all__ = [
    "activation_safety_mutation_fence",
    "activation_safety_read_lease",
]
