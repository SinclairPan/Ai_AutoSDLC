from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from ai_sdlc.core.stage_review.activation import (
    ActivationEvidence,
    ActivationProbeEvidence,
    ActivationSessionObservation,
    ActivationSessionOutcome,
    IsolationPlatformEvidence,
    advance_activation_policy,
    assess_activation,
    baseline_activation_policy,
    resolve_gate_applicability,
)

STAGES = (
    "requirement",
    "design-contract",
    "implementation",
    "frontend-evidence",
    "local-pr-review",
)


def test_baseline_policy_is_phase_one_shadow_only() -> None:
    policy = baseline_activation_policy()

    assert policy.active_phase == 1
    assert policy.enabled_risk_levels == ()
    assert policy.offline_optimization_enabled is False
    assert policy.sample_size.minimum_total_shadow_sessions == 50
    assert policy.observation_window_days == 14
    assert policy.confidence_requirement == 0.95
    assert policy.required_isolation_platforms == ("linux", "macos", "windows")


def test_phase_one_cannot_advance_without_full_isolation_matrix() -> None:
    policy = baseline_activation_policy()
    evidence = _phase_one_evidence(isolation_level="detected_only")

    assessment = assess_activation(policy, evidence)

    assert assessment.eligible is False
    assert "isolation_matrix" in assessment.failed_guards
    assert advance_activation_policy(policy, assessment) is None


def test_phase_one_accepts_verified_precommand_refusal_on_unavailable_platform() -> (
    None
):
    policy = baseline_activation_policy()
    evidence = _phase_one_evidence(windows_unavailable=True)

    assessment = assess_activation(policy, evidence)

    assert assessment.eligible is True
    assert "isolation_matrix" not in assessment.failed_guards


def test_phase_one_cannot_advance_with_late_critical_evidence() -> None:
    policy = baseline_activation_policy()
    evidence = _with_outcome(
        _phase_one_evidence(),
        0,
        late_critical=True,
    )

    assessment = assess_activation(policy, evidence)

    assert assessment.eligible is False
    assert "late_critical_rate" in assessment.failed_guards


def test_phase_one_cannot_advance_with_reversal_evidence() -> None:
    policy = baseline_activation_policy()
    evidence = _with_outcome(
        _phase_one_evidence(),
        0,
        reversal=True,
    )

    assessment = assess_activation(policy, evidence)

    assert assessment.eligible is False
    assert "reversal_rate" in assessment.failed_guards


def test_phase_one_cannot_advance_with_escape_evidence() -> None:
    policy = baseline_activation_policy()
    evidence = _with_outcome(
        _phase_one_evidence(),
        0,
        escape=True,
    )

    assessment = assess_activation(policy, evidence)

    assert assessment.eligible is False
    assert "escape_rate" in assessment.failed_guards


def test_low_sample_zero_events_cannot_bypass_confidence_gate() -> None:
    policy_payload = baseline_activation_policy().model_dump(
        mode="json",
        exclude={"policy_digest"},
    )
    policy_payload["sample_size"] = {
        "minimum_total_shadow_sessions": 0,
        "minimum_shadow_sessions_per_stage": 0,
        "minimum_total_enforce_sessions": 0,
        "minimum_shadow_sessions_per_new_combination": 0,
        "minimum_enforce_sessions_per_new_combination": 0,
    }
    policy_payload["observation_window_days"] = 0
    policy = type(baseline_activation_policy()).model_validate(policy_payload)
    complete = _phase_one_evidence()
    payload = complete.model_dump(mode="json", exclude={"evidence_digest"})
    payload["sessions"] = payload["sessions"][:3]
    payload["session_record_digests"] = payload["session_record_digests"][:3]
    payload["session_outcomes"] = payload["session_outcomes"][:3]

    assessment = assess_activation(policy, ActivationEvidence.model_validate(payload))

    assert assessment.eligible is False
    assert {
        "reversal_confidence",
        "late_critical_confidence",
        "escape_confidence",
    } <= set(assessment.failed_guards)
    assert all(interval.upper > 0.5 for interval in assessment.quality_intervals)


def test_phase_one_advances_to_low_risk_enforce_from_frozen_evidence() -> None:
    policy = baseline_activation_policy()
    assessment = assess_activation(policy, _phase_one_evidence())

    promoted = advance_activation_policy(policy, assessment)

    assert assessment.eligible is True
    assert promoted is not None
    assert promoted.active_phase == 2
    assert promoted.enabled_stages == tuple(sorted(STAGES))
    assert promoted.enabled_risk_levels == ("low",)
    assert promoted.effective_at == assessment.assessed_at
    assert promoted.previous_policy_digest == policy.policy_digest


