"""在项目根目录内无跟随、稳定地读取可信输入文件。"""

from __future__ import annotations

import ctypes
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

_READ_SIZE = 64 * 1024
_REPARSE_POINT = 0x400
_IS_WINDOWS = os.name == "nt"


def read_stable_bytes(root: Path, path: Path) -> bytes:
    """读取普通文件，并拒绝越界、链接和读取期间发生的替换。"""

    canonical_root, relative = _lexical_relative(root, path)
    if os.name != "nt" and hasattr(os, "O_NOFOLLOW"):
        return _read_posix(canonical_root, relative)
    return _read_portable(canonical_root, relative)


def read_stable_text(
    root: Path,
    path: Path,
    *,
    encoding: str = "utf-8",
) -> str:
    return read_stable_bytes(root, path).decode(encoding)


def _lexical_relative(root: Path, path: Path) -> tuple[Path, Path]:
    canonical_root = root.resolve(strict=True)
    candidate = path if path.is_absolute() else canonical_root / path
    try:
        relative = candidate.relative_to(canonical_root)
    except ValueError as exc:
        raise ValueError(f"trusted file must stay within project: {path}") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"trusted file path is not canonical: {path}")
    return canonical_root, relative


def _read_posix(root: Path, relative: Path) -> bytes:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(root, directory_flags)
    try:
        for part in relative.parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(
            relative.parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        try:
            return _read_descriptor(file_fd, relative)
        finally:
            os.close(file_fd)
    except OSError as exc:
        raise ValueError(
            f"trusted file is unavailable or uses a symlink: {relative.as_posix()}"
        ) from exc
    finally:
        os.close(directory_fd)


def _read_descriptor(file_fd: int, relative: Path) -> bytes:
    before = os.fstat(file_fd)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"trusted file must be regular: {relative.as_posix()}")
    chunks: list[bytes] = []
    while chunk := os.read(file_fd, _READ_SIZE):
        chunks.append(chunk)
    after = os.fstat(file_fd)
    if _stat_identity(before) != _stat_identity(after):
        raise ValueError(f"trusted file changed while reading: {relative.as_posix()}")
    return b"".join(chunks)


def _read_portable(root: Path, relative: Path) -> bytes:
    candidate = root / relative
    _reject_link_path(root, relative)
    try:
        with candidate.open("rb") as stream:
            _validate_opened_handle(root, candidate, stream.fileno(), relative)
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(
                    f"trusted file must be regular: {relative.as_posix()}"
                )
            data = stream.read()
            after = os.fstat(stream.fileno())
            _validate_opened_handle(root, candidate, stream.fileno(), relative)
    except OSError as exc:
        raise ValueError(
            f"trusted file is unavailable: {relative.as_posix()}"
        ) from exc
    _reject_link_path(root, relative)
    if _stat_identity(before) != _stat_identity(after):
        raise ValueError(f"trusted file changed while reading: {relative.as_posix()}")
    return data


def _validate_opened_handle(
    root: Path,
    candidate: Path,
    file_descriptor: int,
    relative: Path,
) -> None:
    opened = _opened_file_path(file_descriptor)
    if opened is None:
        if _IS_WINDOWS:
            raise ValueError(
                f"trusted file opened handle is unavailable: {relative.as_posix()}"
            )
        return
    expected = os.path.normcase(os.path.abspath(candidate))
    actual = os.path.normcase(os.path.abspath(opened))
    canonical_root = os.path.normcase(os.path.abspath(root))
    try:
        within_root = os.path.commonpath((canonical_root, actual)) == canonical_root
    except ValueError:
        within_root = False
    if not within_root or actual != expected:
        raise ValueError(
            f"trusted file opened handle escaped project: {relative.as_posix()}"
        )


def _opened_file_path(file_descriptor: int) -> Path | None:
    if not _IS_WINDOWS:
        return None
    try:
        import msvcrt

        get_osfhandle = cast(
            Callable[[int], int],
            msvcrt.__dict__["get_osfhandle"],
        )
        win_dll = cast(Callable[..., Any], ctypes.__dict__["WinDLL"])
        handle = get_osfhandle(file_descriptor)
        kernel32 = win_dll("kernel32", use_last_error=True)
        final_path = kernel32.GetFinalPathNameByHandleW
        final_path.argtypes = (
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
        )
        final_path.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        length = final_path(
            ctypes.c_void_p(handle),
            buffer,
            len(buffer),
            0,
        )
    except (ImportError, OSError, ValueError):
        return None
    if not length or length >= len(buffer):
        return None
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = f"\\\\{value[8:]}"
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def _reject_link_path(root: Path, relative: Path) -> None:
    current = root
    for part in relative.parts:
        current /= part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise ValueError(
                f"trusted file is unavailable: {relative.as_posix()}"
            ) from exc
        attributes = getattr(metadata, "st_file_attributes", 0)
        if stat.S_ISLNK(metadata.st_mode) or attributes & _REPARSE_POINT:
            raise ValueError(
                f"trusted file uses a symlink: {relative.as_posix()}"
            )


def _stat_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


__all__ = ["read_stable_bytes", "read_stable_text"]
