"""激活策略的版本推进与单次关闭适用性重算。"""

from __future__ import annotations

from typing import Literal

from ai_sdlc.core.stage_review.activation_models import (
    ACTIVATION_RISKS,
    ACTIVATION_STAGES,
    ActivationAssessment,
    ActivationSampleSize,
    RiskLevel,
    StageGateActivationPolicy,
)
from ai_sdlc.core.stage_review.close_gate_models import GateApplicabilityDecision
from ai_sdlc.core.stage_review.optimization.attribution import AttributionPolicy
from ai_sdlc.core.stage_review.registry_versions import require_version
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id


def baseline_activation_policy() -> StageGateActivationPolicy:
    attribution_policy = AttributionPolicy.baseline()
    return StageGateActivationPolicy(
        policy_id="bundled.stage-gate-activation",
        policy_version="1.0.0",
        active_phase=1,
        effective_at="2026-01-01T00:00:00+00:00",
        gate_contract_version="1.0.0",
        sample_size=_sample_size_for_phase(1),
        observation_window_days=14,
        outcome_maturity_window_days=14,
        enabled_stages=ACTIVATION_STAGES,
        enabled_risk_levels=(),
        confidence_requirement=0.95,
        maximum_reversal_rate_upper=0.1,
        maximum_late_critical_rate_upper=0.1,
        maximum_escape_rate_upper=0.1,
        activation_escape_cause_ids=(
            "deterministic_gate_gap",
            "evidence_visibility_gap",
            "risk_classification_gap",
        ),
        attribution_policy_digest=attribution_policy.policy_digest,
        required_isolation_platforms=("linux", "macos", "windows"),
        trusted_evidence_workflow_paths=(
            ".github/workflows/activation-evidence.yml",
        ),
        evidence_predicate_type="https://slsa.dev/provenance/v1",
        evidence_purpose="stage-gate-activation",
    )


def advance_activation_policy(
    policy: StageGateActivationPolicy,
    assessment: ActivationAssessment,
    *,
    grandfathered_loop_ids: tuple[str, ...] = (),
) -> StageGateActivationPolicy | None:
    trusted_policy = _trusted_policy(policy)
    trusted_assessment = ActivationAssessment.model_validate(
        assessment.model_dump(mode="json")
    )
    if trusted_assessment.policy_digest != trusted_policy.policy_digest:
        raise ValueError("activation assessment policy digest mismatch")
    if not trusted_assessment.eligible or trusted_policy.active_phase == 4:
        return None
    return _promoted_policy(
        trusted_policy,
        trusted_assessment,
        grandfathered_loop_ids,
    )


def resolve_gate_applicability(
    *,
    policy: StageGateActivationPolicy,
    stage_key: str,
    risk_level: str,
    loop_id: str,
    loop_created_at: str,
    gate_contract_version: str,
) -> GateApplicabilityDecision:
    trusted = _trusted_policy(policy)
    parse_utc(loop_created_at)
    require_version(gate_contract_version)
    stage_enabled = stage_key in trusted.enabled_stages
    risk_enabled = risk_level in trusted.enabled_risk_levels
    mode, reason = _applicability_mode(
        trusted,
        stage_enabled=stage_enabled,
        risk_level=risk_level,
        risk_enabled=risk_enabled,
        loop_id=loop_id,
        loop_created_at=loop_created_at,
        gate_contract_version=gate_contract_version,
    )
    return GateApplicabilityDecision(
        decision_id=stable_id(
            "gate-applicability-decision",
            trusted.policy_digest,
            stage_key,
            risk_level,
            loop_id,
        ),
        gate_id="stage-close-authorizer",
        stage_key=stage_key,
        loop_id=loop_id,
        mode=mode,
        policy_id=trusted.policy_id,
        policy_version=trusted.policy_version,
        policy_digest=trusted.policy_digest,
        reason_code=reason,
    )


