"""Deterministic Lean Code policy evaluation over a frozen source snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_sdlc.core.lean_code_evidence import verification_digest
from ai_sdlc.core.lean_code_findings import (
    apply_structured_exceptions,
    bugfix_findings,
    generated_scope_findings,
    scope_findings,
    targeted_verification_findings,
    unknown_findings,
    unsupported_findings,
)
from ai_sdlc.core.lean_code_findings import (
    make_finding as _finding,
)
from ai_sdlc.core.lean_code_metrics import collect_lean_metrics
from ai_sdlc.core.lean_code_models import (
    FileClassification,
    FileMetric,
    FunctionMetric,
    LeanEvaluationReport,
    LeanException,
    LeanFinding,
    LeanPolicy,
    RegressionEvidence,
    evaluation_profile_for,
)
from ai_sdlc.core.lean_code_policy import stable_artifact_digest
from ai_sdlc.core.loop_models import LoopStatus
from ai_sdlc.core.pr_review_models import FindingResolutionStatus, FindingSeverity
from ai_sdlc.core.source_snapshot import SourceSnapshot
from ai_sdlc.models.work import WorkType


@dataclass(frozen=True)
class LeanEvaluationOptions:
    """Inputs for one bounded deterministic evaluation."""

    root: Path
    loop_id: str
    work_item_id: str
    work_type: WorkType
    source_snapshot: SourceSnapshot
    policy: LeanPolicy
    declared_scope: tuple[str, ...]
    task_refs: tuple[str, ...] = ()
    acceptance_refs: tuple[str, ...] = ()
    verification_refs: tuple[str, ...] = ()
    regression_evidence: tuple[RegressionEvidence, ...] = ()
    exceptions: tuple[LeanException, ...] = ()
    evaluation_round: int = 1
    previous_findings: tuple[LeanFinding, ...] = ()
    previous_report_digest: str = ""
    previous_verification_digest: str = ""
    previous_had_actionable_findings: bool = False


def evaluate_lean_code(options: LeanEvaluationOptions) -> LeanEvaluationReport:
    """Evaluate scope, classification, maintainability budgets, and bug evidence."""

    metrics = collect_lean_metrics(
        options.root,
        options.source_snapshot,
        options.declared_scope,
    )
    policy_digest = stable_artifact_digest(options.policy)
    findings = _collect_findings(options, metrics)
    findings, exception_ids = apply_structured_exceptions(
        options.root,
        options.exceptions,
        findings,
        policy_digest,
        options.source_snapshot,
        options.evaluation_round,
        options.previous_report_digest,
    )
    status = _evaluation_status(
        options.work_type,
        metrics.unknown_files,
        metrics.unsupported_semantic_files,
        findings,
        options.policy,
    )
    return _build_report(
        options, metrics, findings, exception_ids, status, policy_digest
    )


def _collect_findings(options: LeanEvaluationOptions, metrics) -> list[LeanFinding]:
    findings = [
        *scope_findings(metrics.scope_drift, options.evaluation_round),
        *unknown_findings(metrics.unknown_files, options.evaluation_round),
        *unsupported_findings(
            metrics.unsupported_semantic_files, options.evaluation_round
        ),
        *generated_scope_findings(
            metrics.classification_counts.get(str(FileClassification.GENERATED), 0),
            len(options.task_refs),
            options.policy,
            options.evaluation_round,
        ),
        *targeted_verification_findings(
            options.evaluation_round,
            options.previous_had_actionable_findings,
            bool(options.verification_refs),
            verification_digest(options.verification_refs),
            options.previous_verification_digest,
        ),
        *_budget_findings(metrics.files, options.policy, options.evaluation_round),
        *bugfix_findings(
            options.root,
            options.loop_id,
            options.work_type,
            options.regression_evidence,
            options.source_snapshot.diff_hash,
            options.evaluation_round,
            tuple(
                item.path
                for item in metrics.files
                if item.classification == FileClassification.HANDWRITTEN_PRODUCT
            ),
        ),
    ]
    return findings


def _build_report(options, metrics, findings, exception_ids, status, policy_digest):
    stop_reason = ""
    actionable = _unresolved_actionable_signatures(findings)
    if (
        options.evaluation_round >= options.policy.max_rounds
        and status in {LoopStatus.NEEDS_FIX, LoopStatus.BLOCKED}
        and actionable
    ):
        status = LoopStatus.NEEDS_USER
        stop_reason = "max_rounds_reached:" + ",".join(actionable)
    return LeanEvaluationReport(
        loop_id=options.loop_id,
        work_item_id=options.work_item_id,
        work_type=options.work_type,
        evaluation_profile=evaluation_profile_for(options.work_type),
        evaluation_round=options.evaluation_round,
        source_snapshot_digest=stable_artifact_digest(options.source_snapshot),
        diff_hash=options.source_snapshot.diff_hash,
        policy_digest=policy_digest,
        enforcement_mode=options.policy.enforcement_mode,
        verification_digest=verification_digest(options.verification_refs),
        status=status,
        metrics=metrics,
        findings=findings,
        exception_ids=exception_ids,
        risk_accepted=bool(exception_ids),
        previous_signatures=[
            item.stable_signature for item in options.previous_findings
        ],
        stop_reason=stop_reason,
    )


def _budget_findings(
    files: list[FileMetric],
    policy: LeanPolicy,
    round_number: int,
) -> list[LeanFinding]:
    findings: list[LeanFinding] = []
    for file in files:
        if file.classification != FileClassification.HANDWRITTEN_PRODUCT:
            continue
        if file.head_lines > policy.file_line_budget:
            findings.append(_file_budget_finding(file, policy, round_number))
        findings.extend(_function_findings(file, policy, round_number))
    return findings


def _file_budget_finding(
    file: FileMetric,
    policy: LeanPolicy,
    round_number: int,
) -> LeanFinding:
    historical = not file.is_new and not _significantly_changed(file, policy)
    claim = (
        "Historical file exceeds the initial line budget."
        if historical
        else "Changed file exceeds the initial line budget."
    )
    return _finding(
        rule_id="lean.file-budget",
        severity=FindingSeverity.ADVISORY,
        path=file.path,
        claim=claim,
        measured=file.head_lines,
        budget=policy.file_line_budget,
        risk="Size is a maintainability signal, not a behavioral failure.",
        fix="Simplify only when behavior, cohesion, and evidence remain clearer.",
        verification="Run focused behavior tests after any optional refactor.",
        round_number=round_number,
    )


def _function_findings(
    file: FileMetric,
    policy: LeanPolicy,
    round_number: int,
) -> list[LeanFinding]:
    findings: list[LeanFinding] = []
    for function in file.functions:
        if function.logical_lines > policy.function_line_budget:
            findings.append(
                _function_budget_finding(file, function, policy, round_number)
            )
            if _function_risk(function, file, policy):
                findings.append(
                    _function_risk_finding(file, function, policy, round_number)
                )
        if (
            function.public
            and function.is_new
            and function.caller_count < policy.public_caller_minimum
        ):
            findings.append(
                _public_caller_finding(file, function, policy, round_number)
            )
    return findings


def _function_budget_finding(
    file: FileMetric,
    function: FunctionMetric,
    policy: LeanPolicy,
    round_number: int,
) -> LeanFinding:
    return _finding(
        rule_id="lean.function-budget",
        severity=FindingSeverity.ADVISORY,
        path=file.path,
        symbol=function.symbol,
        claim="Function exceeds the initial logical line budget without an automatic blocker.",
        measured=function.logical_lines,
        budget=policy.function_line_budget,
        risk="The function may become harder to review, but size alone is insufficient.",
        fix="Keep the direct implementation unless a cohesive simplification is clearer.",
        verification="Preserve focused behavior and error-path tests.",
        round_number=round_number,
    )


def _function_risk_finding(
    file: FileMetric,
    function: FunctionMetric,
    policy: LeanPolicy,
    round_number: int,
) -> LeanFinding:
    return _finding(
        rule_id="lean.function-risk",
        severity=FindingSeverity.REQUIRED,
        path=file.path,
        symbol=function.symbol,
        claim="Oversized function also increases deterministic complexity, nesting, or coupling risk.",
        measured=f"lines={function.logical_lines},complexity={function.complexity},nesting={function.max_nesting},fan_out={file.import_fan_out}",
        budget=f"lines={policy.function_line_budget},complexity={policy.complexity_budget},nesting={policy.nesting_budget},fan_out={policy.fan_out_budget}",
        risk="Multiple risk signals indicate mixed responsibility or costly review surface.",
        fix="Make a behavior-preserving, finding-scoped simplification.",
        verification="Run focused tests for every retained branch and error path.",
        round_number=round_number,
    )


def _public_caller_finding(
    file: FileMetric,
    function: FunctionMetric,
    policy: LeanPolicy,
    round_number: int,
) -> LeanFinding:
    return _finding(
        rule_id="lean.public-callers",
        severity=FindingSeverity.REQUIRED,
        path=file.path,
        symbol=function.symbol,
        claim="New public abstraction has fewer than three distinct product caller symbols.",
        measured=function.caller_count,
        budget=policy.public_caller_minimum,
        risk="An unused general abstraction adds API and failure-mode surface without current value.",
        fix="Use a private local helper or direct implementation until callers are real.",
        verification="Re-scan distinct handwritten product caller symbols.",
        round_number=round_number,
    )


def _function_risk(
    function: FunctionMetric, file: FileMetric, policy: LeanPolicy
) -> bool:
    complexity = (
        function.complexity >= policy.complexity_budget
        and function.complexity - function.base_complexity >= policy.complexity_delta
    )
    nesting = (
        function.max_nesting >= policy.nesting_budget
        and function.max_nesting > function.base_max_nesting
    )
    fan_out = (
        file.import_fan_out >= policy.fan_out_budget
        and file.import_fan_out - file.base_import_fan_out >= policy.fan_out_delta
    )
    return complexity or nesting or fan_out or function.duplicate_count > 1


def _significantly_changed(file: FileMetric, policy: LeanPolicy) -> bool:
    return (
        file.added_lines + file.deleted_lines >= policy.significant_changed_lines
        or file.changed_ratio >= policy.significant_changed_ratio
    )


def _evaluation_status(
    work_type: WorkType,
    unknown_files: list[str],
    unsupported_files: list[str],
    findings: list[LeanFinding],
    policy: LeanPolicy,
) -> LoopStatus:
    if work_type == WorkType.UNCERTAIN or unknown_files or unsupported_files:
        return LoopStatus.NEEDS_USER
    unresolved = [
        item
        for item in findings
        if item.resolution
        not in {
            FindingResolutionStatus.FIXED,
            FindingResolutionStatus.WAIVED,
            FindingResolutionStatus.NOT_APPLICABLE,
        }
    ]
    if any(item.severity == FindingSeverity.BLOCKER for item in unresolved):
        return LoopStatus.NEEDS_FIX
    if any(item.severity == FindingSeverity.REQUIRED for item in unresolved):
        mode = str(policy.enforcement_mode)
        if mode == "blocking":
            return LoopStatus.BLOCKED
        if mode == "warning":
            return LoopStatus.NEEDS_FIX
    return LoopStatus.PASSED


def _unresolved_actionable_signatures(current: list[LeanFinding]) -> list[str]:
    return sorted(
        item.stable_signature
        for item in current
        if item.severity in {FindingSeverity.BLOCKER, FindingSeverity.REQUIRED}
        and item.resolution
        not in {
            FindingResolutionStatus.FIXED,
            FindingResolutionStatus.WAIVED,
            FindingResolutionStatus.NOT_APPLICABLE,
        }
    )


__all__ = ["LeanEvaluationOptions", "evaluate_lean_code"]
