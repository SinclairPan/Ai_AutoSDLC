"""Deterministic integrity findings and structured-exception handling."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from ai_sdlc.core.lean_code_evidence import regression_evidence_issue
from ai_sdlc.core.lean_code_exception_review import (
    _evidence_issue,
    _expiry_issue,
    _reviewer_decision_issue,
)
from ai_sdlc.core.lean_code_models import (
    FileClassification,
    LeanException,
    LeanFinding,
    LeanPolicy,
    RegressionEvidence,
    stable_finding_signature,
)
from ai_sdlc.core.lean_code_policy import STRUCTURED_EXCEPTION_RULES
from ai_sdlc.core.pr_review_models import FindingResolutionStatus, FindingSeverity
from ai_sdlc.core.source_snapshot import SourceSnapshot
from ai_sdlc.models.work import WorkType


def scope_findings(
    paths: list[str],
    round_number: int,
    task_scope_matches: dict[str, list[str]] | None = None,
) -> list[LeanFinding]:
    """Treat frozen-scope drift as an integrity blocker."""

    findings = [
        make_finding(
            "lean.scope-drift",
            FindingSeverity.BLOCKER,
            path,
            "Changed path is outside the scope frozen at implementation start.",
            path,
            "declared-scope",
            "Unapproved behavior and review surface can bypass the work item.",
            "Update the work item before changing this path, or revert the drift.",
            "Rebuild the source snapshot against the frozen scope.",
            round_number,
        )
        for path in paths
    ]
    matches = task_scope_matches or {}
    for finding in findings:
        finding.evidence.append(f"matched_task_ids={matches.get(finding.path, [])}")
    return findings


def unknown_findings(paths: list[str], round_number: int) -> list[LeanFinding]:
    """Keep unknown provenance visible while the status fails closed to needs_user."""

    return [
        make_finding(
            "lean.classification-unknown",
            FindingSeverity.ADVISORY,
            path,
            "Changed file cannot be classified with reliable evidence.",
            "unknown",
            "known-classification",
            "Applying a product budget or zero risk would be a false conclusion.",
            "Provide a policy-backed classification or review the file manually.",
            "Confirm provenance and re-run lean-check.",
            round_number,
        )
        for path in paths
    ]


def unsupported_findings(paths: list[str], round_number: int) -> list[LeanFinding]:
    """Expose unavailable semantic adapters without inventing zero-valued metrics."""

    return [
        make_finding(
            "lean.semantic-capability",
            FindingSeverity.ADVISORY,
            path,
            "Reliable semantic metrics are unsupported for this changed product file.",
            "unsupported",
            "exact-or-conservative-adapter",
            "A zero complexity or caller count would be a false measurement.",
            "Use a supported adapter or complete an independent manual review.",
            "Record the adapter capability and reviewed language boundary.",
            round_number,
        )
        for path in paths
    ]


def generated_scope_findings(
    generated_count: int,
    task_count: int,
    policy: LeanPolicy,
    round_number: int,
) -> list[LeanFinding]:
    """Flag abnormal generated-file growth relative to frozen task count."""

    budget = max(task_count, 1) * policy.generated_files_per_task_budget
    if generated_count <= budget:
        return []
    return [
        make_finding(
            "lean.generated-scope",
            FindingSeverity.REQUIRED,
            "generated/",
            "Generated file growth exceeds the task-relative scope budget.",
            generated_count,
            budget,
            "Generated surface can hide scope expansion even when LOC is exempt.",
            "Narrow generation inputs or add explicit tasks before regenerating.",
            "Rebuild metrics against the frozen task set.",
            round_number,
        )
    ]


def targeted_verification_findings(
    evaluation_round: int,
    previous_had_actionable_findings: bool,
    has_current_verification: bool,
    verification_digest: str,
    previous_verification_digest: str,
) -> list[LeanFinding]:
    """Require second-round evidence to change after an actionable first round."""

    if evaluation_round < 2 or not previous_had_actionable_findings:
        return []
    if has_current_verification and verification_digest != previous_verification_digest:
        return []
    return [
        make_finding(
            "lean.targeted-verification",
            FindingSeverity.REQUIRED,
            "",
            "Second-round code changed without newly recorded targeted verification.",
            verification_digest,
            "fresh-verification-evidence",
            "A finding can disappear after code edits without proof of focused behavior checks.",
            "Record a new verification command or digest-bound test result, then re-evaluate.",
            "Compare verification evidence digests between evaluation rounds.",
            evaluation_round,
        )
    ]


def bugfix_findings(
    root: Path,
    loop_id: str,
    work_type: WorkType,
    evidence: tuple[RegressionEvidence, ...],
    diff_hash: str,
    round_number: int,
    changed_product_paths: tuple[str, ...] = (),
) -> list[LeanFinding]:
    """Require assertion-bound RED/GREEN evidence for production issues."""

    if work_type != WorkType.PRODUCTION_ISSUE:
        return []
    path = changed_product_paths[0] if changed_product_paths else ""
    if not evidence:
        return [_missing_bugfix_evidence(path, round_number)]
    invalid = next(
        (
            issue
            for item in evidence
            if (issue := regression_evidence_issue(root, item, loop_id))
        ),
        "",
    )
    if invalid:
        return [_invalid_bugfix_evidence(path, invalid, round_number)]
    if any(item.green_diff_hash != diff_hash for item in evidence):
        return [_stale_bugfix_evidence(path, round_number)]
    return []


def apply_structured_exceptions(
    root: Path,
    exceptions: tuple[LeanException, ...],
    findings: list[LeanFinding],
    policy_digest: str,
    snapshot: SourceSnapshot,
    round_number: int,
    previous_report_digest: str,
) -> tuple[list[LeanFinding], list[str]]:
    """Retain waived findings and emit blockers for invalid exception evidence."""

    accepted: list[str] = []
    invalid: list[LeanFinding] = []
    for exception in exceptions:
        target = next(
            (
                item
                for item in findings
                if item.stable_signature == exception.stable_signature
            ),
            None,
        )
        issue = exception_issue(
            root,
            exception,
            target,
            policy_digest,
            snapshot,
            previous_report_digest,
        )
        if issue:
            invalid.append(_invalid_exception_finding(exception, issue, round_number))
            continue
        if target is not None:
            target.resolution = FindingResolutionStatus.WAIVED
            accepted.append(exception.exception_id)
    return [*findings, *invalid], accepted


def make_finding(
    rule_id: str,
    severity: FindingSeverity,
    path: str,
    claim: str,
    measured: int | float | str,
    budget: int | float | str,
    risk: str,
    fix: str,
    verification: str,
    round_number: int,
    symbol: str = "",
) -> LeanFinding:
    """Build a stable finding shared by integrity and budget rules."""

    signature = stable_finding_signature(
        rule_id=rule_id,
        classification=FileClassification.HANDWRITTEN_PRODUCT,
        path=Path(path),
        symbol=symbol,
        evidence_locator=f"{rule_id}:{symbol or path}",
    )
    return LeanFinding(
        finding_id=f"lean-{signature.removeprefix('sha256:')[:16]}",
        stable_signature=signature,
        rule_id=rule_id,
        severity=severity,
        path=path,
        symbol=symbol,
        claim=claim,
        evidence=[f"path={path}", f"symbol={symbol}"],
        measured_value=measured,
        configured_budget=budget,
        risk=risk,
        suggested_fix=fix,
        required_verification=[verification],
        round_number=round_number,
    )


def exception_issue(
    root: Path,
    exception: LeanException,
    target: LeanFinding | None,
    policy_digest: str,
    snapshot: SourceSnapshot,
    previous_report_digest: str,
) -> str:
    if exception.decision_status != "approved":
        return "decision is not approved"
    if target is None or target.rule_id != exception.rule_id:
        return "stable signature does not identify the declared rule finding"
    if target.rule_id not in STRUCTURED_EXCEPTION_RULES:
        return "rule does not permit a structured exception"
    if target.path != exception.path or (
        exception.symbol and target.symbol != exception.symbol
    ):
        return "path or symbol does not match the bound finding"
    if (
        not previous_report_digest
        or exception.evaluation_digest != previous_report_digest
    ):
        return "evaluation binding is missing or stale"
    if (
        exception.policy_digest != policy_digest
        or exception.diff_hash != snapshot.diff_hash
    ):
        return "policy or diff binding is stale"
    if (
        exception.base_commit != snapshot.base_commit
        or exception.head_commit != snapshot.head_commit
    ):
        return "commit binding is stale"
    if not any(fnmatch.fnmatchcase(exception.path, item) for item in exception.scope):
        return "path is outside the exception scope"
    expiry_issue = _expiry_issue(exception.expires_at)
    if expiry_issue:
        return expiry_issue
    evidence_issue = _evidence_issue(root, exception)
    if evidence_issue:
        return evidence_issue
    return _reviewer_decision_issue(root, exception, target)


def _invalid_exception_finding(
    exception: LeanException,
    issue: str,
    round_number: int,
) -> LeanFinding:
    return make_finding(
        "lean.exception-invalid",
        FindingSeverity.BLOCKER,
        exception.path,
        f"Structured exception {exception.exception_id} is invalid: {issue}.",
        exception.exception_id,
        "valid-structured-exception",
        "Invalid evidence cannot authorize risk acceptance or closure.",
        "Correct or remove the exception, then rerun lean-check.",
        "Revalidate scope, commits, policy, expiry, and evidence digests.",
        round_number,
    )


def _missing_bugfix_evidence(path: str, round_number: int) -> LeanFinding:
    return make_finding(
        "lean.bugfix-regression",
        FindingSeverity.REQUIRED,
        path,
        "Production issue has no structured RED/GREEN regression evidence.",
        0,
        1,
        "The fix can pass without proving the reported behavior was reproduced.",
        "Record the same target assertion failing before and passing after the fix.",
        "Replay the bound command and verify source/output digests.",
        round_number,
    )


def _invalid_bugfix_evidence(path: str, issue: str, round_number: int) -> LeanFinding:
    return make_finding(
        "lean.bugfix-evidence-invalid",
        FindingSeverity.BLOCKER,
        path,
        f"Structured RED/GREEN evidence is not verifiable: {issue}.",
        issue,
        "digest-bound-local-artifacts",
        "Self-reported strings can make an untested fix look verified.",
        "Bind RED output, GREEN output, and test source to local byte digests.",
        "Re-read every artifact digest and the RED assertion signature.",
        round_number,
    )


def _stale_bugfix_evidence(path: str, round_number: int) -> LeanFinding:
    return make_finding(
        "lean.bugfix-evidence-stale",
        FindingSeverity.BLOCKER,
        path,
        "GREEN regression evidence is not bound to the evaluated diff.",
        "stale",
        "fresh-diff-hash",
        "Evidence from different code cannot authorize closure.",
        "Re-run the targeted regression command on the current source snapshot.",
        "Match the GREEN diff hash to the current Lean input.",
        round_number,
    )


__all__ = [
    "apply_structured_exceptions",
    "bugfix_findings",
    "exception_issue",
    "generated_scope_findings",
    "make_finding",
    "scope_findings",
    "unknown_findings",
    "unsupported_findings",
]
