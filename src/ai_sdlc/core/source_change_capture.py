"""Capture normalized before/after path states from one selected source view."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_sdlc.core.source_snapshot import SourceSnapshot


@dataclass(frozen=True, slots=True)
class PathState:
    """Git mode and exact identity payload for one path state."""

    mode: str = ""
    payload: bytes = b""


@dataclass(frozen=True, slots=True)
class CapturedPathChange:
    """Normalized state transition for one repository path."""

    before: PathState
    after: PathState


@dataclass(frozen=True, slots=True)
class _RawEntry:
    before_mode: str
    after_mode: str


def affected_paths(snapshot: SourceSnapshot) -> list[str]:
    """Return rename-independent paths affected by one snapshot."""
    return sorted(set(snapshot.changed_files) | set(snapshot.renamed_files.values()))


def capture_path_changes(
    root: Path,
    snapshot: SourceSnapshot,
    *,
    git_config_args: tuple[str, ...] = (),
) -> dict[str, CapturedPathChange]:
    """Capture all path transitions with at most one patch materialization."""
    paths = affected_paths(snapshot)
    if snapshot.source_kind == "local-git-range":
        before = _tree_states(root, snapshot.base_commit, paths)
        after = _tree_states(root, snapshot.head_commit, paths)
    elif snapshot.source_kind == "local-staged":
        before = _tree_states(root, "HEAD", paths)
        after = _index_states(root, paths)
    elif snapshot.source_kind == "local-unstaged":
        return _unstaged_changes(root, paths, git_config_args)
    else:
        from ai_sdlc.core.source_snapshot_view import _patched_index

        before = _tree_states(root, snapshot.base_commit, paths)
        with _patched_index(root, snapshot) as env:
            after = _index_states(root, paths, env=env)
    absent = PathState()
    return {
        path: CapturedPathChange(
            before=before.get(path, absent),
            after=after.get(path, absent),
        )
        for path in paths
    }


def capture_index_changes(
    root: Path,
    base_commit: str,
    paths: list[str],
    env: dict[str, str],
) -> dict[str, CapturedPathChange]:
    """Capture a materialized index against its base tree."""
    before = _tree_states(root, base_commit, paths)
    after = _index_states(root, paths, env=env)
    absent = PathState()
    return {
        path: CapturedPathChange(
            before=before.get(path, absent),
            after=after.get(path, absent),
        )
        for path in paths
    }


def read_tree_states(
    root: Path,
    revision: str,
    paths: list[str],
) -> dict[str, PathState]:
    """Read selected tree states with bounded Git process count."""
    return _tree_states(root, revision, paths)


def read_index_states(
    root: Path,
    paths: list[str],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, PathState]:
    """Read selected index states with bounded Git process count."""
    return _index_states(root, paths, env=env)


def _unstaged_changes(
    root: Path,
    paths: list[str],
    git_config_args: tuple[str, ...],
) -> dict[str, CapturedPathChange]:
    index_states = _index_states(root, paths)
    raw_entries = _unstaged_raw_entries(root, git_config_args)
    absent = PathState()
    changes: dict[str, CapturedPathChange] = {}
    for path in paths:
        raw = raw_entries.get(path)
        before = index_states.get(path, absent)
        if raw is not None and not raw.before_mode:
            before = absent
        after_hint = raw.after_mode if raw is not None else before.mode
        changes[path] = CapturedPathChange(
            before=before,
            after=_worktree_state(root, path, after_hint),
        )
    return changes


def _tree_states(
    root: Path,
    revision: str,
    paths: list[str],
) -> dict[str, PathState]:
    if not paths:
        return {}
    selected = set(paths)
    payload = _git(root, "ls-tree", "-r", "-z", revision)
    entries: dict[str, tuple[str, str]] = {}
    for record in payload.split(b"\0"):
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split()
        if not record:
            continue
        if not separator or len(fields) != 3:
            raise ValueError("git ls-tree returned malformed source state")
        path = _decode_path(raw_path)
        if path not in selected:
            continue
        entries[path] = (
            fields[0].decode("ascii", errors="strict"),
            fields[2].decode("ascii", errors="strict"),
        )
    return _entry_states(root, entries)


def _index_states(
    root: Path,
    paths: list[str],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, PathState]:
    if not paths:
        return {}
    selected = set(paths)
    payload = _git(root, "ls-files", "--stage", "-z", env=env)
    entries: dict[str, tuple[str, str]] = {}
    for record in payload.split(b"\0"):
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split()
        if not record:
            continue
        if not separator or len(fields) != 3:
            raise ValueError("git index returned malformed source state")
        path = _decode_path(raw_path)
        if fields[2] == b"0" and path in selected:
            entries[path] = (
                fields[0].decode("ascii", errors="strict"),
                fields[1].decode("ascii", errors="strict"),
            )
    return _entry_states(root, entries, env=env)


def _entry_states(
    root: Path,
    entries: dict[str, tuple[str, str]],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, PathState]:
    blobs = read_git_blobs(
        root,
        sorted(
            {
                object_id
                for mode, object_id in entries.values()
                if mode != "160000" and set(object_id) != {"0"}
            }
        ),
        env=env,
    )
    states: dict[str, PathState] = {}
    for path, (mode, object_id) in entries.items():
        if set(object_id) == {"0"}:
            continue
        payload = object_id.encode("ascii") if mode == "160000" else blobs[object_id]
        states[path] = PathState(mode=mode, payload=payload)
    return states


def read_git_blobs(
    root: Path,
    object_ids: list[str],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, bytes]:
    if not object_ids:
        return {}
    request = b"".join(item.encode("ascii") + b"\n" for item in object_ids)
    result = _run_git(root, "cat-file", "--batch", env=env, input_bytes=request)
    payload = result.stdout
    offset = 0
    blobs: dict[str, bytes] = {}
    for expected in object_ids:
        line_end = payload.find(b"\n", offset)
        if line_end < 0:
            raise ValueError("git cat-file batch header is truncated")
        fields = payload[offset:line_end].split()
        if len(fields) != 3 or fields[0].decode("ascii") != expected:
            raise ValueError("git cat-file batch returned an invalid blob header")
        if fields[1] != b"blob" or not fields[2].isdigit():
            raise ValueError("git cat-file batch returned a non-blob object")
        size = int(fields[2])
        start = line_end + 1
        end = start + size
        if end >= len(payload) or payload[end : end + 1] != b"\n":
            raise ValueError("git cat-file batch blob is truncated")
        blobs[expected] = payload[start:end]
        offset = end + 1
    if offset != len(payload):
        raise ValueError("git cat-file batch returned trailing data")
    return blobs


def _unstaged_raw_entries(
    root: Path,
    git_config_args: tuple[str, ...],
) -> dict[str, _RawEntry]:
    payload = _git(
        root,
        *git_config_args,
        "diff",
        "--raw",
        "--no-abbrev",
        "-z",
        "--no-renames",
    )
    fields = payload.split(b"\0")
    entries: dict[str, _RawEntry] = {}
    index = 0
    while index < len(fields) and fields[index]:
        metadata = fields[index].split()
        if len(metadata) != 5 or not metadata[0].startswith(b":"):
            raise ValueError("git raw diff returned malformed source state")
        path = _decode_path(fields[index + 1])
        entries[path] = _RawEntry(
            before_mode=_mode(metadata[0][1:]),
            after_mode=_mode(metadata[1]),
        )
        index += 2
    return entries


def _worktree_state(root: Path, path: str, mode_hint: str) -> PathState:
    target = root / path
    if target.is_symlink():
        return PathState(mode="120000", payload=os.fsencode(os.readlink(target)))
    if mode_hint == "160000":
        return PathState(mode="160000", payload=_gitlink_payload(target))
    if not target.is_file():
        return PathState()
    mode = (
        mode_hint
        if mode_hint in {"100644", "100755"}
        else "100755"
        if target.stat(follow_symlinks=False).st_mode & 0o111
        else "100644"
    )
    return PathState(mode=mode, payload=target.read_bytes())


def _gitlink_payload(path: Path) -> bytes:
    object_id = _git(path, "rev-parse", "HEAD").strip()
    status = _git(
        path,
        "status",
        "--porcelain=v2",
        "-z",
        "--untracked-files=all",
    )
    if not status:
        return object_id
    raise ValueError(f"dirty gitlink is unsupported: {path}")


def _mode(payload: bytes) -> str:
    value = payload.decode("ascii", errors="strict")
    return "" if value == "000000" else value


def _decode_path(payload: bytes) -> str:
    return payload.decode("utf-8", errors="strict").replace("\\", "/")


def _git(
    root: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> bytes:
    result = _run_git(root, *args, env=env)
    return result.stdout


def _run_git(
    root: Path,
    *args: str,
    env: dict[str, str] | None = None,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            input=input_bytes,
            capture_output=True,
            check=False,
            env=env,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"git {' '.join(args)} timed out") from exc
    except OSError as exc:
        raise ValueError(f"git {' '.join(args)} is unavailable: {exc}") from exc
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"git {' '.join(args)} failed: {message}")
    return result


__all__ = [
    "CapturedPathChange",
    "PathState",
    "affected_paths",
    "capture_index_changes",
    "capture_path_changes",
    "read_git_blobs",
    "read_index_states",
    "read_tree_states",
]
