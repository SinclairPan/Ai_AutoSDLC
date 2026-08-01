from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from ai_sdlc.core.stage_review.optimization import (
    snapshot_trusted_files,
    snapshot_windows_io,
    snapshot_windows_relative,
)
from ai_sdlc.core.stage_review.optimization.snapshot_trust_anchor import (
    SnapshotControlTrustAnchor,
)


def test_windows_leaf_operations_use_the_verified_directory_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[tuple[int, str, int]] = []
    hardened: list[int] = []
    closed: list[int] = []

    @contextmanager
    def open_directory(_: Path) -> Iterator[int]:
        yield 41

    def open_relative(
        directory_handle: int,
        name: str,
        *,
        share_mode: int = 0x7,
        **_: object,
    ) -> int:
        opened.append((directory_handle, name, share_mode))
        return 80 + len(opened)

    monkeypatch.setattr(
        snapshot_windows_io,
        "_open_registered_directory",
        open_directory,
    )
    monkeypatch.setattr(
        snapshot_windows_io,
        "_open_windows_relative",
        open_relative,
        raising=False,
    )
    monkeypatch.setattr(
        snapshot_windows_io,
        "_open_windows_path",
        lambda *_args, **_kwargs: pytest.fail("leaf opened by absolute path"),
    )
    monkeypatch.setattr(snapshot_windows_io, "_verify_regular_file", lambda _: None)
    monkeypatch.setattr(
        snapshot_windows_io,
        "_verify_registered_directory",
        lambda _: None,
    )
    monkeypatch.setattr(
        snapshot_windows_io,
        "_harden_windows_handle_acl",
        lambda handle, **_: hardened.append(handle),
        raising=False,
    )
    monkeypatch.setattr(snapshot_windows_io, "_read_handle", lambda _: b"trusted")
    monkeypatch.setattr(snapshot_windows_io, "_write_handle", lambda *_: None)
    monkeypatch.setattr(snapshot_windows_io, "_rename_handle", lambda *_a, **_k: None)
    monkeypatch.setattr(
        snapshot_windows_io, "_mark_handle_for_deletion", lambda _: None
    )
    monkeypatch.setattr(
        snapshot_windows_io, "_acquire_windows_lock", lambda *_: object()
    )
    monkeypatch.setattr(snapshot_windows_io, "_release_windows_lock", lambda *_: None)
    monkeypatch.setattr(snapshot_windows_io, "_close_windows_handle", closed.append)

    assert snapshot_windows_io._windows_secure_read(tmp_path, "read.json") == b"trusted"
    snapshot_windows_io._windows_secure_publish(
        tmp_path,
        "created.json",
        ".create.tmp",
        b"created",
        replace=False,
    )
    snapshot_windows_io._windows_secure_publish(
        tmp_path,
        "replaced.json",
        ".replace.tmp",
        b"replaced",
        replace=True,
    )
    snapshot_windows_io._windows_secure_unlink(tmp_path, "removed.json")
    with snapshot_windows_io._windows_secure_lock(
        tmp_path,
        ".trusted.lock",
        timeout_seconds=0.1,
    ):
        pass

    assert opened == [
        (41, "read.json", 0x7),
        (41, ".create.tmp", 0x7),
        (41, ".replace.tmp", 0x7),
        (41, "removed.json", 0x7),
        (41, ".trusted.lock", 0x3),
    ]
    assert hardened == [81, 82, 83, 85]
    assert closed == [81, 82, 83, 84, 85]


