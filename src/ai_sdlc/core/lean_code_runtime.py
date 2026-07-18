"""Bounded Lean Code artifact orchestration inside an Implementation Loop."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from ai_sdlc.core.implementation_models import ImplementationInput
from ai_sdlc.core.implementation_store import (
    implementation_artifacts,
    read_input,
    read_loop_run,
    repo_relative_path,
    resolve_implementation_loop_run_path,
)
from ai_sdlc.core.lean_code_artifacts import (
    LeanArtifactPaths,
    lean_artifact_paths,
    read_current_report,
    write_lean_artifacts,
)
from ai_sdlc.core.lean_code_close import validate_lean_close, validate_lean_integrity
from ai_sdlc.core.lean_code_decisions import LeanNoGoOptions, persist_lean_no_go
from ai_sdlc.core.lean_code_inputs import (
    LeanEvaluationSource,
    prepare_lean_evaluation,
)
from ai_sdlc.core.lean_code_models import (
    LeanEvaluationReport,
)
from ai_sdlc.core.lean_code_policy import load_lean_policy, stable_artifact_digest
from ai_sdlc.core.loop_artifacts import LoopArtifactStore
from ai_sdlc.core.loop_models import LoopRound, LoopStatus, utc_now_iso
from ai_sdlc.core.loop_policy import LoopPolicyError
from ai_sdlc.core.pr_review_models import FindingSeverity


class LeanCommandStatus(StrEnum):
    """CLI-facing outcomes for deterministic Lean checks."""

    READY = "ready"
    NEEDS_FIX = "needs_fix"
    NEEDS_USER = "needs_user"
    BLOCKED = "blocked"


class LeanCheckResult(BaseModel):
    """Machine-readable lean-check result."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    status: LeanCommandStatus
    result: str
    next_action: str = ""
    blocker: str = ""
    loop_id: str = ""
    loop_status: LoopStatus | str = ""
    evaluation_round: int = 0
    diff_hash: str = ""
    blocker_count: int = 0
    required_count: int = 0
    advisory_count: int = 0
    report_path: str = ""
    stop_reason: str = ""
    risk_accepted: bool = False
    requires_model: bool = False
    writes_artifacts: bool = True
    writes_code: bool = False


@dataclass(frozen=True)
class LeanCheckOptions:
    """Inputs accepted by the lean-check CLI action."""

    root: Path
    loop_id: str = ""
    source_kind: str = "local-unstaged"
    base_ref: str = ""
    head_ref: str = "HEAD"
    patch_file: str = ""
    regression_evidence_paths: tuple[str, ...] = ()
    exception_paths: tuple[str, ...] = ()


def run_lean_check(options: LeanCheckOptions) -> LeanCheckResult:
    """Build a fresh snapshot, evaluate it, and append one bounded LoopRound."""

    root = options.root.resolve()
    loaded = _load_implementation(root, options.loop_id)
    if isinstance(loaded, LeanCheckResult):
        return loaded
    loop_run, impl_input = loaded
    if "lean-code" not in impl_input.quality_profiles:
        return _blocked(
            "Lean Code profile is not enabled for this implementation loop.",
            loop_run.loop_id,
        )
    try:
        policy = load_lean_policy(root)
    except (LoopPolicyError, ValidationError, ValueError) as exc:
        return _blocked(f"Lean policy is malformed: {exc}", loop_run.loop_id)
    evaluation_round = _lean_round_count(loop_run) + 1
    if evaluation_round > policy.max_rounds:
        return _blocked(
            f"Lean evaluation maximum of {policy.max_rounds} rounds has been reached.",
            loop_run.loop_id,
        )
    try:
        snapshot, report, evaluation_input = prepare_lean_evaluation(
            root,
            loop_run,
            impl_input,
            policy,
            evaluation_round,
            LeanEvaluationSource(
                source_kind=options.source_kind,
                base_ref=options.base_ref,
                head_ref=options.head_ref,
                patch_file=options.patch_file,
                regression_evidence_paths=options.regression_evidence_paths,
                exception_paths=options.exception_paths,
            ),
        )
    except (OSError, ValueError, ValidationError) as exc:
        return _blocked(f"Lean evaluation input is malformed: {exc}", loop_run.loop_id)
    artifacts = lean_artifact_paths(root, loop_run.loop_id, evaluation_round)
    write_lean_artifacts(root, artifacts, snapshot, evaluation_input, report, policy)
    _update_loop_run(root, loop_run, artifacts, report)
    return _result_from_report(root, artifacts, report)


