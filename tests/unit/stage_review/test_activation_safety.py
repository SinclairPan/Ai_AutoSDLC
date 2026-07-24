from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_sdlc.core.stage_review import (
    activation_evidence_runtime,
    activation_fence,
    activation_policy_store,
)
from ai_sdlc.core.stage_review.activation_fence import (
    activation_safety_mutation_fence,
    activation_safety_read_lease,
)
from ai_sdlc.core.stage_review.activation_models import (
    ActivationAssessment,
    ActivationEvidence,
    ActivationProbeEvidence,
    ActivationSafetyHold,
    ActivationSafetyRecoverySample,
    ActivationSessionObservation,
    ActivationSessionOutcome,
    IsolationPlatformEvidence,
)
from ai_sdlc.core.stage_review.activation_outcomes import (
    activation_recovery_session_records,
)
from ai_sdlc.core.stage_review.activation_policy import baseline_activation_policy
from ai_sdlc.core.stage_review.activation_safety import (
    activation_evaluation_cohort,
    active_activation_safety_holds,
    active_activation_safety_holds_for_lineage,
    affected_activation_safety_holds,
    persist_activation_safety_hold,
    record_activation_safety_recovery,
    release_eligible_activation_safety_holds,
)
from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    atomic_write_json,
    resolve_canonical_shared_state,
    resolve_repository_project_id,
)
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.review_completion import ReviewSessionCompletion
from ai_sdlc.core.stage_review.session_paths import (
    _session_scope_root as session_scope_root,
)


def test_activation_fence_supports_read_upgrade_and_nested_mutation(
    tmp_path: Path,
) -> None:
    project_id = resolve_repository_project_id(tmp_path)

    with activation_safety_read_lease(tmp_path, project_id), pytest.raises(
        ResourceLockUnavailableError,
        match="cannot upgrade an active read lease",
    ), activation_safety_mutation_fence(tmp_path, project_id):
        pass
    with (
        activation_safety_mutation_fence(tmp_path, project_id),
        activation_safety_mutation_fence(tmp_path, project_id),
    ):
        pass


def test_activation_fence_clears_reused_pid_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = resolve_repository_project_id(tmp_path)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    owner = shared / "activation-safety-fence/writer-intent.lock"
    atomic_write_json(
        owner,
        {
            "pid": 4242,
            "thread_id": 7,
            "thread_token": "thread.old",
            "started_at": 1.0,
            "process_start": "ps:old",
        },
    )
    monkeypatch.setattr(activation_fence, "_clear_dead_owner", lambda _path: False)
    monkeypatch.setattr(
        activation_fence,
        "_process_start_identity",
        lambda _pid: "ps:new",
    )

    assert activation_fence._clear_stale_owner(owner) is True  # noqa: SLF001
    assert not owner.exists()


def test_activation_fence_keeps_live_owner_when_start_identity_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = resolve_repository_project_id(tmp_path)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    owner = shared / "activation-safety-fence/writer-intent.lock"
    atomic_write_json(
        owner,
        {
            "pid": 4242,
            "thread_id": 7,
            "thread_token": "thread.live",
            "started_at": 1.0,
            "process_start": "ps:known",
        },
    )
    monkeypatch.setattr(activation_fence, "_clear_dead_owner", lambda _path: False)
    monkeypatch.setattr(
        activation_fence,
        "_process_start_identity",
        lambda _pid: None,
    )

    assert activation_fence._clear_stale_owner(owner) is False  # noqa: SLF001
    assert owner.is_file()


