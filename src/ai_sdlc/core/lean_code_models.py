"""Versioned models for deterministic Lean Code evaluation artifacts."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.loop_models import LoopArtifactModel, LoopStatus
from ai_sdlc.core.pr_review_models import FindingResolutionStatus, FindingSeverity
from ai_sdlc.models.work import WorkType


class FileClassification(StrEnum):
    """Classification applied before maintainability budgets are interpreted."""

    HANDWRITTEN_PRODUCT = "handwritten_product"
    HANDWRITTEN_TEST = "handwritten_test"
    GENERATED = "generated"
    FIXTURE = "fixture"
    VENDORED = "vendored"
    SNAPSHOT = "snapshot"
    DECLARATIVE = "declarative"
    UNKNOWN = "unknown"


class MetricCapability(StrEnum):
    """Confidence contract for one language or metric adapter."""

    EXACT = "exact"
    CONSERVATIVE = "conservative"
    UNSUPPORTED = "unsupported"


class LeanEnforcementMode(StrEnum):
    """Project policy modes for non-integrity Lean findings."""

    REPORT = "report"
    WARNING = "warning"
    BLOCKING = "blocking"


class LeanEvaluationProfile(StrEnum):
    """Derived profile while preserving the original WorkType in artifacts."""

    FEATURE = "feature"
    BUGFIX = "bugfix"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"


class LeanPolicy(LoopArtifactModel):
    """Versioned policy snapshot used by a single evaluation."""

    artifact_kind: str = "lean-code-policy"
    policy_version: str = "1.0"
    enforcement_mode: LeanEnforcementMode = LeanEnforcementMode.WARNING
    max_rounds: int = Field(default=2, ge=1, le=2)
    file_line_budget: int = Field(default=400, ge=1)
    function_line_budget: int = Field(default=50, ge=1)
    complexity_budget: int = Field(default=11, ge=1)
    complexity_delta: int = Field(default=2, ge=1)
    nesting_budget: int = Field(default=5, ge=1)
    fan_out_budget: int = Field(default=12, ge=1)
    fan_out_delta: int = Field(default=3, ge=1)
    public_caller_minimum: int = Field(default=3, ge=1)
    generated_files_per_task_budget: int = Field(default=5, ge=1)
    significant_changed_lines: int = Field(default=20, ge=1)
    significant_changed_ratio: float = Field(default=0.25, gt=0, le=1)


class RegressionEvidence(LoopArtifactModel):
    """Replay-oriented RED/GREEN evidence for a production issue."""

    artifact_kind: str = "lean-regression-evidence"
    test_id: str
    test_symbol: str = ""
    command_argv: list[str]
    cwd: str = "."
    red_source: str
    red_diff_hash: str
    red_exit_code: int
    green_source: str
    green_diff_hash: str
    green_exit_code: int
    failure_signature: str
    red_output_ref: str = ""
    red_output_digest: str
    green_output_ref: str = ""
    green_output_digest: str
    test_source_ref: str = ""
    test_source_digest: str = ""
    red_receipt_ref: str = ""
    red_receipt_digest: str = ""
    green_receipt_ref: str = ""
    green_receipt_digest: str = ""
    toolchain_fingerprint: str
    test_refs: list[str]

    @model_validator(mode="after")
    def _require_red_then_green(self) -> RegressionEvidence:
        if self.red_exit_code == 0:
            raise ValueError("RED evidence must have a non-zero exit code")
        if self.green_exit_code != 0:
            raise ValueError("GREEN evidence must have a zero exit code")
        if not self.failure_signature.startswith("assertion:"):
            raise ValueError("RED evidence must describe the target assertion failure")
        return self


class LeanReviewerFindingDecision(BaseModel):
    """One reviewer's evidence-bound decision for one exact Lean finding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stable_signature: str
    rule_id: str
    path: str
    symbol: str = ""
    verdict: str
    rationale: str
    contract_kind: str
    contract_path: str
    contract_digest: str
    contract_symbol: str
    exact_locators: list[str]
    exact_locator_digests: dict[str, str]
    verification_evidence_refs: list[str]
    verification_evidence_digests: dict[str, str]

    @model_validator(mode="after")
    def _require_semantic_evidence(self) -> LeanReviewerFindingDecision:
        required = (
            self.stable_signature,
            self.rule_id,
            self.path,
            self.verdict,
            self.rationale,
            self.contract_kind,
            self.contract_path,
            self.contract_digest,
            self.contract_symbol,
        )
        if not all(item.strip() for item in required):
            raise ValueError("Lean reviewer decision is incomplete")
        if self.verdict not in {"approved", "rejected"}:
            raise ValueError("Lean reviewer verdict is invalid")
        if not self.exact_locators or not self.verification_evidence_refs:
            raise ValueError("Lean reviewer semantic evidence is missing")
        if set(self.exact_locators) != set(self.exact_locator_digests):
            raise ValueError("Lean reviewer exact locator evidence is incomplete")
        if set(self.verification_evidence_refs) != set(
            self.verification_evidence_digests
        ):
            raise ValueError("Lean reviewer verification evidence is incomplete")
        return self


