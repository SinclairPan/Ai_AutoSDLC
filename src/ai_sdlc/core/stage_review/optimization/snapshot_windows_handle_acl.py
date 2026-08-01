"""通过已打开 Windows handle 收敛并复验 Owner 与 DACL。"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
from typing import Any

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.snapshot_windows_security import (
    _close_windows_handle,
)

_ACCESS_ALLOWED_ACE_TYPE = 0
_ACL_REVISION = 2
_CONTAINER_INHERIT_ACE = 0x2
_DACL_SECURITY_INFORMATION = 0x4
_ERROR_INSUFFICIENT_BUFFER = 122
_FILE_ALL_ACCESS = 0x001F01FF
_OBJECT_INHERIT_ACE = 0x1
_OWNER_SECURITY_INFORMATION = 0x1
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_SE_DACL_PROTECTED = 0x1000
_SE_FILE_OBJECT = 1
_TOKEN_QUERY = 0x0008
_TOKEN_USER_CLASS = 1


class _SidAndAttributes(ctypes.Structure):
    _fields_ = (("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD))


class _TokenUser(ctypes.Structure):
    _fields_ = (("user", _SidAndAttributes),)


class _Acl(ctypes.Structure):
    _fields_ = (
        ("revision", wintypes.BYTE),
        ("sbz1", wintypes.BYTE),
        ("acl_size", wintypes.WORD),
        ("ace_count", wintypes.WORD),
        ("sbz2", wintypes.WORD),
    )


class _AclSizeInformation(ctypes.Structure):
    _fields_ = (
        ("ace_count", wintypes.DWORD),
        ("acl_bytes_in_use", wintypes.DWORD),
        ("acl_bytes_free", wintypes.DWORD),
    )


class _AceHeader(ctypes.Structure):
    _fields_ = (
        ("ace_type", wintypes.BYTE),
        ("ace_flags", wintypes.BYTE),
        ("ace_size", wintypes.WORD),
    )


class _AccessAllowedAce(ctypes.Structure):
    _fields_ = (
        ("header", _AceHeader),
        ("mask", wintypes.DWORD),
        ("sid_start", wintypes.DWORD),
    )


def _harden_windows_handle_acl(handle: int, *, is_directory: bool = False) -> None:
    """只接受当前 SID Owner，并在同一 handle 上发布受保护的单 ACE DACL。"""
    sid_buffer, current_sid = _current_user_sid()
    owner, _, descriptor = _read_security(handle)
    try:
        if not _equal_sid(owner, current_sid):
            raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
    finally:
        _local_free(descriptor)
    acl_buffer, acl = _single_user_acl(current_sid, is_directory=is_directory)
    result = _set_security_info()(
        wintypes.HANDLE(handle),
        _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.c_void_p(acl),
        None,
    )
    if result != 0:
        raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
    _verify_handle_acl(handle, current_sid, is_directory=is_directory)
    del acl_buffer, sid_buffer


def _current_user_sid() -> tuple[Any, int]:
    token = wintypes.HANDLE()
    if not _open_process_token()(
        _get_current_process()(),
        _TOKEN_QUERY,
        ctypes.byref(token),
    ):
        raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
    if token.value is None:
        raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
    token_value = int(token.value)
    try:
        required = wintypes.DWORD()
        _get_token_information()(
            token,
            _TOKEN_USER_CLASS,
            None,
            0,
            ctypes.byref(required),
        )
        if _last_error() != _ERROR_INSUFFICIENT_BUFFER or required.value == 0:
            raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
        buffer = ctypes.create_string_buffer(required.value)
        if not _get_token_information()(
            token,
            _TOKEN_USER_CLASS,
            buffer,
            required,
            ctypes.byref(required),
        ):
            raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
        sid = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents.user.sid
        if not sid:
            raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
        return buffer, int(sid)
    finally:
        _close_windows_handle(token_value)


def _single_user_acl(current_sid: int, *, is_directory: bool) -> tuple[Any, int]:
    sid_length = int(_advapi32().GetLengthSid(ctypes.c_void_p(current_sid)))
    if sid_length <= 0:
        raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
    acl_size = (
        ctypes.sizeof(_Acl)
        + ctypes.sizeof(_AccessAllowedAce)
        - ctypes.sizeof(wintypes.DWORD)
        + sid_length
    )
    buffer = ctypes.create_string_buffer(acl_size)
    acl = ctypes.addressof(buffer)
    if not _advapi32().InitializeAcl(ctypes.c_void_p(acl), acl_size, _ACL_REVISION):
        raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
    flags = _OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE if is_directory else 0
    if not _advapi32().AddAccessAllowedAceEx(
        ctypes.c_void_p(acl),
        _ACL_REVISION,
        flags,
        _FILE_ALL_ACCESS,
        ctypes.c_void_p(current_sid),
    ):
        raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
    return buffer, acl


def _verify_handle_acl(handle: int, current_sid: int, *, is_directory: bool) -> None:
    owner, dacl, descriptor = _read_security(handle)
    try:
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not _advapi32().GetSecurityDescriptorControl(
            ctypes.c_void_p(descriptor),
            ctypes.byref(control),
            ctypes.byref(revision),
        ):
            raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
        if not control.value & _SE_DACL_PROTECTED or not _equal_sid(owner, current_sid):
            raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
        _verify_single_ace(dacl, current_sid, is_directory=is_directory)
    finally:
        _local_free(descriptor)


def _verify_single_ace(dacl: int, current_sid: int, *, is_directory: bool) -> None:
    information = _AclSizeInformation()
    if (
        not _advapi32().GetAclInformation(
            ctypes.c_void_p(dacl),
            ctypes.byref(information),
            ctypes.sizeof(information),
            2,
        )
        or information.ace_count != 1
    ):
        raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
    ace_pointer = ctypes.c_void_p()
    if not _advapi32().GetAce(
        ctypes.c_void_p(dacl),
        0,
        ctypes.byref(ace_pointer),
    ):
        raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
    if ace_pointer.value is None:
        raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
    ace = ctypes.cast(ace_pointer, ctypes.POINTER(_AccessAllowedAce)).contents
    expected_flags = _OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE if is_directory else 0
    sid_address = int(ace_pointer.value) + ctypes.sizeof(_AceHeader) + 4
    valid = (
        ace.header.ace_type == _ACCESS_ALLOWED_ACE_TYPE
        and ace.header.ace_flags == expected_flags
        and ace.mask == _FILE_ALL_ACCESS
        and _equal_sid(sid_address, current_sid)
    )
    if not valid:
        raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")


def _read_security(handle: int) -> tuple[int, int, int]:
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = _get_security_info()(
        wintypes.HANDLE(handle),
        _SE_FILE_OBJECT,
        _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if result != 0 or not owner.value or not dacl.value or not descriptor.value:
        if descriptor.value:
            _local_free(int(descriptor.value))
        raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
    return int(owner.value), int(dacl.value), int(descriptor.value)


def _equal_sid(left: int, right: int) -> bool:
    return bool(_advapi32().EqualSid(ctypes.c_void_p(left), ctypes.c_void_p(right)))


def _local_free(pointer: int) -> None:
    if _kernel32().LocalFree(ctypes.c_void_p(pointer)):
        raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")


def _advapi32() -> Any:
    return _windows_dll("advapi32")


def _kernel32() -> Any:
    return _windows_dll("kernel32")


def _windows_dll(name: str) -> Any:
    if os.name != "nt":
        raise RuntimeError("Windows handle ACL is unavailable")
    pointer_type = ctypes.c_void_p
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None or pointer_type is None:
        raise RuntimeError("Windows API loader is unavailable")
    return loader(name, use_last_error=True)


def _get_current_process() -> Any:
    function = _kernel32().GetCurrentProcess
    function.restype = wintypes.HANDLE
    return function


def _open_process_token() -> Any:
    function = _advapi32().OpenProcessToken
    function.restype = wintypes.BOOL
    return function


def _get_token_information() -> Any:
    function = _advapi32().GetTokenInformation
    function.restype = wintypes.BOOL
    return function


def _get_security_info() -> Any:
    function = _advapi32().GetSecurityInfo
    function.restype = wintypes.DWORD
    return function


def _set_security_info() -> Any:
    function = _advapi32().SetSecurityInfo
    function.restype = wintypes.DWORD
    return function


def _last_error() -> int:
    pointer_type = ctypes.c_void_p
    getter = getattr(ctypes, "get_last_error", None)
    return int(getter()) if getter is not None and pointer_type is not None else 0
