"""统一 source snapshot 的确定性与 freshness 测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_sdlc.core.source_snapshot import (
    SourceSnapshotOptions,
    build_source_snapshot,
    revalidate_source_snapshot,
)


def test_unstaged_snapshot_includes_untracked_unicode_file(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    target = tmp_path / "src" / "精简 评估.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('first')\n", encoding="utf-8")

    first = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )

    assert first.changed_files == ["src/精简 评估.py"]
    assert first.untracked_files == ["src/精简 评估.py"]
    assert first.diff_hash.startswith("sha256:")
    assert revalidate_source_snapshot(tmp_path, first).fresh is True

    target.write_text("print('second')\n", encoding="utf-8")
    freshness = revalidate_source_snapshot(tmp_path, first)
    assert freshness.fresh is False
    assert freshness.reason == "diff_hash_changed"


def test_unstaged_snapshot_excludes_interpreter_cache_artifacts(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write(tmp_path, "src/订单.py", "print('base')\n")
    _git(tmp_path, "add", "src/订单.py")
    _git(tmp_path, "commit", "-m", "add source")
    _write(tmp_path, "src/订单.py", "print('changed')\n")
    _write_bytes(tmp_path, "src/__pycache__/订单.cpython-311.pyc", b"\x00cache")
    _write(tmp_path, ".pytest_cache/v/cache/nodeids", "[]\n")
    _write(tmp_path, ".ruff_cache/index", "cache\n")
    _write(tmp_path, "htmlcov/index.html", "coverage\n")
    _write(tmp_path, "build/temp.txt", "build\n")

    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )

    assert snapshot.changed_files == ["src/订单.py"]
    assert snapshot.untracked_files == []


def test_staged_snapshot_changes_when_index_changes(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('one')\n", encoding="utf-8")
    _git(tmp_path, "add", "src/app.py")
    first = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-staged")
    )

    target.write_text("print('two')\n", encoding="utf-8")
    _git(tmp_path, "add", "src/app.py")
    second = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-staged")
    )

    assert first.index_identity != second.index_identity
    assert first.diff_hash != second.diff_hash
    assert revalidate_source_snapshot(tmp_path, first).fresh is False


def test_unstaged_snapshot_becomes_stale_when_only_index_changes(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    _write(tmp_path, "src/app.py", "print('base')\n")
    _write(tmp_path, "src/staged.py", "print('base')\n")
    _git(tmp_path, "add", "src/app.py", "src/staged.py")
    _git(tmp_path, "commit", "-m", "add sources")
    _write(tmp_path, "src/app.py", "print('unstaged')\n")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )

    _write(tmp_path, "src/staged.py", "print('staged')\n")
    _git(tmp_path, "add", "src/staged.py")
    freshness = revalidate_source_snapshot(tmp_path, snapshot)

    assert freshness.fresh is False
    assert freshness.reason == "index_identity_changed"


@pytest.mark.parametrize("index_flag", ["--assume-unchanged", "--skip-worktree"])
def test_unstaged_snapshot_becomes_stale_when_index_flags_change(
    tmp_path: Path,
    index_flag: str,
) -> None:
    _init_repo(tmp_path)
    _write(tmp_path, "src/app.py", "print('base')\n")
    _write(tmp_path, "src/hidden.py", "print('base')\n")
    _git(tmp_path, "add", "src/app.py", "src/hidden.py")
    _git(tmp_path, "commit", "-m", "add sources")
    _write(tmp_path, "src/app.py", "print('unstaged')\n")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )

    _git(tmp_path, "update-index", index_flag, "src/hidden.py")
    _write(tmp_path, "src/hidden.py", "print('hidden change')\n")
    freshness = revalidate_source_snapshot(tmp_path, snapshot)

    assert freshness.fresh is False
    assert freshness.reason == "index_identity_changed"


def test_git_range_snapshot_records_rename_delete_and_binary(tmp_path: Path) -> None:
    base = _init_repo(tmp_path)
    _write(tmp_path, "old name.py", "print('old')\n")
    _write_bytes(tmp_path, "asset.bin", b"\x00\x01")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "add files")
    base = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "mv", "old name.py", "新 名称.py")
    (tmp_path / "asset.bin").unlink()
    _write_bytes(tmp_path, "new.bin", b"\x00\x02")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "rename and binary")

    snapshot = build_source_snapshot(
        SourceSnapshotOptions(
            root=tmp_path,
            source_kind="local-git-range",
            base_ref=base,
            head_ref="HEAD",
        )
    )

    assert "新 名称.py" in snapshot.changed_files
    assert "asset.bin" in snapshot.deleted_files
    assert "new.bin" in snapshot.binary_files
    assert snapshot.renamed_files == {"新 名称.py": "old name.py"}


def test_patch_snapshot_hashes_raw_bytes_and_detects_tamper(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    patch = tmp_path / "change.patch"
    patch.write_bytes(
        b"diff --git a/src/app.py b/src/app.py\n"
        b"new file mode 100644\n"
        b"--- /dev/null\n+++ b/src/app.py\n@@ -0,0 +1 @@\n+print('x')\n"
    )
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(
            root=tmp_path,
            source_kind="patch",
            patch_file="change.patch",
        )
    )

    assert snapshot.changed_files == ["src/app.py"]
    assert revalidate_source_snapshot(tmp_path, snapshot).fresh is True
    patch.write_bytes(patch.read_bytes() + b"\n")
    assert revalidate_source_snapshot(tmp_path, snapshot).fresh is False


def _init_repo(root: Path) -> str:
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    _write(root, "README.md", "# Test\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initial")
    return _git(root, "rev-parse", "HEAD")


def _write(root: Path, relative: str, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _write_bytes(root: Path, relative: str, content: bytes) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    if result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", errors="replace"))
    return result.stdout.decode("utf-8", errors="strict").strip()
