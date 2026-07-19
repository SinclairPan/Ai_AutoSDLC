"""Lean Code artifact paths, schemas, persistence, and Markdown rendering."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from ai_sdlc.core.implementation_store import (
    implementation_artifacts,
    repo_relative_path,
)
from ai_sdlc.core.lean_code_models import (
    LeanEvaluationInput,
    LeanEvaluationReport,
    LeanException,
    LeanFinding,
    RegressionEvidence,
)
from ai_sdlc.core.lean_code_policy import stable_artifact_digest
from ai_sdlc.core.loop_artifacts import LoopArtifactStore
from ai_sdlc.core.loop_models import LoopArtifactModel
from ai_sdlc.core.pr_review_models import FindingSeverity
from ai_sdlc.core.source_snapshot import SourceSnapshot


class LeanCurrentPointer(LoopArtifactModel):
    """Digest-bound pointer to the current machine report."""

    artifact_kind: str = "lean-code-current-pointer"
    loop_id: str
    evaluation_round: int = Field(ge=1, le=2)
    report_path: str
    report_digest: str
    snapshot_path: str
    snapshot_digest: str
    policy_path: str
    policy_digest: str
    findings_path: str
    findings_digest: str
    input_path: str
    input_digest: str
    diff_hash: str


class LeanFindingsArtifact(LoopArtifactModel):
    """Persisted findings for one evaluation round."""

    artifact_kind: str = "lean-code-findings"
    loop_id: str
    evaluation_round: int = Field(ge=1, le=2)
    findings: list[LeanFinding] = Field(default_factory=list)


class LeanFixPlan(LoopArtifactModel):
    """Finding-scoped plan; it never applies code changes itself."""

    artifact_kind: str = "lean-code-fix-plan"
    loop_id: str
    evaluation_round: int = Field(ge=1, le=2)
    finding_signatures: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    required_verification: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class LeanArtifactPaths:
    """Stable artifact paths for one evaluation round."""

    lean_dir: Path
    policy_path: Path
    current_path: Path
    round_dir: Path
    snapshot_path: Path
    input_path: Path
    findings_path: Path
    report_path: Path
    report_md_path: Path
    fix_plan_path: Path
    fix_plan_md_path: Path


def lean_artifact_paths(
    root: Path, loop_id: str, round_number: int
) -> LeanArtifactPaths:
    """Return project-local paths without creating them."""

    lean_dir = implementation_artifacts(root, loop_id).loop_dir / "lean"
    round_dir = lean_dir / f"round-{round_number:03d}"
    return LeanArtifactPaths(
        lean_dir=lean_dir,
        policy_path=lean_dir / "policy-snapshot.json",
        current_path=lean_dir / "current.json",
        round_dir=round_dir,
        snapshot_path=round_dir / "source-snapshot.json",
        input_path=round_dir / "evaluation-input.json",
        findings_path=round_dir / "findings.json",
        report_path=round_dir / "report.json",
        report_md_path=round_dir / "report.md",
        fix_plan_path=round_dir / "fix-plan.json",
        fix_plan_md_path=round_dir / "fix-plan.md",
    )


def write_lean_artifacts(
    root: Path,
    paths: LeanArtifactPaths,
    snapshot: SourceSnapshot,
    evaluation_input: LeanEvaluationInput,
    report: LeanEvaluationReport,
    policy,
) -> None:
    """Persist machine truth, projections, fix plan, and current pointer."""

    store = LoopArtifactStore(root)
    plan = build_fix_plan(report)
    store.write_json_artifact(paths.policy_path, policy)
    store.write_json_artifact(paths.snapshot_path, snapshot)
    store.write_json_artifact(paths.input_path, evaluation_input)
    findings = LeanFindingsArtifact(
        loop_id=report.loop_id,
        evaluation_round=report.evaluation_round,
        findings=report.findings,
    )
    store.write_json_artifact(paths.findings_path, findings)
    store.write_json_artifact(paths.report_path, report)
    store.write_markdown_artifact(paths.report_md_path, render_report(report))
    store.write_json_artifact(paths.fix_plan_path, plan)
    store.write_markdown_artifact(paths.fix_plan_md_path, render_fix_plan(plan))
    store.write_json_artifact(
        paths.current_path,
        LeanCurrentPointer(
            loop_id=report.loop_id,
            evaluation_round=report.evaluation_round,
            report_path=repo_relative_path(root, paths.report_path),
            report_digest=stable_artifact_digest(report),
            snapshot_path=repo_relative_path(root, paths.snapshot_path),
            snapshot_digest=stable_artifact_digest(snapshot),
            policy_path=repo_relative_path(root, paths.policy_path),
            policy_digest=stable_artifact_digest(policy),
            findings_path=repo_relative_path(root, paths.findings_path),
            findings_digest=stable_artifact_digest(findings),
            input_path=repo_relative_path(root, paths.input_path),
            input_digest=stable_artifact_digest(evaluation_input),
            diff_hash=report.diff_hash,
        ),
    )


def build_fix_plan(report: LeanEvaluationReport) -> LeanFixPlan:
    """Exclude advisory-only suggestions from the targeted fix plan."""

    actionable = [
        item
        for item in report.findings
        if item.severity in {FindingSeverity.BLOCKER, FindingSeverity.REQUIRED}
    ]
    return LeanFixPlan(
        loop_id=report.loop_id,
        evaluation_round=report.evaluation_round,
        finding_signatures=[item.stable_signature for item in actionable],
        allowed_paths=sorted({item.path for item in actionable if item.path}),
        steps=[item.suggested_fix for item in actionable],
        required_verification=sorted(
            {value for item in actionable for value in item.required_verification}
        ),
    )


def read_current_report(root: Path, loop_id: str) -> LeanEvaluationReport | None:
    """Read and digest-check the current Lean report when it exists."""

    pointer_path = (
        implementation_artifacts(root, loop_id).loop_dir / "lean" / "current.json"
    )
    if not pointer_path.is_file():
        return None
    pointer = read_current_pointer(root, loop_id)
    report_path = safe_project_path(root, pointer.report_path)
    report = LeanEvaluationReport.model_validate_json(report_path.read_text("utf-8"))
    if stable_artifact_digest(report) != pointer.report_digest:
        raise ValueError("current Lean report digest mismatch")
    return report


def read_current_pointer(root: Path, loop_id: str) -> LeanCurrentPointer:
    path = implementation_artifacts(root, loop_id).loop_dir / "lean" / "current.json"
    return LeanCurrentPointer.model_validate_json(path.read_text("utf-8"))


def read_regression_evidence(
    root: Path, paths: tuple[str, ...]
) -> tuple[RegressionEvidence, ...]:
    return tuple(
        RegressionEvidence.model_validate_json(
            safe_project_path(root, path).read_text("utf-8")
        )
        for path in paths
    )


def read_lean_exceptions(
    root: Path, paths: tuple[str, ...]
) -> tuple[LeanException, ...]:
    return tuple(
        LeanException.model_validate_json(
            safe_project_path(root, path).read_text("utf-8")
        )
        for path in paths
    )


def safe_project_path(root: Path, path: str) -> Path:
    candidate = (root / path).resolve()
    candidate.relative_to(root.resolve())
    return candidate


def render_report(report: LeanEvaluationReport) -> str:
    """Render a concise human projection of the JSON machine truth."""

    lines = [
        "# Lean Code Evaluation",
        "",
        f"- Loop: `{report.loop_id}`",
        f"- Round: {report.evaluation_round}",
        f"- Status: `{report.status}`",
        f"- Diff: `{report.diff_hash}`",
        "",
        "## Findings",
    ]
    lines.extend(
        f"- [{item.severity}] {item.rule_id}: {item.path or '-'} {item.claim}"
        for item in report.findings
    )
    return "\n".join(lines) + "\n"


def render_fix_plan(plan: LeanFixPlan) -> str:
    """Render a non-executable plan for the independent Implementation Agent."""

    lines = [
        "# Lean Code Fix Plan",
        "",
        "Implementation Agent applies these scoped steps:",
    ]
    lines.extend(f"- {step}" for step in plan.steps)
    if not plan.steps:
        lines.append("- No BLOCKER or REQUIRED finding requires a code change.")
    return "\n".join(lines) + "\n"


__all__ = [
    "LeanArtifactPaths",
    "LeanCurrentPointer",
    "LeanFixPlan",
    "lean_artifact_paths",
    "read_current_pointer",
    "read_current_report",
    "read_lean_exceptions",
    "read_regression_evidence",
    "safe_project_path",
    "write_lean_artifacts",
]
