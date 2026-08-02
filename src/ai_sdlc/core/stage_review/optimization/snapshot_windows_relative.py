"""Windows 可信叶文件的目录句柄相对打开原语。"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.snapshot_windows_creation import (
    _current_user_security_descriptor,
)

_CREATE_NEW = 1
_OPEN_ALWAYS = 4
_OPEN_EXISTING = 3
_FILE_CREATE = 2
_FILE_OPEN = 1
_FILE_OPEN_IF = 3
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_NORMAL = 0x80
_FILE_DIRECTORY_FILE = 0x1
_FILE_NON_DIRECTORY_FILE = 0x40
_FILE_SYNCHRONOUS_IO_NONALERT = 0x20
_FILE_OPEN_REPARSE_POINT = 0x00200000
_FILE_READ_ATTRIBUTES = 0x80
_FILE_SHARE_ALL = 0x1 | 0x2 | 0x4
_FILE_SHARE_READ_WRITE = 0x1 | 0x2
_FILE_TRAVERSE = 0x20
_OBJECT_CASE_INSENSITIVE = 0x40
_READ_CONTROL = 0x00020000
_WRITE_DAC = 0x00040000
_SYNCHRONIZE = 0x00100000
_STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034
_STATUS_OBJECT_NAME_COLLISION = 0xC0000035
_STATUS_OBJECT_PATH_NOT_FOUND = 0xC000003A


class _UnicodeString(ctypes.Structure):
    _fields_ = (
        ("length", wintypes.USHORT),
        ("maximum_length", wintypes.USHORT),
        ("buffer", wintypes.LPWSTR),
    )


class _ObjectAttributes(ctypes.Structure):
    _fields_ = (
        ("length", wintypes.ULONG),
        ("root_directory", wintypes.HANDLE),
        ("object_name", ctypes.POINTER(_UnicodeString)),
        ("attributes", wintypes.ULONG),
        ("security_descriptor", wintypes.LPVOID),
        ("security_quality_of_service", wintypes.LPVOID),
    )


class _IoStatusBlock(ctypes.Structure):
    _fields_ = (
        ("status", ctypes.c_void_p),
        ("information", ctypes.c_size_t),
    )


def _open_windows_relative(
    directory_handle: int,
    name: str,
    *,
    desired_access: int,
    creation_disposition: int,
    share_mode: int = _FILE_SHARE_ALL,
    is_directory: bool = False,
    acl_access: bool = True,
) -> int:
    """相对已验证目录句柄打开对象，禁止重新解析祖先字符串路径。"""
    _validate_relative_name(name)
    name_buffer, unicode_name = _unicode_name(name)
    security = _relative_security_descriptor(creation_disposition, is_directory)
    with security as descriptor:
        attributes = _ObjectAttributes(
            ctypes.sizeof(_ObjectAttributes),
            wintypes.HANDLE(directory_handle),
            ctypes.pointer(unicode_name),
            _OBJECT_CASE_INSENSITIVE,
            ctypes.c_void_p(descriptor) if descriptor is not None else None,
            None,
        )
        handle = wintypes.HANDLE()
        io_status = _IoStatusBlock()
        access = desired_access | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
        if acl_access:
            access |= _READ_CONTROL | _WRITE_DAC
        status = _nt_create_file()(
            ctypes.byref(handle),
            access,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            _FILE_ATTRIBUTE_DIRECTORY if is_directory else _FILE_ATTRIBUTE_NORMAL,
            share_mode,
            _native_disposition(creation_disposition),
            (_FILE_DIRECTORY_FILE if is_directory else _FILE_NON_DIRECTORY_FILE)
            | _FILE_SYNCHRONOUS_IO_NONALERT
            | _FILE_OPEN_REPARSE_POINT,
            None,
            0,
        )
    del name_buffer
    if int(status) >= 0:
        if handle.value is None:
            raise SharedStateIntegrityError("snapshot trusted file open failed")
        return int(handle.value)
    _raise_open_error(int(status), name)
    raise AssertionError("unreachable")


def _relative_security_descriptor(
    creation_disposition: int,
    is_directory: bool,
) -> AbstractContextManager[int | None]:
    if creation_disposition in (_CREATE_NEW, _OPEN_ALWAYS):
        return _current_user_security_descriptor(is_directory=is_directory)
    return nullcontext(None)


def _open_windows_relative_directory(
    parent_handle: int,
    name: str,
    *,
    create: bool,
    harden: bool,
) -> int:
    """以稳定父目录 handle 打开或创建单层目录，并阻止命名替换。"""
    return _open_windows_relative(
        parent_handle,
        name,
        desired_access=_FILE_TRAVERSE,
        creation_disposition=_OPEN_ALWAYS if create else _OPEN_EXISTING,
        share_mode=_FILE_SHARE_READ_WRITE,
        is_directory=True,
        acl_access=harden,
    )


def _unicode_name(name: str) -> tuple[Any, _UnicodeString]:
    buffer = ctypes.create_unicode_buffer(name)
    byte_length = len(name.encode("utf-16-le"))
    return buffer, _UnicodeString(
        byte_length,
        byte_length + ctypes.sizeof(wintypes.WCHAR),
        ctypes.cast(buffer, wintypes.LPWSTR),
    )


def _native_disposition(creation_disposition: int) -> int:
    mapping = {
        _CREATE_NEW: _FILE_CREATE,
        _OPEN_EXISTING: _FILE_OPEN,
        _OPEN_ALWAYS: _FILE_OPEN_IF,
    }
    try:
        return mapping[creation_disposition]
    except KeyError as exc:
        raise SharedStateIntegrityError(
            "snapshot trusted file disposition is unsafe"
        ) from exc


def _raise_open_error(status: int, name: str) -> None:
    code = ctypes.c_ulong(status).value
    if code in (_STATUS_OBJECT_NAME_NOT_FOUND, _STATUS_OBJECT_PATH_NOT_FOUND):
        raise FileNotFoundError(name)
    if code == _STATUS_OBJECT_NAME_COLLISION:
        raise FileExistsError(name)
    raise SharedStateIntegrityError("snapshot trusted file open failed")


def _nt_create_file() -> Any:
    if os.name != "nt":
        raise RuntimeError("Windows relative file API is unavailable")
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise RuntimeError("Windows API loader is unavailable")
    function = loader("ntdll", use_last_error=True).NtCreateFile
    function.argtypes = (
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(_ObjectAttributes),
        ctypes.POINTER(_IoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
    )
    function.restype = wintypes.LONG
    return function


def _validate_relative_name(name: str) -> None:
    if not name or Path(name).name != name or name in (".", ".."):
        raise SharedStateIntegrityError("snapshot trusted file name is unsafe")