def test_bad_post_promotion_outcome_creates_one_precisely_scoped_hold(
    tmp_path: Path,
) -> None:
    policy = baseline_activation_policy().model_copy(
        update={
            "active_phase": 2,
            "enabled_risk_levels": ("low",),
            "previous_policy_digest": _digest("phase-1"),
            "activation_assessment_digest": _digest("phase-1-assessment"),
        }
    )
    project_id = resolve_repository_project_id(tmp_path)
    evidence, assessment = _bad_evidence(project_id, policy.policy_digest)

    first = persist_activation_safety_hold(
        tmp_path,
        policy=policy,
        evidence=evidence,
        assessment=assessment,
    )
    second = persist_activation_safety_hold(
        tmp_path,
        policy=policy,
        evidence=evidence,
        assessment=assessment,
    )

    assert len(first) == 1
    assert second == first
    assert tuple(
        (item.stage_key, item.risk_level)
        for item in first[0].affected_combinations
    ) == (("implementation", "low"),)
    assert active_activation_safety_holds(
        tmp_path,
        policy_digest=policy.policy_digest,
    ) == first


def test_bad_outcomes_create_independent_holds_per_stage_and_risk(
    tmp_path: Path,
) -> None:
    policy = _phase_two_policy()
    project_id = resolve_repository_project_id(tmp_path)
    evidence, assessment = _bad_evidence(project_id, policy.policy_digest)
    second_observation = evidence.sessions[0].model_copy(
        update={
            "session_id": "session.bad.design",
            "stage_key": "design-contract",
            "risk_level": "medium",
        }
    )
    second_outcome = ActivationSessionOutcome.model_validate(
        {
            **evidence.session_outcomes[0].model_dump(
                mode="json",
                exclude={"outcome_digest"},
            ),
            "session_id": second_observation.session_id,
            "session_record_digest": _digest("record.design"),
            "finding_chain_head_digest": _digest("finding-chain.design"),
            "attribution_set_digest": _digest("attributions.design"),
            "finding_event_digests": (_digest("reversal.design"),),
        }
    )
    evidence = ActivationEvidence.model_validate(
        {
            **evidence.model_dump(mode="json", exclude={"evidence_digest"}),
            "sessions": (*evidence.sessions, second_observation),
            "session_record_digests": (
                *evidence.session_record_digests,
                second_outcome.session_record_digest,
            ),
            "session_outcomes": (*evidence.session_outcomes, second_outcome),
        }
    )
    assessment = ActivationAssessment.model_validate(
        {
            **assessment.model_dump(
                mode="json",
                exclude={"assessment_digest"},
            ),
            "evidence_digest": evidence.evidence_digest,
        }
    )

    holds = persist_activation_safety_hold(
        tmp_path,
        policy=policy,
        evidence=evidence,
        assessment=assessment,
    )

    assert len(holds) == 2
    assert {
        tuple(
            (item.stage_key, item.risk_level)
            for item in hold.affected_combinations
        )
        for hold in holds
    } == {
        (("implementation", "low"),),
        (("design-contract", "medium"),),
    }


def test_hold_releases_only_after_unique_independent_samples_and_window(
    tmp_path: Path,
) -> None:
    project_id = resolve_repository_project_id(tmp_path)
    started = datetime(2026, 7, 1, tzinfo=UTC)
    policy = _phase_two_policy()
    hold = _hold(project_id, started, policy)
    _persist_hold(tmp_path, hold)
    first = _recovery_sample(
        tmp_path,
        hold,
        "session.recovery.1",
        started + timedelta(days=1),
    )
    second = _recovery_sample(
        tmp_path,
        hold,
        "session.recovery.2",
        started + timedelta(days=2),
    )
    record_activation_safety_recovery(tmp_path, first)
    record_activation_safety_recovery(tmp_path, first)

    assert (
        release_eligible_activation_safety_holds(
            tmp_path,
            policy=policy,
            assessed_at=(started + timedelta(days=16)).isoformat(),
        )
        == ()
    )

    record_activation_safety_recovery(tmp_path, second)
    assert (
        release_eligible_activation_safety_holds(
            tmp_path,
            policy=policy,
            assessed_at=(started + timedelta(days=13)).isoformat(),
        )
        == ()
    )

    releases = release_eligible_activation_safety_holds(
        tmp_path,
        policy=policy,
        assessed_at=(started + timedelta(days=17)).isoformat(),
    )

    assert len(releases) == 1
    assert releases[0].hold_digest == hold.hold_digest
    assert set(releases[0].recovery_sample_digests) == {
        first.sample_digest,
        second.sample_digest,
    }
    assert (
        active_activation_safety_holds(
            tmp_path,
            policy_digest=hold.policy_digest,
        )
        == ()
    )


