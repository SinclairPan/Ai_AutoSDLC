"""Normal-user root CLI surface stays compact without deleting compatibility paths."""

from __future__ import annotations

from typer.main import get_command
from typer.testing import CliRunner

from ai_sdlc.cli.main import app

runner = CliRunner()

_VISIBLE = {
    "init",
    "adopt",
    "doctor",
    "status",
    "recover",
    "run",
    "adapter",
    "workitem",
    "verify",
    "loop",
    "pr-review",
    "self-update",
}
_HIDDEN = {
    "index",
    "scan",
    "refresh",
    "agentops",
    "enterprise",
    "gate",
    "rules",
    "studio",
    "stage",
    "program",
    "host-runtime",
    "handoff",
    "telemetry",
    "provenance",
    "trace",
}


def test_root_command_metadata_exposes_only_normal_user_surface() -> None:
    root_command = get_command(app)

    assert all(root_command.commands[name].hidden is False for name in _VISIBLE)
    assert all(root_command.commands[name].hidden is True for name in _HIDDEN)


def test_hidden_compatibility_command_remains_callable() -> None:
    result = runner.invoke(app, ["rules", "--help"])

    assert result.exit_code == 0, result.output
    assert "show" in result.output
