"""从既有 SourceSnapshot 投影排除评审工件的受保护身份。"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ai_sdlc.core.source_content_identity import CHANGE_IDENTITY_KIND
from ai_sdlc.core.source_snapshot import (
    SourceSnapshot,
    SourceSnapshotOptions,
    build_source_snapshot,
    source_snapshot_identity_issue,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
    normalize_repo_path,
)

_REVIEW_ROOT_PREFIX = ".ai-sdlc/state/stage-review/"
_SAFE_ID_SEGMENT = re.compile(r"[a-z0-9][a-z0-9._-]*")
_WINDOWS_RESERVED_BASENAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_SOURCE_SNAPSHOT_POLICY = CanonicalizationPolicy(
    set_like_fields=frozenset(
        {
            "changed_files",
            "untracked_files",
            "deleted_files",
            "binary_files",
            "safe_eol_paths",
        }
    ),
    path_fields=frozenset(
        {
            "changed_files",
            "untracked_files",
            "deleted_files",
            "binary_files",
            "safe_eol_paths",
        }
    ),
)


@dataclass(frozen=True, slots=True)
class CandidateSourceBinding:
    change_surface: list[str]
    snapshot_digest: str
    source_tree_digest: str
    change_surface_digest: str


def _source_snapshot_binding_digest(
    snapshot: SourceSnapshot,
    *,
    exclusions: list[str],
    protected_source_set: list[str],
    policy_digests: list[str],
) -> str:
    """按 Candidate 相同排除与保护域计算 SourceSnapshot 摘要。"""
    return candidate_source_binding(
        snapshot,
        exclusions,
        protected_source_set,
        policy_digests,
    ).snapshot_digest


def _review_artifact_path_allowed(path: str, exclusion_root: str) -> bool:
    """只接受规范 Session 根内路径，并拒绝跨平台别名。"""
    normalized = normalize_repo_path(path)
    root = normalize_repo_path(exclusion_root)
    if _is_at_or_below(normalized, root):
        _windows_path_key(normalized)
        return True
    if _is_portable_alias_at_or_below(normalized, root):
        raise ValueError("non-canonical review artifact alias")
    return False


def _review_session_root(
    project_id: str,
    work_item_id: str,
    stage_instance_id: str,
    review_session_id: str,
) -> str:
    """从受信 Session 身份唯一派生评审工件根。"""

    parts = [project_id, work_item_id, stage_instance_id, review_session_id]
    if any(not _safe_identity_segment(part) for part in parts):
        raise ValueError("review session identity must be a safe path segment")
    return normalize_repo_path(
        f"{_REVIEW_ROOT_PREFIX}{project_id}/sessions/"
        f"{work_item_id}/{stage_instance_id}/{review_session_id}"
    )


def _safe_identity_segment(part: str) -> bool:
    """限制为各平台具有同一物理目录身份的 canonical segment。"""

    basename = part.split(".", maxsplit=1)[0]
    return (
        part not in {".", ".."}
        and not part.endswith(".")
        and _SAFE_ID_SEGMENT.fullmatch(part) is not None
        and normalize_repo_path(part) == part
        and basename not in _WINDOWS_RESERVED_BASENAMES
    )


def _is_at_or_below(path: str, root: str) -> bool:
    return path == root or path.startswith(f"{root}/")


def _is_portable_alias_at_or_below(path: str, root: str) -> bool:
    """识别会在 Win32 上映射到同一评审目录的路径别名。"""

    path_key = _windows_path_key(path)
    root_key = _windows_path_key(root)
    return path_key == root_key or path_key.startswith(f"{root_key}/")


def _windows_path_key(path: str) -> str:
    normalized = path.replace("\\", "/")
    if (
        not normalized
        or normalized != normalized.strip()
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:/", normalized)
    ):
        raise ValueError(f"non-canonical review artifact alias: {path}")
    parts: list[str] = []
    for raw_part in normalized.split("/"):
        if (
            not raw_part
            or raw_part in {".", ".."}
            or raw_part != raw_part.rstrip(" .")
        ):
            raise ValueError(f"non-canonical review artifact alias: {path}")
        parts.append(raw_part.lower())
    return "/".join(parts)


def _outside_roots(path: str, roots: list[str]) -> bool:
    return not any(_is_at_or_below(path, root) for root in roots)


def _source_snapshot_payload(
    snapshot: SourceSnapshot,
    exclusions: list[str],
) -> dict[str, object]:
    """移除当前评审 Session 路径并保留全部源码变化身份。"""

    if snapshot.change_identity_kind != CHANGE_IDENTITY_KIND:
        raise ValueError("source snapshot requires complete change identities")
    issue = source_snapshot_identity_issue(snapshot)
    if issue:
        raise ValueError(f"invalid source snapshot: {issue}")
    return {
        "source_kind": snapshot.source_kind,
        "base_commit": snapshot.base_commit,
        "changed_files": _paths(snapshot.changed_files, exclusions),
        "untracked_files": _paths(snapshot.untracked_files, exclusions),
        "deleted_files": _paths(snapshot.deleted_files, exclusions),
        "binary_files": _paths(snapshot.binary_files, exclusions),
        "renamed_files": {
            path: old
            for path, old in sorted(snapshot.renamed_files.items())
            if _retained(path, exclusions) and _retained(old, exclusions)
        },
        "file_digests": _mapping(snapshot.file_digests, exclusions),
        "canonical_digest_kind": snapshot.canonical_digest_kind,
        "canonical_file_digests": _mapping(
            snapshot.canonical_file_digests, exclusions
        ),
        "change_identity_kind": snapshot.change_identity_kind,
        "raw_change_identities": _mapping(
            snapshot.raw_change_identities, exclusions
        ),
        "portable_change_identities": _mapping(
            snapshot.portable_change_identities, exclusions
        ),
        "safe_eol_paths": _paths(snapshot.safe_eol_paths, exclusions),
    }


def candidate_source_binding(
    snapshot: SourceSnapshot,
    exclusions: list[str],
    protected_roots: list[str],
    policy_digests: list[str],
) -> CandidateSourceBinding:
    payload = _source_snapshot_payload(snapshot, exclusions)
    change_surface = sorted(_string_mapping(payload["raw_change_identities"]))
    outside = [
        path for path in change_surface if _outside_roots(path, protected_roots)
    ]
    if outside:
        raise ValueError(
            "source snapshot change is outside protected_source_set: "
            + ", ".join(outside)
        )
    return CandidateSourceBinding(
        change_surface=change_surface,
        snapshot_digest=canonical_digest(payload, _SOURCE_SNAPSHOT_POLICY),
        source_tree_digest=canonical_digest(
            {
                "source_snapshot": payload,
                "protected_source_set": protected_roots,
                "policy_digests": policy_digests,
            },
            _SOURCE_SNAPSHOT_POLICY,
        ),
        change_surface_digest=canonical_digest(
            {
                "change_surface": change_surface,
                "raw_change_identities": payload["raw_change_identities"],
            },
            _SOURCE_SNAPSHOT_POLICY,
        ),
    )


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("stage-review artifact field must be a string list")
    return list(value)


def _string_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in value.items()
    ):
        raise ValueError("stage-review artifact field must be a string map")
    return dict(value)


def _artifact_digests(
    root: Path,
    snapshot: SourceSnapshot,
    paths: list[str],
) -> dict[str, str]:
    """从冻结 SourceSnapshot 或仓库内实际不可变工件派生摘要。"""

    normalized = sorted({normalize_repo_path(path) for path in paths})
    return {
        path: snapshot.file_digests.get(path) or _repository_file_digest(root, path)
        for path in normalized
    }


def _require_fresh_protected_snapshot(
    root: Path,
    snapshot: SourceSnapshot,
    exclusions: list[str],
) -> None:
    """重放当前仓库，并忽略仅属于当前 Session 的评审工件。"""

    try:
        current = build_source_snapshot(
            SourceSnapshotOptions(
                root=root,
                source_kind=snapshot.source_kind,
                base_ref=snapshot.base_ref,
                head_ref=snapshot.head_ref,
                patch_file=snapshot.patch_file,
            )
        )
    except (OSError, ValueError) as exc:
        raise ValueError(f"candidate source snapshot is unavailable: {exc}") from exc
    expected = _source_snapshot_payload(snapshot, exclusions)
    actual = _source_snapshot_payload(current, exclusions)
    if canonical_digest(expected, _SOURCE_SNAPSHOT_POLICY) != canonical_digest(
        actual, _SOURCE_SNAPSHOT_POLICY
    ):
        raise ValueError("candidate source snapshot is stale")


def _repository_file_digest(root: Path, path: str) -> str:
    project_root = root.resolve()
    target = project_root / path
    try:
        resolved = target.resolve(strict=True)
        resolved.relative_to(project_root)
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError(f"candidate artifact is unavailable inside repository: {path}") from exc
    if not resolved.is_file():
        raise ValueError(f"candidate artifact is not a file: {path}")
    return f"sha256:{hashlib.sha256(resolved.read_bytes()).hexdigest()}"


def _retained(path: str, exclusions: list[str]) -> bool:
    for root in exclusions:
        if _is_at_or_below(path, root):
            _windows_path_key(path)
            return False
        if _is_portable_alias_at_or_below(path, root):
            raise ValueError(f"non-canonical review artifact alias: {path}")
    return True


def _paths(values: list[str], exclusions: list[str]) -> list[str]:
    return sorted(path for path in values if _retained(path, exclusions))


def _mapping(values: Mapping[str, str], exclusions: list[str]) -> dict[str, str]:
    return dict(
        sorted(
            (path, value)
            for path, value in values.items()
            if _retained(path, exclusions)
        )
    )
