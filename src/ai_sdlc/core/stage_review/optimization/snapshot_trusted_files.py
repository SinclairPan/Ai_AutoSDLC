"""Snapshot 信任锚使用的跨平台受保护文件原语。"""

from __future__ import annotations

import os
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from stat import S_IMODE, S_ISDIR, S_ISREG

if os.name != "nt":
    import fcntl

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.snapshot_windows_handle_acl import (
    _harden_windows_handle_acl,
)
from ai_sdlc.core.stage_review.optimization.snapshot_windows_io import (
    _register_windows_directory,
    _windows_secure_lock,
    _windows_secure_publish,
    _windows_secure_read,
    _windows_secure_unlink,
)
from ai_sdlc.core.stage_review.optimization.snapshot_windows_relative import (
    _open_windows_relative_directory,
)
from ai_sdlc.core.stage_review.optimization.snapshot_windows_security import (
    _close_windows_handle,
    _open_windows_path,
    _windows_handle_metadata,
    _windows_path_identity,
)

_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _prepare_trusted_directory(path: Path) -> Path:
    try:
        if os.name == "nt":
            return _prepare_windows_directory(path)
        with _open_posix_directory(path):
            pass
        return path.absolute()
    except SharedStateIntegrityError:
        raise
    except (OSError, RuntimeError) as exc:
        raise SharedStateIntegrityError("snapshot trusted root is unsafe") from exc


@contextmanager
def _open_posix_directory(path: Path) -> Iterator[int]:
    absolute = path.absolute()
    boundary = _trusted_boundary(absolute).absolute()
    parts = absolute.parts
    boundary_parts = boundary.parts
    if parts[: len(boundary_parts)] != boundary_parts:
        raise SharedStateIntegrityError("snapshot trusted root is unsafe")
    descriptor = os.open(
        absolute.anchor,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    creation_start = _trusted_creation_start(boundary)
    try:
        for index, name in enumerate(parts[1:], start=1):
            trusted_component = index >= len(boundary_parts) - 1
            if index >= creation_start:
                with suppress(FileExistsError):
                    os.mkdir(name, 0o700, dir_fd=descriptor)
            next_descriptor = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            metadata = os.fstat(next_descriptor)
            _verify_posix_directory(metadata, trusted=trusted_component)
            os.close(descriptor)
            descriptor = next_descriptor
        yield descriptor
    finally:
        os.close(descriptor)


def _trusted_creation_start(boundary: Path) -> int:
    if boundary.name == "trusted":
        return len(boundary.parts) - 3
    return len(boundary.parts) - 1


def _verify_posix_directory(metadata: os.stat_result, *, trusted: bool) -> None:
    if not S_ISDIR(metadata.st_mode):
        raise SharedStateIntegrityError("snapshot trusted root is unsafe")
    if trusted and (
        metadata.st_uid != os.getuid() or S_IMODE(metadata.st_mode) != 0o700
    ):
        raise SharedStateIntegrityError(
            "snapshot trusted root permissions are unsafe"
        )


def _prepare_windows_directory(path: Path) -> Path:
    absolute = path.absolute()
    boundary = _trusted_boundary(absolute).absolute()
    parts = absolute.parts
    boundary_parts = boundary.parts
    if parts[: len(boundary_parts)] != boundary_parts:
        raise SharedStateIntegrityError("snapshot trusted root is unsafe")
    creation_start = _trusted_creation_start(boundary)
    identities: list[tuple[Path, tuple[int, int]]] = []
    current = Path(absolute.anchor)
    directory_handle = _open_windows_path(current)
    try:
        _windows_directory_identity(directory_handle)
        for index, name in enumerate(parts[1:], start=1):
            current = current / name
            trusted_component = index >= len(boundary_parts) - 1
            next_handle = _open_windows_relative_directory(
                directory_handle,
                name,
                create=index >= creation_start,
                harden=trusted_component,
            )
            try:
                identity = _windows_directory_identity(next_handle)
                if trusted_component:
                    _harden_windows_handle_acl(next_handle, is_directory=True)
                    if _windows_directory_identity(next_handle) != identity:
                        raise SharedStateIntegrityError(
                            "snapshot trusted directory identity changed"
                        )
                    identities.append((current, identity))
            except BaseException:
                _close_windows_handle(next_handle)
                raise
            previous_handle = directory_handle
            directory_handle = next_handle
            _close_windows_handle(previous_handle)
        _verify_windows_identities(identities)
        _register_windows_directory(absolute, tuple(identities))
    finally:
        _close_windows_handle(directory_handle)
    return absolute


def _windows_directory_identity(handle: int) -> tuple[int, int]:
    identity, attributes = _windows_handle_metadata(handle)
    if (
        attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        or not attributes & _FILE_ATTRIBUTE_DIRECTORY
    ):
        raise SharedStateIntegrityError("snapshot trusted root is unsafe")
    return identity


def _verify_windows_identities(
    identities: list[tuple[Path, tuple[int, int]]],
) -> None:
    for path, expected in identities:
        if _windows_path_identity(path) != expected:
            raise SharedStateIntegrityError(
                "snapshot trusted directory identity changed"
            )


def _trusted_boundary(path: Path) -> Path:
    for candidate in (path, *path.parents):
        local_trusted = (
            candidate.name == "trusted"
            and candidate.parent.name == "state"
            and candidate.parent.parent.name == ".ai-sdlc"
        )
        if candidate.name == "ai-sdlc-trusted-state" or local_trusted:
            return candidate
    raise SharedStateIntegrityError("snapshot trusted root is unsafe")


def _read_secure_file(directory: Path, name: str) -> bytes:
    if os.name == "nt":
        return _windows_secure_read(directory, name)
    with _open_posix_directory(directory) as directory_fd:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        try:
            metadata = os.fstat(descriptor)
            if not S_ISREG(metadata.st_mode) or S_IMODE(metadata.st_mode) & 0o077:
                raise SharedStateIntegrityError("snapshot trusted file is unsafe")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 65_536):
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(descriptor)


