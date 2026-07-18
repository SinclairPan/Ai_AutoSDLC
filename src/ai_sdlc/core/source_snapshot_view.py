"""Read file content from the exact source view named by a SourceSnapshot."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ai_sdlc.core.source_snapshot import SourceSnapshot


def file_versions(
    root: Path, snapshot: SourceSnapshot, path: str
) -> tuple[bytes, bytes]:
    """Return before/after bytes from the frozen snapshot, including renames."""

    base_path = snapshot.renamed_files.get(path, path)
    if snapshot.source_kind == "local-git-range":
        return _revision_blob(root, snapshot.base_commit, base_path), _revision_blob(
            root, snapshot.head_commit, path
        )
    if snapshot.source_kind == "local-staged":
        return _revision_blob(root, "HEAD", base_path), _index_blob(root, path)
    if snapshot.source_kind == "local-unstaged":
        before = (
            b"" if path in snapshot.untracked_files else _index_blob(root, base_path)
        )
        return before, _worktree_blob(root, path)
    with _patched_index(root, snapshot) as env:
        return _revision_blob(root, snapshot.base_commit, base_path), _index_blob(
            root, path, env=env
        )


def python_sources(root: Path, snapshot: SourceSnapshot) -> dict[str, bytes]:
    """Return every Python file from the same frozen after-view as the metrics."""

    if snapshot.source_kind == "local-git-range":
        return _revision_python_sources(root, snapshot.head_commit)
    if snapshot.source_kind == "local-staged":
        return _index_python_sources(root)
    if snapshot.source_kind == "patch":
        with _patched_index(root, snapshot) as env:
            return _index_python_sources(root, env=env)
    paths = set(_nul_paths(_git(root, "ls-files", "-z")))
    paths.update(snapshot.untracked_files)
    return {
        path: _worktree_blob(root, path)
        for path in sorted(paths)
        if path.endswith(".py") and (root / path).is_file()
    }


def _revision_python_sources(root: Path, revision: str) -> dict[str, bytes]:
    paths = _nul_paths(_git(root, "ls-tree", "-r", "--name-only", "-z", revision))
    return {
        path: _revision_blob(root, revision, path)
        for path in paths
        if path.endswith(".py")
    }


def _index_python_sources(
    root: Path, *, env: dict[str, str] | None = None
) -> dict[str, bytes]:
    paths = _nul_paths(_git(root, "ls-files", "-z", env=env))
    return {
        path: _index_blob(root, path, env=env) for path in paths if path.endswith(".py")
    }


@contextmanager
def _patched_index(root: Path, snapshot: SourceSnapshot) -> Iterator[dict[str, str]]:
    if not snapshot.patch_file:
        raise ValueError("patch source has no patch_file")
    patch_path = (root / snapshot.patch_file).resolve()
    patch_path.relative_to(root.resolve())
    with _patch_index(root, patch_path, snapshot.base_commit) as env:
        yield env


def patch_name_status(root: Path, patch_file: str, base_commit: str) -> bytes:
    """Read rename-aware name status from the isolated patched index."""

    patch_path = (root / patch_file).resolve()
    patch_path.relative_to(root.resolve())
    with _patch_index(root, patch_path, base_commit) as env:
        return _git(root, "diff", "--cached", "--name-status", "-z", "-M", env=env)


@contextmanager
def _patch_index(
    root: Path, patch_path: Path, base_commit: str
) -> Iterator[dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="ai-sdlc-patch-") as directory:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
        _git(root, "read-tree", base_commit or "HEAD", env=env)
        _git(
            root,
            "apply",
            "--cached",
            "--binary",
            "--whitespace=nowarn",
            str(patch_path),
            env=env,
        )
        yield env


def _revision_blob(root: Path, revision: str, path: str) -> bytes:
    return _optional_git(root, "show", f"{revision}:{path}")


def _index_blob(root: Path, path: str, *, env: dict[str, str] | None = None) -> bytes:
    return _optional_git(root, "show", f":{path}", env=env)


def _worktree_blob(root: Path, path: str) -> bytes:
    target = root / path
    return target.read_bytes() if target.is_file() else b""


def _optional_git(root: Path, *args: str, env: dict[str, str] | None = None) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, check=False, env=env
    )
    return result.stdout if result.returncode == 0 else b""


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, check=False, env=env
    )
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"git {' '.join(args)} failed: {message}")
    return result.stdout


def _nul_paths(payload: bytes) -> list[str]:
    return [
        item.decode("utf-8", errors="strict").replace("\\", "/")
        for item in payload.split(b"\0")
        if item
    ]


__all__ = ["file_versions", "patch_name_status", "python_sources"]
