"""Activation epoch 的跨进程读写租约。"""

from __future__ import annotations

import ctypes
import os
import re
import secrets
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypedDict, cast

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    ShortFileLock,
    _clear_dead_owner,
    _unlink_with_retry,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id

_LOCAL = threading.local()
_THREAD_TOKENS: dict[int, str] = {}
_THREAD_TOKENS_LOCK = threading.Lock()
_ACTIVE_LEASE_TOKENS: set[str] = set()
_ACTIVE_LEASE_TOKENS_LOCK = threading.Lock()
_PROCESS_START_CACHE: dict[int, str] = {}


class _LocalFenceState(TypedDict):
    readers: set[Path]
    mutation_depth: int
    final_policy_digests: list[str]


def _activation_safety_read_lease_active(root: Path, project_id: str) -> bool:
    """返回当前线程是否已经持有同一项目的激活安全读租约。"""

    return bool(_local_fence_state(_fence_root(root, project_id))["readers"])


def _activation_safety_final_read_lease_active(
    root: Path,
    project_id: str,
    policy_digest: str,
) -> bool:
    state = _local_fence_state(_fence_root(root, project_id))
    return bool(state["readers"]) and policy_digest in state["final_policy_digests"]


@contextmanager
def _activation_safety_final_read_lease(
    root: Path,
    project_id: str,
    *,
    policy_digest: str,
) -> Iterator[None]:
    """标记已完成 evidence refresh 且绑定策略版本的最终提交租约。"""

    with activation_safety_read_lease(root, project_id):
        state = _local_fence_state(_fence_root(root, project_id))
        state["final_policy_digests"].append(policy_digest)
        try:
            yield
        finally:
            state["final_policy_digests"].remove(policy_digest)


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
    if state["readers"]:
        yield
        return
    registry_lock = fence_root / "registry.lock"
    writer_intent = fence_root / "writer-intent.lock"
    lease_token = secrets.token_hex(32)
    lease_id = stable_id(
        "activation-safety-reader",
        str(os.getpid()),
        str(threading.get_ident()),
        lease_token,
    )
    lease_path = fence_root / "readers" / f"{lease_id}.json"
    owner_lock_path = _lease_owner_lock_path(fence_root, lease_token)
    owner_lock = ShortFileLock(owner_lock_path, timeout_seconds=5)
    _clear_orphan_owner_locks(fence_root)
    owner_lock.__enter__()
    _set_lease_token_active(lease_token, active=True)
    acquired = False
    primary_error: BaseException | None = None
    deadline = time.monotonic() + 60
    try:
        while True:
            with ShortFileLock(registry_lock, timeout_seconds=5):
                if writer_intent.is_file():
                    _clear_stale_owner(writer_intent)
                if not writer_intent.is_file():
                    _clear_completed_reader_markers(fence_root)
                    acquired = create_json_exclusive(
                        lease_path,
                        _owner_payload(lease_token),
                    )
            if acquired:
                break
            if time.monotonic() >= deadline:
                raise ResourceLockUnavailableError(
                    "timed out waiting for activation safety read lease"
                )
            time.sleep(0.01)
        state["readers"].add(lease_path)
        yield
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if acquired:
            state["readers"].discard(lease_path)
        _set_lease_token_active(lease_token, active=False)
        try:
            owner_lock.__exit__(None, None, None)
        except Exception as cleanup_error:
            _note_deferred_marker_cleanup(primary_error, cleanup_error)
        if acquired:
            try:
                _unlink_owned_marker(lease_path, lease_token)
            except Exception as cleanup_error:
                _note_deferred_marker_cleanup(primary_error, cleanup_error)
        _cleanup_owner_lock_file(owner_lock_path, primary_error)


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
    lease_token = secrets.token_hex(32)
    owner_lock_path = _lease_owner_lock_path(fence_root, lease_token)
    owner_lock = ShortFileLock(owner_lock_path, timeout_seconds=5)
    _clear_orphan_owner_locks(fence_root)
    owner_lock.__enter__()
    _set_lease_token_active(lease_token, active=True)
    primary_error: BaseException | None = None
    try:
        while not intent_owned:
            with ShortFileLock(registry_lock, timeout_seconds=5):
                if writer_intent.is_file():
                    _clear_stale_owner(writer_intent)
                if not writer_intent.is_file():
                    intent_owned = create_json_exclusive(
                        writer_intent,
                        _owner_payload(lease_token),
                    )
            if intent_owned:
                break
            if time.monotonic() >= deadline:
                raise ResourceLockUnavailableError(
                    "timed out waiting for activation safety mutation lease"
                )
            time.sleep(0.01)
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
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        state["mutation_depth"] = 0
        _set_lease_token_active(lease_token, active=False)
        try:
            owner_lock.__exit__(None, None, None)
        except Exception as cleanup_error:
            _note_deferred_marker_cleanup(primary_error, cleanup_error)
        if intent_owned:
            try:
                with ShortFileLock(registry_lock, timeout_seconds=5):
                    _unlink_owned_marker(writer_intent, lease_token)
            except Exception as cleanup_error:
                _note_deferred_marker_cleanup(primary_error, cleanup_error)
        _cleanup_owner_lock_file(owner_lock_path, primary_error)