def test_applicability_is_recomputed_from_policy_not_local_mode() -> None:
    baseline = baseline_activation_policy()
    phase_two = advance_activation_policy(
        baseline,
        assess_activation(baseline, _phase_one_evidence()),
    )
    assert phase_two is not None

    enforce = resolve_gate_applicability(
        policy=phase_two,
        stage_key="implementation",
        risk_level="low",
        loop_id="loop.new",
        loop_created_at=_after(phase_two.effective_at),
        gate_contract_version=phase_two.gate_contract_version,
    )
    shadow = resolve_gate_applicability(
        policy=phase_two,
        stage_key="implementation",
        risk_level="high",
        loop_id="loop.high",
        loop_created_at=_after(phase_two.effective_at),
        gate_contract_version=phase_two.gate_contract_version,
    )

    assert enforce.mode == "enforce"
    assert shadow.mode == "shadow"
    assert enforce.policy_digest == phase_two.policy_digest


def test_grandfathering_requires_explicit_policy_coverage() -> None:
    baseline = baseline_activation_policy()
    phase_two = advance_activation_policy(
        baseline,
        assess_activation(baseline, _phase_one_evidence()),
        grandfathered_loop_ids=("loop.explicit",),
    )
    assert phase_two is not None
    old = _before(phase_two.effective_at)

    grandfathered = resolve_gate_applicability(
        policy=phase_two,
        stage_key="implementation",
        risk_level="low",
        loop_id="loop.explicit",
        loop_created_at=old,
        gate_contract_version="0.9.0",
    )
    not_exempt = resolve_gate_applicability(
        policy=phase_two,
        stage_key="implementation",
        risk_level="low",
        loop_id="loop.unlisted",
        loop_created_at=old,
        gate_contract_version="0.9.0",
    )

    assert grandfathered.mode == "grandfathered"
    assert not_exempt.mode == "enforce"
    assert not_exempt.reason_code == "eligible-combination-contract-upgrade-required"


def test_unclassified_risk_cannot_bypass_an_active_stage() -> None:
    baseline = baseline_activation_policy()
    phase_two = advance_activation_policy(
        baseline,
        assess_activation(baseline, _phase_one_evidence()),
    )
    assert phase_two is not None

    decision = resolve_gate_applicability(
        policy=phase_two,
        stage_key="implementation",
        risk_level="unclassified",
        loop_id="loop.unclassified",
        loop_created_at=_after(phase_two.effective_at),
        gate_contract_version=phase_two.gate_contract_version,
    )

    assert decision.mode == "enforce"
    assert decision.reason_code == "active-stage-risk-classification-required"


def test_assessment_cannot_advance_a_different_policy() -> None:
    policy = baseline_activation_policy()
    assessment = assess_activation(policy, _phase_one_evidence())
    changed = policy.model_copy(update={"policy_version": "1.0.1"})

    with pytest.raises(ValueError, match="policy_digest"):
        advance_activation_policy(changed, assessment)


def test_assessment_identity_binds_the_exact_evidence() -> None:
    policy = baseline_activation_policy()
    first = _phase_one_evidence()
    second = _with_probes(
        first,
        _passing_probes().model_copy(
            update={"planner_benchmark_p95_seconds": 0.75}
        ),
    )

    first_assessment = assess_activation(policy, first)
    second_assessment = assess_activation(policy, second)

    assert first.evidence_digest != second.evidence_digest
    assert first_assessment.assessment_id != second_assessment.assessment_id
    assert first_assessment.evidence_digest == first.evidence_digest


def test_duplicate_isolation_platform_evidence_is_rejected() -> None:
    evidence = _phase_one_evidence()

    with pytest.raises(ValueError, match="duplicate isolation platform"):
        ActivationEvidence(
            project_id=evidence.project_id,
            assessed_at=evidence.assessed_at,
            sessions=evidence.sessions,
            session_record_digests=evidence.session_record_digests,
            isolation_matrix=(evidence.isolation_matrix[0],) * 2,
            isolation_record_digests=evidence.isolation_record_digests,
            probes=evidence.probes,
            probe_record_digest=evidence.probe_record_digest,
            session_outcomes=evidence.session_outcomes,
        )


