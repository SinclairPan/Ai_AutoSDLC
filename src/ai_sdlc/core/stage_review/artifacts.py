"""Stage Review 跨 Worktree 状态根、短锁与原子工件原语。"""

from __future__ import annotations

import atexit
import ctypes
import errno
import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
from collections.abc import Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from ai_sdlc.core.stage_review.registry_versions import require_machine_id

_FILESYSTEM_RETRY_DELAYS = (0.02, 0.05, 0.1)
_PENDING_TEMPORARY_CLEANUP: set[Path] = set()
_PENDING_TEMPORARY_CLEANUP_LOCK = threading.Lock()


class ResourceLockUnavailableError(RuntimeError):
    """短时跨进程锁不可安全取得。"""


class SharedStateIntegrityError(RuntimeError):
    """共享状态血缘或项目身份无法可信恢复。"""


def portable_content_digest_name(digest: str) -> str:
    """保留逻辑摘要，仅返回可跨平台用于文件名的十六进制载荷。"""

    payload = digest.removeprefix("sha256:")
    if (
        len(digest) != 71
        or not digest.startswith("sha256:")
        or len(payload) != 64
        or any(character not in "0123456789abcdef" for character in payload)
    ):
        raise ValueError("content digest is invalid")
    return payload


def resolve_canonical_shared_state(root: Path, project_id: str) -> Path:
    """把所有 Git Worktree 映射到相同项目级状态根。"""

    stable_project_id = require_machine_id(project_id, "project_id")
    repository_root = _resolve_integrity_path(root, "Repository")
    common_git_dir = _git_common_dir(repository_root)
    if common_git_dir is None:
        base = repository_root / ".ai-sdlc" / "state" / "shared"
    else:
        base = common_git_dir / "ai-sdlc-shared-state"
    return base / "projects" / stable_project_id


def _resolve_trusted_project_state(root: Path, project_id: str) -> Path:
    """解析独立于可重建 canonical state 的项目可信锚根。"""

    stable_project_id = require_machine_id(project_id, "project_id")
    repository_root = _resolve_integrity_path(root, "Repository")
    common_git_dir = _git_common_dir(repository_root)
    if common_git_dir is None:
        base = repository_root / ".ai-sdlc" / "state" / "trusted"
    else:
        base = common_git_dir / "ai-sdlc-trusted-state"
    return base / "projects" / stable_project_id


def resolve_repository_project_id(root: Path) -> str:
    """跨 Worktree 解析同一个稳定项目身份，不依赖目录名。"""

    repository_root = _resolve_integrity_path(root, "Repository")
    common_git_dir = _git_common_dir(repository_root)
    shared_base = (
        common_git_dir / "ai-sdlc-shared-state"
        if common_git_dir is not None
        else repository_root / ".ai-sdlc" / "state" / "shared"
    )
    identity_path = shared_base / "repository-project.json"
    if identity_path.is_file():
        project_id = str(read_json_object(identity_path).get("project_id", ""))
        return require_machine_id(project_id, "project_id")
    seed = (
        _resolve_integrity_path(
            shared_base,
            "Shared state",
        )
        .as_posix()
        .encode("utf-8")
    )
    return f"project.{hashlib.sha256(seed).hexdigest()[:24]}"


def _file_lock_is_active(path: Path) -> bool:
    """清理已死亡持有者后返回文件锁是否仍由活动进程持有。"""

    if not path.is_file():
        return False
    _clear_dead_owner(path)
    return path.is_file()


