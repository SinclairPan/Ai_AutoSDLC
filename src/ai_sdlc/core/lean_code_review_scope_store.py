"""Read and verify the sidecar that anchors a closed Lean review scope."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ai_sdlc.core.lean_code_models import LeanEvaluationReport
from ai_sdlc.core.lean_code_review_scope_models import (
    LEAN_CLOSED_SCOPE_NAME,
    ClosedLeanReviewScope,
)
from ai_sdlc.core.pr_review_models import ReviewPack, ReviewRun
from ai_sdlc.core.source_snapshot import (
    SourceSnapshot,
    SourceSnapshotOptions,
    build_source_snapshot,
)


def read_closed_scope(
    root: Path,
    review_pack_path: str,
    decisions: dict[str, Any],
) -> tuple[ClosedLeanReviewScope | None, str]:
    """Load the canonical sidecar only when its ReviewPack anchor matches."""
    if not review_pack_path:
        return None, "Closed Lean review scope has no review pack path."
    expected_path = decisions.get("lean_closed_scope_path")
    expected_digest = decisions.get("lean_closed_scope_digest")
    if not isinstance(expected_path, str) or not isinstance(expected_digest, str):
        return None, "Closed Lean review scope reference is incomplete."
    try:
        pack_path = safe_path(root, review_pack_path)
        scope_path = pack_path.with_name(LEAN_CLOSED_SCOPE_NAME)
        if scope_path.relative_to(root.resolve()).as_posix() != expected_path:
            return None, "Closed Lean review scope path is not canonical."
        if file_digest(scope_path) != expected_digest:
            return None, "Closed Lean review scope digest changed."
        scope = ClosedLeanReviewScope.model_validate_json(scope_path.read_text("utf-8"))
    except (OSError, ValueError, ValidationError) as exc:
        return None, f"Closed Lean review scope cannot be verified: {exc}"
    return scope, ""


def read_review_pack(
    root: Path,
    review_pack_path: str,
) -> tuple[ReviewPack | None, str]:
    """Read a review pack through the same project-relative path boundary."""
    if not review_pack_path:
        return None, ""
    try:
        pack = ReviewPack.model_validate_json(
            safe_path(root, review_pack_path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError, ValidationError) as exc:
        return None, f"Lean review pack cannot be verified: {exc}"
    return pack, ""


def has_stored_lean_metadata(binding: ReviewRun | ReviewPack) -> bool:
    """Return whether schema-v1 fields retain any Lean artifact binding value."""
    return any(
        bool(value)
        for name, value in binding.model_dump().items()
        if name.startswith("lean_")
        and name not in {"lean_risk_accepted", "lean_exception_ids"}
    )


def has_stored_lean_disposition(binding: ReviewRun | ReviewPack) -> bool:
    """Return whether risk acceptance fields carry a persisted disposition."""
    return binding.lean_risk_accepted or bool(binding.lean_exception_ids)


def closed_scope_disposition_blocker(
    root: Path,
    review_run: ReviewRun,
    pack: ReviewPack,
    scope: ClosedLeanReviewScope,
) -> str:
    """Bind a historical risk disposition only when its frozen diff is reviewed."""
    try:
        report = LeanEvaluationReport.model_validate_json(
            safe_path(root, scope.lean_report.path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError, ValidationError) as exc:
        return f"Closed Lean risk disposition cannot be verified: {exc}"
    matches_review, source_blocker = _closed_scope_matches_review(
        root,
        review_run,
        scope,
    )
    if source_blocker:
        return source_blocker
    stored_match = pack.policy_decisions.get("lean_closed_scope_matches_review")
    if stored_match is not None and stored_match is not matches_review:
        return "Closed Lean source-match decision changed after PR review."
    expected_risk = report.risk_accepted if matches_review else False
    expected_ids = report.exception_ids if matches_review else []
    actual = (
        review_run.lean_risk_accepted,
        review_run.lean_exception_ids,
        pack.lean_risk_accepted,
        pack.lean_exception_ids,
    )
    expected = (expected_risk, expected_ids, expected_risk, expected_ids)
    return (
        "Closed Lean risk disposition changed after PR review."
        if actual != expected
        else ""
    )


def _closed_scope_matches_review(
    root: Path,
    review_run: ReviewRun,
    scope: ClosedLeanReviewScope,
) -> tuple[bool, str]:
    """Recompute content equivalence from the saved source descriptor."""
    descriptor = review_run.diff_source
    try:
        evaluated = SourceSnapshot.model_validate_json(
            safe_path(root, scope.lean_snapshot.path).read_text(encoding="utf-8")
        )
        current = build_source_snapshot(
            SourceSnapshotOptions(
                root=root,
                source_kind=str(descriptor.source_kind),
                base_ref=descriptor.base_ref or review_run.base_ref,
                head_ref=descriptor.head_ref or review_run.head_ref or "HEAD",
                patch_file=descriptor.patch_file,
            )
        )
    except (OSError, ValueError, ValidationError) as exc:
        return False, f"Closed Lean review source cannot be verified: {exc}"
    matches = (
        evaluated.diff_hash == current.diff_hash
        and evaluated.changed_files == current.changed_files
        and evaluated.file_digests == current.file_digests
    )
    return matches, ""


def safe_path(root: Path, path: str) -> Path:
    """Resolve a project-relative artifact without allowing path escape."""
    candidate = (root / path).resolve()
    candidate.relative_to(root.resolve())
    return candidate


def file_digest(path: Path) -> str:
    """Return the exact-byte SHA-256 used by persisted review anchors."""
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def valid_timestamp(value: str) -> bool:
    """Return whether a timestamp is parseable and timezone-aware."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.utcoffset() is not None


__all__ = [
    "closed_scope_disposition_blocker",
    "file_digest",
    "has_stored_lean_disposition",
    "has_stored_lean_metadata",
    "read_closed_scope",
    "read_review_pack",
    "safe_path",
    "valid_timestamp",
]
