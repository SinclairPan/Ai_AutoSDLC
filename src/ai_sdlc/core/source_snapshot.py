"""Deterministic source snapshots shared by Lean Code and PR review gates."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_sdlc.core.loop_models import LoopArtifactModel

_SOURCE_KINDS = {"local-git-range", "local-staged", "local-unstaged", "patch"}


class SourceSnapshot(LoopArtifactModel):
    """Persisted identity of the exact source evaluated by a quality gate."""

    artifact_kind: str = "source-snapshot"
    source_kind: str
    base_ref: str = ""
    head_ref: str = ""
    base_commit: str = ""
    head_commit: str = ""
    diff_hash: str
    changed_files: list[str] = Field(default_factory=list)
    untracked_files: list[str] = Field(default_factory=list)
    deleted_files: list[str] = Field(default_factory=list)
    binary_files: list[str] = Field(default_factory=list)
    renamed_files: dict[str, str] = Field(default_factory=dict)
    file_digests: dict[str, str] = Field(default_factory=dict)
    index_identity: str = ""
    patch_file: str = ""

    @field_validator("source_kind")
    @classmethod
    def _require_supported_source(cls, value: str) -> str:
        if value not in _SOURCE_KINDS:
            raise ValueError(f"unsupported source_kind: {value}")
        return value


class SourceFreshness(BaseModel):
    """Read-only freshness result for a persisted source snapshot."""

    model_config = ConfigDict(extra="forbid")

    fresh: bool
    reason: str = ""
    current_diff_hash: str = ""


@dataclass(frozen=True)
class SourceSnapshotOptions:
    """Inputs used to build one deterministic source snapshot."""

    root: Path
    source_kind: str = "local-git-range"
    base_ref: str = ""
    head_ref: str = "HEAD"
    patch_file: str = ""


@dataclass(frozen=True)
class _SnapshotParts:
    diff_bytes: bytes
    status_bytes: bytes
    numstat_bytes: bytes
    base_ref: str
    head_ref: str
    base_commit: str
    head_commit: str
    index_identity: str = ""
    patch_file: str = ""
    untracked_files: tuple[str, ...] = ()
    untracked_payload: bytes = b""


def build_source_snapshot(options: SourceSnapshotOptions) -> SourceSnapshot:
    """Build a source identity without invoking a model or modifying the index."""

    root = options.root.resolve()
    if options.source_kind not in _SOURCE_KINDS:
        raise ValueError(f"unsupported source_kind: {options.source_kind}")
    parts = _build_parts(root, options)
    statuses = _parse_name_status(parts.status_bytes)
    changed = sorted({item[1] for item in statuses} | set(parts.untracked_files))
    if not changed:
        raise ValueError("source snapshot contains no changed files")
    payload = parts.diff_bytes + parts.untracked_payload
    snapshot = SourceSnapshot(
        source_kind=options.source_kind,
        base_ref=parts.base_ref,
        head_ref=parts.head_ref,
        base_commit=parts.base_commit,
        head_commit=parts.head_commit,
        diff_hash=_digest(payload),
        changed_files=changed,
        untracked_files=list(parts.untracked_files),
        deleted_files=sorted(path for status, path, _ in statuses if status == "D"),
        binary_files=_binary_files(root, parts, statuses),
        renamed_files={path: old for status, path, old in statuses if status == "R"},
        file_digests={},
        index_identity=parts.index_identity,
        patch_file=parts.patch_file,
    )
    return snapshot.model_copy(
        update={"file_digests": _snapshot_digests(root, snapshot)}
    )


def revalidate_source_snapshot(root: Path, snapshot: SourceSnapshot) -> SourceFreshness:
    """Rebuild a snapshot and compare the source identity fail-closed."""

    try:
        current = build_source_snapshot(
            SourceSnapshotOptions(
                root=root,
                source_kind=snapshot.source_kind,
                base_ref=snapshot.base_ref,
                head_ref=snapshot.head_ref,
                patch_file=snapshot.patch_file,
            )
        )
    except (OSError, ValueError) as exc:
        return SourceFreshness(fresh=False, reason=f"source_unavailable:{exc}")
    if current.diff_hash != snapshot.diff_hash:
        return SourceFreshness(
            fresh=False,
            reason="diff_hash_changed",
            current_diff_hash=current.diff_hash,
        )
    if (
        current.base_commit != snapshot.base_commit
        or current.head_commit != snapshot.head_commit
    ):
        return SourceFreshness(
            fresh=False,
            reason="commit_changed",
            current_diff_hash=current.diff_hash,
        )
    if (
        snapshot.source_kind in {"local-staged", "local-unstaged"}
        and current.index_identity != snapshot.index_identity
    ):
        return SourceFreshness(
            fresh=False,
            reason="index_identity_changed",
            current_diff_hash=current.diff_hash,
        )
    return SourceFreshness(fresh=True, current_diff_hash=current.diff_hash)


def _build_parts(root: Path, options: SourceSnapshotOptions) -> _SnapshotParts:
    if options.source_kind == "local-git-range":
        return _git_range_parts(root, options)
    if options.source_kind == "local-staged":
        return _worktree_parts(root, staged=True)
    if options.source_kind == "local-unstaged":
        return _worktree_parts(root, staged=False)
    return _patch_parts(root, options)


def _git_range_parts(root: Path, options: SourceSnapshotOptions) -> _SnapshotParts:
    if not options.base_ref.strip():
        raise ValueError("base_ref is required for local-git-range")
    head_ref = options.head_ref or "HEAD"
    base_commit = _git_text(root, "merge-base", options.base_ref, head_ref)
    head_commit = _git_text(root, "rev-parse", head_ref)
    from ai_sdlc.core.source_snapshot_view import selected_git_diff

    diff, status, numstat = selected_git_diff(
        root,
        "local-git-range",
        base_commit=base_commit,
        head_commit=head_commit,
    )
    return _SnapshotParts(
        diff_bytes=diff,
        status_bytes=status,
        numstat_bytes=numstat,
        base_ref=options.base_ref,
        head_ref=head_ref,
        base_commit=base_commit,
        head_commit=head_commit,
    )


def _worktree_parts(root: Path, *, staged: bool) -> _SnapshotParts:
    head = _git_text(root, "rev-parse", "HEAD")
    if staged:
        from ai_sdlc.core.source_snapshot_view import selected_git_diff

        diff, status, numstat = selected_git_diff(
            root,
            "local-staged",
            base_commit=head,
        )
    else:
        diff = _git(root, "diff", "--binary", "--no-ext-diff", "--no-textconv")
        status = _git(root, "diff", "--name-status", "-z", "-M")
        numstat = _git(root, "diff", "--numstat", "-z", "-M")
    discovered = (
        ()
        if staged
        else tuple(
            _nul_paths(_git(root, "ls-files", "--others", "--exclude-standard", "-z"))
        )
    )
    untracked = tuple(path for path in discovered if not _is_runtime_artifact(path))
    return _SnapshotParts(
        diff_bytes=diff,
        status_bytes=status,
        numstat_bytes=numstat,
        base_ref="HEAD" if staged else "INDEX",
        head_ref="INDEX" if staged else "WORKTREE",
        base_commit=head,
        head_commit=head,
        index_identity=_index_identity(root),
        untracked_files=untracked,
        untracked_payload=_untracked_payload(root, untracked),
    )


def _patch_parts(root: Path, options: SourceSnapshotOptions) -> _SnapshotParts:
    patch_file = options.patch_file
    if not patch_file.strip():
        raise ValueError("patch_file is required for patch source")
    path = (root / patch_file).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("patch_file must stay inside the repository") from exc
    if not path.is_file():
        raise ValueError(f"patch_file not found: {patch_file}")
    patch = path.read_bytes()
    head_ref = options.head_ref.strip() or "HEAD"
    head = _git_text(root, "rev-parse", head_ref)
    from ai_sdlc.core.source_snapshot_view import patch_diff_metadata

    status, numstat = patch_diff_metadata(
        root,
        path.relative_to(root).as_posix(),
        head,
    )

    return _SnapshotParts(
        diff_bytes=patch,
        status_bytes=status,
        numstat_bytes=numstat,
        base_ref="patch-file",
        head_ref=head_ref,
        base_commit=head,
        head_commit=head,
        patch_file=path.relative_to(root).as_posix(),
    )


def _index_identity(root: Path) -> str:
    entries = _git(root, "ls-files", "-s", "-z")
    flags = _git(root, "ls-files", "-v", "-z")
    return _digest(entries + b"\0INDEX-FLAGS\0" + flags)


def _parse_name_status(payload: bytes) -> list[tuple[str, str, str]]:
    fields = [item for item in payload.split(b"\0") if item]
    parsed: list[tuple[str, str, str]] = []
    index = 0
    while index < len(fields):
        status = fields[index].decode("ascii", errors="strict")
        index += 1
        if status.startswith(("R", "C")):
            old = _decode_path(fields[index])
            path = _decode_path(fields[index + 1])
            parsed.append(("R", path, old))
            index += 2
        else:
            parsed.append((status[:1], _decode_path(fields[index]), ""))
            index += 1
    return parsed


def _binary_files(
    root: Path,
    parts: _SnapshotParts,
    statuses: list[tuple[str, str, str]],
) -> list[str]:
    changed_paths = {path for _status, path, _old in statuses}
    tracked_paths = changed_paths - set(parts.untracked_files)
    numstat = _parse_numstat(parts.numstat_bytes)
    if set(numstat) != tracked_paths:
        raise ValueError("numstat paths do not match source status")
    binary = {path for path, is_binary in numstat.items() if is_binary}
    for path in parts.untracked_files:
        target = root / path
        if (
            not target.is_symlink()
            and target.is_file()
            and b"\0" in target.read_bytes()[:8192]
        ):
            binary.add(path)
    return sorted(binary)


def _parse_binary_numstat(payload: bytes) -> list[str]:
    return [path for path, is_binary in _parse_numstat(payload).items() if is_binary]


def _parse_numstat(payload: bytes) -> dict[str, bool]:
    if not payload:
        return {}
    if not payload.endswith(b"\0"):
        raise ValueError("git numstat is not NUL terminated")
    fields = payload.split(b"\0")
    fields.pop()
    parsed: dict[str, bool] = {}
    index = 0
    while index < len(fields):
        if not fields[index]:
            raise ValueError("git numstat contains an empty record")
        record = fields[index].split(b"\t", 2)
        index += 1
        if len(record) != 3:
            raise ValueError("malformed git numstat record")
        added, deleted, encoded_path = record
        binary = added == b"-" and deleted == b"-"
        if not binary and (not added.isdigit() or not deleted.isdigit()):
            raise ValueError("git numstat counts are invalid")
        if encoded_path:
            path = _decode_path(encoded_path)
        else:
            if index + 1 >= len(fields):
                raise ValueError("malformed git rename numstat record")
            if not fields[index] or not fields[index + 1]:
                raise ValueError("git rename numstat path is empty")
            path = _decode_path(fields[index + 1])
            index += 2
        if path in parsed:
            raise ValueError("git numstat contains a duplicate path")
        parsed[path] = binary
    return parsed


def _snapshot_digests(root: Path, snapshot: SourceSnapshot) -> dict[str, str]:
    from ai_sdlc.core.source_snapshot_view import file_versions

    digests: dict[str, str] = {}
    for path in snapshot.changed_files:
        _before, after = file_versions(root, snapshot, path)
        if after:
            digests[path] = _digest(after)
    return digests


def _untracked_payload(root: Path, paths: tuple[str, ...]) -> bytes:
    payload = bytearray()
    for path in sorted(paths):
        raw_path = path.encode("utf-8")
        payload.extend(b"\0UNTRACKED\0" + raw_path + b"\0")
        target = root / path
        if target.is_symlink():
            payload.extend(b"SYMLINK\0")
            payload.extend(hashlib.sha256(os.fsencode(os.readlink(target))).digest())
            continue
        if not target.is_file():
            raise ValueError(f"untracked source path is not a file: {path}")
        executable = (
            b"1" if target.stat(follow_symlinks=False).st_mode & 0o111 else b"0"
        )
        payload.extend(b"FILE\0" + executable + b"\0")
        payload.extend(hashlib.sha256(target.read_bytes()).digest())
    return bytes(payload)


def _nul_paths(payload: bytes) -> list[str]:
    return [_decode_path(item) for item in payload.split(b"\0") if item]


def _is_runtime_artifact(path: str) -> bool:
    normalized = path.replace("\\", "/")
    segments = normalized.split("/")
    return (
        normalized.startswith((".ai-sdlc/loops/", ".ai-sdlc/reviews/"))
        or "__pycache__" in segments
        or bool(set(segments) & {".pytest_cache", ".ruff_cache", ".mypy_cache"})
        or normalized in {".coverage"}
        or segments[0] in {"htmlcov", "build", "dist"}
        or normalized.endswith((".pyc", ".pyo"))
    )


def _decode_path(payload: bytes) -> str:
    return payload.decode("utf-8", errors="strict").replace("\\", "/")


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _git_text(root: Path, *args: str) -> str:
    return _git(root, *args).decode("utf-8", errors="strict").strip()


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"git {' '.join(args)} failed: {message}")
    return result.stdout


__all__ = [
    "SourceFreshness",
    "SourceSnapshot",
    "SourceSnapshotOptions",
    "build_source_snapshot",
    "revalidate_source_snapshot",
]
