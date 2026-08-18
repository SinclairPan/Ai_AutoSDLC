"""Read-only CLI mapping from existing Loop results to review inputs."""

from __future__ import annotations

import base64
import codecs
import hashlib
import json
import os
import re
import subprocess
from collections.abc import MutableMapping, Sequence
from pathlib import Path
from typing import cast

import typer

from ai_sdlc.core.loop_review_service import (
    LoopReviewPreparation,
    LoopReviewServiceError,
    RecordLoopReviewOptions,
    prepare_loop_review,
    record_loop_review,
)
from ai_sdlc.core.review_kernel import LoopReviewType, ReviewInput, build_review_input
from ai_sdlc.core.source_snapshot import SourceSnapshotOptions, build_source_snapshot
from ai_sdlc.core.stable_file_read import consume_stable_chunks, read_stable_text
from ai_sdlc.utils.helpers import find_project_root

_STAGE_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "requirement": (
        "requirement-intake.json",
        "requirement-brief.md",
        "clarification-questions.md",
        "acceptance-checklist.md",
    ),
    "design-contract": (
        "design-contract-input.json",
        "design-contract-report.json",
        "design-contract-report.md",
    ),
    "implementation": (
        "implementation-input.json",
        "implementation-report.json",
        "implementation-report.md",
        "verification-evidence.json",
        "implementation-tasks.json",
        "implementation-progress.json",
    ),
    "frontend-evidence": (
        "frontend-evidence-input.json",
        "frontend-evidence-snapshot.json",
        "frontend-evidence-report.json",
        "frontend-evidence-report.md",
    ),
}
_STAGE_CLOSE_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "requirement": ("requirement-intake.json",),
    "design-contract": (
        "design-contract-input.json",
        "design-contract-report.json",
    ),
    "implementation": (
        "implementation-input.json",
        "implementation-report.json",
        "implementation-tasks.json",
        "implementation-progress.json",
    ),
    "frontend-evidence": (
        "frontend-evidence-snapshot.json",
        "frontend-evidence-report.json",
    ),
}
_STAGE_PREDECESSORS: dict[str, tuple[str, str, str]] = {
    "design-contract": (
        "design-contract-input.json",
        "requirement_loop_id",
        "requirement",
    ),
    "implementation": (
        "implementation-input.json",
        "design_contract_loop_id",
        "design-contract",
    ),
    "frontend-evidence": (
        "frontend-evidence-input.json",
        "implementation_loop_id",
        "implementation",
    ),
}
_STAGE_POINTER_NAMES = {
    "requirement": "current-requirement.json",
    "design-contract": "current-design-contract.json",
    "implementation": "current-implementation.json",
    "frontend-evidence": "current-frontend-evidence.json",
}
_LOCAL_REQUIRED = ("review-pack.json", "findings.json")
_LOCAL_OPTIONAL = ("resolution.yaml", "verification-evidence.json")
_CURRENT_LOCAL_REVIEW = Path(".ai-sdlc") / "reviews" / "pr" / "current-review.json"
_RISK_TERMS: dict[str, tuple[str, ...]] = {
    "public-api": ("public api", "public-api", "schema", "contract"),
    "security": ("security", "authorization", "permission", "secret"),
    "data-integrity": ("database", "migration", "transaction", "data loss"),
    "concurrency": ("concurrency", "parallel", "race", "lock"),
    "frontend": ("frontend", "browser", "accessibility", "ui", "ux"),
}
_RISK_SCAN_OVERLAP = (
    max(len(term) for terms in _RISK_TERMS.values() for term in terms) + 2
)
_TEXT_RISK_SUFFIXES = {
    ".bat",
    ".cfg",
    ".cmd",
    ".css",
    ".csv",
    ".diff",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".patch",
    ".ps1",
    ".py",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}
_BINARY_RISK_SUFFIXES = {
    ".avi",
    ".bmp",
    ".db",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp4",
    ".otf",
    ".pdf",
    ".png",
    ".sqlite",
    ".tar",
    ".ttf",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".zip",
}
_GIT_ROUTING_ENV = {
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_REPLACE_REF_BASE",
    "GIT_CONFIG",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
}


