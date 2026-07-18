"""Local PR Review 对 fresh Lean artifact 与 digest 链的测试。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from ai_sdlc.core.close_check import _local_pr_review_artifact_blocker
from ai_sdlc.core.implementation_models import (
    ImplementationCurrentPointer,
    ImplementationInput,
    ImplementationProgress,
    ImplementationTaskProgress,
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
from ai_sdlc.core.lean_code_runtime import LeanCheckOptions, run_lean_check
from ai_sdlc.core.loop_artifacts import LoopArtifactStore
from ai_sdlc.core.loop_models import LoopRound, LoopRun, LoopStatus, LoopType
from ai_sdlc.core.pr_review_models import ReviewAttestation, ReviewRun
from ai_sdlc.core.pr_review_pack import ReviewPackBuildOptions, build_review_pack
from ai_sdlc.core.pr_review_provider import MockReviewerFixture
from ai_sdlc.core.pr_review_service import (
    PRReviewCommandStatus,
    PRReviewStartOptions,
    attest_pr_review,
    close_pr_review,
    start_pr_review,
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


def _seed_lean_loop(root: Path, loop_id: str) -> None:
    _init_repo(root)
    _write(root, "src/app.py", "def _small():\n    return 1\n")
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
            declared_scope=["src/app.py"],
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
    result = run_lean_check(LeanCheckOptions(root=root, loop_id=loop_id))
    assert result.status == "ready"


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
