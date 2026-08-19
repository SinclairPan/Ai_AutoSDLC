"""Enterprise project stacks share one bounded, read-only normal user path."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ai_sdlc.cli.main import app
from ai_sdlc.core.loop_models import LoopStatus, LoopType
from ai_sdlc.core.loop_router import LoopRouteItem, LoopRouteResult, LoopRouteStatus
from ai_sdlc.routers.bootstrap import init_project

runner = CliRunner()


@pytest.mark.parametrize(
    ("relative_path", "content"),
    [
        ("package.json", '{"name":"enterprise-node","private":true}\n'),
        (
            "pom.xml",
            "<project><modelVersion>4.0.0</modelVersion>"
            "<groupId>example</groupId><artifactId>enterprise-java</artifactId>"
            "</project>\n",
        ),
        (
            "pyproject.toml",
            '[project]\nname = "enterprise-python"\nversion = "1.0.0"\n',
        ),
    ],
)
def test_enterprise_stack_gets_only_current_loop_rules_without_project_writes(
    tmp_path: Path,
    relative_path: str,
    content: str,
) -> None:
    project = tmp_path / relative_path.split(".", maxsplit=1)[0]
    project.mkdir()
    init_project(project)
    (project / relative_path).write_text(content, encoding="utf-8")
    _git(project, "init", "--initial-branch=main")
    _git(project, "config", "user.email", "enterprise@example.com")
    _git(project, "config", "user.name", "Enterprise Fixture")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "enterprise baseline")
    before = _repository_state(project)
    routed = LoopRouteResult(
        status=LoopRouteStatus.ROUTED,
        result="Current delivery Loop: implementation impl-enterprise (needs_review).",
        current_loop=LoopRouteItem(
            loop_type=LoopType.IMPLEMENTATION,
            loop_id="impl-enterprise",
            status=LoopStatus.NEEDS_REVIEW,
            next_action="Run the selected implementation experts.",
        ),
        next_action="Run the selected implementation experts.",
    )

    with (
        patch("ai_sdlc.cli.main.maybe_render_update_notice"),
        patch("ai_sdlc.cli.run_cmd.find_project_root", return_value=project),
        patch("ai_sdlc.cli.commands.find_project_root", return_value=project),
        patch("ai_sdlc.cli.run_cmd.route_five_loops", return_value=routed),
        patch("ai_sdlc.core.loop_router.route_five_loops", return_value=routed),
    ):
        human = runner.invoke(app, ["run"])
        machine = runner.invoke(app, ["run", "--json"])
        status = runner.invoke(app, ["status"])

    assert human.exit_code == 0, human.output
    assert "Applicable Rules" in human.output
    assert "tdd" in human.output
    assert "verification" in human.output
    assert machine.exit_code == 0, machine.output
    payload = json.loads(machine.stdout)
    assert [item["name"] for item in payload["applicable_rules"]] == [
        "tdd",
        "verification",
    ]
    assert len(payload["applicable_rules"]) <= 2
    assert status.exit_code == 0, status.output
    assert "Result:" in status.output
    assert "Next:" in status.output
    assert "Blockers:" in status.output
    assert "AI-SDLC Status" not in status.output

    generic_output = "\n".join((human.output, machine.stdout, status.output)).lower()
    for unrelated in (
        "public-primevue",
        "enterprise-vue2",
        "ai-sdlc release",
        "competition",
        "workitem 010",
        "telemetry",
        "certificate",
        "proof ledger",
    ):
        assert unrelated not in generic_output
    assert _repository_state(project) == before


def _repository_state(root: Path) -> tuple[str, str, str]:
    return (
        _git(root, "rev-parse", "HEAD"),
        _git(root, "write-tree"),
        _git(root, "status", "--porcelain=v1", "--untracked-files=all"),
    )


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip())
    return result.stdout.strip()
