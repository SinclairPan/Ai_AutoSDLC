"""Real-process contracts for the normal-path CLI update notice."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click
import packaging_backend
import pytest

from ai_sdlc.routers.bootstrap import init_project


def _copy_dependency_site_packages(destination: Path) -> Path:
    source = Path(click.__file__).resolve().parents[1]
    destination.mkdir(parents=True, exist_ok=True)

    for item in source.iterdir():
        if item.name == "ai_sdlc":
            continue
        if item.name.startswith("ai_sdlc-") and item.name.endswith(".dist-info"):
            continue
        if item.name.startswith("ai_sdlc") and item.suffix == ".pth":
            continue

        target = destination / item.name
        if target.exists():
            continue
        try:
            target.symlink_to(item, target_is_directory=item.is_dir())
        except OSError:
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)

    return destination


def test_dependency_overlay_exposes_runtime_deps_without_candidate(
    tmp_path: Path,
) -> None:
    overlay = _copy_dependency_site_packages(tmp_path / "dependency-overlay")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(overlay)
    probe = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            (
                "import importlib.util; "
                "import jinja2, pydantic, rich, typer, yaml; "
                "assert importlib.util.find_spec('ai_sdlc') is None"
            ),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr


def _update_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "AI_SDLC_UPDATE_ADVISOR_TEST_INSTALLED": "1",
            "AI_SDLC_UPDATE_ADVISOR_TEST_VERSION": "1.0.0",
            "AI_SDLC_UPDATE_ADVISOR_TEST_CHANNEL": "github-archive",
            "AI_SDLC_UPDATE_ADVISOR_TEST_LATEST_VERSION": "v2.0.0",
            "AI_SDLC_UPDATE_ADVISOR_CACHE_DIR": str(tmp_path / "cache"),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def test_json_process_keeps_stdout_clean_and_emits_one_stderr_notice(
    tmp_path: Path,
) -> None:
    init_project(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "ai_sdlc", "status", "--json"],
        cwd=tmp_path,
        env=_update_env(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    json.loads(result.stdout)
    notices = [
        line
        for line in result.stderr.splitlines()
        if line.startswith("AI_SDLC_UPDATE_NOTICE ")
    ]
    assert len(notices) == 1
    assert json.loads(notices[0].split(" ", 1)[1])["latest_version"] == "2.0.0"


def test_stale_update_cache_fails_open_after_refresh_error_and_backoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.core import update_advisor

    init_project(tmp_path)
    env = _update_env(tmp_path)
    env.pop("AI_SDLC_UPDATE_ADVISOR_TEST_LATEST_VERSION")
    env["AI_SDLC_UPDATE_ADVISOR_FORCE_TTY"] = "1"
    hooks = tmp_path / "offline-hooks"
    hooks.mkdir()
    install_log = tmp_path / "unexpected-install.txt"
    (hooks / "sitecustomize.py").write_text(
        """
import os
import urllib.error
from pathlib import Path

import ai_sdlc.core.update_advisor as advisor
import ai_sdlc.cli.self_update_cmd as updater

def fail_fetch(_timeout):
    raise urllib.error.URLError("offline")

advisor.fetch_latest_github_release = fail_fetch

def unexpected_install(*_args, **_kwargs):
    Path(os.environ["AI_SDLC_UNEXPECTED_INSTALL_LOG"]).write_text("called")

