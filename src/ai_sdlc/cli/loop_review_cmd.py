"""Read-only CLI mapping from existing Loop results to review inputs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import cast

import typer

from ai_sdlc.core.review_kernel import LoopReviewType, ReviewInput, build_review_input
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
    ),
    "frontend-evidence": (
        "frontend-evidence-input.json",
        "frontend-evidence-snapshot.json",
        "frontend-evidence-report.json",
        "frontend-evidence-report.md",
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
_LOCAL_REQUIRED = ("review-pack.json", "findings.json")
_LOCAL_OPTIONAL = ("resolution.yaml", "verification-evidence.json")
_RISK_TERMS: dict[str, tuple[str, ...]] = {
    "public-api": ("public api", "public-api", "schema", "contract"),
    "security": ("security", "authorization", "permission", "secret"),
    "data-integrity": ("database", "migration", "transaction", "data loss"),
    "concurrency": ("concurrency", "parallel", "race", "lock"),
    "frontend": ("frontend", "browser", "accessibility", "ui", "ux"),
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


def loop_review(
    loop_type: str = typer.Option(..., "--type", help="Loop result type."),
    loop_id: str = typer.Option(..., "--loop-id", help="Existing Loop id."),
    expect_digest: str = typer.Option(
        "",
        "--expect-digest",
        help="Fail if the current substantive input no longer matches this digest.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Build or recheck one read-only dynamic-expert review input."""

    try:
        root = find_project_root()
        if root is None:
            raise ValueError("Project is not initialized; .ai-sdlc is missing.")
        review_input = resolve_review_input(
            root,
            loop_type=loop_type,
            loop_id=loop_id,
        )
        expected = expect_digest.strip().lower()
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

    _emit(review_input.model_dump(mode="json"), json_output=json_output)


