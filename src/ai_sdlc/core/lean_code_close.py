"""Fail-closed integrity and freshness validation for Lean-enabled close."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from ai_sdlc.core.implementation_store import (
    implementation_artifacts,
    read_input,
    read_loop_run,
    resolve_implementation_loop_run_path,
)
from ai_sdlc.core.lean_code_artifacts import (
    LeanFindingsArtifact,
    lean_artifact_paths,
    read_current_pointer,
    safe_project_path,
)
from ai_sdlc.core.lean_code_evidence import (
    regression_evidence_issue,
    verification_digest,
)
from ai_sdlc.core.lean_code_execution import validate_execution_receipt
from ai_sdlc.core.lean_code_findings import exception_issue
from ai_sdlc.core.lean_code_inputs import (
    acceptance_evidence_digests,
    implementation_task_bindings,
)
from ai_sdlc.core.lean_code_models import (
    LeanEvaluationInput,
    LeanEvaluationReport,
    LeanException,
    LeanPolicy,
    RegressionEvidence,
)
from ai_sdlc.core.lean_code_policy import load_lean_policy, stable_artifact_digest
from ai_sdlc.core.loop_models import LoopStatus
from ai_sdlc.core.loop_policy import LoopPolicyError
from ai_sdlc.core.source_snapshot import SourceSnapshot, revalidate_source_snapshot


@dataclass(frozen=True)
class _LeanChain:
    loop_run: object
    impl_input: object
    pointer: object
    report: LeanEvaluationReport
    snapshot: SourceSnapshot
    policy: LeanPolicy
    findings: LeanFindingsArtifact
    evaluation_input: LeanEvaluationInput


def validate_lean_close(root: Path, loop_id: str) -> str:
    """Return empty only when the complete Lean chain is fresh and closable."""

    chain, issue = _validated_chain(root.resolve(), loop_id)
    if issue or chain is None:
        return issue
    report = chain.report
    if report.status != LoopStatus.PASSED:
        return f"Lean evaluation is not passed: {report.status}."
    if any(item.severity == "BLOCKER" for item in report.blocking_findings):
        return "Lean report contains unresolved BLOCKER findings."
    if report.enforcement_mode != "report" and report.blocking_findings:
        return "Lean report contains unresolved REQUIRED findings."
    return ""


def validate_lean_integrity(root: Path, loop_id: str) -> str:
    """Validate the complete chain without requiring a passed disposition."""

    _chain, issue = _validated_chain(root.resolve(), loop_id)
    return issue


def _validated_chain(root: Path, loop_id: str) -> tuple[_LeanChain | None, str]:
    loaded = _implementation_input(root, loop_id)
    if isinstance(loaded, str):
        return None, loaded
    loop_run, impl_input = loaded
    if (
        loop_run.input_digest
        and stable_artifact_digest(impl_input) != loop_run.input_digest
    ):
        return (
            None,
            "Implementation input digest mismatch; the frozen input was modified.",
        )
    if "lean-code" not in impl_input.quality_profiles:
        return None, ""
    try:
        chain = _load_chain(root, loop_run, impl_input)
        current_policy = load_lean_policy(root)
    except (OSError, ValueError, ValidationError, LoopPolicyError) as exc:
        return None, f"Lean report is malformed: {exc}"
    try:
        issues = (
            _digest_issue(chain),
            _binding_issue(root, chain),
            _external_evidence_issue(root, chain),
        )
    except (OSError, ValueError, ValidationError) as exc:
        return None, f"Lean evidence artifact is malformed: {exc}"
    for issue in issues:
        if issue:
            return None, issue
    if stable_artifact_digest(current_policy) != chain.report.policy_digest:
        return None, "Lean report is stale: policy digest changed."
    freshness = revalidate_source_snapshot(root, chain.snapshot)
    if not freshness.fresh:
        return None, f"Lean report is stale: {freshness.reason}."
    return chain, ""


def _load_chain(root, loop_run, impl_input) -> _LeanChain:
    pointer = read_current_pointer(root, loop_run.loop_id)
    return _LeanChain(
        loop_run=loop_run,
        impl_input=impl_input,
        pointer=pointer,
        report=_read_model(root, pointer.report_path, LeanEvaluationReport),
        snapshot=_read_model(root, pointer.snapshot_path, SourceSnapshot),
        policy=_read_model(root, pointer.policy_path, LeanPolicy),
        findings=_read_model(root, pointer.findings_path, LeanFindingsArtifact),
        evaluation_input=_read_model(root, pointer.input_path, LeanEvaluationInput),
    )


def _implementation_input(root: Path, loop_id: str):
    path, blocker = resolve_implementation_loop_run_path(root, loop_id)
    if blocker:
        return blocker
    try:
        loop_run = read_loop_run(path)
        impl_input = read_input(
            implementation_artifacts(root, loop_run.loop_id).input_path
        )
    except (OSError, ValueError) as exc:
        return f"Implementation loop artifact is malformed: {exc}"
    return loop_run, impl_input


def _read_model(root: Path, relative: str, model_type):
    path = safe_project_path(root, relative)
    return model_type.model_validate_json(path.read_text("utf-8"))


def _digest_issue(chain: _LeanChain) -> str:
    values = (
        (chain.report, chain.pointer.report_digest, "report"),
        (chain.snapshot, chain.pointer.snapshot_digest, "source snapshot"),
        (chain.policy, chain.pointer.policy_digest, "policy snapshot"),
        (chain.findings, chain.pointer.findings_digest, "findings"),
        (chain.evaluation_input, chain.pointer.input_digest, "evaluation input"),
    )
    for artifact, expected, label in values:
        if stable_artifact_digest(artifact) != expected:
            return f"Lean {label} is malformed or tampered: digest mismatch."
    return ""


def _binding_issue(root: Path, chain: _LeanChain) -> str:
    for issue in (
        _identity_binding_issue(root, chain),
        _source_binding_issue(chain),
        _policy_binding_issue(chain),
    ):
        if issue:
            return issue
    return ""


def _identity_binding_issue(root: Path, chain: _LeanChain) -> str:
    report, value = chain.report, chain.evaluation_input
    if (
        len(
            {
                report.loop_id,
                value.loop_id,
                chain.findings.loop_id,
                chain.loop_run.loop_id,
            }
        )
        != 1
    ):
        return "Lean loop identity does not match across the artifact chain."
    if (
        report.work_item_id != value.work_item_id
        or value.work_item_id != chain.impl_input.work_item_id
    ):
        return "Lean work item identity does not match across the artifact chain."
    if (
        report.work_type != value.work_type
        or value.work_type != chain.impl_input.work_type
    ):
        return "Lean work type does not match across the artifact chain."
    if report.evaluation_profile != value.evaluation_profile:
        return "Lean evaluation profile does not match the input."
    if (
        report.evaluation_round != value.evaluation_round
        or report.evaluation_round != chain.findings.evaluation_round
    ):
        return "Lean evaluation round does not match across the artifact chain."
    if value.declared_scope != chain.impl_input.declared_scope:
        return "Lean declared scope does not match the Implementation input."
    task_refs, tasks_digest = implementation_task_bindings(root, chain.impl_input)
    if value.tasks_refs != list(task_refs) or value.tasks_digest != tasks_digest:
        return "Lean tasks evidence does not match the Implementation task snapshot."
    acceptance_refs = [chain.impl_input.spec_path]
    acceptance_digests = acceptance_evidence_digests(root, chain.impl_input)
    if (
        value.acceptance_refs != acceptance_refs
        or value.acceptance_digests != acceptance_digests
    ):
        return "Lean acceptance evidence does not match the Implementation input."
    return ""


def _source_binding_issue(chain: _LeanChain) -> str:
    report, value, snapshot = chain.report, chain.evaluation_input, chain.snapshot
    if stable_artifact_digest(snapshot) != report.source_snapshot_digest:
        return "Lean source snapshot digest does not match the report."
    if report.diff_hash != snapshot.diff_hash or report.diff_hash != value.diff_hash:
        return "Lean diff hash does not match the artifact chain."
    identities = (
        (value.base_ref, snapshot.base_ref),
        (value.head_ref, snapshot.head_ref),
        (value.base_commit, snapshot.base_commit),
        (value.head_commit, snapshot.head_commit),
        (value.changed_files, snapshot.changed_files),
    )
    return (
        "Lean source identity does not match the evaluation input."
        if any(actual != expected for actual, expected in identities)
        else ""
    )


def _policy_binding_issue(chain: _LeanChain) -> str:
    report, value, policy = chain.report, chain.evaluation_input, chain.policy
    if report.policy_digest != stable_artifact_digest(policy):
        return "Lean policy snapshot digest does not match the report."
    if (
        value.policy_digest != report.policy_digest
        or value.policy_version != policy.policy_version
    ):
        return "Lean evaluation input policy does not match the report."
    if report.enforcement_mode != policy.enforcement_mode:
        return "Lean enforcement disposition does not match the policy snapshot."
    if chain.findings.findings != report.findings:
        return "Lean findings do not match the report."
    return ""


def _external_evidence_issue(root: Path, chain: _LeanChain) -> str:
    for issue in (
        _verification_issue(root, chain),
        _regression_issue(root, chain),
        _exception_binding_issue(root, chain),
    ):
        if issue:
            return issue
    return ""


def _verification_issue(root: Path, chain: _LeanChain) -> str:
    value = chain.evaluation_input
    refs = value.verification_evidence_refs
    digests = value.verification_evidence_digests
    if digests and set(refs) != set(digests):
        return "Lean targeted verification digest inventory does not match its refs."
    tokens = (
        tuple(f"{reference}:{digests[reference]}" for reference in sorted(refs))
        if digests
        else tuple(refs)
    )
    if verification_digest(tokens) != chain.report.verification_digest:
        return "Lean targeted verification digest does not match the report."
    for reference in refs if digests else ():
        _receipt, issue = validate_execution_receipt(
            root,
            reference,
            expected_digest=digests[reference],
            expected_purpose="targeted-verification",
            expected_loop_id=chain.report.loop_id,
            current_diff_hash=chain.report.diff_hash,
        )
        if issue:
            return f"Lean targeted verification is invalid: {issue}"
    return ""


def _regression_issue(root: Path, chain: _LeanChain) -> str:
    value = chain.evaluation_input
    if set(value.regression_evidence_refs) != set(value.regression_evidence_digests):
        return "Lean regression evidence digest inventory does not match its refs."
    if (
        chain.report.work_type == "production_issue"
        and not value.regression_evidence_refs
        and not chain.report.exception_ids
    ):
        return "Lean production issue report is missing bound regression evidence."
    for reference in value.regression_evidence_refs:
        try:
            evidence = _read_model(root, reference, RegressionEvidence)
        except (OSError, ValueError, ValidationError) as exc:
            return f"Lean regression evidence is unavailable: {exc}"
        if (
            stable_artifact_digest(evidence)
            != value.regression_evidence_digests[reference]
        ):
            return "Lean regression evidence digest is stale."
        issue = regression_evidence_issue(root, evidence, chain.report.loop_id)
        if issue or evidence.green_diff_hash != chain.report.diff_hash:
            return (
                f"Lean regression evidence is invalid: {issue or 'GREEN diff is stale'}"
            )
    return ""


def _exception_binding_issue(root: Path, chain: _LeanChain) -> str:
    value = chain.evaluation_input
    if set(value.exception_refs) != set(value.exception_digests):
        return "Lean exception digest inventory does not match its refs."
    exceptions: list[LeanException] = []
    for reference in value.exception_refs:
        try:
            item = _read_model(root, reference, LeanException)
        except (OSError, ValueError, ValidationError) as exc:
            return f"Lean exception is unavailable: {exc}"
        if stable_artifact_digest(item) != value.exception_digests[reference]:
            return "Lean exception digest is stale."
        exceptions.append(item)
    if {item.exception_id for item in exceptions} != set(chain.report.exception_ids):
        return "Lean exception ids do not match the report."
    return _exception_content_issue(root, chain, exceptions)


def _exception_content_issue(root, chain, exceptions: list[LeanException]) -> str:
    previous_digest = _previous_report_digest(root, chain.report)
    for item in exceptions:
        target = next(
            (
                finding
                for finding in chain.report.findings
                if finding.stable_signature == item.stable_signature
            ),
            None,
        )
        issue = exception_issue(
            root,
            item,
            target,
            chain.report.policy_digest,
            chain.snapshot,
            previous_digest,
        )
        if issue:
            return f"Lean exception is invalid: {issue}"
    return ""


def _previous_report_digest(root: Path, report: LeanEvaluationReport) -> str:
    if not report.exception_ids or report.evaluation_round <= 1:
        return ""
    path = lean_artifact_paths(
        root, report.loop_id, report.evaluation_round - 1
    ).report_path
    previous = LeanEvaluationReport.model_validate_json(path.read_text("utf-8"))
    return stable_artifact_digest(previous)


__all__ = ["validate_lean_close", "validate_lean_integrity"]
