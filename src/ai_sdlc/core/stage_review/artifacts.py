"""Stage Review 跨 Worktree 状态根、短锁与原子工件原语。"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import time
from contextlib import AbstractContextManager, suppress
from pathlib import Path
from types import TracebackType
from typing import Any

from ai_sdlc.core.stage_review.registry_versions import require_machine_id

_WINDOWS_REPLACE_DELAYS = (0.02, 0.05, 0.1)


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
    repository_root = root.resolve()
    common_git_dir = _git_common_dir(repository_root)
    if common_git_dir is None:
        base = repository_root / ".ai-sdlc" / "state" / "shared"
    else:
        base = common_git_dir / "ai-sdlc-shared-state"
    return base / "projects" / stable_project_id


def resolve_repository_project_id(root: Path) -> str:
    """跨 Worktree 解析同一个稳定项目身份，不依赖目录名。"""

    repository_root = root.resolve()
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
    seed = shared_base.resolve(strict=False).as_posix().encode("utf-8")
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

    def __enter__(self) -> ShortFileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                if create_json_exclusive(
                    self.path,
                    {"pid": os.getpid(), "started_at": time.time()},
                ):
                    break
                if _clear_dead_owner(self.path):
                    continue
                if time.monotonic() >= deadline:
                    raise ResourceLockUnavailableError(
                        f"timed out waiting for shared state lock: {self.path}"
                    ) from None
                time.sleep(self.poll_seconds)
            except OSError as exc:
                raise ResourceLockUnavailableError(
                    f"shared state lock unavailable: {self.path}"
                ) from exc
        self._owned = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._owned:
            with suppress(FileNotFoundError):
                self.path.unlink()
            self._owned = False


def read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SharedStateIntegrityError(f"JSON artifact must be an object: {path}")
    return payload


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    serialized = _serialized_json(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_sibling(path)
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def create_json_exclusive(path: Path, payload: dict[str, Any]) -> bool:
    """通过完整临时文件的原子 Hard Link 实现 create-if-absent。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_sibling(path)
    try:
        descriptor = _open_exclusive(temporary)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(_serialized_json(payload))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
    except Exception:
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _temporary_sibling(path: Path) -> Path:
    """生成固定长度临时名，避免深层 Windows 状态目录越过 MAX_PATH。"""

    identity = f"{path.name}\0{os.getpid()}\0{time.monotonic_ns()}".encode()
    token = hashlib.sha256(identity).hexdigest()[:16]
    return path.with_name(f".{token}.tmp")


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
    resolved = path.resolve()
    if not resolved.exists():
        raise SharedStateIntegrityError("Git common-dir path does not exist")
    return resolved


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
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


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
    """通过只读进程句柄探测 PID，避免 Windows 上的 os.kill 终止语义。"""

    win_dll = ctypes.WinDLL  # type: ignore[attr-defined]
    get_last_error = ctypes.get_last_error  # type: ignore[attr-defined]
    kernel32 = win_dll("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return bool(get_last_error() != 87)


def _serialized_json(payload: dict[str, Any]) -> str:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


def _replace_with_retry(source: Path, destination: Path) -> None:
    try:
        source.replace(destination)
        return
    except PermissionError:
        if os.name != "nt":
            raise
    for delay in _WINDOWS_REPLACE_DELAYS:
        time.sleep(delay)
        try:
            source.replace(destination)
            return
        except PermissionError:
            continue
    source.replace(destination)