class ShortFileLock(AbstractContextManager["ShortFileLock"]):
    """只包围本地 CAS 的 create-exclusive 跨进程短锁。"""

    def __init__(
        self,
        path: Path,
        *,
        timeout_seconds: float,
        poll_seconds: float = 0.01,
    ) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self._owned = False
        self._handle: BinaryIO | None = None

    def __enter__(self) -> ShortFileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            handle: BinaryIO | None = None
            try:
                handle = self.path.open("a+b")
                _prepare_lock_file(handle)
                if _try_acquire_os_lock(handle):
                    self._handle = handle
                    self._owned = True
                    return self
            except OSError as exc:
                if handle is not None:
                    handle.close()
                raise ResourceLockUnavailableError(
                    f"shared state lock unavailable: {self.path}"
                ) from exc
            if handle is not None:
                handle.close()
            if time.monotonic() >= deadline:
                raise ResourceLockUnavailableError(
                    f"timed out waiting for shared state lock: {self.path}"
                ) from None
            time.sleep(self.poll_seconds)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        handle = self._handle
        self._handle = None
        self._owned = False
        if handle is None:
            return
        try:
            _release_os_lock(handle)
        finally:
            handle.close()


def read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SharedStateIntegrityError(f"JSON artifact must be an object: {path}")
    return payload


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    serialized = serialized_json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary, descriptor = _create_temporary_file(path)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temporary, path)
    except Exception:
        _unlink_with_retry(temporary, missing_ok=True)
        raise


