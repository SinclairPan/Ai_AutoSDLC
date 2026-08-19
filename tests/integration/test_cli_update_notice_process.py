"""Real-process contracts for the normal-path CLI update notice."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import click
import pytest

from ai_sdlc.routers.bootstrap import init_project


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


def test_windows_launcher_reexec_carries_the_process_only_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.cli import self_update_cmd

    seen: dict[str, object] = {}
    monkeypatch.setenv("AI_SDLC_UPDATE_REPLAY_HANDOFF", '{"process":"only"}')
    monkeypatch.setattr(
        self_update_cmd, "_should_reexec_windows_launcher", lambda: True
    )
    monkeypatch.setattr(self_update_cmd.sys, "executable", r"C:\Python\python.exe")

    def fake_execve(executable, command, env):
        seen.update(executable=executable, command=command, env=env)
        raise RuntimeError("execve intercepted")

    monkeypatch.setattr(self_update_cmd.os, "execve", fake_execve)

    with pytest.raises(RuntimeError, match="intercepted"):
        self_update_cmd._reexec_windows_launcher_if_needed("2.0.0")

    assert seen["executable"] == r"C:\Python\python.exe"
    assert seen["command"][-4:] == [
        "self-update",
        "install",
        "--version",
        "2.0.0",
    ]
    assert seen["env"]["AI_SDLC_SELF_UPDATE_REEXEC"] == "1"
    assert seen["env"]["AI_SDLC_UPDATE_REPLAY_HANDOFF"] == '{"process":"only"}'


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
