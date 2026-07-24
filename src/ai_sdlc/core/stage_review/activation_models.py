"""阶段门禁激活的不可变策略、证据和评估合同。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.optimization.attribution import AttributionPolicy
from ai_sdlc.core.stage_review.registry_versions import (
    normalize_machine_ids,
    normalize_text_set,
    require_machine_id,
    require_version,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc

RiskLevel = Literal["low", "medium", "high", "critical"]
GateMode = Literal["shadow", "enforce"]
IsolationLevel = Literal["enforced", "detected_only", "unproven"]
ACTIVATION_STAGES = (
    "requirement",
    "design-contract",
    "implementation",
    "frontend-evidence",
    "local-pr-review",
)
ACTIVATION_RISKS: tuple[RiskLevel, ...] = (
    "low",
    "medium",
    "high",
    "critical",
)
_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ActivationSampleSize(BaseModel):
    model_config = _CONFIG
    minimum_total_shadow_sessions: int = Field(default=0, ge=0)
    minimum_shadow_sessions_per_stage: int = Field(default=0, ge=0)
    minimum_total_enforce_sessions: int = Field(default=0, ge=0)
    minimum_shadow_sessions_per_new_combination: int = Field(default=0, ge=0)
    minimum_enforce_sessions_per_new_combination: int = Field(default=0, ge=0)


class StageGateActivationPolicy(ArtifactCompatibility):
    schema_version: Literal["stage-gate-activation-policy.v2"] = (
        "stage-gate-activation-policy.v2"
    )
    artifact_kind: Literal["stage-gate-activation-policy"] = (
        "stage-gate-activation-policy"
    )
    policy_id: str
    policy_version: str
    active_phase: int = Field(ge=1, le=4)
    effective_at: str
    gate_contract_version: str
    sample_size: ActivationSampleSize
    observation_window_days: int = Field(ge=0)
    outcome_maturity_window_days: int = Field(default=14, ge=0)
    enabled_stages: tuple[str, ...]
    enabled_risk_levels: tuple[RiskLevel, ...]
    confidence_requirement: float = Field(gt=0.5, lt=1)
    maximum_reversal_events: int = Field(default=0, ge=0)
    maximum_late_critical_events: int = Field(default=0, ge=0)
    maximum_escape_events: int = Field(default=0, ge=0)
    maximum_reversal_rate_upper: float = Field(default=0.1, gt=0, le=1)
    maximum_late_critical_rate_upper: float = Field(default=0.1, gt=0, le=1)
    maximum_escape_rate_upper: float = Field(default=0.1, gt=0, le=1)
    activation_escape_cause_ids: tuple[str, ...]
    attribution_policy_digest: str
    required_isolation_platforms: tuple[str, ...]
    trusted_evidence_workflow_paths: tuple[str, ...] = (
        ".github/workflows/activation-evidence.yml",
    )
    evidence_predicate_type: str = "https://slsa.dev/provenance/v1"
    evidence_purpose: str = "stage-gate-activation"
    grandfathered_loop_ids: tuple[str, ...] = ()
    offline_optimization_enabled: bool = False
    previous_policy_digest: str = ""
    activation_assessment_digest: str = ""
    policy_digest: str = ""

    @field_validator("policy_id")
    @classmethod
    def _policy_id_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "activation policy id")

    @field_validator("policy_version", "gate_contract_version")
    @classmethod
    def _version_is_supported(cls, value: str) -> str:
        return require_version(value)

    @field_validator("effective_at")
    @classmethod
    def _time_is_utc(cls, value: str) -> str:
        parse_utc(value)
        return value

    @field_validator(
        "enabled_stages",
        "required_isolation_platforms",
        "trusted_evidence_workflow_paths",
        "activation_escape_cause_ids",
        mode="before",
    )
    @classmethod
    def _normalize_text_values(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_text_set(value))

    @model_validator(mode="after")
    def _verify_evidence_trust_root(self) -> Self:
        workflows = self.trusted_evidence_workflow_paths
        if not workflows or any(
            not item.startswith(".github/workflows/")
            or not item.endswith((".yml", ".yaml"))
            or ".." in item
            for item in workflows
        ):
            raise ValueError("activation evidence workflow trust root is invalid")
        if self.evidence_predicate_type != "https://slsa.dev/provenance/v1":
            raise ValueError("activation evidence predicate type is unsupported")
        require_machine_id(self.evidence_purpose, "activation evidence purpose")
        return self

    @field_validator("enabled_risk_levels", mode="before")
    @classmethod
    def _normalize_risks(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_text_set(value))

    @field_validator("grandfathered_loop_ids", mode="before")
    @classmethod
    def _normalize_loop_ids(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_machine_ids(value))

    @model_validator(mode="after")
    def _verify_phase_and_digest(self) -> Self:
        attribution_policy = AttributionPolicy.baseline()
        if self.attribution_policy_digest != attribution_policy.policy_digest:
            raise ValueError("activation attribution policy is unsupported")
        if not set(self.activation_escape_cause_ids).issubset(
            attribution_policy.non_optimizable_causes
        ):
            raise ValueError(
                "activation escape causes must be non-optimizable product defects"
            )
        if self.offline_optimization_enabled != (self.active_phase == 4):
            raise ValueError("offline optimization requires activation phase 4")
        if self.active_phase == 1 and self.enabled_risk_levels:
            raise ValueError("phase 1 must remain shadow-only")
        lineage = (self.previous_policy_digest, self.activation_assessment_digest)
        if (self.active_phase > 1) != all(lineage):
            raise ValueError("promoted activation policy requires complete lineage")
        return fill_artifact_digest(self, "policy_digest")


class ActivationSessionObservation(BaseModel):
    model_config = _CONFIG
    session_id: str
    stage_key: str
    risk_level: RiskLevel
    mode: GateMode
    completed_at: str

    @field_validator("completed_at")
    @classmethod
    def _completion_time_is_valid(cls, value: str) -> str:
        parse_utc(value)
        return value


class ActivationSessionRecord(ArtifactCompatibility):
    schema_version: Literal["stage-gate-activation-session-record.v2"] = (
        "stage-gate-activation-session-record.v2"
    )
    artifact_kind: Literal["stage-gate-activation-session-record"] = (
        "stage-gate-activation-session-record"
    )
    record_id: str
    project_id: str
    close_proof_kind: Literal["shadow-attestation", "enforce-certificate"]
    close_proof_id: str
    close_proof_digest: str
    candidate_manifest_digest: str
    panel_plan_digest: str
    review_session_digest: str
    review_completion_digest: str
    scope: FindingScope
    observation: ActivationSessionObservation
    record_digest: str = ""

    @model_validator(mode="after")
    def _verify_record(self) -> Self:
        required = (
            self.record_id,
            self.project_id,
            self.close_proof_id,
            self.close_proof_digest,
            self.candidate_manifest_digest,
            self.panel_plan_digest,
            self.review_session_digest,
            self.review_completion_digest,
        )
        if any(not value.strip() or value != value.strip() for value in required):
            raise ValueError("activation session record identity is invalid")
        if (
            self.scope.project_id != self.project_id
            or self.scope.session_id != self.observation.session_id
        ):
            raise ValueError("activation session record scope lineage diverged")
        return fill_artifact_digest(self, "record_digest")


class ActivationSessionOutcome(ArtifactCompatibility):
    """由项目本地终态事实重建的单个评审会话质量结果。"""

    schema_version: Literal["stage-gate-activation-session-outcome.v1"] = (
        "stage-gate-activation-session-outcome.v1"
    )
    artifact_kind: Literal["stage-gate-activation-session-outcome"] = (
        "stage-gate-activation-session-outcome"
    )
    session_id: str
    session_record_digest: str
    status: Literal["complete", "incomplete"]
    reason_codes: tuple[str, ...] = ()
    had_reversal: bool
    had_late_critical: bool
    had_escape: bool
    finalized_at: str
    observation_cutoff: str
    finding_chain_head_digest: str
    attribution_set_digest: str
    finding_event_digests: tuple[str, ...] = ()
    attribution_decision_digests: tuple[str, ...] = ()
    product_defect_signal_digests: tuple[str, ...] = ()
    outcome_digest: str = ""

    @field_validator(
        "reason_codes",
        "finding_event_digests",
        "attribution_decision_digests",
        "product_defect_signal_digests",
        mode="before",
    )
    @classmethod
    def _normalize_sets(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_text_set(value))

    @model_validator(mode="after")
    def _verify_outcome(self) -> Self:
        if not self.session_id.strip() or self.session_id != self.session_id.strip():
            raise ValueError("activation outcome session identity is invalid")
        sources = (
            self.session_record_digest,
            self.finding_chain_head_digest,
            self.attribution_set_digest,
            *self.finding_event_digests,
            *self.attribution_decision_digests,
        )
        if any(not _valid_sha256(item) for item in sources):
            raise ValueError("activation outcome source digest is invalid")
        if (self.status == "complete") == bool(self.reason_codes):
            raise ValueError("activation outcome completeness contradicts reasons")
        finalized = parse_utc(self.finalized_at)
        cutoff = parse_utc(self.observation_cutoff)
        if finalized < cutoff:
            raise ValueError("activation outcome finalized before observation cutoff")
        if (
            self.had_reversal or self.had_late_critical
        ) and not self.finding_event_digests:
            raise ValueError("activation finding outcome lacks source events")
        if self.had_escape and (
            not self.attribution_decision_digests
            or not self.product_defect_signal_digests
        ):
            raise ValueError("activation escape outcome lacks product defect evidence")
        return fill_artifact_digest(self, "outcome_digest")


class IsolationPlatformEvidence(BaseModel):
    model_config = _CONFIG
    platform_id: str
    isolation_level: IsolationLevel
    candidate_write_blocked: bool
    sibling_write_blocked: bool
    home_write_blocked: bool
    network_blocked: bool
    provider_command_blocked: bool = False
    evidence_digest: str

    @model_validator(mode="after")
    def _verify_fail_closed_claim(self) -> Self:
        boundary_claims = (
            self.candidate_write_blocked,
            self.sibling_write_blocked,
            self.home_write_blocked,
            self.network_blocked,
        )
        if self.provider_command_blocked and (
            self.isolation_level != "unproven" or any(boundary_claims)
        ):
            raise ValueError("pre-command refusal cannot claim enforced boundaries")
        return self


class ActivationProbeEvidence(BaseModel):
    """受保护 CI 对协议、隔离和性能基线的控制探针结果。"""

    model_config = _CONFIG
    canonical_plan_replay_passed: bool
    certificate_integrity_passed: bool
    provider_billing_integrity_passed: bool
    crash_recovery_passed: bool
    hard_budget_integrity_passed: bool
    clean_user_e2e_passed: bool
    planner_benchmark_p95_seconds: float = Field(ge=0)
    work_item_fencing_passed: bool
    hard_constraint_integrity_passed: bool
    non_waivable_integrity_passed: bool
    platform_count: int = Field(ge=1)
    probe_trial_count: int = Field(ge=1)


class ActivationEvaluationCohortBoundary(BaseModel):
    """Safety Release 为下一轮自动晋级建立的不可变评估高水位。"""

    model_config = _CONFIG
    stage_key: str
    risk_level: RiskLevel
    policy_digest: str
    hold_digest: str
    release_digest: str
    released_at: str

    @model_validator(mode="after")
    def _verify_boundary(self) -> Self:
        if not self.stage_key.strip() or self.stage_key != self.stage_key.strip():
            raise ValueError("activation evaluation cohort stage is invalid")
        if any(
            not _valid_sha256(item)
            for item in (
                self.policy_digest,
                self.hold_digest,
                self.release_digest,
            )
        ):
            raise ValueError("activation evaluation cohort lineage is invalid")
        parse_utc(self.released_at)
        return self


class ActivationEvidence(ArtifactCompatibility):
    schema_version: Literal["stage-gate-activation-evidence.v2"] = (
        "stage-gate-activation-evidence.v2"
    )
    artifact_kind: Literal["stage-gate-activation-evidence"] = (
        "stage-gate-activation-evidence"
    )
    project_id: str
    assessed_at: str
    sessions: tuple[ActivationSessionObservation, ...]
    session_record_digests: tuple[str, ...]
    isolation_matrix: tuple[IsolationPlatformEvidence, ...]
    isolation_record_digests: tuple[str, ...]
    probes: ActivationProbeEvidence
    probe_record_digest: str
    session_outcomes: tuple[ActivationSessionOutcome, ...]
    cohort_boundaries: tuple[ActivationEvaluationCohortBoundary, ...] = ()
    evidence_digest: str = ""

    @field_validator("project_id")
    @classmethod
    def _project_identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "activation evidence project_id")

    @field_validator("assessed_at")
    @classmethod
    def _assessment_time_is_valid(cls, value: str) -> str:
        parse_utc(value)
        return value

    @model_validator(mode="after")
    def _verify_evidence(self) -> Self:
        session_ids = [item.session_id for item in self.sessions]
        if len(session_ids) != len(set(session_ids)):
            raise ValueError("duplicate activation session observation")
        platforms = [item.platform_id for item in self.isolation_matrix]
        if len(platforms) != len(set(platforms)):
            raise ValueError("duplicate isolation platform evidence")
        if len(self.session_record_digests) != len(self.sessions):
            raise ValueError("activation session lineage is incomplete")
        outcome_ids = tuple(item.session_id for item in self.session_outcomes)
        if outcome_ids != tuple(session_ids):
            raise ValueError("activation session outcomes are incomplete or unordered")
        if (
            tuple(item.session_record_digest for item in self.session_outcomes)
            != self.session_record_digests
        ):
            raise ValueError("activation session outcome lineage diverged")
        if len(self.isolation_record_digests) != len(self.isolation_matrix):
            raise ValueError("activation isolation lineage is incomplete")
        boundaries = tuple(
            (item.stage_key, item.risk_level)
            for item in self.cohort_boundaries
        )
        if boundaries != tuple(sorted(set(boundaries))):
            raise ValueError("activation evaluation cohort is not canonical")
        release_digests = tuple(
            sorted({item.release_digest for item in self.cohort_boundaries})
        )
        source_digests = (
            *self.session_record_digests,
            *self.isolation_record_digests,
            self.probe_record_digest,
            *release_digests,
        )
        if len(source_digests) != len(set(source_digests)):
            raise ValueError("activation evidence source digest is reused")
        if any(not _valid_sha256(item) for item in source_digests):
            raise ValueError("activation evidence source digest is invalid")
        assessed_at = parse_utc(self.assessed_at)
        if any(parse_utc(item.completed_at) > assessed_at for item in self.sessions):
            raise ValueError("activation session completes after assessment")
        if any(
            parse_utc(item.finalized_at) != assessed_at
            or parse_utc(item.observation_cutoff) > assessed_at
            for item in self.session_outcomes
        ):
            raise ValueError("activation session outcome snapshot time diverged")
        return fill_artifact_digest(self, "evidence_digest")


def _valid_sha256(value: str) -> bool:
    if not value.startswith("sha256:") or len(value) != 71:
        return False
    try:
        int(value[7:], 16)
    except ValueError:
        return False
    return True


class WilsonInterval(BaseModel):
    model_config = _CONFIG
    metric_id: str
    events: int
    trials: int
    confidence: float
    lower: float
    upper: float


class ActivationAssessment(ArtifactCompatibility):
    schema_version: Literal["stage-gate-activation-assessment.v1"] = (
        "stage-gate-activation-assessment.v1"
    )
    artifact_kind: Literal["stage-gate-activation-assessment"] = (
        "stage-gate-activation-assessment"
    )
    assessment_id: str
    policy_digest: str
    evidence_digest: str
    assessed_at: str
    eligible: bool
    failed_guards: tuple[str, ...]
    quality_intervals: tuple[WilsonInterval, ...]
    assessment_digest: str = ""

    @model_validator(mode="after")
    def _verify_assessment(self) -> Self:
        if self.failed_guards != tuple(sorted(set(self.failed_guards))):
            raise ValueError("activation failed guards must be canonical")
        if self.eligible == bool(self.failed_guards):
            raise ValueError("activation eligibility contradicts failed guards")
        parse_utc(self.assessed_at)
        return fill_artifact_digest(self, "assessment_digest")


class ActivationSafetyScope(BaseModel):
    """安全冻结只作用于发生回归的阶段与风险组合。"""

    model_config = _CONFIG
    stage_key: str
    risk_level: RiskLevel

    @field_validator("stage_key")
    @classmethod
    def _stage_key_is_valid(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("activation safety stage key is invalid")
        return value


class ActivationSafetyHold(ArtifactCompatibility):
    """真实运行结果回归后，在下一次产品写入前持久化的安全冻结。"""

    schema_version: Literal["stage-gate-activation-safety-hold.v1"] = (
        "stage-gate-activation-safety-hold.v1"
    )
    artifact_kind: Literal["stage-gate-activation-safety-hold"] = (
        "stage-gate-activation-safety-hold"
    )
    hold_id: str
    project_id: str
    policy_digest: str
    evidence_digest: str
    assessment_digest: str
    triggering_outcome_digests: tuple[str, ...]
    affected_combinations: tuple[ActivationSafetyScope, ...]
    created_at: str
    recovery_not_before: str
    minimum_recovery_sessions: int = Field(ge=1)
    hold_digest: str = ""

    @field_validator(
        "triggering_outcome_digests",
        mode="before",
    )
    @classmethod
    def _canonical_hold_sets(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_text_set(value))

    @model_validator(mode="after")
    def _verify_hold(self) -> Self:
        if not (
            self.hold_id
            and self.project_id
            and self.policy_digest
            and self.evidence_digest
            and self.assessment_digest
            and self.triggering_outcome_digests
            and self.affected_combinations
        ):
            raise ValueError("activation safety hold identity is incomplete")
        combinations = tuple(
            (item.stage_key, item.risk_level) for item in self.affected_combinations
        )
        if combinations != tuple(sorted(set(combinations))):
            raise ValueError("activation safety hold scope must be canonical")
        if parse_utc(self.recovery_not_before) <= parse_utc(self.created_at):
            raise ValueError("activation safety hold recovery window is invalid")
        return fill_artifact_digest(self, "hold_digest")


class ActivationSafetyRecoverySample(ArtifactCompatibility):
    """冻结期间由独立评审产生、且未执行产品 writer 的恢复样本。"""

    schema_version: Literal["stage-gate-activation-safety-recovery.v1"] = (
        "stage-gate-activation-safety-recovery.v1"
    )
    artifact_kind: Literal["stage-gate-activation-safety-recovery"] = (
        "stage-gate-activation-safety-recovery"
    )
    sample_id: str
    hold_id: str
    hold_digest: str
    project_id: str
    policy_digest: str
    stage_key: str
    risk_level: RiskLevel
    candidate_manifest_digest: str
    panel_plan_digest: str
    binding_set_digest: str
    finding_ledger_digest: str
    review_session_digest: str
    review_completion_digest: str
    scope: FindingScope
    review_completed_at: str
    observed_at: str
    sample_digest: str = ""

    @model_validator(mode="after")
    def _verify_sample(self) -> Self:
        identities = (
            self.sample_id,
            self.hold_id,
            self.project_id,
            self.stage_key,
        )
        if any(not item.strip() or item != item.strip() for item in identities):
            raise ValueError("activation safety recovery identity is invalid")
        digests = (
            self.hold_digest,
            self.policy_digest,
            self.candidate_manifest_digest,
            self.panel_plan_digest,
            self.binding_set_digest,
            self.finding_ledger_digest,
            self.review_session_digest,
            self.review_completion_digest,
        )
        if any(not _valid_sha256(item) for item in digests):
            raise ValueError("activation safety recovery digest is invalid")
        if self.scope.project_id != self.project_id or self.scope.session_id == "":
            raise ValueError("activation safety recovery scope diverged")
        if parse_utc(self.observed_at) < parse_utc(self.review_completed_at):
            raise ValueError(
                "activation safety recovery observation predates review completion"
            )
        return fill_artifact_digest(self, "sample_digest")


class ActivationSafetyRelease(ArtifactCompatibility):
    """满足独立样本数和恢复观察窗后生成的不可变解冻证明。"""

    schema_version: Literal["stage-gate-activation-safety-release.v1"] = (
        "stage-gate-activation-safety-release.v1"
    )
    artifact_kind: Literal["stage-gate-activation-safety-release"] = (
        "stage-gate-activation-safety-release"
    )
    release_id: str
    hold_id: str
    hold_digest: str
    project_id: str
    policy_digest: str
    recovery_sample_digests: tuple[str, ...]
    recovery_outcome_digests: tuple[str, ...]
    finding_chain_head_digests: tuple[str, ...]
    attribution_set_digests: tuple[str, ...]
    released_at: str
    release_digest: str = ""

    @model_validator(mode="after")
    def _verify_release(self) -> Self:
        identities = (
            self.release_id,
            self.hold_id,
            self.project_id,
        )
        if any(not item.strip() or item != item.strip() for item in identities):
            raise ValueError("activation safety release identity is invalid")
        digests = (
            self.hold_digest,
            self.policy_digest,
            *self.recovery_sample_digests,
            *self.recovery_outcome_digests,
            *self.finding_chain_head_digests,
            *self.attribution_set_digests,
        )
        source_lengths = {
            len(self.recovery_sample_digests),
            len(self.recovery_outcome_digests),
            len(self.finding_chain_head_digests),
            len(self.attribution_set_digests),
        }
        invalid = tuple(item for item in digests if not _valid_sha256(item))
        if source_lengths == {0} or len(source_lengths) != 1 or invalid:
            raise ValueError(
                f"activation safety release digest is invalid: {invalid}"
            )
        parse_utc(self.released_at)
        return fill_artifact_digest(self, "release_digest")