def test_release_starts_a_new_versioned_activation_evaluation_cohort(
    tmp_path: Path,
) -> None:
    project_id = resolve_repository_project_id(tmp_path)
    started = datetime(2026, 7, 1, tzinfo=UTC)
    policy = _phase_two_policy()
    hold = _hold(project_id, started, policy)
    _persist_hold(tmp_path, hold)
    samples = tuple(
        _recovery_sample(
            tmp_path,
            hold,
            f"session.cohort.{index}",
            started + timedelta(days=index),
        )
        for index in (1, 2)
    )
    for sample in samples:
        record_activation_safety_recovery(tmp_path, sample)
    release = release_eligible_activation_safety_holds(
        tmp_path,
        policy=policy,
        assessed_at=(started + timedelta(days=17)).isoformat(),
    )[0]
    historical = activation_recovery_session_records(samples)
    fresh_sample = _recovery_sample(
        tmp_path,
        hold,
        "session.cohort.fresh",
        started + timedelta(days=18),
    )
    fresh = activation_recovery_session_records((fresh_sample,))

    selected, boundaries = activation_evaluation_cohort(
        tmp_path,
        historical + fresh,
        policy=policy,
    )

    assert selected == fresh
    assert len(boundaries) == 1
    assert boundaries[0].release_digest == release.release_digest
    assert boundaries[0].hold_digest == hold.hold_digest
    assert boundaries[0].released_at == release.released_at


def test_evaluation_cohort_boundary_follows_policy_lineage(
    tmp_path: Path,
) -> None:
    from ai_sdlc.core.stage_review.activation_policy import advance_activation_policy

    project_id = resolve_repository_project_id(tmp_path)
    started = datetime(2026, 7, 1, tzinfo=UTC)
    phase_two = _phase_two_policy()
    hold = _hold(project_id, started, phase_two)
    _persist_hold(tmp_path, hold)
    samples = tuple(
        _recovery_sample(
            tmp_path,
            hold,
            f"session.lineage.{index}",
            started + timedelta(days=index),
        )
        for index in (1, 2)
    )
    for sample in samples:
        record_activation_safety_recovery(tmp_path, sample)
    release_eligible_activation_safety_holds(
        tmp_path,
        policy=phase_two,
        assessed_at=(started + timedelta(days=17)).isoformat(),
    )
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    atomic_write_json(
        shared / "activation/policies/phase-two.json",
        phase_two.model_dump(mode="json"),
    )
    assessment = ActivationAssessment(
        assessment_id="assessment.phase-two-lineage",
        policy_digest=phase_two.policy_digest,
        evidence_digest=_digest("phase-two-lineage-evidence"),
        assessed_at=(started + timedelta(days=18)).isoformat(),
        eligible=True,
        failed_guards=(),
        quality_intervals=(),
    )
    phase_three = advance_activation_policy(phase_two, assessment)
    assert phase_three is not None
    historical = activation_recovery_session_records(samples)

    selected, boundaries = activation_evaluation_cohort(
        tmp_path,
        historical,
        policy=phase_three,
    )

    assert selected == ()
    assert len(boundaries) == 1
    assert boundaries[0].policy_digest == phase_two.policy_digest


