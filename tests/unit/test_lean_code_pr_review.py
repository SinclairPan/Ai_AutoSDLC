"""Local PR Review 对 fresh Lean artifact 与 digest 链的测试。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ai_sdlc.core.close_check import _local_pr_review_artifact_blocker
from ai_sdlc.core.implementation_loop import close_implementation_loop
from ai_sdlc.core.implementation_models import (
    CURRENT_IMPLEMENTATION_PATH,
    ImplementationClose,
    ImplementationCloseOptions,
    ImplementationCurrentPointer,
    ImplementationInput,
    ImplementationProgress,
    ImplementationTaskItem,
    ImplementationTaskProgress,
    ImplementationTasks,
    ImplementationTaskStatus,
)
from ai_sdlc.core.implementation_store import implementation_artifacts
from ai_sdlc.core.lean_code_execution import LeanExecutionOptions, run_lean_command
from ai_sdlc.core.lean_code_models import LeanEvaluationReport, LeanException
from ai_sdlc.core.lean_code_policy import stable_artifact_digest
from ai_sdlc.core.lean_code_review import (
    resolve_lean_review_binding,
    validate_review_run_lean_binding,
)
from ai_sdlc.core.lean_code_review_scope_models import (
    IMPLEMENTATION_CLOSE_PROOF_NAME,
    LEAN_CLOSED_SCOPE_NAME,
    ClosedLeanReviewScope,
)
from ai_sdlc.core.lean_code_runtime import LeanCheckOptions, run_lean_check
from ai_sdlc.core.loop_artifacts import LoopArtifactStore
from ai_sdlc.core.loop_models import LoopRound, LoopRun, LoopStatus, LoopType
from ai_sdlc.core.pr_review_models import ReviewAttestation, ReviewPack, ReviewRun
from ai_sdlc.core.pr_review_pack import ReviewPackBuildOptions, build_review_pack
from ai_sdlc.core.pr_review_provider import MockReviewerFixture
from ai_sdlc.core.pr_review_service import (
    PRReviewCommandStatus,
    PRReviewStartOptions,
    attest_pr_review,
    close_pr_review,
    doctor_pr_review,
    start_pr_review,
    status_pr_review,
)
from ai_sdlc.models.work import WorkType


def test_review_pack_contains_fresh_lean_digest_chain(tmp_path: Path) -> None:
    _seed_lean_loop(tmp_path, "impl-review")
    binding, blocker = resolve_lean_review_binding(tmp_path)
    assert blocker == ""
    assert binding is not None

    result = build_review_pack(
        ReviewPackBuildOptions(
            root=tmp_path,
            base_ref="",
            diff_source="local-unstaged",
            review_id="review-lean",
            loop_id="pr-loop-lean",
            requested_provider="local-agent",
            current_model="gpt-test",
            lean_binding=binding,
        )
    )

    assert result.status == "ready"
    assert result.review_pack is not None
    pack = result.review_pack
    assert pack.lean_report_path == binding.report_path
    assert pack.lean_report_digest == binding.report_digest
    assert pack.lean_report_markdown_digest == binding.report_markdown_digest
    assert pack.lean_input_digest == binding.input_digest
    assert pack.lean_snapshot_digest == binding.snapshot_digest
    assert pack.lean_findings_digest == binding.findings_digest
    assert pack.lean_policy_snapshot_digest == binding.policy_snapshot_digest
    assert pack.lean_diff_hash == binding.diff_hash


def test_pr_review_blocks_lean_binding_for_different_diff_source(
    tmp_path: Path,
) -> None:
    _seed_lean_loop(tmp_path, "impl-review-source-mismatch")
    patch = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", "--no-textconv"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    ).stdout
    patch_path = tmp_path / ".ai-sdlc" / "reviews" / "review-source.patch"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_bytes(patch)

    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="patch",
            patch_file=patch_path.relative_to(tmp_path).as_posix(),
            provider_id="mock-reviewer",
            review_id="review-source-mismatch",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )

    assert started.status == PRReviewCommandStatus.BLOCKED
    assert "Lean source snapshot" in started.blocker
    assert "diff source" in started.blocker


def test_pr_review_preview_blocks_stale_lean_report(tmp_path: Path) -> None:
    _seed_lean_loop(tmp_path, "impl-preview-stale")
    _write(tmp_path, "src/app.py", "def _small():\n    return 2\n")

    dry_run = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-preview-stale",
            dry_run=True,
        )
    )
    doctor = doctor_pr_review(
        root=tmp_path,
        base_ref="",
        diff_source="local-unstaged",
        provider_id="mock-reviewer",
    )

    assert dry_run.status == PRReviewCommandStatus.BLOCKED
    assert doctor.status == PRReviewCommandStatus.BLOCKED
    assert "lean" in dry_run.blocker.lower()
    assert doctor.blocker == dry_run.blocker


def test_closed_implementation_does_not_bind_later_pr_review(tmp_path: Path) -> None:
    loop_id = "impl-closed-before-later-review"
    _seed_lean_loop(tmp_path, loop_id)
    artifacts = implementation_artifacts(tmp_path, loop_id)
    loop_run = LoopRun.model_validate_json(
        artifacts.loop_run_path.read_text(encoding="utf-8")
    )
    loop_run.status = LoopStatus.CLOSED
    LoopArtifactStore(tmp_path).write_json_artifact(
        artifacts.loop_run_path,
        loop_run,
    )
    missing_binding, missing_close_blocker = resolve_lean_review_binding(
        tmp_path,
        allow_closed_source_mismatch=True,
    )
    assert missing_binding is None
    assert "closed state" in missing_close_blocker

    _write_close_state(tmp_path, loop_id)
    matching_binding, blocker = resolve_lean_review_binding(
        tmp_path,
        allow_closed_source_mismatch=True,
    )
    matching_pack = build_review_pack(
        ReviewPackBuildOptions(
            root=tmp_path,
            base_ref="",
            diff_source="local-unstaged",
            review_id="review-closed-implementation",
            loop_id="pr-loop-closed-implementation",
            requested_provider="local-agent",
            current_model="gpt-test",
            lean_binding=matching_binding,
        )
    )
    assert blocker == ""
    assert matching_binding is not None
    assert matching_pack.status == "ready"
    assert matching_pack.review_pack is not None
    assert matching_pack.review_pack.lean_report_path == matching_binding.report_path

    _git(tmp_path, "add", "src/app.py")
    _git(tmp_path, "commit", "-m", "finish implementation")
    _write(tmp_path, "README.md", "# Later documentation change\n")

    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-after-closed-implementation",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )

    assert started.status == PRReviewCommandStatus.STARTED, started.blocker
    review_run = ReviewRun.model_validate_json(
        (Path(started.review_dir) / "review-run.json").read_text(encoding="utf-8")
    )
    assert review_run.lean_report_path == ""
    pack = ReviewPack.model_validate_json(
        (tmp_path / review_run.review_pack_path).read_text(encoding="utf-8")
    )
    assert pack.policy_decisions["lean_binding_resolution"] == (
        "closed_source_mismatch"
    )
    closed_scope = ClosedLeanReviewScope.model_validate_json(
        (tmp_path / review_run.review_pack_path)
        .with_name(LEAN_CLOSED_SCOPE_NAME)
        .read_text(encoding="utf-8")
    )
    assert closed_scope.close.digest
    assert closed_scope.implementation_report.digest
    assert closed_scope.lean_pointer.digest
    assert validate_review_run_lean_binding(tmp_path, review_run) == ""

    closed = close_pr_review(tmp_path)
    attested = attest_pr_review(tmp_path)

    assert closed.status == PRReviewCommandStatus.CLOSED, closed.blocker
    assert attested.status == PRReviewCommandStatus.READY, attested.blocker


def test_closed_scope_rejects_noncanonical_close_report(tmp_path: Path) -> None:
    loop_id = "impl-forged-close-report"
    _seed_lean_loop(tmp_path, loop_id)
    _write_close_state(tmp_path, loop_id, report_path="README.md")

    binding, blocker = resolve_lean_review_binding(
        tmp_path,
        allow_closed_source_mismatch=True,
    )

    assert binding is None
    assert "report_path" in blocker


def test_closed_scope_keeps_snapshot_integrity_errors_blocking(
    tmp_path: Path,
) -> None:
    loop_id = "impl-closed-snapshot-tamper"
    _seed_lean_loop(tmp_path, loop_id)
    _write_close_state(tmp_path, loop_id)
    binding, blocker = resolve_lean_review_binding(
        tmp_path,
        allow_closed_source_mismatch=True,
    )
    assert blocker == ""
    assert binding is not None
    snapshot = tmp_path / binding.snapshot_path
    snapshot.write_text(snapshot.read_text("utf-8") + "\n", encoding="utf-8")

    result = build_review_pack(
        ReviewPackBuildOptions(
            root=tmp_path,
            base_ref="",
            diff_source="local-unstaged",
            review_id="review-closed-snapshot-tamper",
            loop_id="pr-loop-closed-snapshot-tamper",
            requested_provider="local-agent",
            current_model="gpt-test",
            lean_binding=binding,
        )
    )

    assert result.status == "blocked"
    assert "digest" in result.blocker.lower()


def test_matching_closed_review_rechecks_frozen_close_digest(tmp_path: Path) -> None:
    loop_id = "impl-matching-close-digest"
    _seed_lean_loop(tmp_path, loop_id)
    _write_close_state(tmp_path, loop_id)
    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-matching-close-digest",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )
    assert started.status == PRReviewCommandStatus.STARTED, started.blocker
    first_closed = close_pr_review(tmp_path)
    first_attested = attest_pr_review(tmp_path)
    assert first_closed.status == PRReviewCommandStatus.CLOSED
    assert first_attested.status == PRReviewCommandStatus.READY
    review_run = ReviewRun.model_validate_json(
        (Path(started.review_dir) / "review-run.json").read_text(encoding="utf-8")
    )
    assert review_run.lean_report_path
    close_path = implementation_artifacts(tmp_path, loop_id).close_path
    close_payload = json.loads(close_path.read_text(encoding="utf-8"))
    close_payload["closed_by"] = "changed-after-review"
    close_path.write_text(json.dumps(close_payload), encoding="utf-8")

    status = status_pr_review(tmp_path)
    closed = close_pr_review(tmp_path)
    attested = attest_pr_review(tmp_path)

    assert status.status == PRReviewCommandStatus.BLOCKED
    assert closed.status == PRReviewCommandStatus.BLOCKED
    assert attested.status == PRReviewCommandStatus.BLOCKED
    assert "close" in closed.blocker.lower()
    assert "digest" in closed.blocker.lower()
    assert "restore" in status.next_action.lower()
    assert status.next_action == closed.next_action == attested.next_action


def test_matching_closed_review_rechecks_implementation_report_digest(
    tmp_path: Path,
) -> None:
    loop_id = "impl-matching-implementation-report"
    _seed_lean_loop(tmp_path, loop_id)
    _write_close_state(tmp_path, loop_id)
    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-matching-implementation-report",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )
    assert started.status == PRReviewCommandStatus.STARTED, started.blocker
    report_path = implementation_artifacts(tmp_path, loop_id).report_json_path
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["next_action"] = "changed after review"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    closed = close_pr_review(tmp_path)

    assert closed.status == PRReviewCommandStatus.BLOCKED
    assert "implementation report" in closed.blocker.lower()
    assert "digest" in closed.blocker.lower()


def test_historical_review_rechecks_complete_closed_evidence_chain(
    tmp_path: Path,
) -> None:
    loop_id = "impl-historical-complete-chain"
    _seed_lean_loop(tmp_path, loop_id)
    _write_close_state(tmp_path, loop_id)
    binding, blocker = resolve_lean_review_binding(
        tmp_path,
        allow_closed_source_mismatch=True,
    )
    assert blocker == ""
    assert binding is not None
    _git(tmp_path, "add", "src/app.py")
    _git(tmp_path, "commit", "-m", "finish implementation")
    _write(tmp_path, "README.md", "# Later documentation change\n")
    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-historical-complete-chain",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )
    assert started.status == PRReviewCommandStatus.STARTED, started.blocker
    markdown_report = tmp_path / binding.report_markdown_path
    markdown_report.write_text(
        markdown_report.read_text(encoding="utf-8") + "\nchanged after review\n",
        encoding="utf-8",
    )

    closed = close_pr_review(tmp_path)
    attested = attest_pr_review(tmp_path)

    assert closed.status == PRReviewCommandStatus.BLOCKED
    assert attested.status == PRReviewCommandStatus.BLOCKED
    assert "markdown report" in closed.blocker.lower()
    assert "digest" in closed.blocker.lower()


def test_historical_review_rechecks_implementation_report_digest(
    tmp_path: Path,
) -> None:
    loop_id = "impl-historical-implementation-report"
    _seed_lean_loop(tmp_path, loop_id)
    _write_close_state(tmp_path, loop_id)
    _git(tmp_path, "add", "src/app.py")
    _git(tmp_path, "commit", "-m", "finish implementation")
    _write(tmp_path, "README.md", "# Later documentation change\n")
    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-historical-implementation-report",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )
    assert started.status == PRReviewCommandStatus.STARTED, started.blocker
    report_path = implementation_artifacts(tmp_path, loop_id).report_json_path
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["next_action"] = "changed after review"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    closed = close_pr_review(tmp_path)
    attested = attest_pr_review(tmp_path)

    assert closed.status == PRReviewCommandStatus.BLOCKED
    assert attested.status == PRReviewCommandStatus.BLOCKED
    assert "implementation report" in closed.blocker.lower()
    assert "digest" in closed.blocker.lower()


def test_legacy_closed_implementation_is_scoped_on_first_later_review(
    tmp_path: Path,
) -> None:
    loop_id = "impl-legacy-close-scope"
    _seed_lean_loop(tmp_path, loop_id)
    _write_close_state(tmp_path, loop_id)
    artifacts = implementation_artifacts(tmp_path, loop_id)
    _downgrade_close_to_legacy(tmp_path, loop_id)
    _git(tmp_path, "add", "src/app.py")
    _git(tmp_path, "commit", "-m", "finish implementation")
    _write(tmp_path, "README.md", "# Later documentation change\n")

    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-legacy-close-scope",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )

    assert started.status == PRReviewCommandStatus.STARTED, started.blocker
    review_run = ReviewRun.model_validate_json(
        (Path(started.review_dir) / "review-run.json").read_text(encoding="utf-8")
    )
    scope_path = (tmp_path / review_run.review_pack_path).with_name(
        LEAN_CLOSED_SCOPE_NAME
    )
    assert ClosedLeanReviewScope.model_validate_json(scope_path.read_text("utf-8"))
    report = json.loads(artifacts.report_json_path.read_text(encoding="utf-8"))
    report["next_action"] = "changed after review"
    artifacts.report_json_path.write_text(json.dumps(report), encoding="utf-8")

    closed = close_pr_review(tmp_path)

    assert closed.status == PRReviewCommandStatus.BLOCKED
    assert "implementation report" in closed.blocker.lower()
    assert "digest" in closed.blocker.lower()


def test_legacy_close_rejects_report_for_a_different_work_item(
    tmp_path: Path,
) -> None:
    loop_id = "impl-legacy-work-item-mismatch"
    _seed_lean_loop(tmp_path, loop_id)
    _write_close_state(tmp_path, loop_id)
    artifacts = implementation_artifacts(tmp_path, loop_id)
    _downgrade_close_to_legacy(tmp_path, loop_id)
    report = json.loads(artifacts.report_json_path.read_text(encoding="utf-8"))
    report["work_item_id"] = "WI-DIFFERENT"
    artifacts.report_json_path.write_text(json.dumps(report), encoding="utf-8")
    _git(tmp_path, "add", "src/app.py")
    _git(tmp_path, "commit", "-m", "finish implementation")
    _write(tmp_path, "README.md", "# Later documentation change\n")

    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-legacy-work-item-mismatch",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )

    assert started.status == PRReviewCommandStatus.BLOCKED
    assert "work item" in started.blocker.lower()


def test_legacy_close_drift_recommends_restoring_close_artifacts(
    tmp_path: Path,
) -> None:
    loop_id = "impl-legacy-close-drift"
    _seed_lean_loop(tmp_path, loop_id)
    _write_close_state(tmp_path, loop_id)
    artifacts = implementation_artifacts(tmp_path, loop_id)
    _downgrade_close_to_legacy(tmp_path, loop_id)
    _git(tmp_path, "add", "src/app.py")
    _git(tmp_path, "commit", "-m", "finish implementation")
    _write(tmp_path, "README.md", "# Later documentation change\n")
    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-legacy-close-drift",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )
    assert started.status == PRReviewCommandStatus.STARTED, started.blocker
    assert close_pr_review(tmp_path).status == PRReviewCommandStatus.CLOSED
    assert attest_pr_review(tmp_path).status == PRReviewCommandStatus.READY
    close = json.loads(artifacts.close_path.read_text(encoding="utf-8"))
    close["closed_by"] = "changed after review"
    artifacts.close_path.write_text(json.dumps(close), encoding="utf-8")

    status = status_pr_review(tmp_path)
    closed = close_pr_review(tmp_path)
    attested = attest_pr_review(tmp_path)

    assert status.status == PRReviewCommandStatus.BLOCKED
    assert closed.status == PRReviewCommandStatus.BLOCKED
    assert attested.status == PRReviewCommandStatus.BLOCKED
    assert "implementation close" in status.blocker.lower()
    assert "restore" in status.next_action.lower()
    assert status.next_action == closed.next_action == attested.next_action


def test_current_close_requires_its_recorded_proof(tmp_path: Path) -> None:
    loop_id = "impl-current-close-proof-required"
    _seed_lean_loop(tmp_path, loop_id)
    _write_close_state(tmp_path, loop_id)
    artifacts = implementation_artifacts(tmp_path, loop_id)
    artifacts.close_path.with_name(IMPLEMENTATION_CLOSE_PROOF_NAME).unlink()
    _remove_close_proof_marker(tmp_path, loop_id)
    report = json.loads(artifacts.report_json_path.read_text(encoding="utf-8"))
    report["evidence_count"] = 999
    artifacts.report_json_path.write_text(json.dumps(report), encoding="utf-8")

    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-current-close-proof-required",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )

    assert started.status == PRReviewCommandStatus.BLOCKED
    assert "close proof" in started.blocker.lower()
    assert "missing" in started.blocker.lower()
    assert "restore" in started.next_action.lower()


def test_closed_scope_rejects_pointer_identity_mismatch(tmp_path: Path) -> None:
    loop_id = "impl-pointer-identity-mismatch"
    _seed_lean_loop(tmp_path, loop_id)
    _write_close_state(tmp_path, loop_id)
    pointer_path = (
        implementation_artifacts(tmp_path, loop_id).loop_dir / "lean/current.json"
    )
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["diff_hash"] = f"sha256:{'0' * 64}"
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-pointer-identity-mismatch",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )

    assert started.status == PRReviewCommandStatus.BLOCKED
    assert "diff hash" in started.blocker.lower()


def test_closed_scope_uses_sidecars_without_changing_schema_v1_shapes(
    tmp_path: Path,
) -> None:
    loop_id = "impl-schema-v1-sidecars"
    _seed_lean_loop(tmp_path, loop_id)
    _write_close_state(tmp_path, loop_id)
    artifacts = implementation_artifacts(tmp_path, loop_id)
    close = json.loads(artifacts.close_path.read_text(encoding="utf-8"))
    assert "report_digest" not in close
    assert "work_item_id" not in close
    assert close["created_by"] == "ai-sdlc+implementation-close-proof-v1"
    proof_path = artifacts.close_path.with_name(IMPLEMENTATION_CLOSE_PROOF_NAME)
    assert proof_path.exists()
    loop_run = LoopRun.model_validate_json(
        artifacts.loop_run_path.read_text(encoding="utf-8")
    )
    execution_round = next(
        item for item in loop_run.rounds if item.round_kind == "execution"
    )
    assert (
        proof_path.relative_to(tmp_path).as_posix() in execution_round.output_artifacts
    )

    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-schema-v1-sidecars",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )
    assert started.status == PRReviewCommandStatus.STARTED, started.blocker
    pack_path = Path(started.review_dir) / "review-pack.json"
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    assert "lean_closed_scope" not in pack
    assert pack_path.with_name(LEAN_CLOSED_SCOPE_NAME).exists()
    assert pack["policy_decisions"]["lean_closed_scope_path"].endswith(
        LEAN_CLOSED_SCOPE_NAME
    )
    assert pack["policy_decisions"]["lean_closed_scope_digest"].startswith("sha256:")


def test_closed_review_rechecks_scope_sidecar_digest(tmp_path: Path) -> None:
    loop_id = "impl-scope-sidecar-digest"
    _seed_lean_loop(tmp_path, loop_id)
    _write_close_state(tmp_path, loop_id)
    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-scope-sidecar-digest",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )
    assert started.status == PRReviewCommandStatus.STARTED, started.blocker
    scope_path = Path(started.review_dir) / LEAN_CLOSED_SCOPE_NAME
    scope_path.write_text(
        scope_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    status = status_pr_review(tmp_path)
    closed = close_pr_review(tmp_path)

    assert status.status == PRReviewCommandStatus.BLOCKED
    assert "scope digest" in status.blocker.lower()
    assert closed.status == PRReviewCommandStatus.BLOCKED
    assert "scope digest" in closed.blocker.lower()
    assert "rerun" in status.next_action.lower()
    assert status.next_action == closed.next_action


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("artifact_kind", "wrong-close-kind", "artifact kind"),
        ("closed_at", "", "closed_at"),
        ("closed_by", "", "closed_by"),
        ("next_loop_type", "requirement", "next loop"),
    ],
)
def test_closed_scope_rejects_invalid_close_semantics(
    tmp_path: Path,
    field: str,
    value: str,
    expected: str,
) -> None:
    loop_id = f"impl-invalid-close-{field.replace('_', '-')}"
    _seed_lean_loop(tmp_path, loop_id)
    _write_close_state(tmp_path, loop_id)
    close_path = implementation_artifacts(tmp_path, loop_id).close_path
    _downgrade_close_to_legacy(tmp_path, loop_id)
    close = json.loads(close_path.read_text(encoding="utf-8"))
    close[field] = value
    close_path.write_text(json.dumps(close), encoding="utf-8")

    binding, blocker = resolve_lean_review_binding(
        tmp_path,
        allow_closed_source_mismatch=True,
    )

    assert binding is None
    assert expected in blocker.lower()


def test_pr_review_preview_blocks_different_lean_diff_source(tmp_path: Path) -> None:
    _seed_lean_loop(tmp_path, "impl-preview-source-mismatch")
    patch = _git_output(tmp_path, "diff", "--binary", "--no-ext-diff", "--no-textconv")
    patch_path = tmp_path / ".ai-sdlc" / "reviews" / "preview-source.patch"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_bytes(patch)
    patch_ref = patch_path.relative_to(tmp_path).as_posix()

    dry_run = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="patch",
            patch_file=patch_ref,
            provider_id="mock-reviewer",
            review_id="review-preview-source-mismatch",
            dry_run=True,
        )
    )
    doctor = doctor_pr_review(
        root=tmp_path,
        base_ref="",
        diff_source="patch",
        patch_file=patch_ref,
        provider_id="mock-reviewer",
    )

    assert dry_run.status == PRReviewCommandStatus.BLOCKED
    assert doctor.status == PRReviewCommandStatus.BLOCKED
    assert "diff source" in dry_run.blocker
    assert doctor.blocker == dry_run.blocker


def test_pr_review_blocks_patch_binding_for_different_head(tmp_path: Path) -> None:
    patch_file = ".ai-sdlc/reviews/lean-source.patch"
    _seed_lean_loop(
        tmp_path,
        "impl-patch-head-mismatch",
        source_kind="patch",
        patch_file=patch_file,
    )
    head = _git_output(tmp_path, "rev-parse", "HEAD").decode().strip()
    tree = _git_output(tmp_path, "rev-parse", "HEAD^{tree}").decode().strip()
    other = (
        subprocess.run(
            ["git", "commit-tree", tree, "-p", head],
            cwd=tmp_path,
            input=b"other head\n",
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    _git(tmp_path, "branch", "other", other)

    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="patch",
            patch_file=patch_file,
            head_ref="other",
            provider_id="mock-reviewer",
            review_id="review-patch-head-mismatch",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )

    assert started.status == PRReviewCommandStatus.BLOCKED
    assert "Lean source snapshot" in started.blocker
    assert "diff source" in started.blocker


def test_pr_review_accepts_matching_patch_rename_binding(tmp_path: Path) -> None:
    patch_file = ".ai-sdlc/reviews/lean-rename.patch"
    _seed_lean_loop(
        tmp_path,
        "impl-patch-rename",
        source_kind="patch",
        patch_file=patch_file,
        rename_to="src/renamed.py",
    )

    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="patch",
            patch_file=patch_file,
            provider_id="mock-reviewer",
            review_id="review-patch-rename",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )

    assert started.status == PRReviewCommandStatus.STARTED, started.blocker


def test_pr_review_includes_untracked_files_from_matching_unstaged_source(
    tmp_path: Path,
) -> None:
    _seed_lean_loop(
        tmp_path,
        "impl-review-untracked",
        include_untracked=True,
    )

    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-untracked",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )

    assert started.status == PRReviewCommandStatus.STARTED, started.blocker
    diff = (Path(started.review_dir) / "diff.patch").read_text("utf-8")
    assert "tests/untracked_probe.py" in diff
    assert "print('untracked')" in diff

    closed = close_pr_review(tmp_path)

    assert closed.status == PRReviewCommandStatus.CLOSED, closed.blocker


def test_pr_review_blocks_changed_untracked_file_after_unstaged_review(
    tmp_path: Path,
) -> None:
    _seed_lean_loop(
        tmp_path,
        "impl-review-untracked-changed",
        include_untracked=True,
    )
    start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-untracked-changed",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )
    _write(tmp_path, "tests/untracked_probe.py", "print('changed')\n")

    closed = close_pr_review(tmp_path)

    assert closed.status == PRReviewCommandStatus.BLOCKED
    assert "changed" in closed.blocker.lower()


def test_review_run_detects_lean_report_tamper_at_same_path(tmp_path: Path) -> None:
    _seed_lean_loop(tmp_path, "impl-tamper")
    binding, blocker = resolve_lean_review_binding(tmp_path)
    assert blocker == ""
    assert binding is not None
    review_run = ReviewRun(
        review_id="review-tamper",
        loop_id="pr-loop-tamper",
        lean_report_path=binding.report_path,
        lean_report_digest=binding.report_digest,
        lean_report_markdown_path=binding.report_markdown_path,
        lean_report_markdown_digest=binding.report_markdown_digest,
        lean_input_path=binding.input_path,
        lean_input_digest=binding.input_digest,
        lean_snapshot_path=binding.snapshot_path,
        lean_snapshot_digest=binding.snapshot_digest,
        lean_findings_path=binding.findings_path,
        lean_findings_digest=binding.findings_digest,
        lean_policy_path=binding.policy_path,
        lean_policy_snapshot_digest=binding.policy_snapshot_digest,
        lean_diff_hash=binding.diff_hash,
        lean_policy_digest=binding.policy_digest,
        lean_implementation_loop_id=binding.implementation_loop_id,
        lean_work_item_id=binding.work_item_id,
    )
    assert validate_review_run_lean_binding(tmp_path, review_run) == ""

    report = tmp_path / binding.report_path
    report.write_text(report.read_text("utf-8") + "\n", encoding="utf-8")

    assert "changed" in validate_review_run_lean_binding(tmp_path, review_run).lower()


def test_review_run_cannot_remove_required_lean_binding(tmp_path: Path) -> None:
    _seed_lean_loop(tmp_path, "impl-binding-removed")
    binding, blocker = resolve_lean_review_binding(tmp_path)
    assert blocker == ""
    assert binding is not None
    review_run = ReviewRun(
        review_id="review-binding-removed",
        loop_id="pr-loop-binding-removed",
        lean_report_path=binding.report_path,
        lean_report_digest=binding.report_digest,
        lean_report_markdown_path=binding.report_markdown_path,
        lean_report_markdown_digest=binding.report_markdown_digest,
        lean_input_path=binding.input_path,
        lean_input_digest=binding.input_digest,
        lean_snapshot_path=binding.snapshot_path,
        lean_snapshot_digest=binding.snapshot_digest,
        lean_findings_path=binding.findings_path,
        lean_findings_digest=binding.findings_digest,
        lean_policy_path=binding.policy_path,
        lean_policy_snapshot_digest=binding.policy_snapshot_digest,
        lean_diff_hash=binding.diff_hash,
        lean_policy_digest=binding.policy_digest,
        lean_implementation_loop_id=binding.implementation_loop_id,
        lean_work_item_id=binding.work_item_id,
    ).model_copy(update={"lean_report_path": ""})

    validation = validate_review_run_lean_binding(tmp_path, review_run)

    assert "incomplete" in validation.lower()


def test_partial_lean_binding_is_not_treated_as_legacy(tmp_path: Path) -> None:
    legacy = ReviewRun(review_id="legacy-review", loop_id="legacy-loop")
    assert validate_review_run_lean_binding(tmp_path, legacy) == ""

    for update in (
        {"lean_report_digest": "sha256:deadbeef"},
        {"lean_implementation_loop_id": "impl-old"},
    ):
        validation = validate_review_run_lean_binding(
            tmp_path,
            legacy.model_copy(update=update),
        )
        assert "incomplete" in validation.lower()


def test_review_pack_prevents_fully_removed_lean_binding(tmp_path: Path) -> None:
    _seed_lean_loop(tmp_path, "impl-pack-binding")
    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-pack-binding",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )
    assert started.status == PRReviewCommandStatus.STARTED
    review_run = ReviewRun.model_validate_json(
        (Path(started.review_dir) / "review-run.json").read_text(encoding="utf-8")
    )
    empty_binding = {
        name: []
        if name == "lean_exception_ids"
        else False
        if name == "lean_risk_accepted"
        else ""
        for name in ReviewRun.model_fields
        if name.startswith("lean_")
    }
    stripped = review_run.model_copy(update=empty_binding)
    (tmp_path / CURRENT_IMPLEMENTATION_PATH).unlink()

    validation = validate_review_run_lean_binding(tmp_path, stripped)

    assert "incomplete" in validation.lower()
    pack_path = tmp_path / review_run.review_pack_path
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    for name in tuple(pack):
        if name.startswith("lean_"):
            pack[name] = (
                []
                if name == "lean_exception_ids"
                else False
                if name == "lean_risk_accepted"
                else ""
            )
    pack["lean_report_digest"] = "sha256:partial"
    pack_path.write_text(json.dumps(pack), encoding="utf-8")

    validation = validate_review_run_lean_binding(tmp_path, stripped)

    assert "incomplete" in validation.lower()


def test_close_uses_profile_marker_when_lean_fields_are_removed(tmp_path: Path) -> None:
    _seed_lean_loop(tmp_path, "impl-binding-downgrade")
    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-binding-downgrade",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )
    assert started.status == PRReviewCommandStatus.STARTED
    review_run_path = Path(started.review_dir) / "review-run.json"
    review_run = json.loads(review_run_path.read_text(encoding="utf-8"))
    pack_path = tmp_path / review_run["review_pack_path"]
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    for payload in (pack, review_run):
        for name in tuple(payload):
            if name.startswith("lean_"):
                payload[name] = (
                    []
                    if name == "lean_exception_ids"
                    else False
                    if name == "lean_risk_accepted"
                    else ""
                )
    pack_path.write_text(json.dumps(pack), encoding="utf-8")
    review_run["review_pack_digest"] = hashlib.sha256(
        pack_path.read_bytes()
    ).hexdigest()
    review_run_path.write_text(json.dumps(review_run), encoding="utf-8")
    (tmp_path / CURRENT_IMPLEMENTATION_PATH).unlink()

    closed = close_pr_review(tmp_path)

    assert closed.status == PRReviewCommandStatus.BLOCKED
    assert "lean" in closed.blocker.lower()


def test_pr_binding_rejects_source_snapshot_tamper(tmp_path: Path) -> None:
    _seed_lean_loop(tmp_path, "impl-snapshot-pr-tamper")
    binding, blocker = resolve_lean_review_binding(tmp_path)
    assert blocker == ""
    assert binding is not None
    snapshot = tmp_path / binding.snapshot_path
    payload = json.loads(snapshot.read_text("utf-8"))
    payload["changed_files"] = []
    snapshot.write_text(json.dumps(payload), encoding="utf-8")

    current, blocker = resolve_lean_review_binding(tmp_path)

    assert current is None
    assert "snapshot" in blocker.lower()
    assert "digest" in blocker.lower()


def test_legacy_review_run_defaults_to_no_lean_binding() -> None:
    model = ReviewRun(review_id="legacy-review", loop_id="legacy-loop")

    assert model.lean_report_path == ""
    assert model.lean_report_digest == ""
    assert model.lean_input_digest == ""


def test_pr_review_service_persists_binding_and_blocks_lean_tamper(
    tmp_path: Path,
) -> None:
    _seed_lean_loop(tmp_path, "impl-service")

    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-lean-service",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )

    assert started.status == PRReviewCommandStatus.STARTED
    review_run = ReviewRun.model_validate_json(
        (Path(started.review_dir) / "review-run.json").read_text(encoding="utf-8")
    )
    assert review_run.lean_report_path
    assert review_run.lean_report_digest
    assert review_run.lean_input_digest
    report_path = tmp_path / review_run.lean_report_path
    report_path.write_text(
        report_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    closed = close_pr_review(tmp_path)

    assert closed.status == PRReviewCommandStatus.BLOCKED
    assert "changed" in closed.blocker.lower()


def test_attestation_and_close_check_keep_lean_digest_chain(tmp_path: Path) -> None:
    _seed_lean_loop(tmp_path, "impl-attestation")
    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-lean-attestation",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )
    closed = close_pr_review(tmp_path)

    assert started.status == PRReviewCommandStatus.STARTED
    assert closed.status == PRReviewCommandStatus.CLOSED, closed.blocker
    final_report = Path(closed.final_report_path).read_text(encoding="utf-8")
    assert "lean_report:" in final_report
    assert "lean_diff_hash:" in final_report
    review_run = ReviewRun.model_validate_json(
        (Path(started.review_dir) / "review-run.json").read_text(encoding="utf-8")
    )
    assert _local_pr_review_artifact_blocker(tmp_path, review_run) == ""
    attested = attest_pr_review(tmp_path)
    assert attested.status == PRReviewCommandStatus.READY
    attestation = ReviewAttestation.model_validate_json(
        Path(attested.attestation_path).read_text(encoding="utf-8")
    )
    assert attestation.review_pack_digest
    assert attestation.findings_digest
    assert attestation.final_report_digest
    assert attestation.lean_report_digest
    lean_report = tmp_path / attestation.lean_report_path
    lean_report.write_text(
        lean_report.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    blocker = _local_pr_review_artifact_blocker(tmp_path, review_run)

    assert "lean" in blocker.lower()
    assert "changed" in blocker.lower()


def test_lean_exception_risk_propagates_to_pr_verdict_and_attestation(
    tmp_path: Path,
) -> None:
    _seed_risk_accepted_loop(tmp_path, "impl-risk")
    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            diff_source="local-unstaged",
            provider_id="mock-reviewer",
            review_id="review-lean-risk",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )

    closed = close_pr_review(tmp_path)
    attested = attest_pr_review(tmp_path)

    assert started.status == PRReviewCommandStatus.STARTED
    assert closed.status == PRReviewCommandStatus.CLOSED
    assert closed.verdict == "risk_accepted"
    attestation = ReviewAttestation.model_validate_json(
        Path(attested.attestation_path).read_text(encoding="utf-8")
    )
    assert attestation.lean_risk_accepted is True
    assert attestation.lean_exception_ids == ["EX-PR"]


def _seed_lean_loop(
    root: Path,
    loop_id: str,
    *,
    source_kind: str = "local-unstaged",
    patch_file: str = "",
    include_untracked: bool = False,
    rename_to: str = "",
) -> None:
    _init_repo(root)
    target_path = rename_to or "src/app.py"
    if rename_to:
        _git(root, "mv", "src/app.py", rename_to)
    else:
        _write(root, target_path, "def _small():\n    return 1\n")
    if include_untracked:
        _write(root, "tests/untracked_probe.py", "print('untracked')\n")
    if source_kind == "patch":
        target = root / patch_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            _git_output(
                root,
                "diff",
                *(("HEAD",) if rename_to else ()),
                "--binary",
                "--no-ext-diff",
                "--no-textconv",
            )
        )
    artifacts = implementation_artifacts(root, loop_id)
    store = LoopArtifactStore(root)
    store.create_loop_run_dir(loop_id, loop_type=LoopType.IMPLEMENTATION.value)
    store.write_json_artifact(
        artifacts.input_path,
        ImplementationInput(
            loop_id=loop_id,
            work_item_id="WI-REVIEW",
            work_item_path="specs/WI-REVIEW",
            spec_path="specs/WI-REVIEW/spec.md",
            plan_path="specs/WI-REVIEW/plan.md",
            tasks_path="specs/WI-REVIEW/tasks.md",
            design_contract_loop_id="design-review",
            work_type=WorkType.NEW_REQUIREMENT,
            quality_profiles=["lean-code"],
            declared_scope=[
                target_path,
                *(["tests/untracked_probe.py"] if include_untracked else []),
            ],
        ),
    )
    store.write_json_artifact(
        artifacts.loop_run_path,
        LoopRun(
            loop_id=loop_id,
            loop_type=LoopType.IMPLEMENTATION,
            status=LoopStatus.NEEDS_REVIEW,
            current_round=1,
            rounds=[LoopRound(round_number=1, status=LoopStatus.NEEDS_REVIEW)],
        ),
    )
    store.write_json_artifact(
        artifacts.pointer_path,
        ImplementationCurrentPointer(
            loop_id=loop_id,
            loop_run_path=artifacts.loop_run_path.relative_to(root).as_posix(),
        ),
    )
    result = run_lean_check(
        LeanCheckOptions(
            root=root,
            loop_id=loop_id,
            source_kind=source_kind,
            patch_file=patch_file,
        )
    )
    assert result.status == "ready", result.blocker


def _seed_risk_accepted_loop(root: Path, loop_id: str) -> None:
    _init_repo(root)
    _write(root, "src/app.py", "def _small():\n    return 1\n")
    _write(root, "tests/risk_probe.py", "print('risk path verified')\n")
    _git(root, "add", "tests/risk_probe.py")
    _git(root, "commit", "-m", "add risk probe fixture")
    artifacts = implementation_artifacts(root, loop_id)
    store = LoopArtifactStore(root)
    impl_input = ImplementationInput(
        loop_id=loop_id,
        work_item_id="WI-REVIEW",
        work_item_path="specs/WI-REVIEW",
        spec_path="specs/WI-REVIEW/spec.md",
        plan_path="specs/WI-REVIEW/plan.md",
        tasks_path="specs/WI-REVIEW/tasks.md",
        design_contract_loop_id="design-review",
        work_type=WorkType.PRODUCTION_ISSUE,
        quality_profiles=["lean-code"],
        declared_scope=["src/app.py"],
    )
    store.write_json_artifact(artifacts.input_path, impl_input)
    store.write_json_artifact(
        artifacts.loop_run_path,
        LoopRun(
            loop_id=loop_id,
            loop_type=LoopType.IMPLEMENTATION,
            status=LoopStatus.NEEDS_REVIEW,
            current_round=1,
            rounds=[LoopRound(round_number=1, status=LoopStatus.NEEDS_REVIEW)],
        ),
    )
    store.write_json_artifact(
        artifacts.pointer_path,
        ImplementationCurrentPointer(
            loop_id=loop_id,
            loop_run_path=artifacts.loop_run_path.relative_to(root).as_posix(),
        ),
    )
    first = run_lean_check(LeanCheckOptions(root=root, loop_id=loop_id))
    assert first.status == "needs_fix"
    first_report = LeanEvaluationReport.model_validate_json(
        (root / first.report_path).read_text("utf-8")
    )
    snapshot = json.loads(
        (artifacts.loop_dir / "lean" / "round-001" / "source-snapshot.json").read_text(
            "utf-8"
        )
    )
    finding = next(
        item
        for item in first_report.findings
        if item.rule_id == "lean.bugfix-regression"
    )
    proof_ref = f".ai-sdlc/loops/implementation/{loop_id}/lean/exception-proof.txt"
    _write(root, proof_ref, "approved risk\n")
    exception = LeanException(
        exception_id="EX-PR",
        rule_id=finding.rule_id,
        path="src/app.py",
        stable_signature=finding.stable_signature,
        reason="The reproduction environment is unavailable for this bounded review.",
        owner="implementation-owner",
        approver="quality-owner",
        evidence_refs=[proof_ref],
        evidence_digests={
            proof_ref: "sha256:"
            + hashlib.sha256((root / proof_ref).read_bytes()).hexdigest()
        },
        scope=["src/app.py"],
        policy_digest=first_report.policy_digest,
        base_commit=snapshot["base_commit"],
        head_commit=snapshot["head_commit"],
        diff_hash=first_report.diff_hash,
        evaluation_digest=stable_artifact_digest(first_report),
        expires_at="2099-01-01T00:00:00Z",
    )
    exception_ref = f".ai-sdlc/loops/implementation/{loop_id}/lean/exception.json"
    (root / exception_ref).write_text(exception.model_dump_json(), encoding="utf-8")
    verified = run_lean_command(
        LeanExecutionOptions(
            root=root,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(sys.executable, "tests/risk_probe.py"),
            test_source_ref="tests/risk_probe.py",
        )
    )
    assert verified.status == "ready"
    store.write_json_artifact(
        artifacts.progress_path,
        ImplementationProgress(
            loop_id=loop_id,
            work_item_id="WI-REVIEW",
            tasks=[
                ImplementationTaskProgress(
                    task_id="T11",
                    status=ImplementationTaskStatus.DONE,
                    evidence=[verified.receipt_path],
                )
            ],
        ),
    )
    second = run_lean_check(
        LeanCheckOptions(root=root, loop_id=loop_id, exception_paths=(exception_ref,))
    )
    assert second.status == "ready", second


def _write_close_state(
    root: Path,
    loop_id: str,
    *,
    report_path: str = "",
) -> None:
    artifacts = implementation_artifacts(root, loop_id)
    loop_run = LoopRun.model_validate_json(
        artifacts.loop_run_path.read_text(encoding="utf-8")
    )
    store = LoopArtifactStore(root)
    if report_path:
        loop_run.status = LoopStatus.CLOSED
        store.write_json_artifact(artifacts.loop_run_path, loop_run)
        store.write_json_artifact(
            artifacts.close_path,
            ImplementationClose(loop_id=loop_id, report_path=report_path),
        )
        return
    loop_run.status = LoopStatus.NEEDS_REVIEW
    store.write_json_artifact(artifacts.loop_run_path, loop_run)
    _write(root, "specs/WI-REVIEW/spec.md", "# Acceptance\n\n- AC-1\n")
    _write(root, "specs/WI-REVIEW/plan.md", "# Plan\n")
    _write(root, "specs/WI-REVIEW/tasks.md", "# Tasks\n\n- T11\n")
    _git(root, "add", "specs/WI-REVIEW")
    _git(root, "commit", "-m", "add implementation contract")
    task = ImplementationTaskItem(
        task_id="T11",
        required=True,
        files=["src/app.py"],
        acceptance=["AC-1"],
    )
    store.write_json_artifact(
        artifacts.tasks_path,
        ImplementationTasks(
            loop_id=loop_id,
            work_item_id="WI-REVIEW",
            items=[task],
        ),
    )
    store.write_json_artifact(
        artifacts.progress_path,
        ImplementationProgress(
            loop_id=loop_id,
            work_item_id="WI-REVIEW",
            tasks=[
                ImplementationTaskProgress(
                    task_id="T11",
                    status=ImplementationTaskStatus.DONE,
                    evidence=["src/app.py"],
                )
            ],
        ),
    )
    refreshed = run_lean_check(LeanCheckOptions(root=root, loop_id=loop_id))
    assert refreshed.status == "ready", refreshed.blocker
    closed = close_implementation_loop(
        ImplementationCloseOptions(root=root, loop_id=loop_id, yes=True)
    )
    assert closed.closed is True, closed.blocker


def _downgrade_close_to_legacy(root: Path, loop_id: str) -> None:
    artifacts = implementation_artifacts(root, loop_id)
    proof_path = artifacts.close_path.with_name(IMPLEMENTATION_CLOSE_PROOF_NAME)
    proof_path.unlink()
    loop_run = LoopRun.model_validate_json(
        artifacts.loop_run_path.read_text(encoding="utf-8")
    )
    proof_ref = proof_path.relative_to(root).as_posix()
    execution_round = next(
        item for item in loop_run.rounds if item.round_kind == "execution"
    )
    execution_round.output_artifacts.remove(proof_ref)
    LoopArtifactStore(root).write_json_artifact(artifacts.loop_run_path, loop_run)
    close = json.loads(artifacts.close_path.read_text(encoding="utf-8"))
    close["created_by"] = "ai-sdlc"
    artifacts.close_path.write_text(json.dumps(close), encoding="utf-8")


def _remove_close_proof_marker(root: Path, loop_id: str) -> None:
    artifacts = implementation_artifacts(root, loop_id)
    proof_path = artifacts.close_path.with_name(IMPLEMENTATION_CLOSE_PROOF_NAME)
    loop_run = LoopRun.model_validate_json(
        artifacts.loop_run_path.read_text(encoding="utf-8")
    )
    proof_ref = proof_path.relative_to(root).as_posix()
    execution_round = next(
        item for item in loop_run.rounds if item.round_kind == "execution"
    )
    execution_round.output_artifacts.remove(proof_ref)
    LoopArtifactStore(root).write_json_artifact(artifacts.loop_run_path, loop_run)


def _init_repo(root: Path) -> None:
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    _write(root, "README.md", "# Test\n")
    _write(root, ".gitignore", ".ai-sdlc/loops/\n.ai-sdlc/reviews/\n")
    _write(root, "src/app.py", "def _small():\n    return 0\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")


def _write(root: Path, relative: str, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    if result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", errors="replace"))


def _git_output(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    if result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", errors="replace"))
    return result.stdout