class LeanReviewerDecisionArtifact(LoopArtifactModel):
    """Independent reviewer decisions bound to one frozen Lean evaluation."""

    artifact_kind: str = "lean-reviewer-decision"
    decision_id: str
    reviewer_id: str
    reviewer_role: str
    review_project_id: str
    review_work_item_id: str
    review_stage_instance_id: str
    review_session_id: str
    review_pass_id: str
    review_pass_digest: str
    review_assignment_digest: str
    decision_payload_digest: str
    diff_hash: str
    policy_digest: str
    evaluation_digest: str
    decisions: list[LeanReviewerFindingDecision]

    @model_validator(mode="after")
    def _require_independent_decisions(self) -> LeanReviewerDecisionArtifact:
        required = (
            self.decision_id,
            self.reviewer_id,
            self.reviewer_role,
            self.review_project_id,
            self.review_work_item_id,
            self.review_stage_instance_id,
            self.review_session_id,
            self.review_pass_id,
            self.review_pass_digest,
            self.review_assignment_digest,
            self.decision_payload_digest,
            self.diff_hash,
            self.policy_digest,
            self.evaluation_digest,
        )
        if not all(item.strip() for item in required) or not self.decisions:
            raise ValueError("Lean reviewer decision artifact is incomplete")
        signatures = [item.stable_signature for item in self.decisions]
        if len(signatures) != len(set(signatures)):
            raise ValueError("Lean reviewer decisions must be unique by signature")
        from ai_sdlc.core.lean_code_exception_review import (
            reviewer_decision_payload_digest,
        )

        expected = reviewer_decision_payload_digest(
            self.diff_hash,
            self.policy_digest,
            self.evaluation_digest,
            self.decisions,
        )
        if self.decision_payload_digest != expected:
            raise ValueError("Lean reviewer decision payload digest is invalid")
        return self


class LeanException(LoopArtifactModel):
    """A bounded exception that changes enforcement without hiding a finding."""

    artifact_kind: str = "lean-code-exception"
    exception_id: str
    rule_id: str
    path: str
    symbol: str = ""
    stable_signature: str
    reason: str
    owner: str
    approver: str
    evidence_refs: list[str]
    evidence_digests: dict[str, str]
    reviewer_decision_refs: list[str] = Field(default_factory=list)
    reviewer_decision_digests: dict[str, str] = Field(default_factory=dict)
    scope: list[str]
    policy_digest: str
    base_commit: str
    head_commit: str
    diff_hash: str
    evaluation_digest: str
    expires_at: str = ""
    decision_status: str = "approved"

    @field_validator(
        "exception_id",
        "rule_id",
        "path",
        "stable_signature",
        "reason",
        "owner",
        "approver",
        "policy_digest",
        "diff_hash",
        "evaluation_digest",
    )
    @classmethod
    def _require_exception_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Lean exception field must not be empty")
        return value


class LeanFinding(LoopArtifactModel):
    """One deterministic Lean Code finding using PR review severity semantics."""

    artifact_kind: str = "lean-code-finding"
    finding_id: str
    stable_signature: str
    rule_id: str
    severity: FindingSeverity
    path: str
    symbol: str = ""
    claim: str
    evidence: list[str]
    measured_value: int | float | str
    configured_budget: int | float | str
    risk: str
    suggested_fix: str
    required_verification: list[str]
    resolution: FindingResolutionStatus = FindingResolutionStatus.UNRESOLVED
    round_number: int = Field(ge=1, le=2)


