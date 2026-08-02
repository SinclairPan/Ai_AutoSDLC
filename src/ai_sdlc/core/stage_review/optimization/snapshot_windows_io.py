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
# Win32 class=3 会把相对名称按进程 CWD 解析；native class=10 才保留源文件父目录。
_FILE_RENAME_INFORMATION_CLASS = 10
_FILE_DISPOSITION_INFO_CLASS = 4
_FILE_SHARE_READ = 0x1
_FILE_SHARE_READ_WRITE = 0x1 | 0x2
_LOCKFILE_FAIL_IMMEDIATELY = 0x1
_LOCKFILE_EXCLUSIVE_LOCK = 0x2
_REGISTRY_LOCK = threading.RLock()
_STATUS_OBJECT_NAME_COLLISION = 0xC0000035
_DIRECTORY_IDENTITIES: dict[
    str,
    tuple[tuple[Path, tuple[int, int]], ...],
] = {}


class _FileRenameInfo(ctypes.Structure):
    _fields_ = (
        ("replace_if_exists", wintypes.BOOLEAN),
        ("root_directory", wintypes.HANDLE),
        ("file_name_length", wintypes.DWORD),
        ("file_name", wintypes.WCHAR * 1),
    )


class _IoStatusValue(ctypes.Union):
    _fields_ = (
        ("status", wintypes.LONG),
        ("pointer", wintypes.LPVOID),
    )


class _IoStatusBlock(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = (
        ("value", _IoStatusValue),
        ("information", ctypes.c_size_t),
    )


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
        primary_failure: BaseException | None = None
        try:
            try:
                _verify_registered_directory(directory)
                _verify_regular_file(handle)
                _harden_windows_handle_acl(handle)
                _verify_registered_directory(directory)
                _write_handle(handle, payload)
                _rename_handle(
                    handle,
                    name,
                    replace=replace,
                )
                renamed = True
            except FileExistsError:
                if replace:
                    raise
        except BaseException as exc:
            primary_failure = exc
            raise
        finally:
            cleanup_failure: BaseException | None = None
            if not renamed:
                try:
                    _mark_handle_for_deletion(handle)
                except BaseException as exc:
                    cleanup_failure = exc
                    if primary_failure is not None:
                        primary_failure.add_note(
                            f"snapshot trusted temporary cleanup failed: {exc}"
                        )
            try:
                _close_windows_handle(handle)
            except BaseException as exc:
                if primary_failure is not None:
                    primary_failure.add_note(
                        f"snapshot trusted temporary close failed: {exc}"
                    )
                elif cleanup_failure is not None:
                    cleanup_failure.add_note(
                        f"snapshot trusted temporary close failed: {exc}"
                    )
                else:
                    raise
            if primary_failure is None and cleanup_failure is not None:
                raise cleanup_failure


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
def _open_registered_directory(
    directory: Path,
    *,
    desired_access: int = 0,
) -> Iterator[int]:
    expected = _registered_identities(directory)
    _verify_registered_directory(directory)
    handle = _open_windows_path(directory, desired_access=desired_access)
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
    name: str,
    *,
    replace: bool,
) -> None:
    buffer, information = _build_rename_information(
        name,
        replace=replace,
    )
    io_status = _IoStatusBlock()
    status = int(
        _nt_set_information_file()(
            wintypes.HANDLE(handle),
            ctypes.byref(io_status),
            ctypes.byref(information),
            ctypes.sizeof(buffer),
            _FILE_RENAME_INFORMATION_CLASS,
        )
    )
    ntstatus = ctypes.c_uint32(status).value
    if ntstatus == 0:
        return
    if ntstatus == _STATUS_OBJECT_NAME_COLLISION:
        raise FileExistsError(name)
    winerror = int(_rtl_nt_status_to_dos_error()(status))
    raise SharedStateIntegrityError(
        "snapshot trusted file publish failed "
        f"(ntstatus=0x{ntstatus:08X}, winerror={winerror})"
    )


def _nt_set_information_file() -> Any:
    if os.name != "nt":
        raise RuntimeError("Windows native file API is unavailable")
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise RuntimeError("Windows API loader is unavailable")
    function = loader("ntdll").NtSetInformationFile
    function.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_IoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        ctypes.c_int,
    )
    function.restype = wintypes.LONG
    return function


def _rtl_nt_status_to_dos_error() -> Any:
    if os.name != "nt":
        raise RuntimeError("Windows native status API is unavailable")
    pointer_type = ctypes.c_void_p
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None or pointer_type is None:
        raise RuntimeError("Windows API loader is unavailable")
    function = loader("ntdll").RtlNtStatusToDosError
    function.argtypes = (wintypes.LONG,)
    function.restype = wintypes.ULONG
    return function


def _build_rename_information(
    name: str,
    *,
    replace: bool,
) -> tuple[Any, _FileRenameInfo]:
    encoded_name = name.encode("utf-16-le")
    buffer = ctypes.create_string_buffer(
        ctypes.sizeof(_FileRenameInfo) + len(encoded_name)
    )
    information = _FileRenameInfo.from_buffer(buffer)
    information.replace_if_exists = int(replace)
    information.file_name_length = len(encoded_name)
    ctypes.memmove(
        ctypes.addressof(buffer) + _FileRenameInfo.file_name.offset,
        encoded_name,
        len(encoded_name),
    )
    return buffer, information


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
    if (
        not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or ":" in name
        or Path(name).name != name
    ):
        raise SharedStateIntegrityError("snapshot trusted file name is unsafe")