def test_ancestor_regression_hold_blocks_and_recovers_under_current_policy(
    tmp_path: Path,
) -> None:
    from ai_sdlc.core.stage_review.activation_policy import advance_activation_policy

    project_id = resolve_repository_project_id(tmp_path)
    started = datetime(2026, 7, 1, tzinfo=UTC)
    phase_two = _phase_two_policy()
    original = _hold(project_id, started, phase_two)
    _persist_hold(tmp_path, original)
    original_samples = tuple(
        _recovery_sample(
            tmp_path,
            original,
            f"session.ancestor.original.{index}",
            started + timedelta(days=index),
        )
        for index in (1, 2)
    )
    for sample in original_samples:
        record_activation_safety_recovery(tmp_path, sample)
    release_eligible_activation_safety_holds(
        tmp_path,
        policy=phase_two,
        assessed_at=(started + timedelta(days=17)).isoformat(),
    )
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    atomic_write_json(
        shared / "activation/policies/phase-two.json",
        phase_two.model_dump(mode="json"),
    )
    assessment = ActivationAssessment(
        assessment_id="assessment.ancestor-phase-two",
        policy_digest=phase_two.policy_digest,
        evidence_digest=_digest("ancestor-phase-two-evidence"),
        assessed_at=(started + timedelta(days=18)).isoformat(),
        eligible=True,
        failed_guards=(),
        quality_intervals=(),
    )
    phase_three = advance_activation_policy(phase_two, assessment)
    assert phase_three is not None
    regression = ActivationSafetyHold.model_validate(
        {
            **original.model_dump(mode="json", exclude={"hold_digest"}),
            "hold_id": "hold.ancestor-regression",
            "triggering_outcome_digests": (_digest("ancestor-regression"),),
            "created_at": (started + timedelta(days=20)).isoformat(),
            "recovery_not_before": (started + timedelta(days=34)).isoformat(),
        }
    )
    _persist_hold(tmp_path, regression)

    blocked = affected_activation_safety_holds(
        tmp_path,
        policy=phase_three,
        stage_key="implementation",
        risk_level="low",
        assessed_at=(started + timedelta(days=20)).isoformat(),
    )

    assert blocked == (regression,)
    assert active_activation_safety_holds_for_lineage(
        tmp_path,
        policy=phase_three,
    ) == (regression,)
    recovery_samples = tuple(
        _recovery_sample(
            tmp_path,
            regression,
            f"session.ancestor.recovery.{index}",
            started + timedelta(days=20 + index),
        )
        for index in (1, 2)
    )
    for sample in recovery_samples:
        record_activation_safety_recovery(tmp_path, sample)

    assert (
        affected_activation_safety_holds(
            tmp_path,
            policy=phase_three,
            stage_key="implementation",
            risk_level="low",
            assessed_at=(started + timedelta(days=40)).isoformat(),
        )
        == ()
    )


def test_recovery_sample_identity_ignores_candidate_replay(
    tmp_path: Path,
) -> None:
    project_id = resolve_repository_project_id(tmp_path)
    started = datetime(2026, 7, 1, tzinfo=UTC)
    policy = _phase_two_policy()
    hold = _hold(project_id, started, policy)
    _persist_hold(tmp_path, hold)
    first = _recovery_sample(
        tmp_path,
        hold,
        "session.recovery.1",
        started + timedelta(days=1),
    )
    replay = first.model_copy(
        update={
            "sample_id": "sample.candidate-replay",
            "candidate_manifest_digest": _digest("different-candidate"),
            "sample_digest": "",
        }
    )
    record_activation_safety_recovery(tmp_path, first)
    with pytest.raises(ValueError, match="completion lineage diverged"):
        record_activation_safety_recovery(tmp_path, replay)

    assert (
        release_eligible_activation_safety_holds(
            tmp_path,
            policy=policy,
            assessed_at=(started + timedelta(days=30)).isoformat(),
        )
        == ()
    )


def test_recovery_release_rederives_and_rejects_late_bad_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = resolve_repository_project_id(tmp_path)
    started = datetime(2026, 7, 1, tzinfo=UTC)
    policy = _phase_two_policy()
    hold = _hold(project_id, started, policy)
    _persist_hold(tmp_path, hold)
    samples = tuple(
        _recovery_sample(
            tmp_path,
            hold,
            f"session.recovery.{index}",
            started + timedelta(days=index),
        )
        for index in (1, 2)
    )
    for sample in samples:
        record_activation_safety_recovery(tmp_path, sample)
    monkeypatch.setattr(
        "ai_sdlc.core.stage_review.activation_safety."
        "derive_activation_recovery_session_outcomes",
        lambda *_args, **_kwargs: (
            SimpleNamespace(
                status="complete",
                had_reversal=False,
                had_late_critical=True,
                had_escape=False,
            ),
            SimpleNamespace(
                status="complete",
                had_reversal=False,
                had_late_critical=False,
                had_escape=False,
            ),
        ),
    )

    assert (
        release_eligible_activation_safety_holds(
            tmp_path,
            policy=policy,
            assessed_at=(started + timedelta(days=30)).isoformat(),
        )
        == ()
    )


