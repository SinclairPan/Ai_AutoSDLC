"""激活后真实结果回归的不可变安全冻结。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import cast

from ai_sdlc.core.stage_review.activation_fence import (
    activation_safety_mutation_fence,
)
from ai_sdlc.core.stage_review.activation_models import (
    ActivationAssessment,
    ActivationEvaluationCohortBoundary,
    ActivationEvidence,
    ActivationSafetyHold,
    ActivationSafetyRecoverySample,
    ActivationSafetyRelease,
    ActivationSafetyScope,
    ActivationSessionOutcome,
    ActivationSessionRecord,
    RiskLevel,
    StageGateActivationPolicy,
)
from ai_sdlc.core.stage_review.activation_outcomes import (
    activation_recovery_session_records,
    derive_activation_recovery_session_outcomes,
    lock_activation_outcome_sources,
)
from ai_sdlc.core.stage_review.activation_policy import baseline_activation_policy
from ai_sdlc.core.stage_review.activation_policy_anchor import (
    read_activation_policy_anchor,
)
from ai_sdlc.core.stage_review.artifacts import (
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
    resolve_repository_project_id,
)
from ai_sdlc.core.stage_review.candidate import (
    CandidateManifest,
    candidate_binding_digest,
)
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id
from ai_sdlc.core.stage_review.review_completion import ReviewSessionCompletion
from ai_sdlc.core.stage_review.session_paths import (
    _session_scope_root as session_scope_root,
)
from ai_sdlc.core.stage_review.stage_review_execution import (
    StageReviewExecutionOutcome,
)


def activation_evaluation_cohort(
    root: Path,
    records: tuple[ActivationSessionRecord, ...],
    *,
    policy: StageGateActivationPolicy,
) -> tuple[
    tuple[ActivationSessionRecord, ...],
    tuple[ActivationEvaluationCohortBoundary, ...],
]:
    """保留全量审计，但用已验证 Release 建立下一轮晋级样本高水位。"""

    project_id = resolve_repository_project_id(root)
    lineage = {
        item.policy_digest
        for item in _activation_policy_lineage(root, project_id, policy)
    }
    holds = {
        item.hold_digest: item
        for item in _read_holds(root, project_id)
        if item.policy_digest in lineage
    }
    releases = tuple(
        item
        for item in _read_releases(root, project_id)
        if item.policy_digest in lineage
    )
    released_holds = {item.hold_digest for item in releases}
    active = tuple(
        item
        for item in holds.values()
        if item.hold_digest not in released_holds
    )
    latest: dict[tuple[str, str], ActivationEvaluationCohortBoundary] = {}
    for release in releases:
        hold = holds.get(release.hold_digest)
        if hold is None:
            raise ValueError("activation evaluation cohort hold is unavailable")
        for scope in hold.affected_combinations:
            combination = (scope.stage_key, scope.risk_level)
            if any(
                combination
                in {
                    (item.stage_key, item.risk_level)
                    for item in blocker.affected_combinations
                }
                for blocker in active
            ):
                continue
            boundary = ActivationEvaluationCohortBoundary(
                stage_key=scope.stage_key,
                risk_level=scope.risk_level,
                policy_digest=release.policy_digest,
                hold_digest=hold.hold_digest,
                release_digest=release.release_digest,
                released_at=release.released_at,
            )
            current = latest.get(combination)
            if current is None or (
                parse_utc(boundary.released_at),
                boundary.release_digest,
            ) > (
                parse_utc(current.released_at),
                current.release_digest,
            ):
                latest[combination] = boundary
    selected = tuple(
        item
        for item in records
        if (
            (boundary := latest.get(
                (
                    item.observation.stage_key,
                    item.observation.risk_level,
                )
            ))
            is None
            or parse_utc(item.observation.completed_at)
            > parse_utc(boundary.released_at)
        )
    )
    return selected, tuple(latest[key] for key in sorted(latest))


def revalidate_activation_safety_releases(
    root: Path,
    *,
    policy: StageGateActivationPolicy,
    assessed_at: str,
) -> tuple[ActivationSafetyHold, ...]:
    """在任何 cohort/晋级评估前重验当前策略血缘上的全部 Release。"""

    project_id = resolve_repository_project_id(root)
    with activation_safety_mutation_fence(root, project_id):
        _require_writable_policy_epoch(root, project_id, policy)
        return _revalidate_activation_safety_releases_locked(
            root,
            policy=policy,
            assessed_at=assessed_at,
        )


def _revalidate_activation_safety_releases_locked(
    root: Path,
    *,
    policy: StageGateActivationPolicy,
    assessed_at: str,
) -> tuple[ActivationSafetyHold, ...]:
    project_id = resolve_repository_project_id(root)
    created = []
    for lineage_policy in _activation_policy_lineage(root, project_id, policy):
        created.extend(
            _revalidate_released_activation_safety_holds(
                root,
                policy=lineage_policy,
                stage_key=None,
                risk_level=None,
                assessed_at=assessed_at,
            )
        )
    return tuple(created)


def persist_activation_safety_hold(
    root: Path,
    *,
    policy: StageGateActivationPolicy,
    evidence: ActivationEvidence,
    assessment: ActivationAssessment,
) -> tuple[ActivationSafetyHold, ...]:
    project_id = resolve_repository_project_id(root)
    with activation_safety_mutation_fence(root, project_id):
        _require_writable_policy_epoch(root, project_id, policy)
        return _persist_activation_safety_hold_locked(
            root,
            policy=policy,
            evidence=evidence,
            assessment=assessment,
        )


def _persist_activation_safety_hold_locked(
    root: Path,
    *,
    policy: StageGateActivationPolicy,
    evidence: ActivationEvidence,
    assessment: ActivationAssessment,
) -> tuple[ActivationSafetyHold, ...]:
    if policy.active_phase == 1:
        return ()
    triggering = tuple(
        item
        for item in evidence.session_outcomes
        if item.status == "incomplete"
        or item.had_reversal
        or item.had_late_critical
        or item.had_escape
    )
    if not triggering:
        return ()
    observations = {item.session_id: item for item in evidence.sessions}
    recovery_not_before = (
        parse_utc(assessment.assessed_at)
        + timedelta(days=policy.outcome_maturity_window_days)
    ).isoformat()
    sample = policy.sample_size
    minimum_recovery_sessions = max(
        1,
        sample.minimum_total_shadow_sessions,
        sample.minimum_total_enforce_sessions,
        sample.minimum_shadow_sessions_per_new_combination,
        sample.minimum_enforce_sessions_per_new_combination,
    )
    by_combination: dict[tuple[str, RiskLevel], list[ActivationSessionOutcome]] = {}
    for outcome in triggering:
        observation = observations[outcome.session_id]
        by_combination.setdefault(
            (observation.stage_key, observation.risk_level),
            [],
        ).append(outcome)
    holds = []
    for (stage_key, risk_level), raw_outcomes in sorted(by_combination.items()):
        outcomes = tuple(raw_outcomes)
        hold_id = stable_id(
            "activation-safety-hold",
            policy.policy_digest,
            stage_key,
            risk_level,
            *(
                f"{item.session_id}:{item.finding_chain_head_digest}:"
                f"{item.attribution_set_digest}:{item.status}:"
                f"{item.had_reversal}:{item.had_late_critical}:{item.had_escape}"
                for item in outcomes
            ),
        )
        hold = ActivationSafetyHold(
            hold_id=hold_id,
            project_id=evidence.project_id,
            policy_digest=policy.policy_digest,
            evidence_digest=evidence.evidence_digest,
            assessment_digest=assessment.assessment_digest,
            triggering_outcome_digests=tuple(
                item.outcome_digest for item in outcomes
            ),
            affected_combinations=(
                ActivationSafetyScope(
                    stage_key=stage_key,
                    risk_level=risk_level,
                ),
            ),
            created_at=assessment.assessed_at,
            recovery_not_before=recovery_not_before,
            minimum_recovery_sessions=minimum_recovery_sessions,
        )
        path = _holds_root(root, evidence.project_id) / f"{hold_id}.json"
        if create_json_exclusive(path, hold.model_dump(mode="json")):
            holds.append(hold)
            continue
        current = ActivationSafetyHold.model_validate(read_json_object(path))
        if current != hold:
            raise ValueError("activation safety hold identity fork")
        holds.append(current)
    return tuple(holds)


def active_activation_safety_holds(
    root: Path,
    *,
    policy_digest: str,
) -> tuple[ActivationSafetyHold, ...]:
    project_id = resolve_repository_project_id(root)
    holds_root = _holds_root(root, project_id)
    holds = tuple(
        ActivationSafetyHold.model_validate(read_json_object(path))
        for path in sorted(holds_root.glob("*.json"))
    )
    releases = _read_releases(root, project_id)
    released = {item.hold_digest for item in releases}
    return tuple(
        item
        for item in holds
        if item.policy_digest == policy_digest and item.hold_digest not in released
    )


def active_activation_safety_holds_for_lineage(
    root: Path,
    *,
    policy: StageGateActivationPolicy,
) -> tuple[ActivationSafetyHold, ...]:
    """纯读返回当前 policy 血缘中尚未 Release 的全部安全冻结。"""

    project_id = resolve_repository_project_id(root)
    lineage = {
        item.policy_digest
        for item in _activation_policy_lineage(root, project_id, policy)
    }
    releases = {
        item.hold_digest
        for item in _read_releases(root, project_id)
        if item.policy_digest in lineage
    }
    return tuple(
        item
        for item in _read_holds(root, project_id)
        if item.policy_digest in lineage and item.hold_digest not in releases
    )


def record_activation_safety_recovery(
    root: Path,
    sample: ActivationSafetyRecoverySample,
) -> ActivationSafetyRecoverySample:
    trusted = ActivationSafetyRecoverySample.model_validate(
        sample.model_dump(mode="json")
    )
    project_id = resolve_repository_project_id(root)
    if trusted.project_id != project_id:
        raise ValueError("activation safety recovery project mismatch")
    with activation_safety_mutation_fence(root, project_id):
        _require_writable_policy_digest(
            root,
            project_id,
            trusted.policy_digest,
        )
        holds = active_activation_safety_holds(
            root,
            policy_digest=trusted.policy_digest,
        )
        hold = next(
            (
                item
                for item in holds
                if item.hold_id == trusted.hold_id
                and item.hold_digest == trusted.hold_digest
            ),
            None,
        )
        if hold is None:
            raise ValueError("activation safety recovery hold is not active")
        combination = (trusted.stage_key, trusted.risk_level)
        if combination not in {
            (item.stage_key, item.risk_level)
            for item in hold.affected_combinations
        }:
            raise ValueError("activation safety recovery scope is not affected")
        if parse_utc(trusted.observed_at) < parse_utc(hold.created_at):
            raise ValueError("activation safety recovery predates hold")
        _require_authoritative_completion(root, trusted, hold)
        path = (
            _recovery_root(root, trusted.project_id)
            / f"{trusted.sample_id}.json"
        )
        if create_json_exclusive(path, trusted.model_dump(mode="json")):
            return trusted
        current = ActivationSafetyRecoverySample.model_validate(
            read_json_object(path)
        )
        if current != trusted:
            raise ValueError("activation safety recovery identity fork")
        return current


def release_eligible_activation_safety_holds(
    root: Path,
    *,
    policy: StageGateActivationPolicy,
    assessed_at: str,
) -> tuple[ActivationSafetyRelease, ...]:
    project_id = resolve_repository_project_id(root)
    with activation_safety_mutation_fence(root, project_id):
        _require_writable_policy_epoch(root, project_id, policy)
        return _release_eligible_activation_safety_holds_locked(
            root,
            policy=policy,
            assessed_at=assessed_at,
        )


def _release_eligible_activation_safety_holds_locked(
    root: Path,
    *,
    policy: StageGateActivationPolicy,
    assessed_at: str,
) -> tuple[ActivationSafetyRelease, ...]:
    now = parse_utc(assessed_at)
    project_id = resolve_repository_project_id(root)
    samples = _read_recovery_samples(root, project_id)
    releases = []
    for hold in active_activation_safety_holds(
        root,
        policy_digest=policy.policy_digest,
    ):
        if now < parse_utc(hold.recovery_not_before):
            continue
        selected = _independent_recovery_samples(hold, samples)
        if len(selected) < hold.minimum_recovery_sessions:
            continue
        mature = tuple(
            item
            for item in selected
            if parse_utc(item.review_completed_at)
            + timedelta(days=policy.outcome_maturity_window_days)
            <= now
        )
        if len(mature) < hold.minimum_recovery_sessions:
            continue
        for item in mature:
            _require_authoritative_completion(root, item, hold)
        records = activation_recovery_session_records(mature)
        with lock_activation_outcome_sources(root, records):
            outcomes = derive_activation_recovery_session_outcomes(
                root,
                mature,
                policy=policy,
                assessed_at=assessed_at,
            )
        clean = tuple(
            (item, outcome)
            for item, outcome in zip(mature, outcomes, strict=True)
            if outcome.status == "complete"
            and not outcome.had_reversal
            and not outcome.had_late_critical
            and not outcome.had_escape
        )
        if len(clean) < hold.minimum_recovery_sessions:
            continue
        used = clean[: hold.minimum_recovery_sessions]
        release = ActivationSafetyRelease(
            release_id=stable_id(
                "activation-safety-release",
                hold.hold_digest,
                *(item.sample_digest for item, _outcome in used),
            ),
            hold_id=hold.hold_id,
            hold_digest=hold.hold_digest,
            project_id=hold.project_id,
            policy_digest=hold.policy_digest,
            recovery_sample_digests=tuple(
                item.sample_digest for item, _outcome in used
            ),
            recovery_outcome_digests=tuple(
                outcome.outcome_digest for _item, outcome in used
            ),
            finding_chain_head_digests=tuple(
                outcome.finding_chain_head_digest
                for _item, outcome in used
            ),
            attribution_set_digests=tuple(
                outcome.attribution_set_digest
                for _item, outcome in used
            ),
            released_at=assessed_at,
        )
        path = _release_root(root, project_id) / f"{release.release_id}.json"
        if create_json_exclusive(path, release.model_dump(mode="json")):
            releases.append(release)
            continue
        current = ActivationSafetyRelease.model_validate(read_json_object(path))
        if current != release:
            raise ValueError("activation safety release identity fork")
        releases.append(current)
    return tuple(releases)


def affected_activation_safety_holds(
    root: Path,
    *,
    policy: StageGateActivationPolicy,
    stage_key: str,
    risk_level: str,
    assessed_at: str,
) -> tuple[ActivationSafetyHold, ...]:
    project_id = resolve_repository_project_id(root)
    with activation_safety_mutation_fence(root, project_id):
        _require_writable_policy_epoch(root, project_id, policy)
        for lineage_policy in _activation_policy_lineage(
            root,
            project_id,
            policy,
        ):
            _revalidate_released_activation_safety_holds(
                root,
                policy=lineage_policy,
                stage_key=stage_key,
                risk_level=risk_level,
                assessed_at=assessed_at,
            )
            release_eligible_activation_safety_holds(
                root,
                policy=lineage_policy,
                assessed_at=assessed_at,
            )
        return tuple(
            hold
            for hold in active_activation_safety_holds_for_lineage(
                root,
                policy=policy,
            )
            if (stage_key, risk_level)
            in {
                (item.stage_key, item.risk_level)
                for item in hold.affected_combinations
            }
        )


def _revalidate_released_activation_safety_holds(
    root: Path,
    *,
    policy: StageGateActivationPolicy,
    stage_key: str | None,
    risk_level: str | None,
    assessed_at: str,
) -> tuple[ActivationSafetyHold, ...]:
    project_id = resolve_repository_project_id(root)
    holds = {
        item.hold_digest: item
        for item in _read_holds(root, project_id)
        if item.policy_digest == policy.policy_digest
    }
    samples = {
        item.sample_digest: item
        for item in _read_recovery_samples(root, project_id)
    }
    candidates: list[
        tuple[
            ActivationSafetyRelease,
            ActivationSafetyHold,
            tuple[ActivationSafetyRecoverySample, ...],
        ]
    ] = []
    unique_samples: dict[str, ActivationSafetyRecoverySample] = {}
    for release in _read_releases(root, project_id):
        hold = holds.get(release.hold_digest)
        if (
            hold is None
            or release.policy_digest != policy.policy_digest
            or (
                stage_key is not None
                and risk_level is not None
                and (stage_key, risk_level)
                not in {
                    (item.stage_key, item.risk_level)
                    for item in hold.affected_combinations
                }
            )
        ):
            continue
        selected = tuple(
            samples.get(digest)
            for digest in release.recovery_sample_digests
        )
        if any(item is None for item in selected):
            raise ValueError(
                "activation safety release recovery sample is unavailable"
            )
        trusted = tuple(cast(ActivationSafetyRecoverySample, item) for item in selected)
        for item in trusted:
            _require_authoritative_completion(root, item, hold)
            unique_samples.setdefault(item.sample_digest, item)
        candidates.append((release, hold, trusted))
    if not candidates:
        return ()
    combined_samples = tuple(unique_samples.values())
    records = activation_recovery_session_records(combined_samples)
    with lock_activation_outcome_sources(root, records):
        combined_outcomes = derive_activation_recovery_session_outcomes(
            root,
            combined_samples,
            policy=policy,
            assessed_at=assessed_at,
        )
    outcomes_by_sample = {
        sample.sample_digest: outcome
        for sample, outcome in zip(
            combined_samples,
            combined_outcomes,
            strict=True,
        )
    }
    created = []
    for release, hold, trusted in candidates:
        outcomes = tuple(
            outcomes_by_sample[item.sample_digest]
            for item in trusted
        )
        bad = tuple(
            item
            for item in outcomes
            if item.status == "incomplete"
            or item.had_reversal
            or item.had_late_critical
            or item.had_escape
        )
        if not bad:
            continue
        hold_id = stable_id(
            "activation-safety-release-regression",
            release.release_digest,
            *(
                f"{item.finding_chain_head_digest}:"
                f"{item.attribution_set_digest}:{item.status}:"
                f"{item.had_reversal}:{item.had_late_critical}:"
                f"{item.had_escape}:{','.join(item.reason_codes)}"
                for item in bad
            ),
        )
        path = _holds_root(root, project_id) / f"{hold_id}.json"
        if path.is_file():
            created.append(
                ActivationSafetyHold.model_validate(read_json_object(path))
            )
            continue
        detected_at = parse_utc(assessed_at)
        regression_hold = ActivationSafetyHold(
            hold_id=hold_id,
            project_id=project_id,
            policy_digest=policy.policy_digest,
            evidence_digest=hold.evidence_digest,
            assessment_digest=hold.assessment_digest,
            triggering_outcome_digests=tuple(
                item.outcome_digest for item in bad
            ),
            affected_combinations=hold.affected_combinations,
            created_at=assessed_at,
            recovery_not_before=(
                detected_at
                + timedelta(days=policy.outcome_maturity_window_days)
            ).isoformat(),
            minimum_recovery_sessions=hold.minimum_recovery_sessions,
        )
        if create_json_exclusive(
            path,
            regression_hold.model_dump(mode="json"),
        ):
            created.append(regression_hold)
            continue
        current = ActivationSafetyHold.model_validate(read_json_object(path))
        if current != regression_hold:
            raise ValueError("activation safety regression hold identity fork")
        created.append(current)
    return tuple(created)


def _require_writable_policy_epoch(
    root: Path,
    project_id: str,
    policy: StageGateActivationPolicy,
) -> None:
    _require_writable_policy_digest(root, project_id, policy.policy_digest)


def _require_writable_policy_digest(
    root: Path,
    project_id: str,
    policy_digest: str,
) -> None:
    current = read_activation_policy_anchor(root)
    if current is None:
        return
    if current.compatibility_mode != "strict":
        raise ValueError("activation safety policy epoch is read-only")
    lineage = {
        item.policy_digest
        for item in _activation_policy_lineage(root, project_id, current)
    }
    if policy_digest not in lineage:
        raise ValueError("activation safety policy epoch changed")


def _activation_policy_lineage(
    root: Path,
    project_id: str,
    policy: StageGateActivationPolicy,
) -> tuple[StageGateActivationPolicy, ...]:
    shared = resolve_canonical_shared_state(root, project_id)
    available = {
        item.policy_digest: item
        for item in (
            StageGateActivationPolicy.model_validate(read_json_object(path))
            for path in sorted(
                (shared / "activation/policies").glob("*.json")
            )
        )
    }
    baseline = baseline_activation_policy()
    available[baseline.policy_digest] = baseline
    available[policy.policy_digest] = policy
    lineage = []
    current: StageGateActivationPolicy | None = policy
    seen: set[str] = set()
    while current is not None and current.policy_digest not in seen:
        lineage.append(current)
        seen.add(current.policy_digest)
        previous = current.previous_policy_digest
        if not previous:
            current = None
            continue
        current = available.get(previous)
        if current is None:
            raise ValueError("activation policy lineage is incomplete")
    return tuple(lineage)


def build_activation_safety_recovery_sample(
    root: Path,
    hold: ActivationSafetyHold,
    *,
    candidate: CandidateManifest,
    outcome: StageReviewExecutionOutcome,
    risk_level: str,
    observed_at: str,
) -> ActivationSafetyRecoverySample:
    trusted_candidate = CandidateManifest.model_validate(
        candidate.model_dump(mode="json")
    )
    trusted_outcome = StageReviewExecutionOutcome.model_validate(
        outcome.model_dump(mode="json")
    )
    if trusted_outcome.status != "completed":
        raise ValueError("activation safety recovery review is incomplete")
    if (trusted_candidate.stage_key, risk_level) not in {
        (item.stage_key, item.risk_level) for item in hold.affected_combinations
    }:
        raise ValueError("activation safety recovery scope is not affected")
    scope = FindingScope(
        project_id=trusted_candidate.project_id,
        work_item_id=trusted_candidate.work_item_id,
        stage_instance_id=trusted_candidate.stage_instance_id,
        session_id=trusted_candidate.review_session_id,
    )
    candidate_digest = candidate_binding_digest(trusted_candidate)
    completion = _read_authoritative_completion(
        root,
        project_id=hold.project_id,
        scope=scope,
    )
    if completion is None:
        raise ValueError("activation safety recovery completion is unavailable")
    if (
        completion.session_digest != trusted_outcome.review_session_digest
        or completion.completion_digest
        != trusted_outcome.review_completion_digest
        or completion.candidate_manifest_digest != candidate_digest
    ):
        raise ValueError("activation safety recovery completion lineage diverged")
    if parse_utc(observed_at) < parse_utc(completion.completed_at):
        raise ValueError(
            "activation safety recovery observation predates review completion"
        )
    return ActivationSafetyRecoverySample(
        sample_id=stable_id(
            "activation-safety-recovery",
            hold.hold_digest,
            trusted_outcome.review_session_digest,
            trusted_outcome.review_completion_digest,
        ),
        hold_id=hold.hold_id,
        hold_digest=hold.hold_digest,
        project_id=hold.project_id,
        policy_digest=hold.policy_digest,
        stage_key=trusted_candidate.stage_key,
        risk_level=cast(RiskLevel, risk_level),
        candidate_manifest_digest=candidate_digest,
        panel_plan_digest=completion.panel_plan_digest,
        binding_set_digest=completion.binding_set_digest,
        finding_ledger_digest=completion.finding_ledger_digest,
        review_session_digest=trusted_outcome.review_session_digest,
        review_completion_digest=trusted_outcome.review_completion_digest,
        scope=scope,
        review_completed_at=completion.completed_at,
        observed_at=completion.completed_at,
    )


def _independent_recovery_samples(
    hold: ActivationSafetyHold,
    samples: tuple[ActivationSafetyRecoverySample, ...],
) -> tuple[ActivationSafetyRecoverySample, ...]:
    affected = {
        (item.stage_key, item.risk_level) for item in hold.affected_combinations
    }
    eligible = tuple(
        item
        for item in samples
        if item.hold_digest == hold.hold_digest
        and item.hold_id == hold.hold_id
        and item.project_id == hold.project_id
        and item.policy_digest == hold.policy_digest
        and (item.stage_key, item.risk_level) in affected
        and parse_utc(item.observed_at) >= parse_utc(hold.created_at)
    )
    selected: dict[
        tuple[str, str],
        ActivationSafetyRecoverySample,
    ] = {}
    for item in sorted(eligible, key=lambda value: value.sample_digest):
        identity = (
            item.review_session_digest,
            item.review_completion_digest,
        )
        selected.setdefault(identity, item)
    return tuple(selected.values())


def _read_recovery_samples(
    root: Path,
    project_id: str,
) -> tuple[ActivationSafetyRecoverySample, ...]:
    return tuple(
        ActivationSafetyRecoverySample.model_validate(read_json_object(path))
        for path in sorted(_recovery_root(root, project_id).glob("*.json"))
    )


def _read_releases(
    root: Path,
    project_id: str,
) -> tuple[ActivationSafetyRelease, ...]:
    return tuple(
        ActivationSafetyRelease.model_validate(read_json_object(path))
        for path in sorted(_release_root(root, project_id).glob("*.json"))
    )


def _read_holds(
    root: Path,
    project_id: str,
) -> tuple[ActivationSafetyHold, ...]:
    return tuple(
        ActivationSafetyHold.model_validate(read_json_object(path))
        for path in sorted(_holds_root(root, project_id).glob("*.json"))
    )


activation_safety_fence = activation_safety_mutation_fence


def _require_authoritative_completion(
    root: Path,
    sample: ActivationSafetyRecoverySample,
    hold: ActivationSafetyHold,
) -> ReviewSessionCompletion:
    completion = _read_authoritative_completion(
        root,
        project_id=sample.project_id,
        scope=sample.scope,
    )
    if completion is None:
        raise ValueError("activation safety recovery completion is unavailable")
    actual = (
        completion.scope,
        completion.session_digest,
        completion.completion_digest,
        completion.candidate_manifest_digest,
        completion.panel_plan_digest,
        completion.binding_set_digest,
        completion.finding_ledger_digest,
        completion.completed_at,
    )
    expected = (
        sample.scope,
        sample.review_session_digest,
        sample.review_completion_digest,
        sample.candidate_manifest_digest,
        sample.panel_plan_digest,
        sample.binding_set_digest,
        sample.finding_ledger_digest,
        sample.review_completed_at,
    )
    if actual != expected:
        raise ValueError("activation safety recovery completion lineage diverged")
    if parse_utc(completion.completed_at) <= parse_utc(hold.created_at):
        raise ValueError("activation safety recovery completion predates hold")
    return completion


def _read_authoritative_completion(
    root: Path,
    *,
    project_id: str,
    scope: FindingScope,
) -> ReviewSessionCompletion | None:
    shared = resolve_canonical_shared_state(root, project_id)
    completion_path = (
        session_scope_root(
            shared / "stage-review-sessions",
            project_id,
            scope,
        )
        / "completion.json"
    )
    if not completion_path.is_file():
        return None
    return ReviewSessionCompletion.model_validate(
        read_json_object(completion_path)
    )


def _holds_root(root: Path, project_id: str) -> Path:
    shared = resolve_canonical_shared_state(root, project_id)
    return shared / "activation" / "safety-holds"


def _recovery_root(root: Path, project_id: str) -> Path:
    shared = resolve_canonical_shared_state(root, project_id)
    return shared / "activation" / "safety-recovery"


def _release_root(root: Path, project_id: str) -> Path:
    shared = resolve_canonical_shared_state(root, project_id)
    return shared / "activation" / "safety-releases"