class LeanEvaluationInput(LoopArtifactModel):
    """Frozen inputs that bind an evaluation to source, policy, and work scope."""

    artifact_kind: str = "lean-code-evaluation-input"
    loop_id: str
    work_item_id: str
    work_type: WorkType
    evaluation_profile: LeanEvaluationProfile
    policy_version: str
    policy_digest: str
    base_ref: str
    head_ref: str
    base_commit: str
    head_commit: str
    diff_hash: str
    declared_scope: list[str]
    task_scopes: dict[str, list[str]] = Field(
        default_factory=dict, exclude_if=lambda value: not value
    )
    changed_files: list[str]
    tasks_refs: list[str]
    tasks_digest: str = ""
    acceptance_refs: list[str]
    acceptance_digests: dict[str, str] = Field(default_factory=dict)
    verification_evidence_refs: list[str]
    verification_evidence_digests: dict[str, str] = Field(default_factory=dict)
    regression_evidence_refs: list[str] = Field(default_factory=list)
    regression_evidence_digests: dict[str, str] = Field(default_factory=dict)
    exception_refs: list[str]
    exception_digests: dict[str, str] = Field(default_factory=dict)
    previous_report_path: str = ""
    previous_report_digest: str = ""
    previous_verification_digest: str = ""
    previous_actionable_signatures: list[str] = Field(default_factory=list)
    evaluation_round: int = Field(ge=1, le=2)


class FunctionMetric(BaseModel):
    """Deterministic measurements for one source function."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    symbol: str
    logical_lines: int = Field(ge=0)
    base_logical_lines: int = Field(default=0, ge=0)
    complexity: int = Field(default=0, ge=0)
    base_complexity: int = Field(default=0, ge=0)
    max_nesting: int = Field(default=0, ge=0)
    base_max_nesting: int = Field(default=0, ge=0)
    caller_count: int = Field(default=0, ge=0)
    caller_evidence: list[str] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    public: bool = False
    is_new: bool = False
    capability: MetricCapability = MetricCapability.UNSUPPORTED
    binding_state: Literal["exact", "plausible", "disproven"] = "disproven"
    execution_state: Literal[
        "executed", "contractual", "referenced_only", "unreachable", "unknown"
    ] = "unreachable"
    invocation_boundary: str = ""
    invocation_evidence: list[str] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    reference_evidence: list[str] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    unlinked_evidence: list[str] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    fingerprint: str = ""
    duplicate_count: int = Field(default=1, ge=1)


class FileMetric(BaseModel):
    """Classification, diff, size, and semantic metrics for one changed file."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    path: str
    classification: FileClassification
    language: str = "unknown"
    capability: MetricCapability = MetricCapability.UNSUPPORTED
    base_lines: int = Field(default=0, ge=0)
    head_lines: int = Field(default=0, ge=0)
    added_lines: int = Field(default=0, ge=0)
    deleted_lines: int = Field(default=0, ge=0)
    import_fan_out: int = Field(default=0, ge=0)
    base_import_fan_out: int = Field(default=0, ge=0)
    functions: list[FunctionMetric] = Field(default_factory=list)
    parse_errors: list[str] = Field(default_factory=list)

    @property
    def is_new(self) -> bool:
        return self.base_lines == 0 and self.head_lines > 0

    @property
    def changed_ratio(self) -> float:
        return (self.added_lines + self.deleted_lines) / max(self.base_lines, 1)


class LeanMetrics(LoopArtifactModel):
    """Aggregate deterministic metrics for one source snapshot."""

    artifact_kind: str = "lean-code-metrics"
    product_added_lines: int = 0
    product_deleted_lines: int = 0
    product_net_lines: int = 0
    test_added_lines: int = 0
    test_deleted_lines: int = 0
    test_net_lines: int = 0
    new_file_count: int = 0
    changed_file_count: int = 0
    classification_counts: dict[str, int] = Field(default_factory=dict)
    unknown_files: list[str] = Field(default_factory=list)
    unsupported_semantic_files: list[str] = Field(default_factory=list)
    duplicate_candidates: list[str] = Field(default_factory=list)
    scope_drift: list[str] = Field(default_factory=list)
    task_scope_matches: dict[str, list[str]] = Field(
        default_factory=dict, exclude_if=lambda value: not value
    )
    files: list[FileMetric] = Field(default_factory=list)


