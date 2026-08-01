"""将 Windows 可信文件操作绑定到已登记的目录身份。"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.snapshot_windows_handle_acl import (
    _harden_windows_handle_acl,
)
from ai_sdlc.core.stage_review.optimization.snapshot_windows_relative import (
    _open_windows_relative,
)
from ai_sdlc.core.stage_review.optimization.snapshot_windows_security import (
    _close_windows_handle,
    _open_windows_path,
    _windows_handle_metadata,
    _windows_path_identity,
)

_CREATE_NEW = 1
_OPEN_ALWAYS = 4
_OPEN_EXISTING = 3
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_DELETE = 0x00010000
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_NORMAL = 0x80
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_RENAME_INFO_CLASS = 3
_FILE_DISPOSITION_INFO_CLASS = 4
_FILE_SHARE_READ = 0x1
_FILE_SHARE_READ_WRITE = 0x1 | 0x2
_LOCKFILE_FAIL_IMMEDIATELY = 0x1
_LOCKFILE_EXCLUSIVE_LOCK = 0x2
_REGISTRY_LOCK = threading.RLock()
_DIRECTORY_IDENTITIES: dict[
    str,
    tuple[tuple[Path, tuple[int, int]], ...],
] = {}


def _register_windows_directory(
    directory: Path,
    identities: tuple[tuple[Path, tuple[int, int]], ...],
) -> None:
    key = _directory_key(directory)
    with _REGISTRY_LOCK:
        existing = _DIRECTORY_IDENTITIES.get(key)
        if existing is not None and existing != identities:
            raise SharedStateIntegrityError(
                "snapshot trusted directory identity changed"
            )
        _DIRECTORY_IDENTITIES[key] = identities


def _windows_secure_read(directory: Path, name: str) -> bytes:
    _validate_leaf_name(name)
    with _open_registered_directory(directory) as directory_handle:
        handle = _open_windows_relative(
            directory_handle,
            name,
            desired_access=_GENERIC_READ,
            creation_disposition=_OPEN_EXISTING,
            share_mode=_FILE_SHARE_READ,
        )
        try:
            _verify_regular_file(handle)
            _harden_windows_handle_acl(handle)
            _verify_registered_directory(directory)
            return _read_handle(handle)
        finally:
            _close_windows_handle(handle)


def _windows_secure_publish(
    directory: Path,
    name: str,
    temporary: str,
    payload: bytes,
    *,
    replace: bool,
) -> None:
    _validate_leaf_name(name)
    _validate_leaf_name(temporary)
    with _open_registered_directory(directory) as directory_handle:
        handle = _open_windows_relative(
            directory_handle,
            temporary,
            desired_access=_GENERIC_READ | _GENERIC_WRITE | _DELETE,
            creation_disposition=_CREATE_NEW,
        )
        renamed = False
        try:
            _verify_registered_directory(directory)
            _verify_regular_file(handle)
            _harden_windows_handle_acl(handle)
            _verify_registered_directory(directory)
            _write_handle(handle, payload)
            _rename_handle(
                handle,
                directory_handle,
                name,
                replace=replace,
            )
            renamed = True
        except FileExistsError:
            if replace:
                raise
        finally:
            if not renamed:
                _mark_handle_for_deletion(handle)
            _close_windows_handle(handle)


def _windows_secure_unlink(directory: Path, name: str) -> None:
    _validate_leaf_name(name)
    try:
        with _open_registered_directory(directory) as directory_handle:
            handle = _open_windows_relative(
                directory_handle,
                name,
                desired_access=_DELETE,
                creation_disposition=_OPEN_EXISTING,
            )
            try:
                _verify_regular_file(handle)
                _verify_registered_directory(directory)
                _mark_handle_for_deletion(handle)
            finally:
                _close_windows_handle(handle)
    except FileNotFoundError:
        return


@contextmanager
def _windows_secure_lock(
    directory: Path,
    name: str,
    *,
    timeout_seconds: float,
) -> Iterator[None]:
    _validate_leaf_name(name)
    with _open_registered_directory(directory) as directory_handle:
        handle = _open_windows_relative(
            directory_handle,
            name,
            desired_access=_GENERIC_READ | _GENERIC_WRITE,
            creation_disposition=_OPEN_ALWAYS,
            share_mode=_FILE_SHARE_READ_WRITE,
        )
        try:
            _verify_regular_file(handle)
            _harden_windows_handle_acl(handle)
            _verify_registered_directory(directory)
            overlap = _acquire_windows_lock(handle, timeout_seconds)
            try:
                yield
            finally:
                _release_windows_lock(handle, overlap)
        finally:
            _close_windows_handle(handle)


@contextmanager
def _open_registered_directory(directory: Path) -> Iterator[int]:
    expected = _registered_identities(directory)
    _verify_registered_directory(directory)
    handle = _open_windows_path(directory)
    try:
        identity, attributes = _windows_handle_metadata(handle)
        if attributes & (_FILE_ATTRIBUTE_REPARSE_POINT):
            raise SharedStateIntegrityError(
                "snapshot trusted directory identity changed"
            )
        if not attributes & _FILE_ATTRIBUTE_DIRECTORY or identity != expected[-1][1]:
            raise SharedStateIntegrityError(
                "snapshot trusted directory identity changed"
            )
        _verify_registered_directory(directory)
        yield handle
    finally:
        _close_windows_handle(handle)


def _registered_identities(
    directory: Path,
) -> tuple[tuple[Path, tuple[int, int]], ...]:
    with _REGISTRY_LOCK:
        identities = _DIRECTORY_IDENTITIES.get(_directory_key(directory))
    if not identities:
        raise SharedStateIntegrityError("snapshot trusted directory is unbound")
    return identities


def _verify_registered_directory(directory: Path) -> None:
    identities = _registered_identities(directory)
    try:
        for path, expected in identities:
            if _windows_path_identity(path) != expected:
                raise SharedStateIntegrityError(
                    "snapshot trusted directory identity changed"
                )
    except (FileNotFoundError, SharedStateIntegrityError) as exc:
        raise SharedStateIntegrityError(
            "snapshot trusted directory identity changed"
        ) from exc


def _verify_regular_file(handle: int) -> None:
    _, attributes = _windows_handle_metadata(handle)
    if attributes & (_FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT):
        raise SharedStateIntegrityError("snapshot trusted file is unsafe")


def _read_handle(handle: int) -> bytes:
    kernel32 = _kernel32()
    chunks: list[bytes] = []
    while True:
        buffer = ctypes.create_string_buffer(65_536)
        count = wintypes.DWORD()
        if not kernel32.ReadFile(
            wintypes.HANDLE(handle),
            buffer,
            len(buffer),
            ctypes.byref(count),
            None,
        ):
            raise SharedStateIntegrityError("snapshot trusted file read failed")
        if count.value == 0:
            return b"".join(chunks)
        chunks.append(buffer.raw[: count.value])


def _write_handle(handle: int, payload: bytes) -> None:
    kernel32 = _kernel32()
    offset = 0
    while offset < len(payload):
        chunk = payload[offset : offset + 65_536]
        buffer = ctypes.create_string_buffer(chunk)
        count = wintypes.DWORD()
        if not kernel32.WriteFile(
            wintypes.HANDLE(handle),
            buffer,
            len(chunk),
            ctypes.byref(count),
            None,
        ):
            raise SharedStateIntegrityError("snapshot trusted file write failed")
        offset += int(count.value)
    if not kernel32.FlushFileBuffers(wintypes.HANDLE(handle)):
        raise SharedStateIntegrityError("snapshot trusted file flush failed")


def _rename_handle(
    handle: int,
    directory_handle: int,
    name: str,
    *,
    replace: bool,
) -> None:
    class _FileRenameInfo(ctypes.Structure):
        _fields_ = (
            ("replace_if_exists", wintypes.BOOLEAN),
            ("root_directory", wintypes.HANDLE),
            ("file_name_length", wintypes.DWORD),
            ("file_name", wintypes.WCHAR * len(name)),
        )

    information = _FileRenameInfo(
        int(replace),
        wintypes.HANDLE(directory_handle),
        len(name.encode("utf-16-le")),
        name,
    )
    success = _kernel32().SetFileInformationByHandle(
        wintypes.HANDLE(handle),
        _FILE_RENAME_INFO_CLASS,
        ctypes.byref(information),
        ctypes.sizeof(information),
    )
    if success:
        return
    error = _last_error()
    if error in (80, 183):
        raise FileExistsError(name)
    raise SharedStateIntegrityError("snapshot trusted file publish failed")


def _mark_handle_for_deletion(handle: int) -> None:
    class _FileDispositionInfo(ctypes.Structure):
        _fields_ = (("delete_file", wintypes.BOOLEAN),)

    information = _FileDispositionInfo(1)
    if not _kernel32().SetFileInformationByHandle(
        wintypes.HANDLE(handle),
        _FILE_DISPOSITION_INFO_CLASS,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        raise SharedStateIntegrityError("snapshot trusted file delete failed")


class _Overlapped(ctypes.Structure):
    _fields_ = (
        ("internal", ctypes.c_void_p),
        ("internal_high", ctypes.c_void_p),
        ("offset", wintypes.DWORD),
        ("offset_high", wintypes.DWORD),
        ("event", wintypes.HANDLE),
    )


def _acquire_windows_lock(handle: int, timeout_seconds: float) -> _Overlapped:
    deadline = time.monotonic() + timeout_seconds
    while True:
        overlap = _Overlapped()
        if _kernel32().LockFileEx(
            wintypes.HANDLE(handle),
            _LOCKFILE_FAIL_IMMEDIATELY | _LOCKFILE_EXCLUSIVE_LOCK,
            0,
            1,
            0,
            ctypes.byref(overlap),
        ):
            return overlap
        if _last_error() != 33 or time.monotonic() >= deadline:
            raise SharedStateIntegrityError("snapshot trusted lock unavailable")
        time.sleep(0.01)


def _release_windows_lock(handle: int, overlap: _Overlapped) -> None:
    if not _kernel32().UnlockFileEx(
        wintypes.HANDLE(handle),
        0,
        1,
        0,
        ctypes.byref(overlap),
    ):
        raise SharedStateIntegrityError("snapshot trusted lock release failed")


def _kernel32() -> Any:
    if os.name != "nt":
        raise RuntimeError("Windows secure I/O is unavailable")
    pointer_type = ctypes.c_void_p
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None or pointer_type is None:
        raise RuntimeError("Windows API loader is unavailable")
    return loader("kernel32", use_last_error=True)


def _last_error() -> int:
    pointer_type = ctypes.c_void_p
    getter = getattr(ctypes, "get_last_error", None)
    if getter is None or pointer_type is None:
        return 0
    return int(getter())


def _directory_key(directory: Path) -> str:
    return str(directory.absolute()).casefold()


def _validate_leaf_name(name: str) -> None:
    if not name or Path(name).name != name or name in (".", ".."):
        raise SharedStateIntegrityError("snapshot trusted file name is unsafe")
