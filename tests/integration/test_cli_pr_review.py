"""Integration tests for the ai-sdlc pr-review CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from ai_sdlc.cli.loop_review_cmd import (
    resolve_review_input,
    validate_review_input_for_close,
)
from ai_sdlc.cli.main import app
from ai_sdlc.core import pr_review_service

runner = CliRunner()


def test_pr_review_help_omits_removed_authority_commands() -> None:
    result = runner.invoke(app, ["pr-review", "--help"])

    assert result.exit_code == 0
    assert "attest" not in result.output
    assert "certificate" not in result.output


def test_pr_review_help_lists_p0_commands() -> None:
    result = runner.invoke(app, ["pr-review", "--help"])

    assert result.exit_code == 0
    assert "doctor" in result.output
    assert "start" in result.output
    assert "status" in result.output
    assert "fix" in result.output
    assert "rerun" in result.output
    assert "record-evidence" in result.output
    assert "close" in result.output
    assert "attest" not in result.output


def test_pr_review_start_dry_run_json_is_read_only(tmp_path: Path) -> None:
    base_commit = _init_repo(tmp_path)
    _commit_file(tmp_path, "src/app.py", "print('hello')\n", "add app")

    with patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path):
        result = runner.invoke(
            app,
            [
                "pr-review",
                "start",
                "--base",
                base_commit,
                "--provider",
                "mock-reviewer",
                "--dry-run",
                "--review-id",
                "review-dry",
                "--json",
            ],
        )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "dry_run"
    assert payload["provider_id"] == "mock-reviewer"
    assert payload["resolved_model"] == "mock-reviewer"
    assert payload["source_adapter"] == "local-git-range"
    assert payload["source_access_status"] == "resolved"
    assert payload["diff_source"]["source_kind"] == "local-git-range"
    assert not (tmp_path / ".ai-sdlc" / "reviews").exists()


def test_pr_review_start_uses_policy_default_provider_when_option_omitted(
    tmp_path: Path,
) -> None:
    base_commit = _init_repo(tmp_path)
    policy_path = tmp_path / ".ai-sdlc" / "project" / "config" / "loop-policy.yaml"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text("default_provider: mock-reviewer\n", encoding="utf-8")
    _commit_file(tmp_path, "src/app.py", "print('hello')\n", "add app")

    with patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path):
        result = runner.invoke(
            app,
            [
                "pr-review",
                "start",
                "--base",
                base_commit,
                "--dry-run",
                "--review-id",
                "review-policy-default-cli",
                "--json",
            ],
        )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "dry_run"
    assert payload["provider_id"] == "mock-reviewer"
    assert payload["resolved_model"] == "mock-reviewer"


def test_pr_review_start_mock_and_status_json(tmp_path: Path) -> None:
    base_commit = _init_repo(tmp_path)
    _commit_file(tmp_path, "src/app.py", "print('hello')\n", "add app")

    with patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path):
        start = runner.invoke(
            app,
            [
                "pr-review",
                "start",
                "--base",
                base_commit,
                "--provider",
                "mock-reviewer",
                "--review-id",
                "review-cli",
                "--json",
            ],
        )
        status = runner.invoke(app, ["pr-review", "status", "--json"])

    assert start.exit_code == 0
    start_payload = json.loads(start.output)
    assert start_payload["status"] == "started"
    assert start_payload["verdict"] == "clean"
    assert Path(start_payload["review_pack_path"]).is_file()
    assert Path(start_payload["findings_path"]).is_file()

    assert status.exit_code == 0
    status_payload = json.loads(status.output)
    assert status_payload["status"] == "started"
    assert status_payload["review_id"] == "review-cli"
    assert status_payload["verdict"] == "clean"


def test_pr_review_start_without_base_uses_default_branch(tmp_path: Path) -> None:
    _init_repo(tmp_path, branch="master")
    _commit_file(tmp_path, "src/app.py", "print('hello')\n", "add app")

    with patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path):
        result = runner.invoke(
            app,
            [
                "pr-review",
                "start",
                "--provider",
                "mock-reviewer",
                "--review-id",
                "review-auto-base",
                "--json",
            ],
        )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "started"
    pack = json.loads(Path(payload["review_pack_path"]).read_text(encoding="utf-8"))
    assert pack["base_ref"] == "master"
    assert pack["source_adapter"] == "local-git-range"


def test_pr_review_start_patch_source_missing_reports_source_blocker(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)

    with patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path):
        result = runner.invoke(
            app,
            [
                "pr-review",
                "start",
                "--diff-source",
                "patch",
                "--patch-file",
                "missing.patch",
                "--provider",
                "mock-reviewer",
                "--dry-run",
                "--review-id",
                "review-missing-patch-cli",
                "--json",
            ],
        )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert payload["source_adapter"] == "patch"
    assert payload["source_access_status"] == "blocked"
    assert "missing.patch" in payload["blocker"]


def test_pr_review_start_patch_source_runs_mock_reviewer(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write_file(tmp_path, "src/app.py", "print('from patch')\n")
    (tmp_path / "change.patch").write_text(
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -0,0 +1 @@\n"
        "+print('from patch')\n",
        encoding="utf-8",
    )

    with patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path):
        result = runner.invoke(
            app,
            [
                "pr-review",
                "start",
                "--diff-source",
                "patch",
                "--patch-file",
                "change.patch",
                "--provider",
                "mock-reviewer",
                "--review-id",
                "review-patch-cli",
                "--json",
            ],
        )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "started"
    assert payload["source_adapter"] == "patch"
    pack = json.loads(Path(payload["review_pack_path"]).read_text(encoding="utf-8"))
    assert pack["diff_source"]["source_kind"] == "patch"
    assert pack["changed_files"] == ["src/app.py"]


def test_pr_review_doctor_json_reports_missing_project() -> None:
    with patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=None):
        result = runner.invoke(app, ["pr-review", "doctor", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert ".ai-sdlc is missing" in payload["blocker"]
    assert "ai-sdlc init" in payload["next_action"]


def test_pr_review_fix_and_close_require_no_blockers_json(tmp_path: Path) -> None:
    base_commit = _init_repo(tmp_path)
    _commit_file(tmp_path, "src/app.py", "print('hello')\n", "add app")

    with patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path):
        start = runner.invoke(
            app,
            [
                "pr-review",
                "start",
                "--base",
                base_commit,
                "--provider",
                "mock-reviewer",
                "--mock-fixture",
                "changes_required",
                "--review-id",
                "review-fix-cli",
                "--json",
            ],
        )
        fix = runner.invoke(app, ["pr-review", "fix", "--json"])
        started = json.loads(start.output)
        reviewed = resolve_review_input(
            tmp_path,
            loop_type="local-pr-review",
            loop_id=started["loop_id"],
        )
        close = runner.invoke(
            app,
            [
                "pr-review",
                "close",
                "--review-id",
                started["review_id"],
                "--loop-id",
                started["loop_id"],
                "--expect-review-digest",
                reviewed.input_digest,
                "--require-no-blockers",
                "--json",
            ],
        )

    assert start.exit_code == 10
    fix_payload = json.loads(fix.output)
    assert fix.exit_code == 0
    assert fix_payload["status"] == "ready"
    assert Path(fix_payload["fix_plan_path"]).is_file()
    assert Path(fix_payload["resolution_path"]).is_file()

    close_payload = json.loads(close.output)
    assert close.exit_code == 0
    assert close_payload["status"] == "closed"
    assert close_payload["verdict"] == "risk_accepted"
    assert Path(close_payload["final_report_path"]).is_file()


def test_pr_review_close_revalidates_resolution_at_transition(tmp_path: Path) -> None:
    base_commit = _init_repo(tmp_path)
    _commit_file(tmp_path, "src/app.py", "print('hello')\n", "add app")

    with patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path):
        start = runner.invoke(
            app,
            [
                "pr-review",
                "start",
                "--base",
                base_commit,
                "--provider",
                "mock-reviewer",
                "--mock-fixture",
                "changes_required",
                "--review-id",
                "review-resolution-transition-cli",
                "--json",
            ],
        )
        fix = runner.invoke(app, ["pr-review", "fix", "--json"])
        started = json.loads(start.output)
        fixed = json.loads(fix.output)
        reviewed = resolve_review_input(
            tmp_path,
            loop_type="local-pr-review",
            loop_id=started["loop_id"],
        )
        resolution_path = Path(fixed["resolution_path"])
        validation_count = 0

        def validate_then_replace_resolution(*args, **kwargs):
            nonlocal validation_count
            validation_count += 1
            result = validate_review_input_for_close(*args, **kwargs)
            if validation_count == 1:
                resolution = yaml.safe_load(resolution_path.read_text(encoding="utf-8"))
                resolution["finding_resolutions"][0].update(
                    {
                        "status": "waived",
                        "reason": "Concurrent unreviewed waiver.",
                        "operator": "other-process",
                        "resolved_at": "2026-08-16T00:00:00Z",
                    }
                )
                resolution_path.write_text(
                    yaml.safe_dump(resolution),
                    encoding="utf-8",
                )
            return result

        with patch(
            "ai_sdlc.cli.pr_review_cmd.validate_review_input_for_close",
            side_effect=validate_then_replace_resolution,
        ):
            close = runner.invoke(
                app,
                [
                    "pr-review",
                    "close",
                    "--review-id",
                    started["review_id"],
                    "--loop-id",
                    started["loop_id"],
                    "--expect-review-digest",
                    reviewed.input_digest,
                    "--json",
                ],
            )

    payload = json.loads(close.output)
    assert validation_count == 2
    assert close.exit_code == 1
    assert payload["status"] == "blocked"
    assert payload["reason"] == "review-input-drift"
    assert not (
        tmp_path
        / ".ai-sdlc"
        / "reviews"
        / "pr"
        / started["review_id"]
        / "final-report.md"
    ).exists()


@pytest.mark.parametrize("reviewed_resolution_exists", [False, True])
def test_pr_review_close_uses_validated_resolution_across_aba(
    tmp_path: Path,
    reviewed_resolution_exists: bool,
) -> None:
    base_commit = _init_repo(tmp_path)
    _commit_file(tmp_path, "src/app.py", "print('hello')\n", "add app")

    with patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path):
        start = runner.invoke(
            app,
            [
                "pr-review",
                "start",
                "--base",
                base_commit,
                "--provider",
                "mock-reviewer",
                "--mock-fixture",
                "changes_required",
                "--review-id",
                "review-resolution-aba-cli",
                "--json",
            ],
        )
        fix = runner.invoke(app, ["pr-review", "fix", "--json"])
        started = json.loads(start.output)
        fixed = json.loads(fix.output)
        resolution_path = Path(fixed["resolution_path"])
        reviewed_text = resolution_path.read_text(encoding="utf-8")
        if not reviewed_resolution_exists:
            resolution_path.unlink()
        reviewed = resolve_review_input(
            tmp_path,
            loop_type="local-pr-review",
            loop_id=started["loop_id"],
        )
        validation_count = 0

        def validate_then_replace_resolution(*args, **kwargs):
            nonlocal validation_count
            validation_count += 1
            result = validate_review_input_for_close(*args, **kwargs)
            if validation_count == 1:
                resolution = yaml.safe_load(reviewed_text)
                resolution["finding_resolutions"][0].update(
                    {
                        "status": "fixed",
                        "evidence_refs": ["transient unreviewed evidence"],
                        "operator": "other-process",
                        "resolved_at": "2026-08-16T00:00:00Z",
                    }
                )
                resolution_path.write_text(
                    yaml.safe_dump(resolution),
                    encoding="utf-8",
                )
            return result

        original_revalidate = pr_review_service.revalidate_review_input_at_transition

        def restore_then_revalidate(*args, **kwargs):
            if reviewed_resolution_exists:
                resolution_path.write_text(reviewed_text, encoding="utf-8")
            else:
                resolution_path.unlink()
            return original_revalidate(*args, **kwargs)

        with (
            patch(
                "ai_sdlc.cli.pr_review_cmd.validate_review_input_for_close",
                side_effect=validate_then_replace_resolution,
            ),
            patch(
                "ai_sdlc.core.pr_review_service.revalidate_review_input_at_transition",
                side_effect=restore_then_revalidate,
            ),
        ):
            close = runner.invoke(
                app,
                [
                    "pr-review",
                    "close",
                    "--review-id",
                    started["review_id"],
                    "--loop-id",
                    started["loop_id"],
                    "--expect-review-digest",
                    reviewed.input_digest,
                    "--json",
                ],
            )

    payload = json.loads(close.output)
    assert validation_count == 2
    assert close.exit_code == 1
    assert payload["status"] == "blocked"
    assert payload["verdict"] == "blocked"
    assert payload["blocker"] == "Unresolved REQUIRED findings remain."
    assert resolution_path.exists() is reviewed_resolution_exists
    if reviewed_resolution_exists:
        resolution = yaml.safe_load(resolution_path.read_text(encoding="utf-8"))
        assert resolution["finding_resolutions"][0]["status"] == "unresolved"


def test_pr_review_fix_dry_run_json_does_not_write_artifacts(tmp_path: Path) -> None:
    base_commit = _init_repo(tmp_path)
    _commit_file(tmp_path, "src/app.py", "print('hello')\n", "add app")

    with patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path):
        start = runner.invoke(
            app,
            [
                "pr-review",
                "start",
                "--base",
                base_commit,
                "--provider",
                "mock-reviewer",
                "--mock-fixture",
                "changes_required",
                "--review-id",
                "review-fix-dry-run-cli",
                "--json",
            ],
        )
        fix = runner.invoke(app, ["pr-review", "fix", "--dry-run", "--json"])

    assert start.exit_code == 10
    payload = json.loads(fix.output)
    assert fix.exit_code == 0
    assert payload["status"] == "ready"
    assert payload["dry_run"] is True
    assert payload["selected_findings_count"] == 1
    assert not Path(payload["fix_plan_path"]).exists()
    assert not Path(payload["resolution_path"]).exists()


def test_pr_review_rerun_json_regenerates_current_review(tmp_path: Path) -> None:
    base_commit = _init_repo(tmp_path)
    _commit_file(tmp_path, "src/app.py", "print('hello')\n", "add app")

    with patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path):
        start = runner.invoke(
            app,
            [
                "pr-review",
                "start",
                "--base",
                base_commit,
                "--provider",
                "mock-reviewer",
                "--review-id",
                "review-rerun-cli",
                "--json",
            ],
        )
        start_payload = json.loads(start.output)
        old_head = json.loads(
            Path(start_payload["review_pack_path"]).read_text(encoding="utf-8")
        )["head_commit"]
        _commit_file(tmp_path, "src/app.py", "print('updated')\n", "update app")
        rerun = runner.invoke(app, ["pr-review", "rerun", "--json"])

    assert rerun.exit_code == 0
    rerun_payload = json.loads(rerun.output)
    new_head = json.loads(
        Path(rerun_payload["review_pack_path"]).read_text(encoding="utf-8")
    )["head_commit"]
    assert rerun_payload["status"] == "started"
    assert new_head != old_head


def test_python_module_help_fallback_lists_pr_review() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ai_sdlc", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0
    assert "pr-review" in result.stdout


def _init_repo(path: Path, *, branch: str = "main") -> str:
    (path / ".ai-sdlc").mkdir()
    _git(path, "init", f"--initial-branch={branch}")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")
    _commit_file(path, "README.md", "# Test\n", "initial")
    return _git(path, "rev-parse", "HEAD")


def _commit_file(path: Path, file_path: str, content: str, message: str) -> None:
    _write_file(path, file_path, content)
    _git(path, "add", file_path)
    _git(path, "commit", "-m", message)


def _write_file(path: Path, file_path: str, content: str) -> None:
    target = path / file_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()