class LeanEvaluationReport(LoopArtifactModel):
    """Machine truth for a single bounded Lean evaluation round."""

    artifact_kind: str = "lean-code-report"
    loop_id: str
    work_item_id: str
    work_type: WorkType
    evaluation_profile: LeanEvaluationProfile
    evaluation_round: int = Field(ge=1, le=2)
    source_snapshot_digest: str
    diff_hash: str
    policy_digest: str
    enforcement_mode: LeanEnforcementMode = LeanEnforcementMode.WARNING
    verification_digest: str = ""
    status: LoopStatus
    metrics: LeanMetrics
    findings: list[LeanFinding] = Field(default_factory=list)
    exception_ids: list[str] = Field(default_factory=list)
    risk_accepted: bool = False
    previous_signatures: list[str] = Field(default_factory=list)
    stop_reason: str = ""

    @property
    def blocking_findings(self) -> list[LeanFinding]:
        return [
            item
            for item in self.findings
            if item.severity in {FindingSeverity.BLOCKER, FindingSeverity.REQUIRED}
            and item.resolution
            not in {
                FindingResolutionStatus.FIXED,
                FindingResolutionStatus.WAIVED,
                FindingResolutionStatus.NOT_APPLICABLE,
            }
        ]


class LeanNoGoDecision(LoopArtifactModel):
    """Operator-owned explanation for stopping the bounded repair loop."""

    artifact_kind: str = "lean-code-no-go"
    decision_id: str
    loop_id: str
    work_item_id: str
    reason: str
    owner: str
    repair_cost: str
    expected_benefit: str
    evidence_refs: list[str]
    evidence_digests: dict[str, str] = Field(default_factory=dict)
    diff_hash: str
    policy_digest: str
    report_digest: str
    decision_status: str = "no_go"

    @field_validator(
        "decision_id",
        "loop_id",
        "work_item_id",
        "reason",
        "owner",
        "repair_cost",
        "expected_benefit",
        "diff_hash",
        "policy_digest",
        "report_digest",
        "evidence_refs",
    )
    @classmethod
    def _require_no_go_value(cls, value: object) -> object:
        if not value or (isinstance(value, str) and not value.strip()):
            raise ValueError("Lean No-Go field must not be empty")
        return value


def stable_finding_signature(
    *,
    rule_id: str,
    classification: FileClassification,
    path: Path,
    symbol: str,
    evidence_locator: str,
) -> str:
    """Return a signature that is stable across line, severity, and value changes."""

    normalized = "|".join(
        (
            rule_id.strip(),
            classification.value,
            path.as_posix().lstrip("./"),
            symbol.strip(),
            evidence_locator.strip(),
        )
    )
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def evaluation_profile_for(work_type: WorkType) -> LeanEvaluationProfile:
    """Map the canonical work type without creating a parallel source enum."""

    if work_type in {WorkType.NEW_REQUIREMENT, WorkType.CHANGE_REQUEST}:
        return LeanEvaluationProfile.FEATURE
    if work_type == WorkType.PRODUCTION_ISSUE:
        return LeanEvaluationProfile.BUGFIX
    if work_type == WorkType.MAINTENANCE_TASK:
        return LeanEvaluationProfile.MAINTENANCE
    return LeanEvaluationProfile.UNKNOWN


__all__ = [
    "FileClassification",
    "LeanEnforcementMode",
    "LeanEvaluationInput",
    "LeanEvaluationReport",
    "LeanEvaluationProfile",
    "LeanException",
    "LeanFinding",
    "LeanPolicy",
    "LeanMetrics",
    "LeanNoGoDecision",
    "FileMetric",
    "FunctionMetric",
    "MetricCapability",
    "RegressionEvidence",
    "evaluation_profile_for",
    "stable_finding_signature",
]
