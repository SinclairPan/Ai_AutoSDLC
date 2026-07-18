"""Bridge a fresh Implementation Lean report into Local PR Review artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_sdlc.core.implementation_store import (
    ImplementationArtifacts,
    implementation_artifacts,
    read_input,
    read_loop_run,
    repo_relative_path,
    resolve_implementation_loop_run_path,
)
from ai_sdlc.core.lean_code_artifacts import LeanCurrentPointer
from ai_sdlc.core.lean_code_models import LeanEvaluationInput, LeanEvaluationReport
from ai_sdlc.core.lean_code_policy import stable_artifact_digest
from ai_sdlc.core.lean_code_runtime import validate_lean_close
from ai_sdlc.core.pr_review_models import ReviewPack, ReviewRun


class LeanReviewBinding(BaseModel):
    """Digest chain consumed by review-pack, review-run, and attestation."""

    model_config = ConfigDict(extra="forbid")

    implementation_loop_id: str
    work_item_id: str
    report_path: str
    report_digest: str
    report_markdown_path: str
    report_markdown_digest: str
    input_path: str
    input_digest: str
    snapshot_path: str
    snapshot_digest: str
    findings_path: str
    findings_digest: str
    policy_path: str
    policy_snapshot_digest: str
    diff_hash: str
    policy_digest: str
    risk_accepted: bool = False
    exception_ids: list[str] = Field(default_factory=list)


def resolve_lean_review_binding(root: Path) -> tuple[LeanReviewBinding | None, str]:
    """Resolve the current enabled Implementation report or return a blocker."""
    resolved_root = root.resolve()
    loop_path, pointer_blocker = resolve_implementation_loop_run_path(resolved_root, "")
    if pointer_blocker:
        if pointer_blocker == "No current implementation loop exists.":
            return None, ""
        return None, pointer_blocker
    try:
        loop_run = read_loop_run(loop_path)
        paths = implementation_artifacts(resolved_root, loop_run.loop_id)
        impl_input = read_input(paths.input_path)
    except (OSError, ValueError) as exc:
        return None, f"Implementation Lean binding is malformed: {exc}"
    if "lean-code" not in impl_input.quality_profiles:
        return None, ""
    close_blocker = validate_lean_close(resolved_root, loop_run.loop_id)
    if close_blocker:
        return (None, close_blocker)
    try:
        binding_files = _read_binding_files(resolved_root, paths)
    except (OSError, ValueError, ValidationError) as exc:
        return None, f"Implementation Lean binding is malformed: {exc}"
    (
        pointer,
        report_path,
        markdown_path,
        input_path,
        snapshot_path,
        findings_path,
        policy_path,
        report,
        evaluation_input,
    ) = binding_files
    if stable_artifact_digest(report) != pointer.report_digest:
        return None, "Implementation Lean report digest does not match its pointer."
    if stable_artifact_digest(evaluation_input) != pointer.input_digest:
        return None, "Implementation Lean input digest does not match its pointer."
    return _binding_from_paths(
        resolved_root,
        loop_run.loop_id,
        impl_input.work_item_id,
        report_path,
        markdown_path,
        input_path,
        snapshot_path,
        findings_path,
        policy_path,
        report,
    ), ""


def _read_binding_files(
    root: Path,
    paths: ImplementationArtifacts,
) -> tuple[
    LeanCurrentPointer,
    Path,
    Path,
    Path,
    Path,
    Path,
    Path,
    LeanEvaluationReport,
    LeanEvaluationInput,
]:
    pointer = LeanCurrentPointer.model_validate_json(
        (paths.loop_dir / "lean" / "current.json").read_text("utf-8")
    )
    report_path = _safe_path(root, pointer.report_path)
    input_path = _safe_path(root, pointer.input_path)
    snapshot_path = _safe_path(root, pointer.snapshot_path)
    findings_path = _safe_path(root, pointer.findings_path)
    policy_path = _safe_path(root, pointer.policy_path)
    return (
        pointer,
        report_path,
        report_path.parent / "report.md",
        input_path,
        snapshot_path,
        findings_path,
        policy_path,
        LeanEvaluationReport.model_validate_json(report_path.read_text("utf-8")),
        LeanEvaluationInput.model_validate_json(input_path.read_text("utf-8")),
    )


def validate_review_run_lean_binding(root: Path, review_run: ReviewRun) -> str:
    """Revalidate every persisted Lean byte digest against the current binding."""

    if not getattr(review_run, "lean_report_path", ""):
        if _has_stored_lean_metadata(review_run):
            return "Stored Lean binding is incomplete."
        pack_requires_lean, pack_blocker = _review_pack_requires_lean(root, review_run)
        if pack_blocker:
            return pack_blocker
        if pack_requires_lean:
            return "Stored Lean binding is incomplete relative to its review pack."
        current, blocker = resolve_lean_review_binding(root)
        if blocker:
            return blocker
        if current is not None:
            return "Required Lean binding is missing from the reviewer run."
        return ""
    stored = _stored_binding(review_run)
    for path, expected, label in _binding_files(stored):
        try:
            actual = _file_digest(_safe_path(root, path))
        except (OSError, ValueError) as exc:
            return f"Lean {label} cannot be verified: {exc}"
        if actual != expected:
            return f"Lean {label} changed after the reviewer run."
    current, blocker = resolve_lean_review_binding(root)
    if blocker:
        return blocker
    if current is None:
        return "Current fresh Lean binding is missing."
    if current.model_dump() != stored.model_dump():
        return "Current Lean binding changed after the reviewer run."
    return ""


def _has_stored_lean_metadata(binding: ReviewRun | ReviewPack) -> bool:
    return any(
        bool(value)
        for name, value in binding.model_dump().items()
        if name.startswith("lean_")
    )


def _review_pack_requires_lean(root: Path, review_run: ReviewRun) -> tuple[bool, str]:
    if not review_run.review_pack_path:
        return False, ""
    try:
        pack = ReviewPack.model_validate_json(
            _safe_path(root, review_run.review_pack_path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError, ValidationError) as exc:
        return False, f"Lean review pack cannot be verified: {exc}"
    required_by_profile = pack.policy_decisions.get("lean_binding_required") is True
    return required_by_profile or _has_stored_lean_metadata(pack), ""


def _binding_from_paths(
    root: Path,
    loop_id: str,
    work_item_id: str,
    report_path: Path,
    markdown_path: Path,
    input_path: Path,
    snapshot_path: Path,
    findings_path: Path,
    policy_path: Path,
    report: LeanEvaluationReport,
) -> LeanReviewBinding:
    return LeanReviewBinding(
        implementation_loop_id=loop_id,
        work_item_id=work_item_id,
        report_path=repo_relative_path(root, report_path),
        report_digest=_file_digest(report_path),
        report_markdown_path=repo_relative_path(root, markdown_path),
        report_markdown_digest=_file_digest(markdown_path),
        input_path=repo_relative_path(root, input_path),
        input_digest=_file_digest(input_path),
        snapshot_path=repo_relative_path(root, snapshot_path),
        snapshot_digest=_file_digest(snapshot_path),
        findings_path=repo_relative_path(root, findings_path),
        findings_digest=_file_digest(findings_path),
        policy_path=repo_relative_path(root, policy_path),
        policy_snapshot_digest=_file_digest(policy_path),
        diff_hash=report.diff_hash,
        policy_digest=report.policy_digest,
        risk_accepted=report.risk_accepted,
        exception_ids=report.exception_ids,
    )


def _stored_binding(review_run: ReviewRun) -> LeanReviewBinding:
    return LeanReviewBinding(
        implementation_loop_id=review_run.lean_implementation_loop_id,
        work_item_id=review_run.lean_work_item_id,
        report_path=review_run.lean_report_path,
        report_digest=review_run.lean_report_digest,
        report_markdown_path=review_run.lean_report_markdown_path,
        report_markdown_digest=review_run.lean_report_markdown_digest,
        input_path=review_run.lean_input_path,
        input_digest=review_run.lean_input_digest,
        snapshot_path=review_run.lean_snapshot_path,
        snapshot_digest=review_run.lean_snapshot_digest,
        findings_path=review_run.lean_findings_path,
        findings_digest=review_run.lean_findings_digest,
        policy_path=review_run.lean_policy_path,
        policy_snapshot_digest=review_run.lean_policy_snapshot_digest,
        diff_hash=review_run.lean_diff_hash,
        policy_digest=review_run.lean_policy_digest,
        risk_accepted=review_run.lean_risk_accepted,
        exception_ids=review_run.lean_exception_ids,
    )


def _binding_files(binding: LeanReviewBinding) -> tuple[tuple[str, str, str], ...]:
    return (
        (binding.report_path, binding.report_digest, "report"),
        (
            binding.report_markdown_path,
            binding.report_markdown_digest,
            "Markdown report",
        ),
        (binding.input_path, binding.input_digest, "input"),
        (binding.snapshot_path, binding.snapshot_digest, "source snapshot"),
        (binding.findings_path, binding.findings_digest, "findings"),
        (binding.policy_path, binding.policy_snapshot_digest, "policy snapshot"),
    )


def _safe_path(root: Path, path: str) -> Path:
    candidate = (root / path).resolve()
    candidate.relative_to(root.resolve())
    return candidate


def _file_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


__all__ = [
    "LeanReviewBinding",
    "resolve_lean_review_binding",
    "validate_review_run_lean_binding",
]