updater.self_update_install = unexpected_install
""".lstrip(),
        encoding="utf-8",
    )
    env["AI_SDLC_UNEXPECTED_INSTALL_LOG"] = str(install_log)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(hooks), env.get("PYTHONPATH", "")) if part
    )

    for key in (
        "AI_SDLC_UPDATE_ADVISOR_TEST_INSTALLED",
        "AI_SDLC_UPDATE_ADVISOR_TEST_VERSION",
        "AI_SDLC_UPDATE_ADVISOR_TEST_CHANNEL",
        "AI_SDLC_UPDATE_ADVISOR_CACHE_DIR",
    ):
        monkeypatch.setenv(key, env[key])
    identity = update_advisor.detect_runtime_identity()
    stale_time = datetime.now(UTC) - timedelta(days=2)
    update_advisor._save_cache(
        identity,
        update_advisor.UpdateCache(
            runtime_identity=identity.runtime_identity,
            installed_version="1.0.0",
            install_channel="github-archive",
            upstream_latest_version="2.0.0",
            channel_latest_version="2.0.0",
            last_checked_at=stale_time.isoformat(),
            last_success_checked_at=stale_time.isoformat(),
            last_check_status="success",
        ),
    )

    results = [
        subprocess.run(
            [sys.executable, "-m", "ai_sdlc", "status"],
            cwd=tmp_path,
            env=env,
            input="y\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        for _ in range(2)
    ]

    for result in results:
        assert result.returncode == 0, result.stderr
        assert "Result:" in result.stdout
        assert "是否升级" not in result.stderr
        assert "AI_SDLC_UPDATE_NOTICE" not in result.stderr
    assert not install_log.exists()


def test_module_invocation_update_replays_business_handler_once_and_exit_code(
    tmp_path: Path,
) -> None:
    init_project(tmp_path)
    hooks = tmp_path / "module-hooks"
    hooks.mkdir()
    process_log = tmp_path / "module-processes.jsonl"
    (hooks / "sitecustomize.py").write_text(
        """
import functools
import json
import os
import sys
from pathlib import Path

import typer
import ai_sdlc.cli.doctor_cmd as doctor_module
import ai_sdlc.cli.self_update_cmd as updater

log = Path(os.environ["AI_SDLC_REPLAY_TEST_LOG"])

def record(kind):
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "kind": kind,
            "argv": sys.argv,
            "orig_argv": sys.orig_argv,
            "handoff": "AI_SDLC_UPDATE_REPLAY_HANDOFF" in os.environ,
            "bypass": "AI_SDLC_UPDATE_REPLAY_BYPASS" in os.environ,
        }) + "\\n")

record("process")
original_doctor = doctor_module.doctor_command

@functools.wraps(original_doctor)
def wrapped_doctor(*args, **kwargs):
    record("handler")
    original_doctor(*args, **kwargs)
    raise typer.Exit(17)

doctor_module.doctor_command = wrapped_doctor

def fake_download(_url, archive_path):
    archive_path.write_bytes(b"archive")

def fake_extract(_archive_path, extract_root, _hint):
    bundle = extract_root / "bundle"
    (bundle / "wheels").mkdir(parents=True)
    (bundle / "wheels" / "ai_sdlc-2.0.0-py3-none-any.whl").write_bytes(b"wheel")
    return bundle

updater._download_asset = fake_download
updater._extract_release_asset = fake_extract
updater._install_bundle_into_current_runtime = lambda *_args: None
updater._read_installed_version = lambda: "2.0.0"
updater._repair_current_user_path_if_possible = lambda: None
updater._verify_bare_cli_version = lambda version: version
""".lstrip(),
        encoding="utf-8",
    )
    env = _update_env(tmp_path)
    env["AI_SDLC_UPDATE_ADVISOR_FORCE_TTY"] = "1"
    env["AI_SDLC_REPLAY_TEST_LOG"] = str(process_log)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(hooks), env.get("PYTHONPATH", "")) if part
    )

    result = subprocess.run(
        [sys.executable, "-m", "ai_sdlc", "doctor"],
        cwd=tmp_path,
        env=env,
        input="y\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 17, result.stderr
    records = [json.loads(line) for line in process_log.read_text().splitlines()]
    processes = [record for record in records if record["kind"] == "process"]
    handlers = [record for record in records if record["kind"] == "handler"]
    assert len(processes) == 2
    assert all(
        record["orig_argv"][-3:] == ["-m", "ai_sdlc", "doctor"] for record in processes
    )
    assert len(handlers) == 1
    assert handlers[0]["handoff"] is False
    assert handlers[0]["bypass"] is False


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows console launcher")
def test_windows_launcher_update_reexec_replays_exact_business_command_once(
    tmp_path: Path,
) -> None:
    init_project(tmp_path)
    launcher = Path(sys.executable).with_name("ai-sdlc.exe")
    assert launcher.is_file(), launcher
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    process_log = tmp_path / "processes.jsonl"
    sitecustomize = hooks / "sitecustomize.py"
    sitecustomize.write_text(
        """
