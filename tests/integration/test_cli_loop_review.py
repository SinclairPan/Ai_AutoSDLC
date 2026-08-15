"""Integration tests for read-only dynamic expert review inputs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ai_sdlc.cli.loop_review_cmd import resolve_review_input
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
    expected_upstream = _write_predecessor_fixture(tmp_path, loop_type, loop_dir)

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
    assert {Path(path).name for path in payload["upstream_context_paths"]} == (
        expected_upstream
    )
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


def test_stage_review_binds_recursive_predecessor_evidence(tmp_path: Path) -> None:
    requirement_dir = (
        tmp_path / ".ai-sdlc" / "loops" / "requirement" / "requirement-001"
    )
    design_dir = (
        tmp_path / ".ai-sdlc" / "loops" / "design-contract" / "design-001"
    )
    implementation_dir = (
        tmp_path / ".ai-sdlc" / "loops" / "implementation" / "implementation-001"
    )
    frontend_dir = (
        tmp_path / ".ai-sdlc" / "loops" / "frontend-evidence" / "frontend-001"
    )
    for loop_dir in (
        requirement_dir,
        design_dir,
        implementation_dir,
        frontend_dir,
    ):
        loop_dir.mkdir(parents=True)
        (loop_dir / "loop-run.json").write_text(
            json.dumps({"current_round": 1}),
            encoding="utf-8",
        )

    for filename in ("requirement-brief.md", "acceptance-checklist.md"):
        (requirement_dir / filename).write_text(filename, encoding="utf-8")
    (design_dir / "design-contract-input.json").write_text(
        json.dumps({"requirement_loop_id": "requirement-001"}),
        encoding="utf-8",
    )
    for filename in ("design-contract-report.json", "design-contract-report.md"):
        (design_dir / filename).write_text(filename, encoding="utf-8")
    (implementation_dir / "implementation-input.json").write_text(
        json.dumps({"design_contract_loop_id": "design-001"}),
        encoding="utf-8",
    )
    for filename in (
        "implementation-report.json",
        "implementation-report.md",
        "verification-evidence.json",
    ):
        (implementation_dir / filename).write_text(filename, encoding="utf-8")
    (frontend_dir / "frontend-evidence-input.json").write_text(
        json.dumps({"implementation_loop_id": "implementation-001"}),
        encoding="utf-8",
    )
    for filename in (
        "frontend-evidence-snapshot.json",
        "frontend-evidence-report.json",
        "frontend-evidence-report.md",
    ):
        (frontend_dir / filename).write_text(filename, encoding="utf-8")

    first = resolve_review_input(
        tmp_path,
        loop_type="frontend-evidence",
        loop_id="frontend-001",
    )

    assert {Path(path).name for path in first.upstream_context_paths} == {
        "requirement-brief.md",
        "acceptance-checklist.md",
        "design-contract-report.json",
        "design-contract-report.md",
        "implementation-report.json",
        "implementation-report.md",
        "verification-evidence.json",
    }

    (requirement_dir / "requirement-brief.md").write_text(
        "changed requirement",
        encoding="utf-8",
    )
    changed = resolve_review_input(
        tmp_path,
        loop_type="frontend-evidence",
        loop_id="frontend-001",
    )
    assert changed.input_digest != first.input_digest


def test_risk_signals_ignore_substrings_in_structural_words(tmp_path: Path) -> None:
    loop_dir = tmp_path / ".ai-sdlc" / "loops" / "requirement" / "requirement-001"
    loop_dir.mkdir(parents=True)
    (loop_dir / "loop-run.json").write_text(
        json.dumps({"current_round": 1}),
        encoding="utf-8",
    )
    (loop_dir / "requirement-brief.md").write_text(
        '{"blocker_count": 0, "required": true, "build": "complete"}',
        encoding="utf-8",
    )
    (loop_dir / "acceptance-checklist.md").write_text("complete", encoding="utf-8")

    review_input = resolve_review_input(
        tmp_path,
        loop_type="requirement",
        loop_id="requirement-001",
    )

    assert review_input.risk_signals == ["general-correctness"]


def test_risk_signals_detect_standalone_short_terms(tmp_path: Path) -> None:
    loop_dir = tmp_path / ".ai-sdlc" / "loops" / "requirement" / "requirement-001"
    loop_dir.mkdir(parents=True)
    (loop_dir / "loop-run.json").write_text(
        json.dumps({"current_round": 1}),
        encoding="utf-8",
    )
    (loop_dir / "requirement-brief.md").write_text(
        "UI state requires a lock",
        encoding="utf-8",
    )
    (loop_dir / "acceptance-checklist.md").write_text("complete", encoding="utf-8")

    review_input = resolve_review_input(
        tmp_path,
        loop_type="requirement",
        loop_id="requirement-001",
    )

    assert review_input.risk_signals == ["concurrency", "frontend"]


def _init_git_repo(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "review@example.com")
    _git(root, "config", "user.name", "Review Test")
    (root / "tracked.txt").write_text("initial\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-m", "initial")


def _write_predecessor_fixture(
    root: Path,
    loop_type: str,
    loop_dir: Path,
) -> set[str]:
    if loop_type == "requirement":
        return set()
    if loop_type == "design-contract":
        (loop_dir / "design-contract-input.json").write_text(
            json.dumps({"requirement_loop_id": ""}),
            encoding="utf-8",
        )
        return set()

    design_dir = root / ".ai-sdlc" / "loops" / "design-contract" / "design-upstream"
    design_dir.mkdir(parents=True)
    (design_dir / "design-contract-input.json").write_text(
        json.dumps({"requirement_loop_id": ""}),
        encoding="utf-8",
    )
    design_files = {"design-contract-report.json", "design-contract-report.md"}
    for filename in design_files:
        (design_dir / filename).write_text(filename, encoding="utf-8")
    if loop_type == "implementation":
        (loop_dir / "implementation-input.json").write_text(
            json.dumps({"design_contract_loop_id": "design-upstream"}),
            encoding="utf-8",
        )
        return design_files

    implementation_dir = (
        root / ".ai-sdlc" / "loops" / "implementation" / "implementation-upstream"
    )
    implementation_dir.mkdir(parents=True)
    (implementation_dir / "implementation-input.json").write_text(
        json.dumps({"design_contract_loop_id": "design-upstream"}),
        encoding="utf-8",
    )
    implementation_files = {
        "implementation-report.json",
        "implementation-report.md",
        "verification-evidence.json",
    }
    for filename in implementation_files:
        (implementation_dir / filename).write_text(filename, encoding="utf-8")
    (loop_dir / "frontend-evidence-input.json").write_text(
        json.dumps({"implementation_loop_id": "implementation-upstream"}),
        encoding="utf-8",
    )
    return design_files | implementation_files


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
