"""验证 Local PR Adapter 使用的持久化输入工件。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ai_sdlc.core.pr_review_models import (
    ReviewPack,
    ReviewRun,
    SourceAdapterResolution,
)


def local_pr_inputs(
    review_run: ReviewRun,
    review_pack: ReviewPack,
) -> tuple[str, ...]:
    paths = [
        review_run.review_pack_path,
        review_pack.source_resolution_path,
        review_pack.diff_path,
        *review_pack.work_item_refs,
        *review_pack.test_results_refs,
        *review_pack.policy_refs,
    ]
    return tuple(sorted({path for path in paths if path.strip()}))


def require_persisted_review_pack(
    root: Path,
    review_run: ReviewRun,
    review_pack: ReviewPack,
) -> None:
    payload = _read_repo_artifact(
        root,
        review_run.review_pack_path,
        "persisted review pack",
    )
    actual_digest = hashlib.sha256(payload).hexdigest()
    if not review_run.review_pack_digest.strip() or (
        actual_digest != review_run.review_pack_digest
    ):
        raise ValueError("local PR persisted review pack digest does not match review run")
    try:
        persisted = ReviewPack.model_validate_json(payload)
    except ValueError as exc:
        raise ValueError("local PR persisted review pack is invalid") from exc
    if persisted != review_pack:
        raise ValueError("local PR persisted review pack does not match adapter facts")


def require_bound_review_inputs(root: Path, review_pack: ReviewPack) -> None:
    resolution_payload = _require_artifact_digest(
        root,
        review_pack.source_resolution_path,
        review_pack.source_resolution_digest,
        "source resolution artifact",
    )
    try:
        resolution = SourceAdapterResolution.model_validate_json(resolution_payload)
    except ValueError as exc:
        raise ValueError("local PR source resolution artifact is invalid") from exc
    if resolution.to_descriptor() != review_pack.diff_source:
        raise ValueError("local PR source resolution artifact contradicts review pack")
    _require_artifact_digest(
        root,
        review_pack.diff_path,
        review_pack.diff_digest,
        "diff artifact digest",
    )


def _require_artifact_digest(
    root: Path,
    relative_path: str,
    expected_digest: str,
    label: str,
) -> bytes:
    payload = _read_repo_artifact(root, relative_path, label)
    actual_digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    if not expected_digest.strip() or actual_digest != expected_digest:
        raise ValueError(f"local PR {label} does not match review pack")
    return payload


def _read_repo_artifact(root: Path, relative_path: str, label: str) -> bytes:
    if not relative_path.strip():
        raise ValueError(f"local PR {label} path is required")
    project_root = root.resolve()
    target = project_root / relative_path
    try:
        resolved = target.resolve(strict=True)
        resolved.relative_to(project_root)
        if not resolved.is_file():
            raise ValueError("not a file")
        return resolved.read_bytes()
    except (OSError, ValueError) as exc:
        raise ValueError(f"local PR {label} is unavailable") from exc