def create_json_exclusive(path: Path, payload: dict[str, Any]) -> bool:
    """通过完整临时文件的原子 Hard Link 实现 create-if-absent。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary, descriptor = _create_temporary_file(path)
    committed: bool | None = None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialized_json_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            committed = False
        else:
            committed = True
    finally:
        try:
            _unlink_with_retry(temporary, missing_ok=True)
        except OSError:
            if committed is None:
                raise
            _schedule_temporary_cleanup(temporary)
    return bool(committed)


def _temporary_sibling(path: Path) -> Path:
    """生成固定长度临时名，避免深层 Windows 状态目录越过 MAX_PATH。"""

    return path.with_name(f".{secrets.token_hex(8)}.tmp")


def _create_temporary_file(path: Path) -> tuple[Path, int]:
    _retry_pending_temporary_cleanup()
    for _ in range(16):
        temporary = _temporary_sibling(path)
        try:
            return temporary, _open_exclusive(temporary)
        except FileExistsError:
            continue
    raise FileExistsError("could not allocate a unique atomic artifact temporary file")


def cleanup_resolved_temporary(path: Path) -> None:
    """清理结果已确定的临时文件；瞬时拒绝时登记后续重试。"""

    cleanup_resolved_path(path)


def cleanup_resolved_path(path: Path) -> bool:
    """清理已不再需要的文件；瞬时拒绝时登记后续重试。"""

    try:
        _unlink_with_retry(path, missing_ok=True)
    except OSError:
        _schedule_temporary_cleanup(path)
        return False
    return True


def _schedule_temporary_cleanup(path: Path) -> None:
    with _PENDING_TEMPORARY_CLEANUP_LOCK:
        _PENDING_TEMPORARY_CLEANUP.add(path)


def _pending_temporary_cleanup() -> tuple[Path, ...]:
    with _PENDING_TEMPORARY_CLEANUP_LOCK:
        return tuple(sorted(_PENDING_TEMPORARY_CLEANUP))


def _retry_pending_temporary_cleanup() -> None:
    for path in _pending_temporary_cleanup():
        try:
            _unlink_with_retry(path, missing_ok=True)
        except OSError:
            continue
        with _PENDING_TEMPORARY_CLEANUP_LOCK:
            _PENDING_TEMPORARY_CLEANUP.discard(path)


atexit.register(_retry_pending_temporary_cleanup)


def bind_repository_project(shared_root: Path, project_id: str) -> None:
    """同一仓库状态域只允许一个稳定 project_id。"""

    base = shared_root.parent.parent
    identity_path = base / "repository-project.json"
    expected = {"project_id": project_id}
    if create_json_exclusive(identity_path, expected):
        return
    if read_json_object(identity_path) != expected:
        raise SharedStateIntegrityError(
            "canonical shared state is already bound to another project_id"
        )


def _git_common_dir(root: Path) -> Path | None:
    git_marker = _find_git_marker(root)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        if git_marker is None:
            return None
        raise SharedStateIntegrityError(
            "Git metadata exists but Git is unavailable"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SharedStateIntegrityError("Git common-dir resolution timed out") from exc
    if result.returncode != 0:
        if git_marker is not None:
            raise SharedStateIntegrityError(
                "Git metadata exists but common-dir resolution failed"
            )
        return None
    raw_path = result.stdout.strip()
    if not raw_path:
        raise SharedStateIntegrityError("Git returned an empty common-dir path")
    path = Path(raw_path)
    if not path.is_absolute():
        path = root / path
    resolved = _resolve_integrity_path(path, "Git common-dir")
    if not resolved.exists():
        raise SharedStateIntegrityError("Git common-dir path does not exist")
    return resolved


def _resolve_integrity_path(path: Path, label: str) -> Path:
    try:
        absolute = path.absolute()
        for candidate in (*reversed(absolute.parents), absolute):
            if os.path.lexists(candidate):
                candidate.resolve(strict=True)
        return path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise SharedStateIntegrityError(f"{label} path cannot be resolved") from exc


def _find_git_marker(root: Path) -> Path | None:
    for candidate in (root, *root.parents):
        marker = candidate / ".git"
        if marker.exists():
            return marker
    return None


def _open_exclusive(path: Path) -> int:
    return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)


def _clear_dead_owner(path: Path) -> bool:
    try:
        payload = read_json_object(path)
    except (FileNotFoundError, json.JSONDecodeError, SharedStateIntegrityError):
        return False
    pid = int(payload.get("pid", 0) or 0)
    if pid <= 0 or _pid_is_active(pid):
        return False
    return _unlink_with_retry(path, missing_ok=True)


def _pid_is_active(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if os.name == "nt":
        return _windows_pid_is_active(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_is_active(pid: int) -> bool:
    """通过进程对象信号探测 PID，避免退出码 259 的歧义。"""

    win_dll = ctypes.WinDLL  # type: ignore[attr-defined]
    get_last_error = ctypes.get_last_error  # type: ignore[attr-defined]
    kernel32 = win_dll("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint32,
    ]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(0x00100000, False, pid)
    if not handle:
        return bool(get_last_error() != 87)
    try:
        wait_result = int(kernel32.WaitForSingleObject(handle, 0))
        return wait_result != 0
    finally:
        kernel32.CloseHandle(handle)


def serialized_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """返回跨平台一致的 canonical UTF-8 + LF JSON 字节。"""

    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _replace_with_retry(source: Path, destination: Path) -> None:
    try:
        source.replace(destination)
        return
    except PermissionError:
        if os.name != "nt":
            raise
    for delay in _FILESYSTEM_RETRY_DELAYS:
        time.sleep(delay)
        try:
            source.replace(destination)
            return
        except PermissionError:
            continue
    source.replace(destination)


def _prepare_lock_file(handle: BinaryIO) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())
    handle.seek(0)


def _try_acquire_os_lock(handle: BinaryIO) -> bool:
    handle.seek(0)
    if os.name == "nt":
        try:
            msvcrt.locking(  # type: ignore[attr-defined]
                handle.fileno(),
                msvcrt.LK_NBLCK,  # type: ignore[attr-defined]
                1,
            )
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            return False
        raise
    return True


def _release_os_lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt.locking(  # type: ignore[attr-defined]
            handle.fileno(),
            msvcrt.LK_UNLCK,  # type: ignore[attr-defined]
            1,
        )
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _unlink_with_retry(path: Path, *, missing_ok: bool) -> bool:
    for delay in (0.0, *_FILESYSTEM_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            if missing_ok:
                return False
            raise
        except PermissionError:
            continue
    try:
        path.unlink()
    except FileNotFoundError:
        if missing_ok:
            return False
        raise
    return True
