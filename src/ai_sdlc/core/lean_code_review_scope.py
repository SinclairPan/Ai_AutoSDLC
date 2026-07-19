"""Validate closed Implementation proof when scoping Lean PR review evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ai_sdlc.core.implementation_models import (
    ImplementationClose,
    ImplementationReport,
)
from ai_sdlc.core.implementation_store import (
    ImplementationArtifacts,
    implementation_artifacts,
    read_loop_run,
    read_report,
    repo_relative_path,
    resolve_implementation_loop_run_path,
)
from ai_sdlc.core.lean_code_artifacts import LeanCurrentPointer
from ai_sdlc.core.lean_code_models import LeanEvaluationInput, LeanEvaluationReport
from ai_sdlc.core.lean_code_review_scope_models import (
    IMPLEMENTATION_CLOSE_PROOF_CREATOR,
    IMPLEMENTATION_CLOSE_PROOF_NAME,
    ClosedLeanReviewScope,
    FrozenArtifact,
    ImplementationCloseProof,
)
from ai_sdlc.core.lean_code_review_scope_store import (
    file_digest as _file_digest,
)
from ai_sdlc.core.lean_code_review_scope_store import safe_path as _safe_path
from ai_sdlc.core.lean_code_review_scope_store import (
    valid_timestamp as _valid_timestamp,
)
from ai_sdlc.core.lean_code_runtime import validate_lean_close
from ai_sdlc.core.loop_models import LoopRun, LoopStatus, LoopType


def validate_implementation_close(
    root: Path,
    loop_run: LoopRun,
    paths: ImplementationArtifacts,
    *,
    expected_work_item_id: str = "",
) -> tuple[ImplementationClose | None, str]:
    """Return a close artifact only when it matches the durable close outputs."""
    if loop_run.status != LoopStatus.CLOSED:
        return None, ""
    try:
        close = ImplementationClose.model_validate_json(
            paths.close_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError, ValidationError) as exc:
        return None, f"Implementation closed state is malformed: {exc}"
    if close.loop_id != loop_run.loop_id:
        return None, "Implementation close artifact targets a different loop."
    if close.report_path != repo_relative_path(root, paths.report_json_path):
        return None, "Implementation close report_path is not canonical."
    try:
        report = read_report(paths.report_json_path)
    except (OSError, ValueError) as exc:
        return None, f"Implementation closed state is malformed: {exc}"
    blocker = _implementation_report_blocker(
        loop_run,
        close,
        report,
        expected_work_item_id,
    )
    if blocker:
        return None, blocker
    blocker = _close_proof_blocker(root, loop_run, close, paths, report)
    if blocker:
        return None, blocker
    blocker = _close_semantics_blocker(close, report)
    if blocker:
        return None, blocker
    if not _execution_round_has_artifact(root, loop_run, paths.close_path):
        return None, "Implementation close is not bound to the execution round."
    return close, ""


def _implementation_report_blocker(
    loop_run: LoopRun,
    close: ImplementationClose,
    report: ImplementationReport,
    expected_work_item_id: str,
) -> str:
    if report.loop_id != loop_run.loop_id or report.status != LoopStatus.PASSED:
        return "Implementation close report is not passed for this loop."
    if close.required_task_count != report.required_task_count:
        return "Implementation close task count does not match its report."
    if expected_work_item_id and report.work_item_id != expected_work_item_id:
        return "Implementation report work item does not match its binding."
    if report.done_count != report.required_task_count:
        return "Implementation close report has incomplete required tasks."
    if report.blocker_count or report.blockers or report.blocked_count:
        return "Implementation close report still contains blockers."
    return ""


def _close_proof_blocker(
    root: Path,
    loop_run: LoopRun,
    close: ImplementationClose,
    paths: ImplementationArtifacts,
    report: ImplementationReport,
) -> str:
    proof_path = paths.close_path.with_name(IMPLEMENTATION_CLOSE_PROOF_NAME)
    if not proof_path.exists():
        return (
            "Implementation close proof is missing."
            if close.created_by == IMPLEMENTATION_CLOSE_PROOF_CREATOR
            or _execution_round_has_artifact(root, loop_run, proof_path)
            else ""
        )
    try:
        proof = ImplementationCloseProof.model_validate_json(
            proof_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError, ValidationError) as exc:
        return f"Implementation close proof is malformed: {exc}"
    expected_paths = (
        (proof.close, paths.close_path, "close"),
        (
            proof.implementation_report,
            paths.report_json_path,
            "Implementation report",
        ),
    )
    for artifact, path, label in expected_paths:
        if artifact.path != repo_relative_path(root, path):
            return f"Implementation close proof {label} path is not canonical."
        if artifact.digest != _file_digest(path):
            return f"Implementation close proof {label} digest changed."
    if proof.implementation_loop_id != report.loop_id:
        return "Implementation close proof loop identity changed."
    if proof.work_item_id != report.work_item_id:
        return "Implementation close proof work item changed."
    return ""


def _close_semantics_blocker(
    close: ImplementationClose,
    report: ImplementationReport,
) -> str:
    if close.artifact_kind != "implementation-close":
        return "Implementation close artifact kind is invalid."
    if not close.closed_by.strip():
        return "Implementation close closed_by is empty."
    if not _valid_timestamp(close.closed_at):
        return "Implementation close closed_at is invalid."
    expected = (
        LoopType.FRONTEND_EVIDENCE
        if report.requires_frontend_evidence
        else LoopType.LOCAL_PR_REVIEW
    )
    if close.next_loop_type != expected:
        return "Implementation close next loop does not match its report."
    return ""


def _execution_round_has_artifact(
    root: Path,
    loop_run: LoopRun,
    path: Path,
) -> bool:
    artifact_ref = repo_relative_path(root, path)
    execution_round = next(
        (item for item in loop_run.rounds if item.round_kind == "execution"),
        None,
    )
    return bool(
        execution_round
        and execution_round.status == LoopStatus.CLOSED
        and artifact_ref in execution_round.output_artifacts
    )


def closed_scope_for_binding(binding: Any) -> ClosedLeanReviewScope:
    """Freeze every persisted artifact used by a closed Lean binding."""
    return ClosedLeanReviewScope(
        implementation_loop_id=binding.implementation_loop_id,
        work_item_id=binding.work_item_id,
        close=FrozenArtifact(
            path=binding.implementation_close_path,
            digest=binding.implementation_close_digest,
        ),
        close_proof=(
            FrozenArtifact(
                path=binding.implementation_close_proof_path,
                digest=binding.implementation_close_proof_digest,
            )
            if binding.implementation_close_proof_path
            else None
        ),
        implementation_report=FrozenArtifact(
            path=binding.implementation_report_path,
            digest=binding.implementation_report_digest,
        ),
        lean_pointer=FrozenArtifact(
            path=binding.pointer_path,
            digest=binding.pointer_digest,
        ),
        lean_report=FrozenArtifact(
            path=binding.report_path, digest=binding.report_digest
        ),
        lean_report_markdown=FrozenArtifact(
            path=binding.report_markdown_path,
            digest=binding.report_markdown_digest,
        ),
        lean_input=FrozenArtifact(path=binding.input_path, digest=binding.input_digest),
        lean_snapshot=FrozenArtifact(
            path=binding.snapshot_path,
            digest=binding.snapshot_digest,
        ),
        lean_findings=FrozenArtifact(
            path=binding.findings_path,
            digest=binding.findings_digest,
        ),
        lean_policy=FrozenArtifact(
            path=binding.policy_path,
            digest=binding.policy_snapshot_digest,
        ),
        diff_hash=binding.diff_hash,
        policy_digest=binding.policy_digest,
    )


def validate_closed_lean_binding(root: Path, binding: Any) -> str:
    """Revalidate the durable close proof carried by a historical binding."""
    if not binding.implementation_closed:
        return "Implementation Lean binding is not closed."
    try:
        scope = closed_scope_for_binding(binding)
    except ValidationError as exc:
        return f"Closed Lean review scope is malformed: {exc}"
    return validate_closed_scope(root, scope)


def validate_closed_scope(root: Path, scope: ClosedLeanReviewScope) -> str:
    """Revalidate a frozen closed scope without using the mutable current source."""
    resolved_root = root.resolve()
    loaded = _closed_loop_context(resolved_root, scope.implementation_loop_id)
    if isinstance(loaded, str):
        return loaded
    loop_run, paths = loaded
    close, blocker = validate_implementation_close(
        resolved_root,
        loop_run,
        paths,
        expected_work_item_id=scope.work_item_id,
    )
    if blocker or close is None:
        return blocker or "Implementation close proof is missing."
    blocker = validate_lean_close(
        resolved_root,
        loop_run.loop_id,
        require_fresh_source=False,
    )
    if blocker:
        return blocker
    blocker = _scope_identity_blocker(resolved_root, scope, paths)
    if blocker:
        return blocker
    blocker = _scope_files_blocker(resolved_root, scope)
    if blocker:
        return blocker
    return _scope_pointer_blocker(resolved_root, scope, paths)


def _closed_loop_context(
    root: Path,
    loop_id: str,
) -> tuple[LoopRun, ImplementationArtifacts] | str:
    loop_path, pointer_blocker = resolve_implementation_loop_run_path(root, loop_id)
    if pointer_blocker:
        return pointer_blocker
    try:
        loop_run = read_loop_run(loop_path)
        return loop_run, implementation_artifacts(root, loop_run.loop_id)
    except (OSError, ValueError) as exc:
        return f"Implementation closed state is malformed: {exc}"


def _scope_identity_blocker(
    root: Path,
    scope: ClosedLeanReviewScope,
    paths: ImplementationArtifacts,
) -> str:
    expected = {
        "close": repo_relative_path(root, paths.close_path),
        "Implementation report": repo_relative_path(root, paths.report_json_path),
        "Lean pointer": repo_relative_path(
            root, paths.loop_dir / "lean" / "current.json"
        ),
    }
    actual = {
        "close": scope.close.path,
        "Implementation report": scope.implementation_report.path,
        "Lean pointer": scope.lean_pointer.path,
    }
    if scope.close_proof is not None:
        expected["close proof"] = repo_relative_path(
            root,
            paths.close_path.with_name(IMPLEMENTATION_CLOSE_PROOF_NAME),
        )
        actual["close proof"] = scope.close_proof.path
    for label, expected_path in expected.items():
        if actual[label] != expected_path:
            return f"Closed Lean {label} path changed."
    return ""


def _scope_files_blocker(root: Path, scope: ClosedLeanReviewScope) -> str:
    artifacts = [
        (scope.close, "Implementation close"),
        (scope.implementation_report, "Implementation report"),
        (scope.lean_pointer, "pointer"),
        (scope.lean_report, "report"),
        (scope.lean_report_markdown, "Markdown report"),
        (scope.lean_input, "input"),
        (scope.lean_snapshot, "source snapshot"),
        (scope.lean_findings, "findings"),
        (scope.lean_policy, "policy snapshot"),
    ]
    if scope.close_proof is not None:
        artifacts.append((scope.close_proof, "Implementation close proof"))
    for artifact, label in artifacts:
        try:
            actual = _file_digest(_safe_path(root, artifact.path))
        except (OSError, ValueError) as exc:
            return f"Closed Lean {label} cannot be verified: {exc}"
        if actual != artifact.digest:
            return f"Closed Lean {label} digest changed after PR review."
    return ""


def _scope_pointer_blocker(
    root: Path,
    scope: ClosedLeanReviewScope,
    paths: ImplementationArtifacts,
) -> str:
    try:
        pointer = LeanCurrentPointer.model_validate_json(
            (paths.loop_dir / "lean" / "current.json").read_text("utf-8")
        )
        report = LeanEvaluationReport.model_validate_json(
            _safe_path(root, pointer.report_path).read_text("utf-8")
        )
        evaluation_input = LeanEvaluationInput.model_validate_json(
            _safe_path(root, pointer.input_path).read_text("utf-8")
        )
    except (OSError, ValueError, ValidationError) as exc:
        return f"Closed Lean binding is malformed: {exc}"
    paths_match = (
        scope.lean_report.path == pointer.report_path
        and scope.lean_input.path == pointer.input_path
        and scope.lean_snapshot.path == pointer.snapshot_path
        and scope.lean_findings.path == pointer.findings_path
        and scope.lean_policy.path == pointer.policy_path
    )
    identity_match = (
        scope.implementation_loop_id == pointer.loop_id
        and pointer.loop_id == report.loop_id
        and scope.work_item_id == evaluation_input.work_item_id
        and pointer.evaluation_round == report.evaluation_round
        and pointer.evaluation_round == evaluation_input.evaluation_round
        and scope.diff_hash == pointer.diff_hash
        and pointer.diff_hash == report.diff_hash
        and scope.policy_digest == report.policy_digest
    )
    if not paths_match or not identity_match:
        return "Closed Lean scope does not match its loop pointer."
    return ""


def historical_scope_blocker(
    root: Path,
    scope: ClosedLeanReviewScope | None,
) -> str:
    """Validate a persisted decision that retained a closed historical scope."""
    if scope is None:
        return "Closed Lean review scope is missing."
    return validate_closed_scope(root, scope)


__all__ = [
    "ClosedLeanReviewScope",
    "closed_scope_for_binding",
    "historical_scope_blocker",
    "validate_closed_lean_binding",
    "validate_implementation_close",
]
