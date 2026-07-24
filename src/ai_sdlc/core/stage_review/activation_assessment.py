"""从不可变运行证据确定性评估下一激活阶段。"""

from __future__ import annotations

from math import sqrt

from ai_sdlc.core.stage_review.activation_models import (
    ACTIVATION_RISKS,
    ACTIVATION_STAGES,
    ActivationAssessment,
    ActivationEvidence,
    ActivationProbeEvidence,
    ActivationSessionObservation,
    ActivationSessionOutcome,
    IsolationPlatformEvidence,
    StageGateActivationPolicy,
    WilsonInterval,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id


def assess_activation(
    policy: StageGateActivationPolicy,
    evidence: ActivationEvidence,
) -> ActivationAssessment:
    trusted_policy = StageGateActivationPolicy.model_validate(
        policy.model_dump(mode="json")
    )
    trusted_evidence = ActivationEvidence.model_validate(
        evidence.model_dump(mode="json")
    )
    intervals = _quality_intervals(
        trusted_policy,
        trusted_evidence.session_outcomes,
    )
    failures = _failed_activation_guards(
        trusted_policy,
        trusted_evidence,
        intervals,
    )
    return ActivationAssessment(
        assessment_id=stable_id(
            "stage-gate-activation-assessment",
            trusted_policy.policy_digest,
            trusted_evidence.evidence_digest,
        ),
        policy_digest=trusted_policy.policy_digest,
        evidence_digest=trusted_evidence.evidence_digest,
        assessed_at=trusted_evidence.assessed_at,
        eligible=not failures,
        failed_guards=failures,
        quality_intervals=intervals,
    )


def _failed_activation_guards(
    policy: StageGateActivationPolicy,
    evidence: ActivationEvidence,
    intervals: tuple[WilsonInterval, ...],
) -> tuple[str, ...]:
    guards = _quality_guards(
        policy,
        evidence.probes,
        evidence.session_outcomes,
        intervals,
    )
    guards["outcome_completeness"] = all(
        item.status == "complete" for item in evidence.session_outcomes
    )
    guards["isolation_matrix"] = _isolation_ready(policy, evidence)
    guards.update(_sample_guards(policy, evidence))
    return tuple(sorted(name for name, passed in guards.items() if not passed))


def _quality_guards(
    policy: StageGateActivationPolicy,
    value: ActivationProbeEvidence,
    outcomes: tuple[ActivationSessionOutcome, ...],
    intervals: tuple[WilsonInterval, ...],
) -> dict[str, bool]:
    events = _outcome_counts(
        tuple(item for item in outcomes if item.status == "complete")
    )
    interval_by_metric = {item.metric_id: item for item in intervals}
    return {
        "canonical_plan_replay": value.canonical_plan_replay_passed,
        "certificate_integrity": value.certificate_integrity_passed,
        "provider_billing_integrity": value.provider_billing_integrity_passed,
        "crash_recovery": value.crash_recovery_passed,
        "hard_budget_integrity": value.hard_budget_integrity_passed,
        "clean_user_e2e": value.clean_user_e2e_passed,
        "planner_latency": value.planner_benchmark_p95_seconds <= 1.0,
        "work_item_fencing": value.work_item_fencing_passed,
        "hard_constraint_integrity": value.hard_constraint_integrity_passed,
        "non_waivable_integrity": value.non_waivable_integrity_passed,
        "reversal_rate": events["reversal"] <= policy.maximum_reversal_events,
        "late_critical_rate": events["late-critical"]
        <= policy.maximum_late_critical_events,
        "escape_rate": events["escape"] <= policy.maximum_escape_events,
        "reversal_confidence": interval_by_metric["reversal"].upper
        <= policy.maximum_reversal_rate_upper,
        "late_critical_confidence": interval_by_metric["late-critical"].upper
        <= policy.maximum_late_critical_rate_upper,
        "escape_confidence": interval_by_metric["escape"].upper
        <= policy.maximum_escape_rate_upper,
    }


def _isolation_ready(
    policy: StageGateActivationPolicy,
    evidence: ActivationEvidence,
) -> bool:
    by_platform = {item.platform_id: item for item in evidence.isolation_matrix}
    return all(
        _platform_is_safe(by_platform.get(platform))
        for platform in policy.required_isolation_platforms
    )


def _platform_is_safe(value: IsolationPlatformEvidence | None) -> bool:
    if value is None:
        return False
    if value.isolation_level == "enforced":
        return all(
            (
                value.candidate_write_blocked,
                value.sibling_write_blocked,
                value.home_write_blocked,
                value.network_blocked,
            )
        )
    return (
        value.isolation_level == "unproven"
        and value.provider_command_blocked
        and not any(
            (
                value.candidate_write_blocked,
                value.sibling_write_blocked,
                value.home_write_blocked,
                value.network_blocked,
            )
        )
    )


def _sample_guards(
    policy: StageGateActivationPolicy,
    evidence: ActivationEvidence,
) -> dict[str, bool]:
    sample = policy.sample_size
    shadow = tuple(item for item in evidence.sessions if item.mode == "shadow")
    enforce = tuple(item for item in evidence.sessions if item.mode == "enforce")
    return {
        "total_shadow_sample": len(shadow) >= sample.minimum_total_shadow_sessions,
        "total_enforce_sample": _low_risk_count(enforce)
        >= sample.minimum_total_enforce_sessions,
        "shadow_stage_sample": _per_stage_ready(
            shadow, sample.minimum_shadow_sessions_per_stage
        ),
        "new_combination_shadow_sample": _new_combinations_ready(
            shadow, sample.minimum_shadow_sessions_per_new_combination
        ),
        "new_combination_enforce_sample": _new_combinations_ready(
            enforce, sample.minimum_enforce_sessions_per_new_combination
        ),
        "observation_window": _window_ready(policy, evidence),
    }


def _per_stage_ready(
    sessions: tuple[ActivationSessionObservation, ...], minimum: int
) -> bool:
    if minimum == 0:
        return True
    return all(
        sum(item.stage_key == stage for item in sessions) >= minimum
        for stage in ACTIVATION_STAGES
    )


def _new_combinations_ready(
    sessions: tuple[ActivationSessionObservation, ...], minimum: int
) -> bool:
    if minimum == 0:
        return True
    combinations = (
        (stage, risk) for stage in ACTIVATION_STAGES for risk in ACTIVATION_RISKS[1:]
    )
    return all(
        sum(item.stage_key == stage and item.risk_level == risk for item in sessions)
        >= minimum
        for stage, risk in combinations
    )


def _low_risk_count(sessions: tuple[ActivationSessionObservation, ...]) -> int:
    return sum(item.risk_level == "low" for item in sessions)


def _window_ready(
    policy: StageGateActivationPolicy,
    evidence: ActivationEvidence,
) -> bool:
    if policy.observation_window_days == 0:
        return True
    if not evidence.sessions:
        return False
    earliest = min(parse_utc(item.completed_at) for item in evidence.sessions)
    elapsed = parse_utc(evidence.assessed_at) - earliest
    return elapsed.total_seconds() >= policy.observation_window_days * 86400


def _quality_intervals(
    policy: StageGateActivationPolicy,
    outcomes: tuple[ActivationSessionOutcome, ...],
) -> tuple[WilsonInterval, ...]:
    complete = tuple(item for item in outcomes if item.status == "complete")
    events = _outcome_counts(complete)
    return tuple(
        _wilson_interval(
            metric,
            count,
            len(complete),
            policy.confidence_requirement,
        )
        for metric, count in events.items()
    )


def _outcome_counts(
    outcomes: tuple[ActivationSessionOutcome, ...],
) -> dict[str, int]:
    return {
        "reversal": sum(item.had_reversal for item in outcomes),
        "late-critical": sum(item.had_late_critical for item in outcomes),
        "escape": sum(item.had_escape for item in outcomes),
    }


def _wilson_interval(
    metric_id: str, events: int, trials: int, confidence: float
) -> WilsonInterval:
    if trials == 0:
        return WilsonInterval(
            metric_id=metric_id,
            events=0,
            trials=0,
            confidence=confidence,
            lower=0,
            upper=1,
        )
    z = _normal_z(confidence)
    ratio = events / trials
    denominator = 1 + z * z / trials
    center = (ratio + z * z / (2 * trials)) / denominator
    spread = z * sqrt((ratio * (1 - ratio) + z * z / (4 * trials)) / trials)
    return WilsonInterval(
        metric_id=metric_id,
        events=events,
        trials=trials,
        confidence=confidence,
        lower=max(0.0, center - spread / denominator),
        upper=min(1.0, center + spread / denominator),
    )


def _normal_z(confidence: float) -> float:
    if abs(confidence - 0.95) > 1e-12:
        raise ValueError("only the governed 95% confidence requirement is supported")
    return 1.959963984540054