def _fence_root(root: Path, project_id: str) -> Path:
    shared = resolve_canonical_shared_state(root, project_id)
    return shared / "activation-safety-fence"


def _local_fence_state(fence_root: Path) -> _LocalFenceState:
    local_states = getattr(_LOCAL, "fence_states", None)
    if local_states is None:
        states: dict[str, _LocalFenceState] = {}
        _LOCAL.fence_states = states
    else:
        states = cast(dict[str, _LocalFenceState], local_states)
    key = str(fence_root.resolve())
    if key not in states:
        states[key] = {
            "readers": set(),
            "mutation_depth": 0,
            "final_policy_digests": [],
        }
    _current_thread_token()
    return states[key]


def _owner_payload(lease_token: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "pid": os.getpid(),
        "thread_id": threading.get_ident(),
        "thread_token": _current_thread_token(),
        "lease_token": lease_token,
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


def _set_lease_token_active(lease_token: str, *, active: bool) -> None:
    with _ACTIVE_LEASE_TOKENS_LOCK:
        if active:
            _ACTIVE_LEASE_TOKENS.add(lease_token)
        else:
            _ACTIVE_LEASE_TOKENS.discard(lease_token)


def _lease_owner_lock_path(fence_root: Path, lease_token: str) -> Path:
    if re.fullmatch(r"[0-9a-f]{64}", lease_token) is None:
        raise ValueError("activation safety lease token is invalid")
    return fence_root / "owner-locks" / f"{lease_token}.lock"


def _marker_fence_root(path: Path) -> Path:
    if path.parent.name == "readers":
        return path.parent.parent
    if path.name == "writer-intent.lock":
        return path.parent
    raise ValueError("activation safety marker path is invalid")


def _clear_completed_reader_markers(fence_root: Path) -> None:
    for marker in (fence_root / "readers").glob("*.json"):
        _clear_stale_owner(marker)


def _clear_orphan_owner_locks(fence_root: Path) -> None:
    """清理已无持有者的 lock 文件；失败不得阻塞已完成租约的 marker 回收。"""
    for index, owner_lock_path in enumerate(
        (fence_root / "owner-locks").glob("*.lock"),
        start=1,
    ):
        if index > 8:
            break
        probe = ShortFileLock(owner_lock_path, timeout_seconds=0)
        try:
            probe.__enter__()
        except ResourceLockUnavailableError:
            continue
        else:
            probe.__exit__(None, None, None)
        _cleanup_owner_lock_file(owner_lock_path, None)


def _lease_owner_lock_is_active(path: Path, lease_token: str) -> bool:
    try:
        owner_lock_path = _lease_owner_lock_path(
            _marker_fence_root(path),
            lease_token,
        )
    except ValueError:
        return True
    probe = ShortFileLock(owner_lock_path, timeout_seconds=0)
    try:
        probe.__enter__()
    except ResourceLockUnavailableError:
        return True
    else:
        probe.__exit__(None, None, None)
        return False


def _cleanup_owner_lock_file(
    path: Path,
    primary_error: BaseException | None,
) -> bool:
    try:
        removed = _unlink_with_retry(path, missing_ok=True)
    except Exception as cleanup_error:
        _note_deferred_marker_cleanup(primary_error, cleanup_error)
        return not path.exists()
    if not removed and primary_error is not None:
        primary_error.add_note(
            "activation safety owner lock cleanup was deferred; "
            "the identity marker was retained"
        )
    return removed


def _unlink_owned_marker(path: Path, lease_token: str) -> bool:
    try:
        payload = read_json_object(path)
    except FileNotFoundError:
        return False
    if payload.get("lease_token") != lease_token:
        return False
    return _unlink_with_retry(path, missing_ok=True)


def _note_deferred_marker_cleanup(
    primary_error: BaseException | None,
    cleanup_error: Exception,
) -> None:
    if primary_error is not None:
        primary_error.add_note(
            "activation safety marker cleanup was deferred: "
            f"{type(cleanup_error).__name__}: {cleanup_error}"
        )


def _clear_stale_owner(path: Path) -> bool:
    try:
        payload = read_json_object(path)
    except (OSError, ValueError):
        return False
    pid = int(payload.get("pid", 0) or 0)
    lease_token = str(payload.get("lease_token", ""))
    if re.fullmatch(r"[0-9a-f]{64}", lease_token):
        if pid == os.getpid():
            # 本进程 token 是 live 权威；临界区结束后的 Windows unlock 延迟
            # 不能把旧 marker 再伪装成活动 writer/reader。
            with _ACTIVE_LEASE_TOKENS_LOCK:
                locally_active = lease_token in _ACTIVE_LEASE_TOKENS
            if locally_active:
                return False
        else:
            if _lease_owner_lock_is_active(path, lease_token):
                return False
        try:
            removed = _unlink_owned_marker(path, lease_token)
        except OSError:
            return False
        _cleanup_owner_lock_file(
            _lease_owner_lock_path(_marker_fence_root(path), lease_token),
            None,
        )
        return removed
    if _clear_dead_owner(path):
        return True
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
    return _unlink_with_retry(path, missing_ok=True)


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
    "_activation_safety_final_read_lease",
    "_activation_safety_final_read_lease_active",
    "_activation_safety_read_lease_active",
    "activation_safety_mutation_fence",
    "activation_safety_read_lease",
]
