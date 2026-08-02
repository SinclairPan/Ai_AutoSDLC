"""Snapshot 可信状态使用的 Windows 目录身份与 ACL 原语。"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_SHARE_ALL = 0x1 | 0x2 | 0x4
_OPEN_EXISTING = 3


class _FileTime(ctypes.Structure):
    _fields_ = (("low", wintypes.DWORD), ("high", wintypes.DWORD))


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = (
        ("attributes", wintypes.DWORD),
        ("creation_time", _FileTime),
        ("access_time", _FileTime),
        ("write_time", _FileTime),
        ("volume_serial_number", wintypes.DWORD),
        ("file_size_high", wintypes.DWORD),
        ("file_size_low", wintypes.DWORD),
        ("number_of_links", wintypes.DWORD),
        ("file_index_high", wintypes.DWORD),
        ("file_index_low", wintypes.DWORD),
    )


def _windows_path_identity(path: Path) -> tuple[int, int]:
    handle = _open_windows_path(path)
    try:
        identity, attributes = _windows_handle_metadata(handle)
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise SharedStateIntegrityError("snapshot trusted root is unsafe")
        return identity
    finally:
        _close_windows_handle(handle)


def _open_windows_path(
    path: Path,
    *,
    desired_access: int = 0,
    creation_disposition: int = _OPEN_EXISTING,
    flags: int = _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
) -> int:
    if os.name != "nt":
        raise RuntimeError("Windows path identity is unavailable")
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise RuntimeError("Windows API loader is unavailable")
    kernel32 = loader("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        desired_access,
        _FILE_SHARE_ALL,
        None,
        creation_disposition,
        flags,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        error = _last_error()
        if error in (2, 3):
            raise FileNotFoundError(str(path))
        if error in (80, 183):
            raise FileExistsError(str(path))
        raise SharedStateIntegrityError("snapshot trusted root is unsafe")
    return int(handle)


def _windows_handle_metadata(handle: int) -> tuple[tuple[int, int], int]:
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise RuntimeError("Windows API loader is unavailable")
    kernel32 = loader("kernel32", use_last_error=True)
    information = _ByHandleFileInformation()
    if not kernel32.GetFileInformationByHandle(
        wintypes.HANDLE(handle),
        ctypes.byref(information),
    ):
        raise SharedStateIntegrityError("snapshot trusted root is unsafe")
    file_index = (int(information.file_index_high) << 32) | int(
        information.file_index_low
    )
    identity = int(information.volume_serial_number), file_index
    return identity, int(information.attributes)


def _close_windows_handle(handle: int) -> None:
    pointer_type = ctypes.c_void_p
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None or pointer_type is None:
        raise RuntimeError("Windows API loader is unavailable")
    kernel32 = loader("kernel32", use_last_error=True)
    try:
        closed = kernel32.CloseHandle(wintypes.HANDLE(handle))
    except (OSError, ValueError) as exc:
        raise SharedStateIntegrityError("snapshot trusted root is unsafe") from exc
    if not closed:
        raise SharedStateIntegrityError("snapshot trusted root is unsafe")


def _last_error() -> int:
    pointer_type = ctypes.c_void_p
    getter = getattr(ctypes, "get_last_error", None)
    if getter is None or pointer_type is None:
        return 0
    return int(getter())
