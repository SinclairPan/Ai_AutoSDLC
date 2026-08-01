"""Windows 新可信对象的当前用户 Owner 与 DACL 创建原语。"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from collections.abc import Iterator
from contextlib import contextmanager

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.snapshot_windows_handle_acl import (
    _advapi32,
    _current_user_sid,
    _single_user_acl,
)

_SECURITY_DESCRIPTOR_REVISION = 1
_SE_DACL_PROTECTED = 0x1000


class _SecurityDescriptor(ctypes.Structure):
    _fields_ = (
        ("revision", wintypes.BYTE),
        ("sbz1", wintypes.BYTE),
        ("control", wintypes.WORD),
        ("owner", wintypes.LPVOID),
        ("group", wintypes.LPVOID),
        ("sacl", wintypes.LPVOID),
        ("dacl", wintypes.LPVOID),
    )


@contextmanager
def _current_user_security_descriptor(*, is_directory: bool) -> Iterator[int]:
    sid_buffer, current_sid = _current_user_sid()
    acl_buffer, acl = _single_user_acl(current_sid, is_directory=is_directory)
    descriptor = _SecurityDescriptor()
    pointer = ctypes.byref(descriptor)
    if not _advapi32().InitializeSecurityDescriptor(
        pointer,
        _SECURITY_DESCRIPTOR_REVISION,
    ):
        raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
    if not _advapi32().SetSecurityDescriptorOwner(
        pointer,
        ctypes.c_void_p(current_sid),
        False,
    ):
        raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
    if not _advapi32().SetSecurityDescriptorDacl(
        pointer,
        True,
        ctypes.c_void_p(acl),
        False,
    ):
        raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
    if not _advapi32().SetSecurityDescriptorControl(
        pointer,
        _SE_DACL_PROTECTED,
        _SE_DACL_PROTECTED,
    ):
        raise SharedStateIntegrityError("snapshot trusted ACL is unsafe")
    yield ctypes.addressof(descriptor)
    del acl_buffer, sid_buffer
