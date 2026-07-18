"""统一 source snapshot 的确定性与 freshness 测试。"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

import ai_sdlc.core.source_snapshot_view as source_snapshot_view
from ai_sdlc.core.lean_code_execution import _snapshot_file_digest
from ai_sdlc.core.source_snapshot import (
    SourceSnapshotOptions,
    _binary_files,
    _parse_binary_numstat,
    _SnapshotParts,
    build_source_snapshot,
    revalidate_source_snapshot,
)
from ai_sdlc.core.source_snapshot_view import (
    file_versions,
    materialized_source_view,
    python_sources,
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


def test_staged_snapshot_marks_only_binary_entry_in_mixed_diff(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write(tmp_path, "src/app.py", "print('ordinary source')\n")
    _write_bytes(tmp_path, "assets/payload.bin", b"\x00\x01\x02")
    _git(tmp_path, "add", "src/app.py", "assets/payload.bin")

    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-staged")
    )

    assert snapshot.changed_files == ["assets/payload.bin", "src/app.py"]
    assert snapshot.binary_files == ["assets/payload.bin"]


def test_staged_snapshot_reads_diff_attributes_from_selected_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _init_repo(tmp_path)
    _write(tmp_path, ".gitattributes", "*.dat -diff\n")
    _write(tmp_path, "assets/payload.dat", "opaque but textual bytes\n")
    _write(tmp_path, "src/app.py", "print('ordinary source')\n")
    _git(tmp_path, "add", ".gitattributes", "assets/payload.dat", "src/app.py")
    _write(tmp_path, ".gitattributes", "# live worktree override\n")
    _write(tmp_path, ".git/info/attributes", "*.dat diff\n")
    _write(tmp_path, "live.attributes", "*.dat diff\n")
    _git(
        tmp_path,
        "config",
        "core.attributesFile",
        str(tmp_path / "live.attributes"),
    )
    template = tmp_path / "hostile-template"
    _write(template, "info/attributes", "*.dat diff\n")
    monkeypatch.setenv("GIT_ATTR_SOURCE", base)
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(template))

    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-staged")
    )

    assert snapshot.binary_files == ["assets/payload.dat"]
    _write(tmp_path, ".gitattributes", "# second live override\n")
    assert revalidate_source_snapshot(tmp_path, snapshot).fresh is True


def test_staged_snapshot_materializes_attributes_without_host_smudge(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    _write(tmp_path, ".gitattributes", "*.dat -diff\n")
    _write(tmp_path, "assets/payload.dat", "opaque but textual bytes\n")
    _git(tmp_path, "add", ".gitattributes", "assets/payload.dat")
    filter_script = tmp_path / "host-filter.py"
    filter_script.write_text(
        "import sys\n"
        "sys.stdout.buffer.write(sys.stdin.buffer.read().replace(b'-diff', b'diff'))\n",
        encoding="utf-8",
    )
    _write(tmp_path, ".git/info/attributes", ".gitattributes filter=evil\n")
    _git(
        tmp_path,
        "config",
        "filter.evil.smudge",
        f'"{sys.executable}" "{filter_script}"',
    )
    _git(tmp_path, "config", "filter.evil.required", "true")

    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-staged")
    )

    assert snapshot.binary_files == ["assets/payload.dat"]


def test_materialized_staged_view_preserves_selected_bytes_without_host_smudge(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    selected = "def test_selected_source():\n    assert 'SELECTED'\n"
    _write(tmp_path, "tests/test_selected.py", selected)
    _git(tmp_path, "add", "tests/test_selected.py")
    filter_script = tmp_path / "host-filter.py"
    filter_script.write_text(
        "import sys\n"
        "sys.stdout.buffer.write("
        "sys.stdin.buffer.read().replace(b'SELECTED', b'HOST_SMUDGED'))\n",
        encoding="utf-8",
    )
    _write(
        tmp_path,
        ".git/info/attributes",
        "tests/test_selected.py filter=evil\n",
    )
    _git(
        tmp_path,
        "config",
        "filter.evil.smudge",
        f'"{sys.executable}" "{filter_script}"',
    )
    _git(tmp_path, "config", "filter.evil.required", "true")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-staged")
    )

    with materialized_source_view(tmp_path, snapshot) as source_view:
        actual = (source_view / "tests/test_selected.py").read_text(encoding="utf-8")

    assert actual == selected


@pytest.mark.parametrize("source_kind", ["local-staged", "local-git-range", "patch"])
def test_materialized_selected_view_preserves_blob_bytes_despite_eol_attributes(
    tmp_path: Path,
    source_kind: str,
) -> None:
    base = _init_repo(tmp_path)
    _write(tmp_path, ".gitattributes", "*.py text eol=crlf\n")
    _write(tmp_path, "src/app.py", "VALUE = 1\nVALUE = 2\n")
    _git(tmp_path, "add", ".gitattributes", "src/app.py")
    if source_kind == "local-staged":
        options = SourceSnapshotOptions(root=tmp_path, source_kind=source_kind)
    elif source_kind == "local-git-range":
        _git(tmp_path, "commit", "-m", "add selected source")
        head = _git(tmp_path, "rev-parse", "HEAD")
        options = SourceSnapshotOptions(
            root=tmp_path,
            source_kind=source_kind,
            base_ref=base,
            head_ref=head,
        )
    else:
        patch = _git(tmp_path, "diff", "--cached", "--binary", "--no-ext-diff")
        _write(tmp_path, "change.patch", patch + "\n")
        options = SourceSnapshotOptions(
            root=tmp_path,
            source_kind=source_kind,
            patch_file="change.patch",
        )
    snapshot = build_source_snapshot(options)
    selected_bytes = file_versions(tmp_path, snapshot, "src/app.py")[1]

    with materialized_source_view(tmp_path, snapshot) as source_view:
        actual = (source_view / "src/app.py").read_bytes()

    assert actual == selected_bytes


def test_materialized_view_reads_regular_blobs_with_one_git_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repo(tmp_path)
    for number in range(8):
        _write(tmp_path, f"src/module_{number}.py", f"VALUE = {number}\n")
    _git(tmp_path, "add", "src")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-staged")
    )
    real_popen = source_snapshot_view.subprocess.Popen
    cat_file_processes = 0

    def counted_popen(*args: object, **kwargs: object):
        nonlocal cat_file_processes
        command = args[0] if args else kwargs.get("args")
        if (
            isinstance(command, (list, tuple))
            and len(command) >= 2
            and command[:2] == ["git", "cat-file"]
        ):
            cat_file_processes += 1
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(source_snapshot_view.subprocess, "Popen", counted_popen)

    with materialized_source_view(tmp_path, snapshot) as source_view:
        assert (source_view / "src/module_7.py").read_bytes() == b"VALUE = 7\n"

    assert cat_file_processes == 1


def test_materialized_staged_view_rejects_index_changed_after_snapshot(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    _write(tmp_path, "tests/test_selected.py", "print('SNAPSHOT')\n")
    _git(tmp_path, "add", "tests/test_selected.py")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-staged")
    )
    _write(tmp_path, "tests/test_selected.py", "print('MUTATED_AFTER_FRESHNESS')\n")
    _git(tmp_path, "add", "tests/test_selected.py")

    with (
        pytest.raises(ValueError, match="index identity"),
        materialized_source_view(tmp_path, snapshot),
    ):
        pass


def test_snapshot_file_digest_uses_persisted_snapshot_not_live_index(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    reference = "tests/test_selected.py"
    _write(tmp_path, reference, "print('SNAPSHOT')\n")
    _git(tmp_path, "add", reference)
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-staged")
    )
    expected = snapshot.file_digests[reference]
    _write(tmp_path, reference, "print('MUTATED_AFTER_FRESHNESS')\n")
    _git(tmp_path, "add", reference)

    assert _snapshot_file_digest(tmp_path, snapshot, reference) == expected


def test_materialized_unstaged_view_rejects_changed_bytes_after_snapshot(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    reference = "tests/test_selected.py"
    _write(tmp_path, reference, "print('ORIGINAL_UNSTAGED')\n")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )
    _write(tmp_path, reference, "print('MUTATED_UNSTAGED')\n")

    with (
        pytest.raises(ValueError, match="unstaged source identity"),
        materialized_source_view(tmp_path, snapshot),
    ):
        pass


def test_materialized_patch_view_rejects_changed_patch_after_snapshot(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    patch_path = tmp_path / "change.patch"
    patch_path.write_text(_new_file_patch("ORIGINAL_PATCH"), encoding="utf-8")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(
            root=tmp_path,
            source_kind="patch",
            patch_file="change.patch",
        )
    )
    patch_path.write_text(_new_file_patch("MUTATED_PATCH"), encoding="utf-8")

    with (
        pytest.raises(ValueError, match="patch identity"),
        materialized_source_view(tmp_path, snapshot),
    ):
        pass


def test_unstaged_snapshot_detects_untracked_symlink_retarget_with_same_bytes(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    _write(tmp_path, "outside_a.txt", "same bytes\n")
    _write(tmp_path, "outside_b.txt", "same bytes\n")
    link = tmp_path / "data" / "link"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to("../outside_a.txt")
    except OSError:
        pytest.skip("symlinks are unavailable in this environment")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )
    link.unlink()
    link.symlink_to("../outside_b.txt")

    freshness = revalidate_source_snapshot(tmp_path, snapshot)

    assert freshness.fresh is False
    assert freshness.reason == "diff_hash_changed"


def test_unstaged_symlink_digest_and_python_source_use_link_target_bytes(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    _write(tmp_path, ".git/info/exclude", "outside.py\n")
    _write(tmp_path, "outside.py", "print('ONE')\n")
    link = tmp_path / "data" / "link.py"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to("../outside.py")
    except OSError:
        pytest.skip("symlinks are unavailable in this environment")
    first = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )
    expected_bytes = b"../outside.py"
    expected_digest = "sha256:" + hashlib.sha256(expected_bytes).hexdigest()

    assert first.file_digests["data/link.py"] == expected_digest
    assert python_sources(tmp_path, first)["data/link.py"] == expected_bytes

    _write(tmp_path, "outside.py", "print('TWO')\n")
    second = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )

    assert second.diff_hash == first.diff_hash
    assert second.file_digests["data/link.py"] == expected_digest


def test_staged_snapshot_ignores_default_user_global_attributes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repo(tmp_path)
    _write(tmp_path, "assets/payload.dat", "ordinary source text\n")
    _git(tmp_path, "add", "assets/payload.dat")
    xdg_home = tmp_path / "host-xdg"
    _write(xdg_home, "git/attributes", "*.dat -diff\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_home))

    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-staged")
    )

    assert snapshot.binary_files == []


def test_git_range_snapshot_reads_diff_attributes_from_selected_head(
    tmp_path: Path,
) -> None:
    base = _init_repo(tmp_path)
    _write(tmp_path, ".gitattributes", "*.dat -diff\n")
    _write(tmp_path, "assets/payload.dat", "opaque but textual bytes\n")
    _write(tmp_path, "src/app.py", "print('ordinary source')\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "add selected attributes")
    head = _git(tmp_path, "rev-parse", "HEAD")
    _write(tmp_path, ".gitattributes", "# live worktree override\n")
    _write(tmp_path, ".git/info/attributes", "*.dat diff\n")

    snapshot = build_source_snapshot(
        SourceSnapshotOptions(
            root=tmp_path,
            source_kind="local-git-range",
            base_ref=base,
            head_ref=head,
        )
    )

    assert snapshot.binary_files == ["assets/payload.dat"]
    _write(tmp_path, ".gitattributes", "# second live override\n")
    assert revalidate_source_snapshot(tmp_path, snapshot).fresh is True


def test_patch_snapshot_reads_diff_attributes_from_selected_patch(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    _write(tmp_path, ".gitattributes", "*.dat -diff\n")
    _write(tmp_path, "assets/payload.dat", "opaque but textual bytes\n")
    _write(tmp_path, "src/app.py", "print('ordinary source')\n")
    _git(tmp_path, "add", ".gitattributes", "assets/payload.dat", "src/app.py")
    patch = _git(tmp_path, "diff", "--cached", "--binary", "--no-ext-diff")
    _write(tmp_path, "change.patch", patch + "\n")
    _write(tmp_path, ".gitattributes", "# live worktree override\n")
    _write(tmp_path, ".git/info/attributes", "*.dat diff\n")

    snapshot = build_source_snapshot(
        SourceSnapshotOptions(
            root=tmp_path,
            source_kind="patch",
            patch_file="change.patch",
        )
    )

    assert snapshot.binary_files == ["assets/payload.dat"]
    _write(tmp_path, ".gitattributes", "# second live override\n")
    assert revalidate_source_snapshot(tmp_path, snapshot).fresh is True


def test_patch_snapshot_status_uses_explicit_non_head_base(tmp_path: Path) -> None:
    base = _init_repo(tmp_path)
    _write(
        tmp_path,
        "change.patch",
        """diff --git a/src/from-patch.py b/src/from-patch.py