def _promoted_policy(
    current: StageGateActivationPolicy,
    assessment: ActivationAssessment,
    grandfathered_loop_ids: tuple[str, ...],
) -> StageGateActivationPolicy:
    next_phase = current.active_phase + 1
    return StageGateActivationPolicy(
        policy_id=current.policy_id,
        policy_version=_next_minor(current.policy_version),
        active_phase=next_phase,
        effective_at=assessment.assessed_at,
        gate_contract_version=current.gate_contract_version,
        sample_size=_sample_size_for_phase(next_phase),
        observation_window_days=14 if next_phase < 4 else 0,
        outcome_maturity_window_days=current.outcome_maturity_window_days,
        enabled_stages=ACTIVATION_STAGES,
        enabled_risk_levels=_enabled_risks(next_phase),
        confidence_requirement=current.confidence_requirement,
        maximum_reversal_events=current.maximum_reversal_events,
        maximum_late_critical_events=current.maximum_late_critical_events,
        maximum_escape_events=current.maximum_escape_events,
        maximum_reversal_rate_upper=current.maximum_reversal_rate_upper,
        maximum_late_critical_rate_upper=current.maximum_late_critical_rate_upper,
        maximum_escape_rate_upper=current.maximum_escape_rate_upper,
        activation_escape_cause_ids=current.activation_escape_cause_ids,
        attribution_policy_digest=current.attribution_policy_digest,
        required_isolation_platforms=current.required_isolation_platforms,
        trusted_evidence_workflow_paths=current.trusted_evidence_workflow_paths,
        evidence_predicate_type=current.evidence_predicate_type,
        evidence_purpose=current.evidence_purpose,
        grandfathered_loop_ids=grandfathered_loop_ids,
        offline_optimization_enabled=next_phase == 4,
        previous_policy_digest=current.policy_digest,
        activation_assessment_digest=assessment.assessment_digest,
    )


def _trusted_policy(policy: StageGateActivationPolicy) -> StageGateActivationPolicy:
    return StageGateActivationPolicy.model_validate(policy.model_dump(mode="json"))


def _sample_size_for_phase(phase: int) -> ActivationSampleSize:
    if phase == 1:
        return ActivationSampleSize(
            minimum_total_shadow_sessions=50,
            minimum_shadow_sessions_per_stage=10,
        )
    if phase == 2:
        return ActivationSampleSize(
            minimum_total_enforce_sessions=30,
            minimum_shadow_sessions_per_new_combination=20,
        )
    if phase == 3:
        return ActivationSampleSize(minimum_enforce_sessions_per_new_combination=10)
    return ActivationSampleSize()


def _enabled_risks(phase: int) -> tuple[RiskLevel, ...]:
    if phase == 1:
        return ()
    if phase == 2:
        return ("low",)
    return ACTIVATION_RISKS


def _applicability_mode(
    policy: StageGateActivationPolicy,
    *,
    stage_enabled: bool,
    risk_level: str,
    risk_enabled: bool,
    loop_id: str,
    loop_created_at: str,
    gate_contract_version: str,
) -> tuple[Literal["shadow", "enforce", "grandfathered"], str]:
    if not stage_enabled:
        return "shadow", "combination-not-enabled"
    if policy.active_phase > 1 and risk_level not in ACTIVATION_RISKS:
        return "enforce", "active-stage-risk-classification-required"
    if not risk_enabled:
        return "shadow", "combination-not-enabled"
    old_loop = parse_utc(loop_created_at) < parse_utc(policy.effective_at)
    if old_loop and loop_id in policy.grandfathered_loop_ids:
        return "grandfathered", "explicit-policy-grandfathering"
    if gate_contract_version != policy.gate_contract_version:
        return "enforce", "eligible-combination-contract-upgrade-required"
    return "enforce", "eligible-combination"


def _next_minor(value: str) -> str:
    major, minor, _patch = (int(item) for item in value.split("."))
    return f"{major}.{minor + 1}.0"