def test_released_hold_reactivates_when_recovery_session_turns_bad(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = resolve_repository_project_id(tmp_path)
    started = datetime(2026, 7, 1, tzinfo=UTC)
    policy = _phase_two_policy()
    hold = _hold(project_id, started, policy)
    _persist_hold(tmp_path, hold)
    samples = tuple(
        _recovery_sample(
            tmp_path,
            hold,
            f"session.recovery.{index}",
            started + timedelta(days=index),
        )
        for index in (1, 2)
    )
    for sample in samples:
        record_activation_safety_recovery(tmp_path, sample)
    releases = release_eligible_activation_safety_holds(
        tmp_path,
        policy=policy,
        assessed_at=(started + timedelta(days=17)).isoformat(),
    )
    assert len(releases) == 1
    bad = _revalidated_outcome(
        samples[0],
        assessed_at=started + timedelta(days=30),
        late=True,
    )
    clean = _revalidated_outcome(
        samples[1],
        assessed_at=started + timedelta(days=30),
        late=False,
    )
    monkeypatch.setattr(
        "ai_sdlc.core.stage_review.activation_safety."
        "derive_activation_recovery_session_outcomes",
        lambda *_args, **_kwargs: (bad, clean),
    )

    affected = affected_activation_safety_holds(
        tmp_path,
        policy=policy,
        stage_key="implementation",
        risk_level="low",
        assessed_at=(started + timedelta(days=30)).isoformat(),
    )

    assert len(affected) == 1
    assert affected[0].hold_digest != hold.hold_digest
    assert affected[0].triggering_outcome_digests == (bad.outcome_digest,)


def test_ineligible_runtime_assessment_persists_hold_before_returning_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _phase_two_policy()
    project_id = resolve_repository_project_id(tmp_path)
    evidence, assessment = _bad_evidence(project_id, policy.policy_digest)
    monkeypatch.setattr(
        activation_policy_store,
        "_read_current_locked",
        lambda _paths: (policy, 1),
    )
    monkeypatch.setattr(
        activation_policy_store,
        "_mature_session_records",
        lambda *_args, **_kwargs: ((), ()),
    )
    monkeypatch.setattr(
        activation_policy_store,
        "_verify_evidence_sources",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        activation_policy_store,
        "assess_activation",
        lambda *_args, **_kwargs: assessment,
    )

    current, actual = activation_policy_store._advance_activation_policy_from_evidence(
        tmp_path,
        evidence,
    )

    assert current == policy
    assert actual == assessment
    assert (
        len(
            active_activation_safety_holds(
                tmp_path,
                policy_digest=policy.policy_digest,
            )
        )
        == 1
    )


def test_phase_four_refresh_still_evaluates_runtime_safety(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _phase_four_policy()
    project_id = resolve_repository_project_id(tmp_path)
    evidence, assessment = _bad_evidence(project_id, policy.policy_digest)
    calls: list[str] = []
    monkeypatch.setattr(
        activation_evidence_runtime,
        "current_activation_policy",
        lambda _root: policy,
    )
    monkeypatch.setattr(
        activation_evidence_runtime,
        "import_activation_evidence_inbox",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        activation_evidence_runtime,
        "revalidate_activation_safety_releases",
        lambda *_args, **_kwargs: calls.append("revalidated"),
    )

    def assemble(*_args: object, **_kwargs: object):
        calls.append("assembled")
        return evidence

    monkeypatch.setattr(
        activation_evidence_runtime,
        "_assemble_activation_evidence",
        assemble,
    )

    def advance(*_args: object, **_kwargs: object):
        calls.append("advanced")
        return policy, assessment

    monkeypatch.setattr(
        activation_evidence_runtime,
        "advance_activation_policy_from_evidence",
        advance,
    )

    current, actual = (
        activation_evidence_runtime._refresh_activation_policy_from_local_evidence(
            tmp_path
        )
    )

    assert current == policy
    assert actual == assessment
    assert calls == ["revalidated", "assembled", "advanced"]


def _bad_evidence(
    project_id: str,
    policy_digest: str,
) -> tuple[ActivationEvidence, ActivationAssessment]:
    observed = ActivationSessionObservation(
        session_id="session.bad",
        stage_key="implementation",
        risk_level="low",
        mode="enforce",
        completed_at="2026-06-01T00:00:00+00:00",
    )
    outcome = ActivationSessionOutcome(
        session_id=observed.session_id,
        session_record_digest=_digest("record"),
        status="complete",
        had_reversal=True,
        had_late_critical=False,
        had_escape=False,
        finalized_at="2026-07-01T00:00:00+00:00",
        observation_cutoff="2026-06-15T00:00:00+00:00",
        finding_chain_head_digest=_digest("finding-chain"),
        attribution_set_digest=_digest("attributions"),
        finding_event_digests=(_digest("reversal"),),
    )
    probe = ActivationProbeEvidence(
        canonical_plan_replay_passed=True,
        certificate_integrity_passed=True,
        provider_billing_integrity_passed=True,
        crash_recovery_passed=True,
        hard_budget_integrity_passed=True,
        clean_user_e2e_passed=True,
        planner_benchmark_p95_seconds=0.1,
        work_item_fencing_passed=True,
        hard_constraint_integrity_passed=True,
        non_waivable_integrity_passed=True,
        platform_count=1,
        probe_trial_count=1,
    )
    isolation = IsolationPlatformEvidence(
        platform_id="linux",
        isolation_level="enforced",
        candidate_write_blocked=True,
        sibling_write_blocked=True,
        home_write_blocked=True,
        network_blocked=True,
        evidence_digest=_digest("isolation"),
    )
    evidence = ActivationEvidence(
        project_id=project_id,
        assessed_at=outcome.finalized_at,
        sessions=(observed,),
        session_record_digests=(outcome.session_record_digest,),
        isolation_matrix=(isolation,),
        isolation_record_digests=(_digest("isolation-record"),),
        probes=probe,
        probe_record_digest=_digest("probe-record"),
        session_outcomes=(outcome,),
    )
    assessment = ActivationAssessment(
        assessment_id="assessment.bad",
        policy_digest=policy_digest,
        evidence_digest=evidence.evidence_digest,
        assessed_at=evidence.assessed_at,
        eligible=False,
        failed_guards=("reversal_count",),
        quality_intervals=(),
    )
    return evidence, assessment


def _hold(
    project_id: str,
    started: datetime,
    policy,
) -> ActivationSafetyHold:
    return ActivationSafetyHold(
        hold_id="hold.one",
        project_id=project_id,
        policy_digest=policy.policy_digest,
        evidence_digest=_digest("evidence"),
        assessment_digest=_digest("assessment"),
        triggering_outcome_digests=(_digest("outcome"),),
        affected_combinations=({"stage_key": "implementation", "risk_level": "low"},),
        created_at=started.isoformat(),
        recovery_not_before=(started + timedelta(days=14)).isoformat(),
        minimum_recovery_sessions=2,
    )


def _phase_two_policy():
    from ai_sdlc.core.stage_review.activation_policy import advance_activation_policy

    baseline = baseline_activation_policy()
    assessment = ActivationAssessment(
        assessment_id="assessment.phase-one",
        policy_digest=baseline.policy_digest,
        evidence_digest=_digest("phase-one-evidence"),
        assessed_at="2026-06-01T00:00:00+00:00",
        eligible=True,
        failed_guards=(),
        quality_intervals=(),
    )
    promoted = advance_activation_policy(baseline, assessment)
    assert promoted is not None
    return promoted


def _phase_four_policy():
    from ai_sdlc.core.stage_review.activation_policy import advance_activation_policy

    policy = baseline_activation_policy()
    for index in range(3):
        assessment = ActivationAssessment(
            assessment_id=f"assessment.phase-{index + 1}",
            policy_digest=policy.policy_digest,
            evidence_digest=_digest(f"phase-{index + 1}-evidence"),
            assessed_at=f"2026-0{index + 4}-01T00:00:00+00:00",
            eligible=True,
            failed_guards=(),
            quality_intervals=(),
        )
        promoted = advance_activation_policy(policy, assessment)
        assert promoted is not None
        policy = promoted
    assert policy.active_phase == 4
    return policy


def _recovery_sample(
    root: Path,
    hold: ActivationSafetyHold,
    session_id: str,
    completed_at: datetime,
) -> ActivationSafetyRecoverySample:
    scope = FindingScope(
        project_id=hold.project_id,
        work_item_id=f"WI-{session_id}",
        stage_instance_id=f"implementation.{session_id}",
        session_id=session_id,
    )
    completion = ReviewSessionCompletion(
        scope=scope,
        session_digest=_digest(f"review:{session_id}"),
        session_head_event_digest=_digest(f"head:{session_id}"),
        candidate_manifest_digest=_digest(f"candidate:{session_id}"),
        panel_plan_digest=_digest(f"plan:{session_id}"),
        binding_set_digest=_digest(f"bindings:{session_id}"),
        initial_review_seal_digest=_digest(f"seal:{session_id}"),
        finding_ledger_digest=_digest(f"ledger:{session_id}"),
        required_pass_digests=(_digest(f"pass:{session_id}"),),
        completed_at=completed_at.isoformat(),
    )
    shared = resolve_canonical_shared_state(root, hold.project_id)
    session_root = session_scope_root(
        shared / "stage-review-sessions",
        hold.project_id,
        scope,
    )
    atomic_write_json(
        session_root / "completion.json",
        completion.model_dump(mode="json"),
    )
    return ActivationSafetyRecoverySample(
        sample_id=f"sample.{session_id}",
        hold_id=hold.hold_id,
        hold_digest=hold.hold_digest,
        project_id=hold.project_id,
        policy_digest=hold.policy_digest,
        stage_key="implementation",
        risk_level="low",
        candidate_manifest_digest=completion.candidate_manifest_digest,
        panel_plan_digest=completion.panel_plan_digest,
        binding_set_digest=completion.binding_set_digest,
        finding_ledger_digest=completion.finding_ledger_digest,
        review_session_digest=completion.session_digest,
        review_completion_digest=completion.completion_digest,
        scope=scope,
        review_completed_at=completion.completed_at,
        observed_at=completion.completed_at,
    )


def _persist_hold(root: Path, hold: ActivationSafetyHold) -> None:
    from ai_sdlc.core.stage_review.artifacts import (
        atomic_write_json,
        resolve_canonical_shared_state,
    )

    shared = resolve_canonical_shared_state(root, hold.project_id)
    atomic_write_json(
        shared / "activation/safety-holds" / f"{hold.hold_id}.json",
        hold.model_dump(mode="json"),
    )


def _revalidated_outcome(
    sample: ActivationSafetyRecoverySample,
    *,
    assessed_at: datetime,
    late: bool,
) -> ActivationSessionOutcome:
    return ActivationSessionOutcome(
        session_id=sample.scope.session_id,
        session_record_digest=sample.sample_digest,
        status="complete",
        had_reversal=False,
        had_late_critical=late,
        had_escape=False,
        finalized_at=assessed_at.isoformat(),
        observation_cutoff=(
            parse_datetime(sample.review_completed_at) + timedelta(days=14)
        ).isoformat(),
        finding_chain_head_digest=_digest(
            f"head:{sample.scope.session_id}:{late}"
        ),
        attribution_set_digest=_digest(
            f"attribution:{sample.scope.session_id}"
        ),
        finding_event_digests=(
            (_digest(f"late:{sample.scope.session_id}"),) if late else ()
        ),
    )


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _digest(label: str) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(label.encode()).hexdigest()}"
