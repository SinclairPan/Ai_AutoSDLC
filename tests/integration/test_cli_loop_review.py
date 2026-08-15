"""Integration tests for read-only dynamic expert review inputs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ai_sdlc.cli.main import app

runner = CliRunner()
pytestmark = pytest.mark.usefixtures("isolated_cli_cwd")


@pytest.mark.parametrize(
    ("loop_type", "filenames", "excluded"),
    [
        (
            "requirement",
            ["requirement-brief.md", "acceptance-checklist.md"],
            "requirement-freeze.json",
        ),
        (
            "design-contract",
            ["design-contract-report.json", "design-contract-report.md"],
            "design-contract-close.json",
        ),
        (
            "implementation",
            [
                "implementation-report.json",
                "implementation-report.md",
                "verification-evidence.json",
            ],
            "implementation-close.json",
        ),
        (
            "frontend-evidence",
            [
                "frontend-evidence-snapshot.json",
                "frontend-evidence-report.json",
                "frontend-evidence-report.md",
            ],
            "frontend-evidence-close.json",
        ),
    ],
)
def test_loop_review_maps_only_substantive_stage_artifacts(
    tmp_path: Path,
    loop_type: str,
    filenames: list[str],
    excluded: str,
) -> None:
    loop_id = f"{loop_type}-001"
    loop_dir = tmp_path / ".ai-sdlc" / "loops" / loop_type / loop_id
    loop_dir.mkdir(parents=True)
    (loop_dir / "loop-run.json").write_text(
        json.dumps({"current_round": 2}),
        encoding="utf-8",
    )
    for filename in [*filenames, excluded]:
        (loop_dir / filename).write_text(f"{filename}\n", encoding="utf-8")

    with patch(
        "ai_sdlc.cli.loop_review_cmd.find_project_root", return_value=tmp_path
    ):
        result = runner.invoke(
            app,
            [
                "loop",
                "review",
                "--type",
                loop_type,
                "--loop-id",
                loop_id,
                "--json",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["loop_id"] == loop_id
    assert payload["loop_type"] == loop_type
    assert payload["round_number"] == 2
    assert {Path(path).name for path in payload["artifact_paths"]} == set(filenames)
    assert excluded not in result.output


def test_local_pr_review_binds_pre_close_artifacts_and_git_state(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    review_dir = tmp_path / ".ai-sdlc" / "reviews" / "pr" / "review-001"
    review_dir.mkdir(parents=True)
    (review_dir / "review-run.json").write_text(
        json.dumps({"review_id": "review-001", "loop_id": "loop-pr-001"}),
        encoding="utf-8",
    )
    included = [
        "review-pack.json",
        "findings.json",
        "resolution.yaml",
        "verification-evidence.json",
    ]
    for filename in included:
        (review_dir / filename).write_text(f"{filename}\n", encoding="utf-8")
    (review_dir / "final-report.md").write_text("must be excluded\n", encoding="utf-8")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("changed\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")

    with patch(
        "ai_sdlc.cli.loop_review_cmd.find_project_root", return_value=tmp_path
    ):
        result = runner.invoke(
            app,
            [
                "loop",
                "review",
                "--type",
                "local-pr-review",
                "--loop-id",
                "loop-pr-001",
                "--json",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert {Path(path).name for path in payload["artifact_paths"]} == set(included)
    assert "final-report.md" not in result.output
    assert any(item.startswith("git-head:") for item in payload["risk_signals"])
    assert any(item.startswith("git-index:") for item in payload["risk_signals"])
    assert any(item.startswith("git-staged-diff:") for item in payload["risk_signals"])

    digest = payload["input_digest"]
    tracked.write_text("changed again\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    with patch(
        "ai_sdlc.cli.loop_review_cmd.find_project_root", return_value=tmp_path
    ):
        drift = runner.invoke(
            app,
            [
                "loop",
                "review",
                "--type",
                "local-pr-review",
                "--loop-id",
                "loop-pr-001",
                "--expect-digest",
                digest,
                "--json",
            ],
        )

    assert drift.exit_code == 1
    assert json.loads(drift.output)["reason"] == "review-input-drift"
    assert not (
        tmp_path / ".ai-sdlc" / "loops" / "local-pr-review" / "loop-pr-001"
    ).exists()


def _init_git_repo(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "review@example.com")
    _git(root, "config", "user.name", "Review Test")
    (root / "tracked.txt").write_text("initial\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-m", "initial")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