def _create_secure_file(directory: Path, name: str, payload: bytes) -> None:
    temporary = f".{secrets.token_hex(8)}.tmp"
    if os.name == "nt":
        _create_windows_file(directory, name, temporary, payload)
        return
    with _open_posix_directory(directory) as directory_fd:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
            dir_fd=directory_fd,
        )
        try:
            _write_descriptor(descriptor, payload)
            with suppress(FileExistsError):
                os.link(
                    temporary,
                    name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                )
            os.fsync(directory_fd)
        finally:
            os.unlink(temporary, dir_fd=directory_fd)


def _create_windows_file(
    directory: Path,
    name: str,
    temporary: str,
    payload: bytes,
) -> None:
    _windows_secure_publish(
        directory,
        name,
        temporary,
        payload,
        replace=False,
    )


def _replace_secure_file(directory: Path, name: str, payload: bytes) -> None:
    temporary = f".{secrets.token_hex(8)}.tmp"
    if os.name == "nt":
        _replace_windows_file(directory, name, temporary, payload)
        return
    with _open_posix_directory(directory) as directory_fd:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
            dir_fd=directory_fd,
        )
        try:
            _write_descriptor(descriptor, payload)
            os.replace(
                temporary,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            os.fsync(directory_fd)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=directory_fd)


def _replace_windows_file(
    directory: Path,
    name: str,
    temporary: str,
    payload: bytes,
) -> None:
    _windows_secure_publish(
        directory,
        name,
        temporary,
        payload,
        replace=True,
    )


def _unlink_secure_file(directory: Path, name: str) -> None:
    try:
        if os.name == "nt":
            _windows_secure_unlink(directory, name)
            return
        with _open_posix_directory(directory) as directory_fd:
            os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        return


@contextmanager
def _secure_file_lock(
    directory: Path,
    name: str,
    *,
    timeout_seconds: float,
) -> Iterator[None]:
    if os.name == "nt":
        with _windows_secure_lock(
            directory,
            name,
            timeout_seconds=timeout_seconds,
        ):
            yield
        return
    with _open_posix_directory(directory) as directory_fd:
        descriptor = os.open(
            name,
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        try:
            _verify_posix_lock(descriptor)
            _acquire_posix_lock(descriptor, timeout_seconds)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _verify_posix_lock(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or S_IMODE(metadata.st_mode) != 0o600
    ):
        raise SharedStateIntegrityError("snapshot trusted lock is unsafe")


def _acquire_posix_lock(descriptor: int, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError as exc:
            if time.monotonic() >= deadline:
                raise SharedStateIntegrityError(
                    "snapshot trusted lock unavailable"
                ) from exc
            time.sleep(0.01)


def _write_descriptor(descriptor: int, payload: bytes) -> None:
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _reject_windows_reparse(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if os.name == "nt":
        _windows_path_identity(path)
        return
    metadata = path.lstat()
    reparse = 0x400
    if path.is_symlink() or getattr(metadata, "st_file_attributes", 0) & reparse:
        raise SharedStateIntegrityError("snapshot trusted root is unsafe")