import json
import os
import sys
from pathlib import Path

log = Path(os.environ["AI_SDLC_REPLAY_TEST_LOG"])
with log.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({
        "argv": sys.argv,
        "handoff": "AI_SDLC_UPDATE_REPLAY_HANDOFF" in os.environ,
        "bypass": "AI_SDLC_UPDATE_REPLAY_BYPASS" in os.environ,
    }) + "\\n")

import ai_sdlc.cli.self_update_cmd as target

def fake_download(_url, archive_path):
    archive_path.write_bytes(b"archive")

def fake_extract(_archive_path, extract_root, _hint):
    bundle = extract_root / "bundle"
    (bundle / "wheels").mkdir(parents=True)
    (bundle / "wheels" / "ai_sdlc-2.0.0-py3-none-any.whl").write_bytes(b"wheel")
    return bundle

target._download_asset = fake_download
target._extract_release_asset = fake_extract
target._install_bundle_into_current_runtime = lambda *_args: None
target._read_installed_version = lambda: "2.0.0"
target._repair_current_user_path_if_possible = lambda: None
target._verify_bare_cli_version = lambda version: version
""".lstrip(),
        encoding="utf-8",
    )
    env = _update_env(tmp_path)
    env["AI_SDLC_UPDATE_ADVISOR_FORCE_TTY"] = "1"
    env["AI_SDLC_REPLAY_TEST_LOG"] = str(process_log)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(hooks), env.get("PYTHONPATH", "")) if part
    )

    result = subprocess.run(
        [str(launcher), "status"],
        cwd=tmp_path,
        env=env,
        input="y\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in process_log.read_text().splitlines()]
    business = [record for record in records if record["argv"][1:] == ["status"]]
    assert len(business) == 2
    assert business[0]["handoff"] is False
    assert business[0]["bypass"] is False
    assert business[1]["handoff"] is False
    assert business[1]["bypass"] is True
    updater = [record for record in records if "self-update" in record["argv"]]
    assert len(updater) == 1
    assert updater[0]["handoff"] is True


@pytest.mark.parametrize("use_stable_shim", [False, True])
@pytest.mark.skipif(sys.platform != "win32", reason="real Windows wheel upgrade")
def test_windows_launcher_can_upgrade_its_live_installed_wheel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_stable_shim: bool,
) -> None:
    metadata = packaging_backend._load_metadata()
    assert metadata["version"] == "2.0.0"
    old_dist = tmp_path / "old-dist"
    new_dist = tmp_path / "new-dist"
    monkeypatch.setattr(
        packaging_backend,
        "_load_metadata",
        lambda: {**metadata, "version": "1.0.0"},
    )
    old_wheel = old_dist / packaging_backend.build_wheel(str(old_dist))
    monkeypatch.setattr(
        packaging_backend,
        "_load_metadata",
        lambda: {**metadata, "version": "2.0.0"},
    )
    new_wheel = new_dist / packaging_backend.build_wheel(str(new_dist))

    venv_dir = tmp_path / "installed-runtime"
    create_venv = subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert create_venv.returncode == 0, create_venv.stderr
    venv_python = venv_dir / "Scripts" / "python.exe"
    launcher = venv_dir / "Scripts" / "ai-sdlc.exe"
    _copy_dependency_site_packages(venv_dir / "Lib" / "site-packages")
    dependency_probe = subprocess.run(
        [
            str(venv_python),
            "-c",
            "import jinja2, pydantic, rich, typer, yaml",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert dependency_probe.returncode == 0, dependency_probe.stderr
    install_old = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--no-deps", str(old_wheel)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert install_old.returncode == 0, install_old.stderr
    assert launcher.is_file(), launcher
    command_launcher = launcher
    if use_stable_shim:
        stable_bin = tmp_path / "stable-bin"
        stable_bin.mkdir()
        command_launcher = stable_bin / "ai-sdlc.exe"
        shutil.copy2(launcher, command_launcher)
        (stable_bin / "ai-sdlc-runtime.txt").write_text(
            f"{venv_python}\n", encoding="utf-8"
        )

    project = tmp_path / "project"
    project.mkdir()
    init_project(project)
    hooks = tmp_path / "wheel-upgrade-hooks"
    hooks.mkdir()
    process_log = tmp_path / "wheel-upgrade-processes.jsonl"
    (hooks / "sitecustomize.py").write_text(
        """