class ReviewInputGuardError(ValueError):
    """A close request no longer matches the input selected for review."""

    def __init__(
        self,
        reason: str,
        *,
        detail: str = "",
        expected_digest: str = "",
        actual_digest: str = "",
    ) -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail
        self.expected_digest = expected_digest
        self.actual_digest = actual_digest

    def payload(self) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "blocked",
            "reason": self.reason,
        }
        if self.detail:
            result["detail"] = self.detail
        if self.expected_digest:
            result["expected_digest"] = self.expected_digest
        if self.actual_digest:
            result["actual_digest"] = self.actual_digest
        return result


def validate_review_input_for_close(
    root: Path,
    *,
    loop_type: str,
    loop_id: str,
    expected_digest: str,
    captured_artifacts: MutableMapping[str, bytes] | None = None,
) -> ReviewInput:
    """Rebuild the reviewed input inside the close process and fail on drift."""

    expected = expected_digest.strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise ReviewInputGuardError(
            "review-input-unavailable",
            detail="Expected review input digest must be 64 lowercase hexadecimal characters.",
        )
    try:
        review_input = resolve_review_input(
            root,
            loop_type=loop_type,
            loop_id=loop_id,
            captured_artifacts=captured_artifacts,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ReviewInputGuardError(
            "review-input-unavailable",
            detail=str(exc),
            expected_digest=expected,
        ) from exc
    if review_input.input_digest != expected:
        raise ReviewInputGuardError(
            "review-input-drift",
            expected_digest=expected,
            actual_digest=review_input.input_digest,
        )
    return review_input


def loop_review(
    loop_type: str = typer.Option(..., "--type", help="Loop result type."),
    loop_id: str = typer.Option(..., "--loop-id", help="Existing Loop id."),
    expect_digest: str = typer.Option(
        "",
        "--expect-digest",
        help="Fail if the current substantive input no longer matches this digest.",
    ),
    read_path: str = typer.Option(
        "",
        "--read-path",
        help="Return one artifact's bytes from the same digest-bound read.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Build or recheck one read-only dynamic-expert review input."""

    try:
        root = find_project_root()
        if root is None:
            raise ValueError("Project is not initialized; .ai-sdlc is missing.")
        expected = expect_digest.strip().lower()
        requested_path = read_path.strip()
        if requested_path and not expected:
            raise ValueError("--read-path requires --expect-digest.")
        prepared, _ = prepare_current_loop_review(root, loop_type, loop_id)
        captured_artifacts: dict[str, bytes] | None = {} if requested_path else None
        review_input = prepared.review_input
        if captured_artifacts is not None:
            review_input = resolve_review_input(
                root,
                loop_type=loop_type,
                loop_id=loop_id,
                review_round_number=prepared.review_input.round_number,
                captured_artifacts=captured_artifacts,
                capture_paths=[requested_path],
            )
            if review_input.input_digest != prepared.review_input.input_digest:
                raise LoopReviewServiceError("review-input-drift")
        if expected and expected != review_input.input_digest:
            _emit(
                {
                    "status": "blocked",
                    "reason": "review-input-drift",
                    "expected_digest": expected,
                    "actual_digest": review_input.input_digest,
                },
                json_output=json_output,
            )
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except LoopReviewServiceError as exc:
        _emit(exc.payload(), json_output=json_output)
        raise typer.Exit(1) from exc
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        _emit(
            {
                "status": "blocked",
                "reason": "review-input-unavailable",
                "detail": str(exc),
            },
            json_output=json_output,
        )
        raise typer.Exit(1) from exc

    payload = review_input.model_dump(mode="json")
    payload.update(
        {
            "review_status": prepared.status,
            "review_reason": prepared.reason,
            "next_action": prepared.next_action,
        }
    )
    if captured_artifacts is not None:
        if len(captured_artifacts) != 1:
            raise typer.Exit(1)
        path, content = next(iter(captured_artifacts.items()))
        payload["review_snapshot"] = _review_snapshot_payload(path, content)
    _emit(payload, json_output=json_output)


def loop_review_record(
    loop_type: str = typer.Option(..., "--type", help="Loop result type."),
    loop_id: str = typer.Option(..., "--loop-id", help="Existing Loop id."),
    expect_digest: str = typer.Option(
        ...,
        "--expect-digest",
        help="Digest returned by the current review input.",
    ),
    result_paths: list[Path] = typer.Option(
        ...,
        "--result",
        help="One single-role ReviewExecution JSON file. Repeat per selected expert.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Record one bounded independent-expert review round."""

    try:
        root = find_project_root()
        if root is None:
            raise ValueError("Project is not initialized; .ai-sdlc is missing.")
        prepared, loop_dir = prepare_current_loop_review(root, loop_type, loop_id)
        overlay = record_loop_review(
            RecordLoopReviewOptions(
                root=root,
                loop_type=cast(LoopReviewType, loop_type),
                loop_id=loop_id,
                expected_digest=expect_digest,
                result_paths=tuple(result_paths),
            ),
            loop_dir=loop_dir,
            input_resolver=lambda round_number: resolve_review_input(
                root,
                loop_type=loop_type,
                loop_id=loop_id,
                review_round_number=round_number,
            ),
        )
    except LoopReviewServiceError as exc:
        _emit(exc.payload(), json_output=json_output)
        raise typer.Exit(1) from exc
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        _emit(
            {
                "status": "blocked",
                "reason": "review-result-invalid",
                "detail": str(exc),
            },
            json_output=json_output,
        )
        raise typer.Exit(1) from exc

    payload = overlay.model_dump(mode="json")
    payload.update(
        {
            "input_digest": prepared.review_input.input_digest,
            "outcome_path": prepared.outcome_path.relative_to(root).as_posix(),
        }
    )
    _emit(payload, json_output=json_output)


def prepare_current_loop_review(
    root: Path,
    loop_type: str,
    loop_id: str,
) -> tuple[LoopReviewPreparation, Path]:
    """Resolve current Loop identity and derive its bounded review state."""

    safe_loop_id = _safe_identifier(loop_id)
    loop_dir = resolve_review_directory(root, loop_type, safe_loop_id)
    prepared = prepare_loop_review(
        root,
        loop_type=cast(LoopReviewType, loop_type),
        loop_id=safe_loop_id,
        loop_dir=loop_dir,
        input_resolver=lambda round_number: resolve_review_input(
            root,
            loop_type=loop_type,
            loop_id=safe_loop_id,
            review_round_number=round_number,
        ),
    )
    return prepared, loop_dir


def resolve_review_directory(root: Path, loop_type: str, loop_id: str) -> Path:
    """Return the canonical existing directory after validating its current pointer."""

    if loop_type == "local-pr-review":
        loop_dir, _, _ = _find_local_review_dir(root, loop_id)
        return loop_dir
    if loop_type not in _STAGE_ARTIFACTS:
        raise ValueError(f"Unsupported review Loop type: {loop_type}")
    _resolve_current_stage_state(root, loop_type, loop_id)
    return root / ".ai-sdlc" / "loops" / loop_type / loop_id


def resolve_review_input(
    root: Path,
    *,
    loop_type: str,
    loop_id: str,
    review_round_number: int | None = None,
    captured_artifacts: MutableMapping[str, bytes] | None = None,
    capture_paths: Sequence[str | Path] | None = None,
) -> ReviewInput:
    """Resolve existing substantive artifacts without creating a parallel Loop."""

    safe_loop_id = _safe_identifier(loop_id)
    if loop_type == "local-pr-review":
        loop_dir, pointer_path, run_path = _find_local_review_dir(root, safe_loop_id)
        artifacts = [
            *(loop_dir / name for name in _LOCAL_REQUIRED),
        ]
        artifacts.extend(
            loop_dir / name for name in _LOCAL_OPTIONAL if (loop_dir / name).is_file()
        )
        diff_path = _local_review_diff(root, loop_dir / "review-pack.json")
        artifacts.append(diff_path)
        risk_signals = [
            *_content_risk_signals(root, artifacts),
            *_git_risk_signals(root),
            *_local_review_source_risk_signals(root, loop_dir / "review-pack.json"),
        ]
        round_number = _read_round_number(root, run_path)
        capture_artifact_paths = (
            list(capture_paths)
            if capture_paths is not None
            else (
                [path for path in artifacts if path != diff_path]
                if captured_artifacts is not None
                else []
            )
        )
        capture_only_paths = (
            [pointer_path, run_path]
            if captured_artifacts is not None and capture_paths is None
            else []
        )
    elif loop_type in _STAGE_ARTIFACTS:
        loop_dir = root / ".ai-sdlc" / "loops" / loop_type / safe_loop_id
        _, run_path = _resolve_current_stage_state(
            root,
            loop_type,
            safe_loop_id,
        )
        stage_source_material = _stage_source_material(root, loop_type, loop_dir)
        artifacts = _unique_paths(
            [
                *(loop_dir / name for name in _STAGE_ARTIFACTS[loop_type]),
                *stage_source_material,
            ]
        )
        upstream_context = _exclude_paths(
            _stage_upstream_context(root, loop_type, loop_dir),
            excluded=artifacts,
        )
        risk_signals = _content_risk_signals(
            root,
            [*artifacts, *upstream_context],
        )
        round_number = _read_round_number(root, run_path)
        capture_artifact_paths = (
            list(capture_paths)
            if capture_paths is not None
            else (
                [
                    *(loop_dir / name for name in _STAGE_CLOSE_ARTIFACTS[loop_type]),
                    *(stage_source_material if loop_type == "design-contract" else []),
                ]
                if captured_artifacts is not None
                else []
            )
        )
        capture_only_paths = (
            [run_path]
            if captured_artifacts is not None and capture_paths is None
            else []
        )
    else:
        raise ValueError(f"Unsupported review Loop type: {loop_type}")

    if review_round_number is not None:
        if review_round_number not in {1, 2}:
            raise ValueError("Review round number must be 1 or 2.")
        round_number = review_round_number

    return build_review_input(
        root,
        loop_id=safe_loop_id,
        loop_type=cast(LoopReviewType, loop_type),
        round_number=round_number,
        artifact_paths=artifacts,
        upstream_context_paths=upstream_context
        if loop_type != "local-pr-review"
        else [],
        risk_signals=risk_signals,
        capture_artifact_paths=capture_artifact_paths,
        capture_only_paths=capture_only_paths,
        captured_artifacts=captured_artifacts,
    )


def _review_snapshot_payload(path: str, content: bytes) -> dict[str, str]:
    try:
        rendered = content.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "path": path,
            "encoding": "base64",
            "content": base64.b64encode(content).decode("ascii"),
        }
    return {
        "path": path,
        "encoding": "utf-8",
        "content": rendered,
    }


def _stage_upstream_context(
    root: Path,
    loop_type: str,
    loop_dir: Path,
    *,
    visited: frozenset[tuple[str, str]] = frozenset(),
) -> list[Path]:
    predecessor = _STAGE_PREDECESSORS.get(loop_type)
    if predecessor is None:
        return []
    input_name, id_field, predecessor_type = predecessor
    input_path = loop_dir / input_name
    try:
        payload = json.loads(read_stable_text(root, input_path, encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Loop input is unreadable: {input_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Loop input root must be an object: {input_path}")
    raw_loop_id = payload.get(id_field, "")
    if not isinstance(raw_loop_id, str):
        raise ValueError(f"Loop predecessor id is invalid: {input_path}")
    predecessor_id = raw_loop_id.strip()
    if not predecessor_id:
        if loop_type == "design-contract":
            return []
        raise ValueError(f"Loop predecessor id is missing: {input_path}")
    safe_predecessor_id = _safe_identifier(predecessor_id)
    identity = (predecessor_type, safe_predecessor_id)
    if identity in visited:
        raise ValueError(
            f"Loop predecessor cycle detected: {predecessor_type}/{safe_predecessor_id}"
        )
    predecessor_dir = (
        root / ".ai-sdlc" / "loops" / predecessor_type / safe_predecessor_id
    )
    inherited = _stage_upstream_context(
        root,
        predecessor_type,
        predecessor_dir,
        visited=visited | {identity},
    )
    predecessor_artifacts = [
        *(predecessor_dir / name for name in _STAGE_ARTIFACTS[predecessor_type]),
        *_stage_source_material(root, predecessor_type, predecessor_dir),
    ]
    return _unique_paths([*inherited, *predecessor_artifacts])


def _stage_source_material(root: Path, loop_type: str, loop_dir: Path) -> list[Path]:
    if loop_type == "requirement":
        return []
    if loop_type == "design-contract":
        payload = _read_json_object(root, loop_dir / "design-contract-input.json")
        return [
            _repo_path(root, value, field_name)
            for field_name in ("spec_path", "plan_path", "tasks_path")
            if isinstance((value := payload.get(field_name)), str) and value.strip()
        ]
    if loop_type == "implementation":
        payload = _read_json_object(root, loop_dir / "implementation-input.json")
        declared_scope = payload.get("declared_scope", [])
        if not isinstance(declared_scope, list) or not all(
            isinstance(item, str) for item in declared_scope
        ):
            raise ValueError(
                f"Loop declared_scope is invalid: {loop_dir / 'implementation-input.json'}"
            )
        return _unique_paths(
            [
                *_expand_repo_patterns(root, declared_scope),
                *_implementation_evidence_material(root, loop_dir),
            ]
        )
    if loop_type == "frontend-evidence":
        input_payload = _read_json_object(
            root, loop_dir / "frontend-evidence-input.json"
        )
        snapshot_payload = _read_json_object(
            root, loop_dir / "frontend-evidence-snapshot.json"
        )
        referenced: list[Path] = []
        source_path = input_payload.get("source_artifact_path", "")
        if isinstance(source_path, str) and source_path.strip():
            referenced.append(_repo_path(root, source_path, "source_artifact_path"))
        records = snapshot_payload.get("artifact_records", [])
        if not isinstance(records, list):
            raise ValueError(
                "Frontend evidence artifact_records must be a list: "
                f"{loop_dir / 'frontend-evidence-snapshot.json'}"
            )
        for record in records:
            if (
                not isinstance(record, dict)
                or record.get("capture_status") != "captured"
            ):
                continue
            artifact_ref = record.get("artifact_ref", "")
            if isinstance(artifact_ref, str) and artifact_ref.strip():
                referenced.append(_repo_path(root, artifact_ref, "artifact_ref"))
        for field_name in ("screenshot_refs", "trace_refs"):
            refs = snapshot_payload.get(field_name, [])
            if not isinstance(refs, list):
                raise ValueError(
                    f"Frontend evidence {field_name} must be a list: "
                    f"{loop_dir / 'frontend-evidence-snapshot.json'}"
                )
            for ref in refs:
                if isinstance(ref, str) and ref.strip():
                    referenced.append(_repo_path(root, ref, field_name))
        return _unique_paths(referenced)
    return []


def _implementation_evidence_material(root: Path, loop_dir: Path) -> list[Path]:
    payload = _read_json_object(root, loop_dir / "verification-evidence.json")
    tasks = payload.get("tasks", [])
    if not isinstance(tasks, list):
        raise ValueError(
            "Implementation verification evidence tasks must be a list: "
            f"{loop_dir / 'verification-evidence.json'}"
        )
    referenced: list[Path] = []
    resolved_root = root.resolve()
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError(
                "Implementation verification evidence task must be an object: "
                f"{loop_dir / 'verification-evidence.json'}"
            )
        evidence = task.get("evidence", [])
        if not isinstance(evidence, list) or not all(
            isinstance(item, str) for item in evidence
        ):
            raise ValueError(
                "Implementation verification evidence paths must be strings: "
                f"{loop_dir / 'verification-evidence.json'}"
            )
        for item in evidence:
            text = item.strip()
            if not text:
                continue
            candidate = Path(text)
            unresolved = (
                candidate if candidate.is_absolute() else resolved_root / candidate
            )
            lexical = _lexical_path(unresolved)
            try:
                lexical.relative_to(resolved_root)
            except ValueError:
                continue
            referenced.extend(_expand_review_material(lexical))
    return _unique_paths(referenced)


def _local_review_diff(root: Path, review_pack_path: Path) -> Path:
    payload = _read_json_object(root, review_pack_path)
    diff_path_text = payload.get("diff_path", "")
    diff_digest = payload.get("diff_digest", "")
    if not isinstance(diff_path_text, str) or not diff_path_text.strip():
        raise ValueError(f"Review pack diff_path is missing: {review_pack_path}")
    if not isinstance(diff_digest, str) or not diff_digest.startswith("sha256:"):
        raise ValueError(f"Review pack diff_digest is invalid: {review_pack_path}")
    diff_path = _repo_path(root, diff_path_text, "diff_path")
    try:
        actual = _file_sha256(root, diff_path)
    except OSError as exc:
        raise ValueError(f"Review diff is unreadable: {diff_path}") from exc
    if diff_digest != f"sha256:{actual}":
        raise ValueError(
            f"Review diff digest does not match review-pack.json: {diff_path}"
        )
    return diff_path


def _read_json_object(root: Path, path: Path) -> dict[str, object]:
    try:
        payload = json.loads(read_stable_text(root, path, encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Loop input is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Loop input root must be an object: {path}")
    return payload


def _repo_path(root: Path, value: str, field_name: str) -> Path:
    resolved_root = root.resolve()
    candidate = Path(value)
    unresolved = candidate if candidate.is_absolute() else resolved_root / candidate
    lexical = Path(os.path.abspath(unresolved))
    try:
        lexical.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"Loop {field_name} escapes the project: {value}") from exc
    try:
        lexical.resolve(strict=False).relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"Loop {field_name} escapes the project: {value}") from exc
    return lexical


def _expand_repo_patterns(root: Path, patterns: list[str]) -> list[Path]:
    expanded: list[Path] = []
    for pattern in patterns:
        pattern_path = Path(pattern)
        if pattern_path.is_absolute() or ".." in pattern_path.parts:
            raise ValueError(f"Loop declared_scope escapes the project: {pattern}")
        matches = sorted(root.glob(pattern))
        for match in matches:
            resolved = _repo_path(root, str(match), "declared_scope")
            expanded.extend(_expand_review_material(resolved))
    return _unique_paths(expanded)


def _expand_review_material(path: Path) -> list[Path]:
    if path.is_symlink():
        return [path]
    if path.is_dir():
        return [
            nested
            for nested in sorted(path.rglob("*"))
            if nested.is_symlink() or nested.is_file()
        ]
    if path.is_file():
        return [path]
    return []


def _unique_paths(paths: list[Path]) -> list[Path]:
    unique: dict[Path, Path] = {}
    for path in paths:
        unique.setdefault(_lexical_path(path), path)
    return list(unique.values())


def _file_sha256(root: Path, path: Path) -> str:
    digest = hashlib.sha256()
    consume_stable_chunks(root, path, digest.update)
    return digest.hexdigest()


def _exclude_paths(paths: list[Path], *, excluded: list[Path]) -> list[Path]:
    excluded_keys = {_lexical_path(path) for path in excluded}
    return [path for path in paths if _lexical_path(path) not in excluded_keys]


def _lexical_path(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _resolve_current_stage_state(
    root: Path,
    loop_type: str,
    loop_id: str,
) -> tuple[Path, Path]:
    pointer_path = (
        root / ".ai-sdlc" / "loops" / loop_type / _STAGE_POINTER_NAMES[loop_type]
    )
    pointer = _read_json_object(root, pointer_path)
    if pointer.get("loop_id") != loop_id:
        raise ValueError(
            f"Current {loop_type} review does not identify Loop {loop_id}."
        )
    raw_run_path = pointer.get("loop_run_path", "")
    if not isinstance(raw_run_path, str) or not raw_run_path.strip():
        raise ValueError(f"Current {loop_type} Loop run path is missing.")
    run_path = _repo_path(root, raw_run_path, "loop_run_path")
    expected_run_path = (
        root / ".ai-sdlc" / "loops" / loop_type / loop_id / "loop-run.json"
    )
    if _lexical_path(run_path) != _lexical_path(expected_run_path):
        raise ValueError(f"Current {loop_type} Loop run path is not canonical.")
    run = _read_json_object(root, run_path)
    if run.get("loop_id") != loop_id or run.get("loop_type") != loop_type:
        raise ValueError(
            f"Current {loop_type} Loop run does not identify Loop {loop_id}."
        )
    return pointer_path, run_path


def _find_local_review_dir(
    root: Path,
    loop_id: str,
) -> tuple[Path, Path, Path]:
    pointer_path = root / _CURRENT_LOCAL_REVIEW
    pointer = _read_json_object(root, pointer_path)
    if pointer.get("loop_id") != loop_id:
        raise ValueError(f"Current local PR review does not identify Loop {loop_id}.")
    raw_review_id = pointer.get("review_id", "")
    raw_run_path = pointer.get("review_run_path", "")
    if not isinstance(raw_review_id, str) or not raw_review_id.strip():
        raise ValueError("Current local PR review id is missing.")
    if not isinstance(raw_run_path, str) or not raw_run_path.strip():
        raise ValueError("Current local PR review run path is missing.")
    review_id = _safe_identifier(raw_review_id)
    run_path = _repo_path(root, raw_run_path, "review_run_path")
    expected_run_path = (
        root / ".ai-sdlc" / "reviews" / "pr" / review_id / "review-run.json"
    )
    if _lexical_path(run_path) != _lexical_path(expected_run_path):
        raise ValueError("Current local PR review run path is not canonical.")
    run = _read_json_object(root, run_path)
    if run.get("review_id") != review_id or run.get("loop_id") != loop_id:
        raise ValueError(
            f"Current local PR review run does not identify Loop {loop_id}."
        )
    for field_name, filename in (
        ("review_pack_path", "review-pack.json"),
        ("findings_path", "findings.json"),
    ):
        raw_artifact_path = run.get(field_name, "")
        if raw_artifact_path in {None, ""}:
            continue
        if not isinstance(raw_artifact_path, str):
            raise ValueError(f"Current local PR review {field_name} is invalid.")
        artifact_path = _repo_path(root, raw_artifact_path, field_name)
        expected_artifact_path = run_path.parent / filename
        if _lexical_path(artifact_path) != _lexical_path(expected_artifact_path):
            raise ValueError(f"Current local PR review {field_name} is not canonical.")
    return run_path.parent, pointer_path, run_path


def _read_round_number(root: Path, path: Path) -> int:
    try:
        payload = json.loads(read_stable_text(root, path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Loop state is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Loop state root must be an object: {path}")
    value = payload.get("current_round", payload.get("round_number", 1))
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 1
    return value


def _content_risk_signals(root: Path, paths: list[Path]) -> list[str]:
    detected: set[str] = set()
    for path in paths:
        if path.is_symlink():
            raise ValueError(f"review path is not a regular file: {path}")
        suffix = path.suffix.lower()
        if suffix in _BINARY_RISK_SUFFIXES:
            continue
        try:
            detected.update(
                _stream_text_risk_signals(
                    root,
                    path,
                    strict_text=suffix in _TEXT_RISK_SUFFIXES,
                )
            )
        except OSError as exc:
            raise ValueError(f"Review artifact is unreadable: {path}") from exc
    risks = [risk for risk in _RISK_TERMS if risk in detected]
    return risks or ["general-correctness"]


def _stream_text_risk_signals(
    root: Path,
    path: Path,
    *,
    strict_text: bool,
) -> set[str]:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    detected: set[str] = set()
    tail = ""
    left_truncated = False

    def scan_chunk(chunk: bytes) -> None:
        nonlocal tail, left_truncated
        combined = tail + decoder.decode(chunk).lower()
        detected.update(
            _matching_risk_signals(
                combined,
                eof=False,
                left_truncated=left_truncated,
            )
        )
        left_truncated = left_truncated or len(combined) > _RISK_SCAN_OVERLAP
        tail = combined[-_RISK_SCAN_OVERLAP:]

    try:
        consume_stable_chunks(root, path, scan_chunk)
        combined = tail + decoder.decode(b"", final=True).lower()
    except UnicodeDecodeError as exc:
        if strict_text:
            raise ValueError(
                f"Review text artifact is not strict UTF-8: {path}"
            ) from exc
        return set()
    detected.update(
        _matching_risk_signals(
            combined,
            eof=True,
            left_truncated=left_truncated,
        )
    )
    return detected


def _matching_risk_signals(
    content: str,
    *,
    eof: bool,
    left_truncated: bool,
) -> set[str]:
    detected: set[str] = set()
    for risk, terms in _RISK_TERMS.items():
        for term in terms:
            pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
            for match in re.finditer(pattern, content):
                if left_truncated and match.start() == 0:
                    continue
                if eof or match.end() < len(content):
                    detected.add(risk)
                    break
            if risk in detected:
                break
    return detected


def _git_risk_signals(root: Path) -> list[str]:
    head = _git_bytes(root, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
    index = _git_bytes(root, "ls-files", "--stage", "-z")
    index_flags = _git_bytes(root, "ls-files", "-v", "-z")
    staged = _git_bytes(root, "diff", "--cached", "--binary", "--no-ext-diff", "--")
    return [
        f"git-head:{head}",
        f"git-index:{hashlib.sha256(index).hexdigest()}",
        f"git-index-flags:{hashlib.sha256(index_flags).hexdigest()}",
        f"git-staged-diff:{hashlib.sha256(staged).hexdigest()}",
    ]


def _local_review_source_risk_signals(root: Path, review_pack_path: Path) -> list[str]:
    payload = _read_json_object(root, review_pack_path)
    diff_source = payload.get("diff_source")
    if diff_source is None:
        return []
    if not isinstance(diff_source, dict):
        raise ValueError(f"Review pack diff_source is invalid: {review_pack_path}")
    source_kind = diff_source.get("source_kind", "")
    if source_kind == "patch":
        patch_file = diff_source.get("patch_file", "")
        head_ref = diff_source.get("head_ref", payload.get("head_ref", "HEAD"))
        if not isinstance(patch_file, str) or not patch_file.strip():
            raise ValueError(f"Review pack patch_file is invalid: {review_pack_path}")
        if not isinstance(head_ref, str) or not head_ref.strip():
            raise ValueError(
                f"Review pack patch head_ref is invalid: {review_pack_path}"
            )
        snapshot = build_source_snapshot(
            SourceSnapshotOptions(
                root=root,
                source_kind=source_kind,
                head_ref=head_ref.strip(),
                patch_file=patch_file.strip(),
            )
        )
        return [
            f"git-selected-source:{source_kind}",
            f"git-selected-head-tip:{snapshot.head_commit}",
            f"git-selected-patch:{snapshot.source_input_digest}",
            f"git-selected-diff:{snapshot.diff_hash}",
        ]
    if source_kind == "local-git-range":
        base_ref = diff_source.get("base_ref", payload.get("base_ref", ""))
        head_ref = diff_source.get("head_ref", payload.get("head_ref", "HEAD"))
        if not isinstance(base_ref, str) or not base_ref.strip():
            raise ValueError(
                f"Review pack local-git-range base_ref is invalid: {review_pack_path}"
            )
        if not isinstance(head_ref, str) or not head_ref.strip():
            raise ValueError(
                f"Review pack local-git-range head_ref is invalid: {review_pack_path}"
            )
        base_ref = base_ref.strip()
        head_ref = head_ref.strip()
        snapshot = build_source_snapshot(
            SourceSnapshotOptions(
                root=root,
                source_kind=source_kind,
                base_ref=base_ref,
                head_ref=head_ref,
            )
        )
        base_tip = (
            _git_bytes(
                root,
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"{base_ref}^{{commit}}",
            )
            .decode("ascii")
            .strip()
        )
        head_tip = (
            _git_bytes(
                root,
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"{head_ref}^{{commit}}",
            )
            .decode("ascii")
            .strip()
        )
        return [
            f"git-selected-source:{source_kind}",
            f"git-selected-base-tip:{base_tip}",
            f"git-selected-head-tip:{head_tip}",
            f"git-selected-diff:{snapshot.diff_hash}",
        ]
    if source_kind not in {"local-staged", "local-unstaged"}:
        return []
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=root, source_kind=source_kind)
    )
    return [
        f"git-selected-source:{source_kind}",
        f"git-selected-diff:{snapshot.diff_hash}",
    ]


def _git_bytes(root: Path, *args: str) -> bytes:
    env = {
        key: value for key, value in os.environ.items() if key not in _GIT_ROUTING_ENV
    }
    result = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "--no-replace-objects",
            "-C",
            str(root),
            *args,
        ],
        check=True,
        capture_output=True,
        env=env,
    )
    return result.stdout


def _safe_identifier(value: str) -> str:
    text = value.strip()
    if not text or text in {".", ".."} or any(char in text for char in "/\\:"):
        raise ValueError(f"Unsafe Loop id: {value!r}")
    return text


def _emit(payload: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    for key, value in payload.items():
        typer.echo(f"{key}: {value}")


__all__ = ["loop_review", "resolve_review_input"]