def test_windows_directory_preparation_uses_relative_handles_and_handle_acl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = tmp_path / "ai-sdlc-trusted-state"
    target = boundary / "projects" / "project.shared" / "snapshot-control"
    opened: list[tuple[int, str, bool, bool]] = []
    hardened: list[tuple[int, bool]] = []
    closed: list[int] = []
    registered: list[tuple[Path, tuple[tuple[Path, tuple[int, int]], ...]]] = []

    def open_relative_directory(
        parent_handle: int,
        name: str,
        *,
        create: bool,
        harden: bool,
    ) -> int:
        opened.append((parent_handle, name, create, harden))
        return 100 + len(opened)

    monkeypatch.setattr(
        snapshot_trusted_files,
        "_create_windows_directory",
        lambda _: pytest.fail("absolute path directory creation used"),
        raising=False,
    )
    monkeypatch.setattr(
        snapshot_trusted_files,
        "_harden_windows_acl",
        lambda _: pytest.fail("path-based directory ACL used"),
        raising=False,
    )
    monkeypatch.setattr(
        snapshot_trusted_files,
        "_open_windows_path",
        lambda *_args, **_kwargs: 40,
        raising=False,
    )
    monkeypatch.setattr(
        snapshot_trusted_files,
        "_open_windows_relative_directory",
        open_relative_directory,
        raising=False,
    )
    monkeypatch.setattr(
        snapshot_trusted_files,
        "_windows_handle_metadata",
        lambda handle: ((7, handle), 0x10),
        raising=False,
    )
    monkeypatch.setattr(
        snapshot_trusted_files,
        "_harden_windows_handle_acl",
        lambda handle, *, is_directory: hardened.append((handle, is_directory)),
        raising=False,
    )
    monkeypatch.setattr(
        snapshot_trusted_files,
        "_close_windows_handle",
        closed.append,
        raising=False,
    )
    monkeypatch.setattr(
        snapshot_trusted_files,
        "_verify_windows_identities",
        lambda _: None,
    )
    monkeypatch.setattr(
        snapshot_trusted_files,
        "_register_windows_directory",
        lambda path, identities: registered.append((path, identities)),
    )

    assert snapshot_trusted_files._prepare_windows_directory(target) == target
    names = list(target.absolute().parts[1:])
    creation_start = snapshot_trusted_files._trusted_creation_start(boundary)
    expected_handles = [101 + index for index in range(len(names))]
    assert opened == [
        (
            40 if index == 0 else expected_handles[index - 1],
            name,
            index + 1 >= creation_start,
            index + 1 >= len(boundary.absolute().parts) - 1,
        )
        for index, name in enumerate(names)
    ]
    trusted_start = len(boundary.absolute().parts) - 1
    assert hardened == [
        (expected_handles[index], True)
        for index in range(trusted_start - 1, len(names))
    ]
    assert closed == [40, *expected_handles]
    assert registered == [
        (
            target,
            tuple(
                (
                    Path(target.absolute().anchor, *names[: index + 1]),
                    (7, expected_handles[index]),
                )
                for index in range(trusted_start - 1, len(names))
            ),
        )
    ]


def test_windows_relative_directory_creation_is_native_and_metachar_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    @contextmanager
    def security_descriptor(*, is_directory: bool) -> Iterator[int]:
        captured["is_directory"] = is_directory
        yield 0x1234

    class NtCreateFile:
        def __call__(self, *arguments: object) -> int:
            handle = ctypes.cast(
                arguments[0],
                ctypes.POINTER(snapshot_windows_relative.wintypes.HANDLE),
            )
            handle.contents.value = 93
            attributes = ctypes.cast(
                arguments[2],
                ctypes.POINTER(snapshot_windows_relative._ObjectAttributes),
            ).contents
            unicode_name = attributes.object_name.contents
            captured["root"] = int(attributes.root_directory)
            captured["name"] = ctypes.wstring_at(unicode_name.buffer)
            captured["descriptor"] = attributes.security_descriptor
            captured["file_attributes"] = arguments[5]
            captured["share_mode"] = arguments[6]
            captured["disposition"] = arguments[7]
            captured["options"] = arguments[8]
            return 0

    monkeypatch.setattr(
        snapshot_windows_relative,
        "_current_user_security_descriptor",
        security_descriptor,
        raising=False,
    )
    monkeypatch.setattr(
        snapshot_windows_relative,
        "_nt_create_file",
        lambda: NtCreateFile(),
    )
    name = "project;Write-Error injected#.shared"

    handle = snapshot_windows_relative._open_windows_relative_directory(
        41,
        name,
        create=True,
        harden=True,
    )

    assert handle == 93
    assert captured == {
        "is_directory": True,
        "root": 41,
        "name": name,
        "descriptor": 0x1234,
        "file_attributes": 0x10,
        "share_mode": 0x3,
        "disposition": 3,
        "options": 0x1 | 0x20 | 0x00200000,
    }