def resolve_review_input(
    root: Path,
    *,
    loop_type: str,
    loop_id: str,
) -> ReviewInput:
    """Resolve existing substantive artifacts without creating a parallel Loop."""

    safe_loop_id = _safe_identifier(loop_id)
    if loop_type == "local-pr-review":
        loop_dir = _find_local_review_dir(root, safe_loop_id)
        artifacts = [loop_dir / name for name in _LOCAL_REQUIRED]
        artifacts.extend(
            loop_dir / name
            for name in _LOCAL_OPTIONAL
            if (loop_dir / name).is_file()
        )
        artifacts.append(_local_review_diff(root, loop_dir / "review-pack.json"))
        risk_signals = [*_content_risk_signals(artifacts), *_git_risk_signals(root)]
        round_number = _read_round_number(loop_dir / "review-run.json")
    elif loop_type in _STAGE_ARTIFACTS:
        loop_dir = root / ".ai-sdlc" / "loops" / loop_type / safe_loop_id
        artifacts = _unique_paths(
            [
                *(loop_dir / name for name in _STAGE_ARTIFACTS[loop_type]),
                *_stage_source_material(root, loop_type, loop_dir),
            ]
        )
        upstream_context = _stage_upstream_context(root, loop_type, loop_dir)
        risk_signals = _content_risk_signals([*artifacts, *upstream_context])
        round_number = _read_round_number(loop_dir / "loop-run.json")
    else:
        raise ValueError(f"Unsupported review Loop type: {loop_type}")

    return build_review_input(
        root,
        loop_id=safe_loop_id,
        loop_type=cast(LoopReviewType, loop_type),
        round_number=round_number,
        artifact_paths=artifacts,
        upstream_context_paths=upstream_context if loop_type != "local-pr-review" else [],
        risk_signals=risk_signals,
    )


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
        payload = json.loads(input_path.read_text(encoding="utf-8"))
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
        raise ValueError(f"Loop predecessor cycle detected: {predecessor_type}/{safe_predecessor_id}")
    predecessor_dir = (
        root
        / ".ai-sdlc"
        / "loops"
        / predecessor_type
        / safe_predecessor_id
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
        payload = _read_json_object(loop_dir / "design-contract-input.json")
        return [
            _repo_path(root, value, field_name)
            for field_name in ("spec_path", "plan_path", "tasks_path")
            if isinstance((value := payload.get(field_name)), str) and value.strip()
        ]
    if loop_type == "implementation":
        payload = _read_json_object(loop_dir / "implementation-input.json")
        declared_scope = payload.get("declared_scope", [])
        if not isinstance(declared_scope, list) or not all(
            isinstance(item, str) for item in declared_scope
        ):
            raise ValueError(
                f"Loop declared_scope is invalid: {loop_dir / 'implementation-input.json'}"
            )
        return _expand_repo_patterns(root, declared_scope)
    if loop_type == "frontend-evidence":
        input_payload = _read_json_object(loop_dir / "frontend-evidence-input.json")
        snapshot_payload = _read_json_object(
            loop_dir / "frontend-evidence-snapshot.json"
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
            if not isinstance(record, dict) or record.get("capture_status") != "captured":
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


def _local_review_diff(root: Path, review_pack_path: Path) -> Path:
    payload = _read_json_object(review_pack_path)
    diff_path_text = payload.get("diff_path", "")
    diff_digest = payload.get("diff_digest", "")
    if not isinstance(diff_path_text, str) or not diff_path_text.strip():
        raise ValueError(f"Review pack diff_path is missing: {review_pack_path}")
    if not isinstance(diff_digest, str) or not diff_digest.startswith("sha256:"):
        raise ValueError(f"Review pack diff_digest is invalid: {review_pack_path}")
    diff_path = _repo_path(root, diff_path_text, "diff_path")
    try:
        actual = hashlib.sha256(diff_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError(f"Review diff is unreadable: {diff_path}") from exc
    if diff_digest != f"sha256:{actual}":
        raise ValueError(f"Review diff digest does not match review-pack.json: {diff_path}")
    return diff_path


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Loop input is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Loop input root must be an object: {path}")
    return payload


def _repo_path(root: Path, value: str, field_name: str) -> Path:
    candidate = Path(value)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve(
        strict=False
    )
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Loop {field_name} escapes the project: {value}") from exc
    return resolved


def _expand_repo_patterns(root: Path, patterns: list[str]) -> list[Path]:
    expanded: list[Path] = []
    for pattern in patterns:
        pattern_path = Path(pattern)
        if pattern_path.is_absolute() or ".." in pattern_path.parts:
            raise ValueError(f"Loop declared_scope escapes the project: {pattern}")
        matches = sorted(root.glob(pattern))
        if not matches:
            raise ValueError(f"Loop declared_scope matches no files: {pattern}")
        for match in matches:
            resolved = _repo_path(root, str(match), "declared_scope")
            if resolved.is_dir():
                expanded.extend(path for path in sorted(resolved.rglob("*")) if path.is_file())
            elif resolved.is_file():
                expanded.append(resolved)
    return _unique_paths(expanded)


def _unique_paths(paths: list[Path]) -> list[Path]:
    return list(dict.fromkeys(paths))


def _find_local_review_dir(root: Path, loop_id: str) -> Path:
    reviews_root = root / ".ai-sdlc" / "reviews" / "pr"
    matches: list[Path] = []
    for run_path in sorted(reviews_root.glob("*/review-run.json")):
        try:
            payload = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Local PR review state is unreadable: {run_path}") from exc
        if isinstance(payload, dict) and payload.get("loop_id") == loop_id:
            matches.append(run_path.parent)
    if len(matches) != 1:
        raise ValueError(
            f"Expected one local PR review for Loop {loop_id}, found {len(matches)}."
        )
    return matches[0]


def _read_round_number(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Loop state is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Loop state root must be an object: {path}")
    value = payload.get("current_round", payload.get("round_number", 1))
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 1
    return value


def _content_risk_signals(paths: list[Path]) -> list[str]:
    chunks: list[str] = []
    for path in paths:
        try:
            chunks.append(path.read_text(encoding="utf-8").lower())
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"Review artifact is not strict UTF-8: {path}") from exc
    content = "\n".join(chunks)
    risks = [
        risk
        for risk, terms in _RISK_TERMS.items()
        if any(_contains_risk_term(content, term) for term in terms)
    ]
    return risks or ["general-correctness"]


def _contains_risk_term(content: str, term: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
    return re.search(pattern, content) is not None


def _git_risk_signals(root: Path) -> list[str]:
    head = _git_bytes(root, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
    index = _git_bytes(root, "ls-files", "--stage", "-z")
    staged = _git_bytes(root, "diff", "--cached", "--binary", "--no-ext-diff", "--")
    return [
        f"git-head:{head}",
        f"git-index:{hashlib.sha256(index).hexdigest()}",
        f"git-staged-diff:{hashlib.sha256(staged).hexdigest()}",
    ]


def _git_bytes(root: Path, *args: str) -> bytes:
    env = {key: value for key, value in os.environ.items() if key not in _GIT_ROUTING_ENV}
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
