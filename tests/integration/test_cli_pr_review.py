"""Integration tests for the ai-sdlc pr-review CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

from ai_sdlc.cli.main import app
from ai_sdlc.core.pr_review_service import (
    PRReviewAttestResult,
    PRReviewCommandStatus,
)
from ai_sdlc.core.source_snapshot import (
    SourceSnapshotOptions,
    build_source_snapshot,
)
from ai_sdlc.core.stage_review.artifacts import (
    resolve_canonical_shared_state,
    resolve_repository_project_id,
)
from ai_sdlc.core.stage_review.ci_certificate import (
    CI_CERTIFICATE_BUNDLE_PATH,
)
from ai_sdlc.core.stage_review.stage_review_execution import (
    StageCloseGateUnavailableError,
)

runner = CliRunner()


def test_pr_close_reports_stage_review_result_and_one_next_action(
    tmp_path: Path,
) -> None:
    failure = StageCloseGateUnavailableError("review-runtime-integrity-failure")
    with (
        patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path),
        patch("ai_sdlc.cli.pr_review_cmd.close_pr_review", side_effect=failure),
    ):
        result = runner.invoke(app, ["pr-review", "close", "--json"])

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert payload["reason_code"] == "review-runtime-integrity-failure"
    assert payload["request_id"]
    assert "ai-sdlc doctor" in payload["next_action"]


def test_pr_attest_reports_stage_review_result_and_one_next_action(
    tmp_path: Path,
) -> None:
    failure = StageCloseGateUnavailableError("review-provider-unavailable")
    bundle = tmp_path / CI_CERTIFICATE_BUNDLE_PATH
    bundle.parent.mkdir(parents=True)
    bundle.write_text('{"certificate_id":"stale"}\n', encoding="utf-8")
    with (
        patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path),
        patch("ai_sdlc.cli.pr_review_cmd.attest_pr_review", side_effect=failure),
    ):
        result = runner.invoke(app, ["pr-review", "attest", "--json"])

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["status"] == "needs_user"
    assert payload["reason_code"] == "review-provider-unavailable"
    assert payload["request_id"]
    assert "Restore an eligible reviewer provider" in payload["next_action"]
    assert "Traceback" not in result.output
    assert not bundle.exists()


def test_pr_attest_text_failure_has_one_result_and_next(tmp_path: Path) -> None:
    failure = StageCloseGateUnavailableError("review-runtime-integrity-failure")
    with (
        patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path),
        patch("ai_sdlc.cli.pr_review_cmd.attest_pr_review", side_effect=failure),
    ):
        result = runner.invoke(app, ["pr-review", "attest"])

    assert result.exit_code == 2
    assert result.output.count("Result:") == 1
    assert result.output.count("Next:") == 1
    assert "Traceback" not in result.output


def test_pr_attest_json_requires_explicit_bundle_commit_and_push(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / CI_CERTIFICATE_BUNDLE_PATH
    bundle.parent.mkdir(parents=True)
    bundle.write_text("{}\n", encoding="utf-8")
    ready = PRReviewAttestResult(
        status=PRReviewCommandStatus.READY,
        review_id="review.enforce",
        stage_review_session_id="session.enforce",
        stage_close_certificate_id="certificate.enforce",
        next_action="stale",
    )
    with (
        patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path),
        patch("ai_sdlc.cli.pr_review_cmd.attest_pr_review", return_value=ready),
        patch(
            "ai_sdlc.cli.pr_review_cmd.export_ci_certificate_bundle",
            return_value=bundle,
        ) as export_bundle,
    ):
        result = runner.invoke(app, ["pr-review", "attest", "--json"])

    assert result.exit_code == 0
    export_bundle.assert_called_once_with(
        tmp_path,
        close_kind="local-pr-review-attest",
        stage_instance_id=ready.review_id,
        review_session_id=ready.stage_review_session_id,
        certificate_id=ready.stage_close_certificate_id,
    )
    payload = json.loads(result.output)
    assert payload["ci_certificate_bundle_path"] == str(bundle)
    assert f"git add -- {CI_CERTIFICATE_BUNDLE_PATH}" in payload["next_action"]
    assert "commit it" in payload["next_action"]
    assert "push the reviewed branch" in payload["next_action"]
    assert "latest-attestation.json" not in payload["next_action"]


def test_pr_attest_text_requires_explicit_bundle_commit_and_push(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / CI_CERTIFICATE_BUNDLE_PATH
    bundle.parent.mkdir(parents=True)
    bundle.write_text("{}\n", encoding="utf-8")
    ready = PRReviewAttestResult(
        status=PRReviewCommandStatus.READY,
        review_id="review.enforce",
        stage_review_session_id="session.enforce",
        stage_close_certificate_id="certificate.enforce",
        next_action="stale",
    )
    with (
        patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path),
        patch("ai_sdlc.cli.pr_review_cmd.attest_pr_review", return_value=ready),
        patch(
            "ai_sdlc.cli.pr_review_cmd.export_ci_certificate_bundle",
            return_value=bundle,
        ),
    ):
        result = runner.invoke(app, ["pr-review", "attest"])

    assert result.exit_code == 0
    assert f"git add -- {CI_CERTIFICATE_BUNDLE_PATH}" in result.output
    assert "push the reviewed branch" in result.output
    assert f"ci_certificate_bundle: {bundle}" in result.output


def test_pr_attest_blocks_non_exportable_ci_bundle_without_traceback(
    tmp_path: Path,
) -> None:
    ready = PRReviewAttestResult(
        status=PRReviewCommandStatus.READY,
        review_id="review.local-patch",
        stage_review_session_id="session.local-patch",
        stage_close_certificate_id="certificate.local-patch",
        next_action="stale",
    )
    with (
        patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path),
        patch("ai_sdlc.cli.pr_review_cmd.attest_pr_review", return_value=ready),
        patch(
            "ai_sdlc.cli.pr_review_cmd.export_ci_certificate_bundle",
            side_effect=ValueError("CI certificate candidate source is not exportable"),
        ),
    ):
        result = runner.invoke(app, ["pr-review", "attest", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert "not exportable" in payload["blocker"]
    assert "local-git-range" in payload["next_action"]
    assert "Traceback" not in result.output


def test_pr_attest_blocks_and_clears_stale_bundle_when_exact_proof_is_missing(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / CI_CERTIFICATE_BUNDLE_PATH
    bundle.parent.mkdir(parents=True)
    bundle.write_text('{"certificate_id":"stale"}\n', encoding="utf-8")
    ready = PRReviewAttestResult(
        status=PRReviewCommandStatus.READY,
        review_id="review.current",
        stage_review_session_id="session.current",
        stage_close_certificate_id="certificate.current",
    )
    with (
        patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path),
        patch("ai_sdlc.cli.pr_review_cmd.attest_pr_review", return_value=ready),
        patch(
            "ai_sdlc.cli.pr_review_cmd.export_ci_certificate_bundle",
            return_value=None,
        ),
    ):
        result = runner.invoke(app, ["pr-review", "attest", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert "exact certificate proof" in payload["blocker"]
    assert not bundle.exists()


def test_pr_attest_failure_clears_previous_exact_bundle_under_attest_lock(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / CI_CERTIFICATE_BUNDLE_PATH
    bundle.parent.mkdir(parents=True)
    bundle.write_text('{"published":"concurrently"}\n', encoding="utf-8")
    ready = PRReviewAttestResult(
        status=PRReviewCommandStatus.READY,
        review_id="review.current",
        stage_review_session_id="session.current",
        stage_close_certificate_id="certificate.current",
    )
    current = SimpleNamespace(
        certificate=SimpleNamespace(
            certificate_id="certificate.current",
            scope=SimpleNamespace(session_id="session.current"),
        )
    )
    with (
        patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path),
        patch("ai_sdlc.cli.pr_review_cmd.attest_pr_review", return_value=ready),
        patch(
            "ai_sdlc.cli.pr_review_cmd.export_ci_certificate_bundle",
            return_value=None,
        ),
        patch(
            "ai_sdlc.cli.pr_review_cmd.read_ci_certificate_bundle",
            return_value=current,
        ),
    ):
        result = runner.invoke(app, ["pr-review", "attest", "--json"])

    assert result.exit_code == 1
    assert not bundle.exists()
    assert json.loads(result.output)["status"] == "blocked"


def test_pr_attest_non_ready_result_clears_stale_ci_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / CI_CERTIFICATE_BUNDLE_PATH
    bundle.parent.mkdir(parents=True)
    bundle.write_text('{"certificate_id":"stale"}\n', encoding="utf-8")
    blocked = PRReviewAttestResult(
        status=PRReviewCommandStatus.BLOCKED,
        blocker="review evidence changed",
    )
    with (
        patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path),
        patch("ai_sdlc.cli.pr_review_cmd.attest_pr_review", return_value=blocked),
    ):
        result = runner.invoke(app, ["pr-review", "attest", "--json"])

    assert result.exit_code == 1
    assert not bundle.exists()


def test_pr_attest_keeps_runtime_lock_out_of_candidate_source(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    _write_file(tmp_path, "src/app.py", "print('candidate')\n")
    blocked = PRReviewAttestResult(
        status=PRReviewCommandStatus.BLOCKED,
        blocker="review evidence changed",
    )
    with (
        patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path),
        patch(
            "ai_sdlc.cli.pr_review_cmd.attest_pr_review",
            return_value=blocked,
        ),
    ):
        result = runner.invoke(app, ["pr-review", "attest", "--json"])

    assert result.exit_code == 1
    shared = resolve_canonical_shared_state(
        tmp_path,
        resolve_repository_project_id(tmp_path),
    )
    assert (shared / "locks/pr-review-attest.lock").is_file()
    assert not (
        tmp_path / ".ai-sdlc/attestations/.pr-review-attest.lock"
    ).exists()
    assert not (tmp_path / ".ai-sdlc/local").exists()
    assert _git(
        tmp_path,
        "status",
        "--porcelain",
        "--untracked-files=all",
    ) == "?? src/app.py"
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )
    assert snapshot.changed_files == ["src/app.py"]


def test_pr_attest_blocks_when_shared_lock_identity_is_corrupt(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    attestation = (
        tmp_path / ".ai-sdlc/reviews/pr/latest-attestation.json"
    )
    attestation.parent.mkdir(parents=True)
    attestation.write_text('{"review_id":"stale"}\n', encoding="utf-8")
    bundle = tmp_path / CI_CERTIFICATE_BUNDLE_PATH
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text('{"certificate_id":"stale"}\n', encoding="utf-8")
    identity = (
        tmp_path / ".git/ai-sdlc-shared-state/repository-project.json"
    )
    identity.parent.mkdir(parents=True)
    identity.write_text("{not-json", encoding="utf-8")

    with patch(
        "ai_sdlc.cli.pr_review_cmd.find_project_root",
        return_value=tmp_path,
    ):
        result = runner.invoke(app, ["pr-review", "attest", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert "shared lock state is unavailable" in payload["blocker"]
    assert "ai-sdlc doctor" in payload["next_action"]
    assert "Traceback" not in result.output
    assert not attestation.exists()
    assert not bundle.exists()


def test_pr_attest_incomplete_identity_clears_stale_ci_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / CI_CERTIFICATE_BUNDLE_PATH
    bundle.parent.mkdir(parents=True)
    bundle.write_text('{"certificate_id":"stale"}\n', encoding="utf-8")
    incomplete = PRReviewAttestResult(
        status=PRReviewCommandStatus.READY,
        review_id="review.current",
        stage_review_session_id="session.current",
    )
    with (
        patch("ai_sdlc.cli.pr_review_cmd.find_project_root", return_value=tmp_path),
        patch("ai_sdlc.cli.pr_review_cmd.attest_pr_review", return_value=incomplete),
    ):
        result = runner.invoke(app, ["pr-review", "attest", "--json"])

    assert result.exit_code == 1
    assert not bundle.exists()


def test_pr_review_help_lists_p0_commands() -> None:
    result = runner.invoke(app, ["pr-review", "--help"])

    assert result.exit_code == 0
    assert "doctor" in result.output
    assert "start" in result.output
    assert "status" in result.output
    assert "fix" in result.output
    assert "rerun" in result.output
    assert "close" in result.output
    assert "attest" in result.output


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
        close = runner.invoke(
            app,
            ["pr-review", "close", "--require-no-blockers", "--json"],
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


def test_pr_review_attest_json_writes_latest_attestation(tmp_path: Path) -> None:
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
                "review-attest-cli",
                "--json",
            ],
        )
        close = runner.invoke(app, ["pr-review", "close", "--json"])
        attest = runner.invoke(app, ["pr-review", "attest", "--json"])

    assert start.exit_code == 0
    assert close.exit_code == 0
    payload = json.loads(attest.output)
    assert attest.exit_code == 0
    assert payload["status"] == "ready"
    assert payload["review_id"] == "review-attest-cli"
    assert "must not call any model" in payload["next_action"]
    attestation = json.loads(
        Path(payload["attestation_path"]).read_text(encoding="utf-8")
    )
    assert attestation["review_id"] == "review-attest-cli"
    assert attestation["diff_source"]["source_kind"] == "local-git-range"
    assert attestation["ci_may_call_model"] is False


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