def test_windows_untrusted_ancestor_open_does_not_request_acl_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class NtCreateFile:
        def __call__(self, *arguments: object) -> int:
            handle = ctypes.cast(
                arguments[0],
                ctypes.POINTER(snapshot_windows_relative.wintypes.HANDLE),
            )
            handle.contents.value = 94
            attributes = ctypes.cast(
                arguments[2],
                ctypes.POINTER(snapshot_windows_relative._ObjectAttributes),
            ).contents
            captured["descriptor"] = attributes.security_descriptor
            captured["access"] = arguments[1]
            return 0

    monkeypatch.setattr(
        snapshot_windows_relative,
        "_nt_create_file",
        lambda: NtCreateFile(),
    )

    handle = snapshot_windows_relative._open_windows_relative_directory(
        41,
        "Users",
        create=False,
        harden=False,
    )

    assert handle == 94
    assert captured == {
        "descriptor": None,
        "access": 0x20 | 0x00100000,
    }


def test_windows_created_leaf_passes_current_user_security_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    @contextmanager
    def security_descriptor(*, is_directory: bool) -> Iterator[int]:
        captured["is_directory"] = is_directory
        yield 0x5678

    class NtCreateFile:
        def __call__(self, *arguments: object) -> int:
            handle = ctypes.cast(
                arguments[0],
                ctypes.POINTER(snapshot_windows_relative.wintypes.HANDLE),
            )
            handle.contents.value = 91
            attributes = ctypes.cast(
                arguments[2],
                ctypes.POINTER(snapshot_windows_relative._ObjectAttributes),
            ).contents
            captured["descriptor"] = attributes.security_descriptor
            return 0

    monkeypatch.setattr(
        snapshot_windows_relative,
        "_current_user_security_descriptor",
        security_descriptor,
        raising=False,
    )
    monkeypatch.setattr(
        snapshot_windows_relative,
        "_nt_create_file",
        lambda: NtCreateFile(),
    )

    handle = snapshot_windows_relative._open_windows_relative(
        41,
        ".created.tmp",
        desired_access=0x40000000,
        creation_disposition=1,
    )

    assert handle == 91
    assert captured == {"is_directory": False, "descriptor": 0x5678}


