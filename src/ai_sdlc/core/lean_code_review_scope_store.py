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
from ai_sdlc.core.source_content_identity import CHANGE_IDENTITY_KIND
from ai_sdlc.core.source_snapshot import (
    SourceSnapshot,
    SourceSnapshotOptions,
    build_source_snapshot,
    source_snapshot_identity_issue,
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


def source_content_equivalent(
    evaluated: SourceSnapshot,
    current: SourceSnapshot,
) -> bool:
    """Return whether two snapshots prove the same source change."""
    return compare_source_content(evaluated, current)[0]


def compare_source_content(
    evaluated: SourceSnapshot,
    current: SourceSnapshot,
) -> tuple[bool, str]:
    """Compare source content and distinguish mismatch from unverifiable legacy."""
    identity_issue = source_snapshot_identity_issue(
        evaluated
    ) or source_snapshot_identity_issue(current)
    if identity_issue:
        return (
            False,
            f"Closed Lean review source identity is unverifiable: {identity_issue}.",
        )
    if evaluated.source_kind == current.source_kind:
        return _same_source_equivalent(evaluated, current), ""
    if not _cross_source_structure_matches(evaluated, current):
        return False, ""
    if (
        evaluated.change_identity_kind == CHANGE_IDENTITY_KIND
        and current.change_identity_kind == CHANGE_IDENTITY_KIND
    ):
        return _change_identities_match(evaluated, current), ""
    if (
        evaluated.diff_hash == current.diff_hash
        and evaluated.file_digests == current.file_digests
    ):
        return True, ""
    return (
        False,
        "Closed Lean review source uses a legacy snapshot whose cross-source "
        "content identity cannot be verified.",
    )


def _same_source_equivalent(
    evaluated: SourceSnapshot,
    current: SourceSnapshot,
) -> bool:
    fields = (
        "base_commit",
        "head_commit",
        "diff_hash",
        "changed_files",
        "untracked_files",
        "deleted_files",
        "binary_files",
        "renamed_files",
        "file_digests",
        "index_identity",
    )
    return all(getattr(evaluated, name) == getattr(current, name) for name in fields)


def _cross_source_structure_matches(
    evaluated: SourceSnapshot,
    current: SourceSnapshot,
) -> bool:
    return evaluated.base_commit == current.base_commit


def _change_identities_match(
    evaluated: SourceSnapshot,
    current: SourceSnapshot,
) -> bool:
    if set(evaluated.raw_change_identities) != set(current.raw_change_identities):
        return False
    safe_eol = {
        path
        for snapshot in (evaluated, current)
        if snapshot.source_kind == "local-unstaged"
        for path in snapshot.safe_eol_paths
    }
    for path, evaluated_raw in evaluated.raw_change_identities.items():
        if evaluated_raw == current.raw_change_identities[path]:
            continue
        if path not in safe_eol or (
            evaluated.portable_change_identities.get(path)
            != current.portable_change_identities.get(path)
        ):
            return False
    return True


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
    return compare_source_content(evaluated, current)


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
    "compare_source_content",
    "file_digest",
    "has_stored_lean_disposition",
    "has_stored_lean_metadata",
    "read_closed_scope",
    "read_review_pack",
    "safe_path",
    "source_content_equivalent",
    "valid_timestamp",
]
