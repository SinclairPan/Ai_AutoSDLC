"""Read-only CLI mapping from existing Loop results to review inputs."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import cast

import typer

from ai_sdlc.core.review_kernel import LoopReviewType, ReviewInput, build_review_input
from ai_sdlc.utils.helpers import find_project_root

_STAGE_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "requirement": ("requirement-brief.md", "acceptance-checklist.md"),
    "design-contract": (
        "design-contract-report.json",
        "design-contract-report.md",
    ),
    "implementation": (
        "implementation-report.json",
        "implementation-report.md",
        "verification-evidence.json",
    ),
    "frontend-evidence": (
        "frontend-evidence-snapshot.json",
        "frontend-evidence-report.json",
        "frontend-evidence-report.md",
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
        risk_signals = [*_content_risk_signals(artifacts), *_git_risk_signals(root)]
        round_number = _read_round_number(loop_dir / "review-run.json")
    elif loop_type in _STAGE_ARTIFACTS:
        loop_dir = root / ".ai-sdlc" / "loops" / loop_type / safe_loop_id
        artifacts = [loop_dir / name for name in _STAGE_ARTIFACTS[loop_type]]
        risk_signals = _content_risk_signals(artifacts)
        round_number = _read_round_number(loop_dir / "loop-run.json")
    else:
        raise ValueError(f"Unsupported review Loop type: {loop_type}")

    return build_review_input(
        root,
        loop_id=safe_loop_id,
        loop_type=cast(LoopReviewType, loop_type),
        round_number=round_number,
        artifact_paths=artifacts,
        upstream_context_paths=[],
        risk_signals=risk_signals,
    )


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
        if any(term in content for term in terms)
    ]
    return risks or ["general-correctness"]


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