def record_lean_no_go(options: LeanNoGoOptions) -> LeanCheckResult:
    """Persist a source-bound No-Go and stop without changing application code."""

    root = options.root.resolve()
    loaded = _load_implementation(root, options.loop_id)
    if isinstance(loaded, LeanCheckResult):
        return loaded
    loop_run, impl_input = loaded
    try:
        report = read_current_report(root, loop_run.loop_id)
        if report is None:
            return _blocked(
                "Lean No-Go requires a current evaluation.", loop_run.loop_id
            )
        integrity_issue = validate_lean_integrity(root, loop_run.loop_id)
        if integrity_issue:
            return _blocked(integrity_issue, loop_run.loop_id)
        decision, _decision_path, blocker = persist_lean_no_go(
            root, loop_run, impl_input, report, options
        )
        if blocker:
            return _blocked(blocker, loop_run.loop_id)
    except (OSError, ValueError, ValidationError) as exc:
        return _blocked(f"Lean No-Go input is malformed: {exc}", loop_run.loop_id)
    if decision is None:
        return _blocked("Lean No-Go decision was not persisted.", loop_run.loop_id)
    result = _result_from_report(
        root,
        lean_artifact_paths(root, loop_run.loop_id, report.evaluation_round),
        report,
    )
    result.status = LeanCommandStatus.NEEDS_USER
    result.loop_status = LoopStatus.NEEDS_USER
    result.stop_reason = f"no_go:{decision.decision_id}"
    result.next_action = (
        "Review the No-Go evidence and decide whether to rescope or stop."
    )
    return result


def _load_implementation(
    root: Path,
    loop_id: str,
    *,
    allow_closed: bool = False,
) -> tuple[object, ImplementationInput] | LeanCheckResult:
    path, blocker = resolve_implementation_loop_run_path(root, loop_id)
    if blocker:
        return _blocked(blocker)
    try:
        loop_run = read_loop_run(path)
        impl_input = read_input(
            implementation_artifacts(root, loop_run.loop_id).input_path
        )
    except (OSError, ValueError) as exc:
        return _blocked(f"Implementation loop artifact is malformed: {exc}")
    if loop_run.status == LoopStatus.CLOSED and not allow_closed:
        return _blocked(
            "Closed implementation loops cannot run Lean evaluation.", loop_run.loop_id
        )
    if (
        loop_run.input_digest
        and stable_artifact_digest(impl_input) != loop_run.input_digest
    ):
        return _blocked(
            "Implementation input digest mismatch; the frozen input was modified.",
            loop_run.loop_id,
        )
    return loop_run, impl_input


def _update_loop_run(root, loop_run, artifacts: LeanArtifactPaths, report) -> None:
    sequence = max((item.round_number for item in loop_run.rounds), default=0) + 1
    loop_run.rounds.append(
        LoopRound(
            round_number=sequence,
            round_kind="lean-evaluation",
            input_artifacts=[repo_relative_path(root, artifacts.input_path)],
            output_artifacts=[repo_relative_path(root, artifacts.report_path)],
            command=["ai-sdlc", "loop", "implementation", "lean-check"],
            status=report.status,
            result=str(report.status),
            next_action=_next_action(report),
        )
    )
    loop_run.current_round = sequence
    loop_run.status = report.status
    loop_run.updated_at = utc_now_iso()
    loop_run.next_action = _next_action(report)
    LoopArtifactStore(root).write_json_artifact(
        implementation_artifacts(root, loop_run.loop_id).loop_run_path,
        loop_run,
    )


def _result_from_report(root, artifacts, report) -> LeanCheckResult:
    counts = {
        severity: sum(item.severity == severity for item in report.findings)
        for severity in FindingSeverity
    }
    status = {
        LoopStatus.PASSED: LeanCommandStatus.READY,
        LoopStatus.NEEDS_FIX: LeanCommandStatus.NEEDS_FIX,
        LoopStatus.NEEDS_USER: LeanCommandStatus.NEEDS_USER,
    }.get(report.status, LeanCommandStatus.BLOCKED)
    return LeanCheckResult(
        status=status,
        result="Lean Code evaluation completed.",
        next_action=_next_action(report),
        loop_id=report.loop_id,
        loop_status=report.status,
        evaluation_round=report.evaluation_round,
        diff_hash=report.diff_hash,
        blocker_count=counts[FindingSeverity.BLOCKER],
        required_count=counts[FindingSeverity.REQUIRED],
        advisory_count=counts[FindingSeverity.ADVISORY],
        report_path=repo_relative_path(root, artifacts.report_path),
        stop_reason=report.stop_reason,
        risk_accepted=report.risk_accepted,
    )


def _next_action(report: LeanEvaluationReport) -> str:
    if report.status == LoopStatus.PASSED:
        return "Run ai-sdlc loop implementation close --yes."
    if report.status == LoopStatus.NEEDS_USER:
        return (
            "Review the stop reason and decide whether to accept risk or record No-Go."
        )
    return "Apply the finding-scoped fix plan, record targeted verification, then run lean-check again."


def _lean_round_count(loop_run) -> int:
    return sum(item.round_kind == "lean-evaluation" for item in loop_run.rounds)


def _blocked(message: str, loop_id: str = "") -> LeanCheckResult:
    return LeanCheckResult(
        status=LeanCommandStatus.BLOCKED,
        result="Lean Code evaluation blocked.",
        blocker=message,
        next_action="Fix the reported artifact or input, then run lean-check again.",
        loop_id=loop_id,
        writes_artifacts=False,
    )


__all__ = [
    "LeanCheckOptions",
    "LeanCheckResult",
    "LeanNoGoOptions",
    "record_lean_no_go",
    "run_lean_check",
    "validate_lean_close",
]