if os.name == "nt":

    def _junction(link: Path, target: Path) -> None:
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr

    def _remove_junction(path: Path) -> None:
        completed = subprocess.run(
            ["cmd", "/c", "rmdir", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr

    def _assert_current_user_owner(path: Path) -> None:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$user=[System.Security.Principal.WindowsIdentity]::GetCurrent().User; "
                "$owner=(Get-Acl -LiteralPath $args[0]).Owner; "
                "$ownerSid=([System.Security.Principal.NTAccount]$owner).Translate("
                "[System.Security.Principal.SecurityIdentifier]); "
                "if ($ownerSid.Value -ne $user.Value) { exit 17 }",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr

    def test_windows_lock_prevents_named_replacement_while_held(
        tmp_path: Path,
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        anchor = SnapshotControlTrustAnchor(root, project_id="project.shared")
        name = ".replacement.lock"
        lock_path = anchor.root / name
        moved_path = anchor.root / ".replacement.moved"

        with snapshot_trusted_files._secure_file_lock(
            anchor.root,
            name,
            timeout_seconds=0.1,
        ), pytest.raises(OSError):
            os.replace(lock_path, moved_path)

        assert lock_path.is_file()
        os.replace(lock_path, moved_path)
        os.replace(moved_path, lock_path)

    def test_windows_created_trusted_objects_use_current_user_owner(
        tmp_path: Path,
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        anchor = SnapshotControlTrustAnchor(root, project_id="project.shared")

        _assert_current_user_owner(anchor.root)
        _assert_current_user_owner(
            anchor.epoch_store.root / "snapshot-project-anchor.key"
        )

    def test_windows_metachar_project_path_never_invokes_a_shell(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = tmp_path / "project;Write-Error injected#.shared"
        root.mkdir()
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *_args, **_kwargs: pytest.fail("trusted setup invoked a shell"),
        )

        anchor = SnapshotControlTrustAnchor(root, project_id="project.shared")

        assert anchor.root.is_dir()

    def test_windows_directory_bootstrap_ignores_transient_parent_junction(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        project = tmp_path / "project"
        project.mkdir()
        external = tmp_path / "external"
        external.mkdir()
        original_project = tmp_path / "project-original"
        target = (
            project
            / ".ai-sdlc"
            / "state"
            / "trusted"
            / "projects"
            / "project.shared"
        )
        original_open = snapshot_trusted_files._open_windows_relative_directory
        attempted = False
        switched = False
        switch_blocked = False

        def switch_around_create(
            parent_handle: int,
            name: str,
            *,
            create: bool,
            harden: bool,
        ) -> int:
            nonlocal attempted, switch_blocked, switched
            if name != ".ai-sdlc" or attempted:
                return original_open(
                    parent_handle,
                    name,
                    create=create,
                    harden=harden,
                )
            attempted = True
            try:
                project.rename(original_project)
            except OSError:
                switch_blocked = True
                return original_open(
                    parent_handle,
                    name,
                    create=create,
                    harden=harden,
                )
            _junction(project, external)
            try:
                handle = original_open(
                    parent_handle,
                    name,
                    create=create,
                    harden=harden,
                )
            finally:
                _remove_junction(project)
                original_project.rename(project)
            switched = True
            return handle

        monkeypatch.setattr(
            snapshot_trusted_files,
            "_open_windows_relative_directory",
            switch_around_create,
        )

        assert snapshot_trusted_files._prepare_windows_directory(target) == target
        assert attempted
        assert switch_blocked or switched
        assert target.is_dir()
        assert not (external / ".ai-sdlc").exists()

    def test_windows_leaf_operations_ignore_transient_parent_junction(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        anchor = SnapshotControlTrustAnchor(root, project_id="project.shared")
        for name, payload in (
            ("read.json", b"trusted"),
            ("replaced.json", b"old"),
            ("removed.json", b"remove"),
        ):
            snapshot_trusted_files._create_secure_file(anchor.root, name, payload)
        projects = anchor.root.parent.parent
        external = tmp_path / "external"
        shutil.copytree(projects, external)
        original_projects = projects.with_name("projects-original")
        external_root = external / anchor.root.relative_to(projects)
        original_open = snapshot_windows_io._open_windows_relative
        opened = 0

        def switch_around_open(
            directory_handle: int,
            name: str,
            **kwargs: object,
        ) -> int:
            nonlocal opened
            projects.rename(original_projects)
            _junction(projects, external)
            try:
                handle = original_open(directory_handle, name, **kwargs)
            finally:
                _remove_junction(projects)
                original_projects.rename(projects)
            opened += 1
            return handle

        monkeypatch.setattr(
            snapshot_windows_io,
            "_open_windows_relative",
            switch_around_open,
        )

        assert (
            snapshot_trusted_files._read_secure_file(anchor.root, "read.json")
            == b"trusted"
        )
        snapshot_trusted_files._create_secure_file(
            anchor.root,
            "created.json",
            b"created",
        )
        snapshot_trusted_files._replace_secure_file(
            anchor.root,
            "replaced.json",
            b"new",
        )
        snapshot_trusted_files._unlink_secure_file(anchor.root, "removed.json")
        with snapshot_trusted_files._secure_file_lock(
            anchor.root,
            ".transient.lock",
            timeout_seconds=0.1,
        ):
            pass

        assert opened == 5
        assert (anchor.root / "created.json").read_bytes() == b"created"
        assert (anchor.root / "replaced.json").read_bytes() == b"new"
        assert not (anchor.root / "removed.json").exists()
        assert (anchor.root / ".transient.lock").is_file()
        assert not (external_root / "created.json").exists()
        assert (external_root / "replaced.json").read_bytes() == b"old"
        assert (external_root / "removed.json").read_bytes() == b"remove"
        assert not (external_root / ".transient.lock").exists()