import importlib.metadata
import json
import os
import shutil
import sys
from pathlib import Path

log = Path(os.environ["AI_SDLC_REPLAY_TEST_LOG"])
with log.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({
        "argv": sys.argv,
        "version": importlib.metadata.version("ai-sdlc"),
        "handoff": "AI_SDLC_UPDATE_REPLAY_HANDOFF" in os.environ,
        "bypass": "AI_SDLC_UPDATE_REPLAY_BYPASS" in os.environ,
    }) + "\\n")

import ai_sdlc.cli.self_update_cmd as target

def fake_download(_url, archive_path):
    archive_path.write_bytes(b"archive")

def fake_extract(_archive_path, extract_root, _hint):
    bundle = extract_root / "bundle"
    wheels = bundle / "wheels"
    wheels.mkdir(parents=True)
    source = Path(os.environ["AI_SDLC_REPLAY_TEST_WHEEL"])
    shutil.copy2(source, wheels / source.name)
    return bundle

target._download_asset = fake_download
target._extract_release_asset = fake_extract
target._repair_current_user_path_if_possible = lambda: None
target._verify_bare_cli_version = lambda version: version
""".lstrip(),
        encoding="utf-8",
    )
    env = _update_env(tmp_path)
    env["AI_SDLC_UPDATE_ADVISOR_FORCE_TTY"] = "1"
    env["AI_SDLC_REPLAY_TEST_LOG"] = str(process_log)
    env["AI_SDLC_REPLAY_TEST_WHEEL"] = str(new_wheel)
    env["PYTHONPATH"] = str(hooks)
    env["PATH"] = os.pathsep.join(
        part for part in (str(command_launcher.parent), env.get("PATH", "")) if part
    )

    result = subprocess.run(
        [str(command_launcher), "status"],
        cwd=project,
        env=env,
        input="y\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    records = [json.loads(line) for line in process_log.read_text().splitlines()]
    business = [record for record in records if record["argv"][1:] == ["status"]]
    updater = [record for record in records if "self-update" in record["argv"]]
    assert [record["version"] for record in business] == ["1.0.0", "2.0.0"]
    assert len(updater) == 1
    assert updater[0]["version"] == "1.0.0"
    assert updater[0]["handoff"] is False
    assert business[1]["handoff"] is False
    assert business[1]["bypass"] is True
    installed = subprocess.run(
        [
            str(venv_python),
            "-c",
            "from importlib.metadata import version; print(version('ai-sdlc'))",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert installed.returncode == 0, installed.stderr
    assert installed.stdout.strip() == "2.0.0"
    stale_launchers = list(launcher.parent.glob(".ai-sdlc-old-*.exe"))
    deadline = time.monotonic() + 10
    while stale_launchers and time.monotonic() < deadline:
        time.sleep(0.05)
        stale_launchers = list(launcher.parent.glob(".ai-sdlc-old-*.exe"))
    assert stale_launchers == []


def test_replay_handoff_and_bypass_are_consumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.cli import self_update_cmd

    request = self_update_cmd.ReplayRequest(
        executable="ai-sdlc",
        argv=("status", "--details"),
    )
    self_update_cmd._publish_replay_handoff(request)

    assert self_update_cmd._consume_replay_handoff() == request
    assert "AI_SDLC_UPDATE_REPLAY_HANDOFF" not in os.environ

    monkeypatch.setenv("AI_SDLC_UPDATE_REPLAY_BYPASS", "1")
    assert self_update_cmd.consume_update_replay_bypass() is True
    assert "AI_SDLC_UPDATE_REPLAY_BYPASS" not in os.environ


def test_replay_uses_exact_argv_and_propagates_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.cli import self_update_cmd

    seen: dict[str, object] = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 17)

    monkeypatch.setattr(self_update_cmd.subprocess, "run", fake_run)
    request = self_update_cmd.ReplayRequest(
        executable="ai-sdlc",
        argv=("loop", "status", "--json", "literal;not-shell"),
    )

    with pytest.raises(click.exceptions.Exit) as exc_info:
        self_update_cmd._replay_updated_command(request)

    assert exc_info.value.exit_code == 17
    assert seen["command"] == [
        "ai-sdlc",
        "loop",
        "status",
        "--json",
        "literal;not-shell",
    ]
    kwargs = seen["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["shell"] is False
    assert kwargs["env"]["AI_SDLC_UPDATE_REPLAY_BYPASS"] == "1"
    assert "AI_SDLC_UPDATE_REPLAY_HANDOFF" not in kwargs["env"]


def test_capture_replay_request_preserves_python_module_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.cli import self_update_cmd

    monkeypatch.setattr(self_update_cmd.sys, "executable", "/runtime/python")
    monkeypatch.setattr(
        self_update_cmd.sys,
        "orig_argv",
        ["/runtime/python", "-I", "-m", "ai_sdlc", "loop", "status"],
    )
    monkeypatch.setattr(
        self_update_cmd.sys,
        "argv",
        ["/site-packages/ai_sdlc/__main__.py", "loop", "status"],
    )

    request = self_update_cmd._capture_replay_request()

    assert request == self_update_cmd.ReplayRequest(
        executable="/runtime/python",
        argv=("-I", "-m", "ai_sdlc", "loop", "status"),
    )


def test_windows_launcher_reexec_carries_the_process_only_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.cli import self_update_cmd

    seen: dict[str, object] = {}
    request = self_update_cmd.ReplayRequest(
        executable=r"C:\Python\Scripts\ai-sdlc.exe",
        argv=("doctor", "--json"),
    )
    self_update_cmd._publish_replay_handoff(request)
    monkeypatch.setattr(
        self_update_cmd, "_should_reexec_windows_launcher", lambda: True
    )
    monkeypatch.setattr(self_update_cmd.sys, "executable", r"C:\Python\python.exe")
    launcher = Path(r"C:\Python\Scripts\ai-sdlc.exe")
    backup = Path(r"C:\Python\Scripts\.ai-sdlc-old-1.exe")
    monkeypatch.setattr(
        self_update_cmd,
        "_prepare_windows_launcher_update",
        lambda: (launcher, backup),
    )

    calls: list[tuple[list[str], dict[str, object]]] = []
    events: list[str] = []

    def fake_cleanup(candidate: Path) -> None:
        seen["cleanup"] = candidate
        events.append("cleanup")

    monkeypatch.setattr(
        self_update_cmd,
        "_start_windows_launcher_cleanup",
        fake_cleanup,
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        events.append("install" if len(calls) == 1 else "replay")
        return subprocess.CompletedProcess(command, 0 if len(calls) == 1 else 17)

    monkeypatch.setattr(self_update_cmd.subprocess, "run", fake_run)

    with pytest.raises(click.exceptions.Exit) as exc_info:
        self_update_cmd._reexec_windows_launcher_if_needed("2.0.0")

    assert exc_info.value.exit_code == 17
    assert calls[0][0][-4:] == [
        "self-update",
        "install",
        "--version",
        "2.0.0",
    ]
    kwargs = calls[0][1]
    assert isinstance(kwargs, dict)
    assert kwargs["shell"] is False
    assert kwargs["check"] is False
    assert kwargs["env"]["AI_SDLC_SELF_UPDATE_REEXEC"] == "1"
    assert "AI_SDLC_UPDATE_REPLAY_HANDOFF" not in kwargs["env"]
    assert calls[1][0] == [request.executable, *request.argv]
    assert seen["cleanup"] == backup
    assert events == ["install", "replay", "cleanup"]


def test_windows_launcher_delegate_failure_is_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.cli import self_update_cmd

    monkeypatch.setattr(
        self_update_cmd, "_should_reexec_windows_launcher", lambda: True
    )
    launcher = Path(r"C:\Python\Scripts\ai-sdlc.exe")
    backup = Path(r"C:\Python\Scripts\.ai-sdlc-old-1.exe")
    restored: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        self_update_cmd,
        "_prepare_windows_launcher_update",
        lambda: (launcher, backup),
    )
    monkeypatch.setattr(
        self_update_cmd,
        "_restore_windows_launcher_path",
        lambda current, previous: restored.append((current, previous)),
    )
    monkeypatch.setattr(
        self_update_cmd.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("missing Python runtime")
        ),
    )

    with pytest.raises(click.exceptions.Exit) as exc_info:
        self_update_cmd._reexec_windows_launcher_if_needed("2.0.0")

    assert exc_info.value.exit_code == 1
    assert restored == [(launcher, backup)]


def test_windows_replay_launch_failure_still_cleans_old_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.cli import self_update_cmd

    request = self_update_cmd.ReplayRequest("ai-sdlc", ("status",))
    self_update_cmd._publish_replay_handoff(request)
    monkeypatch.setattr(
        self_update_cmd, "_should_reexec_windows_launcher", lambda: True
    )
    launcher = Path(r"C:\Python\Scripts\ai-sdlc.exe")
    backup = Path(r"C:\Python\Scripts\.ai-sdlc-old-1.exe")
    monkeypatch.setattr(
        self_update_cmd,
        "_prepare_windows_launcher_update",
        lambda: (launcher, backup),
    )
    calls = 0

    def fake_run(command, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(command, 0)
        raise OSError("missing updated launcher")

    cleaned: list[Path] = []
    monkeypatch.setattr(self_update_cmd.subprocess, "run", fake_run)
    monkeypatch.setattr(
        self_update_cmd,
        "_start_windows_launcher_cleanup",
        cleaned.append,
    )

    with pytest.raises(click.exceptions.Exit) as exc_info:
        self_update_cmd._reexec_windows_launcher_if_needed("2.0.0")

    assert exc_info.value.exit_code == 1
    assert cleaned == [backup]


def test_windows_launcher_failed_update_restores_original_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.cli import self_update_cmd

    launcher = Path(r"C:\Python\Scripts\ai-sdlc.exe")
    backup = Path(r"C:\Python\Scripts\.ai-sdlc-old-1.exe")
    restored: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        self_update_cmd, "_should_reexec_windows_launcher", lambda: True
    )
    monkeypatch.setattr(
        self_update_cmd,
        "_prepare_windows_launcher_update",
        lambda: (launcher, backup),
    )
    monkeypatch.setattr(
        self_update_cmd.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1),
    )
    monkeypatch.setattr(
        self_update_cmd,
        "_restore_windows_launcher_path",
        lambda current, previous: restored.append((current, previous)),
    )

    with pytest.raises(click.exceptions.Exit) as exc_info:
        self_update_cmd._reexec_windows_launcher_if_needed("2.0.0")

    assert exc_info.value.exit_code == 1
    assert restored == [(launcher, backup)]


def test_external_windows_launcher_requires_matching_runtime_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.cli import self_update_cmd

    runtime = tmp_path / "runtime" / "python.exe"
    runtime.parent.mkdir()
    runtime.write_bytes(b"python")
    stable = tmp_path / "stable" / "ai-sdlc.exe"
    stable.parent.mkdir()
    stable.write_bytes(b"launcher")
    marker = stable.with_name("ai-sdlc-runtime.txt")
    marker.write_text(f"{runtime}\n", encoding="utf-8")
    monkeypatch.setattr(self_update_cmd.sys, "argv", [str(stable), "status"])
    monkeypatch.setattr(self_update_cmd.sys, "executable", str(runtime))

    assert self_update_cmd._prepare_windows_launcher_update() == (stable, None)

    marker.write_text(f"{tmp_path / 'other-python.exe'}\n", encoding="utf-8")
    with pytest.raises(self_update_cmd.SelfUpdateError):
        self_update_cmd._prepare_windows_launcher_update()

    marker.write_text(f"{runtime}\n", encoding="utf-8")
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda candidate: candidate == stable or original_is_symlink(candidate),
    )
    with pytest.raises(self_update_cmd.SelfUpdateError):
        self_update_cmd._prepare_windows_launcher_update()


def test_runtime_owned_windows_launcher_does_not_require_strict_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.cli import self_update_cmd

    scripts = tmp_path / "运行 时" / "Scripts"
    scripts.mkdir(parents=True)
    runtime = scripts / "python.exe"
    runtime.write_bytes(b"python")
    launcher = scripts / "ai-sdlc.exe"
    launcher.write_bytes(b"launcher")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        self_update_cmd.sys,
        "argv",
        [str(launcher.relative_to(tmp_path)), "status"],
    )
    monkeypatch.setattr(self_update_cmd.sys, "executable", str(runtime))
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("strict resolve is unavailable for the live launcher")
        ),
    )

    prepared, backup = self_update_cmd._prepare_windows_launcher_update()

    assert prepared == launcher
    assert backup is not None
    assert backup.is_file()
    assert not launcher.exists()


@pytest.mark.parametrize("missing_runtime", [False, True])
def test_windows_launcher_file_validation_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_runtime: bool,
) -> None:
    from ai_sdlc.cli import self_update_cmd

    scripts = tmp_path / "runtime" / "Scripts"
    scripts.mkdir(parents=True)
    runtime = scripts / "python.exe"
    launcher = scripts / "ai-sdlc.exe"
    if missing_runtime:
        launcher.write_bytes(b"launcher")
    else:
        runtime.write_bytes(b"python")
        launcher.mkdir()
    monkeypatch.setattr(self_update_cmd.sys, "argv", [str(launcher), "status"])
    monkeypatch.setattr(self_update_cmd.sys, "executable", str(runtime))

    with pytest.raises(self_update_cmd.SelfUpdateError):
        self_update_cmd._prepare_windows_launcher_update()


def test_windows_launcher_cleanup_never_receives_replay_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.cli import self_update_cmd

    seen: dict[str, object] = {}
    monkeypatch.setenv("AI_SDLC_UPDATE_REPLAY_HANDOFF", '{"secret":"argv"}')
    monkeypatch.setenv("AI_SDLC_UPDATE_REPLAY_BYPASS", "1")
    monkeypatch.setenv("AI_SDLC_SELF_UPDATE_REEXEC", "1")
    monkeypatch.setattr(self_update_cmd.sys, "executable", r"C:\Python\python.exe")

    def fake_popen(command, **kwargs):
        seen.update(command=command, kwargs=kwargs)
        return object()

    monkeypatch.setattr(self_update_cmd.subprocess, "Popen", fake_popen)
    backup = Path(r"C:\Python\Scripts\.ai-sdlc-old-1.exe")

    self_update_cmd._start_windows_launcher_cleanup(backup)

    command = seen["command"]
    assert isinstance(command, list)
    assert command[-1] == str(backup)
    assert "secret" not in " ".join(command)
    kwargs = seen["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["shell"] is False
    assert "AI_SDLC_UPDATE_REPLAY_HANDOFF" not in kwargs["env"]
    assert "AI_SDLC_UPDATE_REPLAY_BYPASS" not in kwargs["env"]
    assert "AI_SDLC_SELF_UPDATE_REEXEC" not in kwargs["env"]


def test_malformed_auto_replay_handoff_fails_before_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.cli import self_update_cmd

    monkeypatch.setenv("AI_SDLC_UPDATE_REPLAY_HANDOFF", "not-json")
    monkeypatch.setattr(
        self_update_cmd,
        "_reexec_windows_launcher_if_needed",
        lambda _version: None,
    )
    release = monkeypatch.setattr(
        self_update_cmd,
        "_release_asset_context",
        lambda _version: pytest.fail("installer must not start"),
    )

    with pytest.raises(click.exceptions.Exit) as exc_info:
        self_update_cmd.self_update_install(version="2.0.0")

    assert release is None
    assert exc_info.value.exit_code == 1
    assert "AI_SDLC_UPDATE_REPLAY_HANDOFF" not in os.environ


def test_replay_launch_failure_is_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.cli import self_update_cmd

    monkeypatch.setattr(
        self_update_cmd.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing launcher")),
    )

    with pytest.raises(click.exceptions.Exit) as exc_info:
        self_update_cmd._replay_updated_command(
            self_update_cmd.ReplayRequest("ai-sdlc", ("status",))
        )

    assert exc_info.value.exit_code == 1