def _phase_one_evidence(
    *,
    isolation_level: str = "enforced",
    windows_unavailable: bool = False,
) -> ActivationEvidence:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    observations = tuple(
        ActivationSessionObservation(
            session_id=f"session.{stage}.{index}",
            stage_key=stage,
            risk_level="low",
            mode="shadow",
            completed_at=(
                start + timedelta(days=14 if index == 9 else index)
            ).isoformat(),
        )
        for stage in STAGES
        for index in range(10)
    )
    isolation = tuple(
        IsolationPlatformEvidence(
            platform_id=platform,
            isolation_level=(
                "unproven"
                if platform == "windows" and windows_unavailable
                else isolation_level
            ),
            candidate_write_blocked=(
                isolation_level == "enforced"
                and not (platform == "windows" and windows_unavailable)
            ),
            sibling_write_blocked=(
                isolation_level == "enforced"
                and not (platform == "windows" and windows_unavailable)
            ),
            home_write_blocked=(
                isolation_level == "enforced"
                and not (platform == "windows" and windows_unavailable)
            ),
            network_blocked=(
                isolation_level == "enforced"
                and not (platform == "windows" and windows_unavailable)
            ),
            provider_command_blocked=(
                platform == "windows" and windows_unavailable
            ),
            evidence_digest=_source_digest(f"platform:{platform}"),
        )
        for platform in ("linux", "macos", "windows")
    )
    session_digests = tuple(
        _source_digest(f"session:{item.session_id}") for item in observations
    )
    assessed_at = (start + timedelta(days=29)).isoformat()
    return ActivationEvidence(
        project_id="project.activation-policy-test",
        assessed_at=assessed_at,
        sessions=observations,
        session_record_digests=session_digests,
        isolation_matrix=isolation,
        isolation_record_digests=tuple(
            _source_digest(f"isolation:{item.platform_id}") for item in isolation
        ),
        probes=_passing_probes(),
        probe_record_digest=_source_digest("probes:passing"),
        session_outcomes=tuple(
            ActivationSessionOutcome(
                session_id=item.session_id,
                session_record_digest=record_digest,
                status="complete",
                had_reversal=False,
                had_late_critical=False,
                had_escape=False,
                finalized_at=assessed_at,
                observation_cutoff=(
                    datetime.fromisoformat(item.completed_at) + timedelta(days=14)
                ).isoformat(),
                finding_chain_head_digest=_source_digest(
                    f"finding-head:{item.session_id}"
                ),
                attribution_set_digest=_source_digest(
                    f"attribution-set:{item.session_id}"
                ),
            )
            for item, record_digest in zip(
                observations,
                session_digests,
                strict=True,
            )
        ),
    )


def _passing_probes() -> ActivationProbeEvidence:
    return ActivationProbeEvidence(
        canonical_plan_replay_passed=True,
        certificate_integrity_passed=True,
        provider_billing_integrity_passed=True,
        crash_recovery_passed=True,
        hard_budget_integrity_passed=True,
        clean_user_e2e_passed=True,
        planner_benchmark_p95_seconds=0.5,
        work_item_fencing_passed=True,
        hard_constraint_integrity_passed=True,
        non_waivable_integrity_passed=True,
        platform_count=3,
        probe_trial_count=30,
    )


def _with_probes(
    evidence: ActivationEvidence,
    probes: ActivationProbeEvidence,
) -> ActivationEvidence:
    payload = evidence.model_dump(mode="json", exclude={"evidence_digest"})
    payload["probes"] = probes.model_dump(mode="json")
    payload["probe_record_digest"] = _source_digest(
        f"probes:{probes.model_dump_json()}"
    )
    return ActivationEvidence.model_validate(payload)


def _with_outcome(
    evidence: ActivationEvidence,
    index: int,
    *,
    reversal: bool = False,
    late_critical: bool = False,
    escape: bool = False,
) -> ActivationEvidence:
    payload = evidence.model_dump(mode="json", exclude={"evidence_digest"})
    current = payload["session_outcomes"][index]
    current["had_reversal"] = reversal
    current["had_late_critical"] = late_critical
    current["had_escape"] = escape
    current["finding_event_digests"] = (
        [_source_digest(f"finding:{index}")]
        if reversal or late_critical
        else []
    )
    current["attribution_decision_digests"] = (
        [_source_digest(f"escape:{index}")] if escape else []
    )
    current["product_defect_signal_digests"] = (
        [_source_digest(f"escape-signal:{index}")] if escape else []
    )
    current["attribution_set_digest"] = _source_digest(
        f"attribution-set:{index}:{escape}"
    )
    current["finding_chain_head_digest"] = _source_digest(
        f"finding-head:{index}:{reversal}:{late_critical}"
    )
    current["outcome_digest"] = ""
    return ActivationEvidence.model_validate(payload)


def _source_digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _after(value: str) -> str:
    return (datetime.fromisoformat(value) + timedelta(seconds=1)).isoformat()


def _before(value: str) -> str:
    return (datetime.fromisoformat(value) - timedelta(seconds=1)).isoformat()