new file mode 100644
--- /dev/null
+++ b/src/from-patch.py
@@ -0,0 +1 @@
+VALUE = 1
""",
    )
    _write(tmp_path, "unrelated.txt", "newer HEAD content\n")
    _git(tmp_path, "add", "unrelated.txt")
    _git(tmp_path, "commit", "-m", "advance current HEAD")

    snapshot = build_source_snapshot(
        SourceSnapshotOptions(
            root=tmp_path,
            source_kind="patch",
            head_ref=base,
            patch_file="change.patch",
        )
    )

    assert snapshot.changed_files == ["src/from-patch.py"]


def test_staged_snapshot_supports_sha256_repository(tmp_path: Path) -> None:
    initialized = subprocess.run(
        [
            "git",
            "init",
            "--quiet",
            "--initial-branch=main",
            "--object-format=sha256",
        ],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    if initialized.returncode:
        pytest.skip("installed Git does not support SHA-256 repositories")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    _write(tmp_path, "README.md", "# SHA-256 Test\n")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "initial")
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    _git(tmp_path, "add", "src/app.py")

    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-staged")
    )

    assert snapshot.changed_files == ["src/app.py"]


def test_staged_sha1_snapshot_ignores_hostile_default_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repo(tmp_path)
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    _git(tmp_path, "add", "src/app.py")
    monkeypatch.setenv("GIT_DEFAULT_HASH", "sha256")

    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-staged")
    )

    assert snapshot.changed_files == ["src/app.py"]


@pytest.mark.parametrize(
    "payload",
    [
        b"1\t0\ta.py\0\0-\t-\tb.bin\0",
        b"-\t-\tb.bin",
        b"x\t0\ta.py\0",
        b"-\t0\tb.bin\0",
    ],
)
def test_binary_numstat_parser_rejects_malformed_payload(payload: bytes) -> None:
    with pytest.raises(ValueError):
        _parse_binary_numstat(payload)


def test_binary_numstat_paths_must_exactly_match_name_status(tmp_path: Path) -> None:
    parts = _SnapshotParts(
        diff_bytes=b"",
        status_bytes=b"",
        numstat_bytes=b"1\t0\ta.py\0",
        base_ref="HEAD",
        head_ref="INDEX",
        base_commit="a",
        head_commit="a",
    )

    with pytest.raises(ValueError, match="numstat paths do not match"):
        _binary_files(tmp_path, parts, [("A", "a.py", ""), ("A", "b.py", "")])


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


def _new_file_patch(marker: str) -> str:
    return (
        "diff --git a/tests/test_selected.py b/tests/test_selected.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/tests/test_selected.py\n"
        "@@ -0,0 +1 @@\n"
        f"+print('{marker}')\n"
    )


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
