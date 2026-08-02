"""在未来 Session 上汇总独立、不可变的 Prospective Shadow 对照证据。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.optimization.candidate_domain_registry import (
    CandidateDomainRegistry,
)
from ai_sdlc.core.stage_review.optimization.controller_models import OptimizationEpoch
from ai_sdlc.core.stage_review.optimization.evaluators import (
    component_runtime_identity,
)
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationStatisticsPolicy,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    TERMINAL_OBSERVATION_KINDS,
    CommittedSessionBinding,
    CommittedSessionBindingStore,
    OptimizationObservationStore,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    PipelineShadowResult,
    ShadowComparisonMetrics,
)
from ai_sdlc.core.stage_review.optimization.pipeline_effects import commit_effect
from ai_sdlc.core.stage_review.optimization.shadow import (
    OptimizationShadowAssignment,
    OptimizationShadowAssignmentStore,
    OptimizationShadowSampleMember,
    OptimizationShadowSamplePlan,
    ShadowSessionInput,
)
from ai_sdlc.core.stage_review.optimization.shadow_execution import (
    ShadowAssignmentExecutor,
    execute_pending_assignments,
)
from ai_sdlc.core.stage_review.optimization.shadow_observations import (
    OptimizationShadowObservation,
    OptimizationShadowObservationStore,
)
from ai_sdlc.core.stage_review.optimization.statistics import (
    _binary_improvement_statistics as binary_improvement_statistics,
)
from ai_sdlc.core.stage_review.optimization.statistics import (
    baseline_statistics_policy,
    required_sample_size,
    resolve_statistics_policy,
)
from ai_sdlc.core.stage_review.provider_usage_models import ProviderUsageEstimatePolicy
from ai_sdlc.core.stage_review.resource_builders import (
    parse_utc,
    stable_id,
    utc_iso,
)

_BAD_FLAGS = (
    "late_critical",
    "reviewer_coverage_leak",
    "false_positive",
    "reversal",
    "stage_reopened",
    "unconfirmed_finding",
)
_BAD_OUTCOMES = (
    "needs_user",
    "blocked",
    "timed_out",
    "abandoned",
    "hard_budget_exhausted",
    "unknown_or_censored",
)


class LocalProspectiveShadowPort:
    def __init__(
        self,
        *,
        assignments: OptimizationShadowAssignmentStore,
        bindings: CommittedSessionBindingStore,
        observations: OptimizationObservationStore,
        shadow_observations: OptimizationShadowObservationStore,
        clock: Callable[[], str],
        minimum_sessions: int,
        minimum_days: int,
        usage_policy_source: Callable[[str], ProviderUsageEstimatePolicy],
        executor: ShadowAssignmentExecutor | None = None,
        domain_registry: CandidateDomainRegistry | None = None,
        statistics_policy: OptimizationStatisticsPolicy | None = None,
    ) -> None:
        self.assignments = assignments
        self.bindings = bindings
        self.observations = observations
        self.shadow_observations = shadow_observations
        self.clock = clock
        self.minimum_sessions = minimum_sessions
        self.minimum_days = minimum_days
        self.usage_policy_source = usage_policy_source
        self.executor = executor
        if domain_registry is None:
            from ai_sdlc.core.stage_review.optimization.candidate_domain_defaults import (
                default_candidate_domain_registry,
            )

            domain_registry = default_candidate_domain_registry()
        self.domain_registry = domain_registry
        self.statistics_policy = statistics_policy or baseline_statistics_policy()

    def runtime_identity(self) -> dict[str, object]:
        return {
            "clock": component_runtime_identity(self.clock),
            "minimum_sessions": self.minimum_sessions,
            "minimum_days": self.minimum_days,
            "usage_policy_source": component_runtime_identity(
                self.usage_policy_source
            ),
            "executor": component_runtime_identity(self.executor),
            "domain_registry_digest": self.domain_registry.snapshot_digest,
            "statistics_policy_digest": self.statistics_policy.policy_digest,
        }

    def observe(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        authorize_effect: Callable[[], None],
        maximum_provider_calls: int = 0,
    ) -> PipelineShadowResult:
        policy = self._statistics_policy(epoch)
        fixed_sample_size = required_sample_size(
            policy,
            alpha=policy.shadow_alpha,
            minimum_count=self.minimum_sessions,
        )
        observed_at = self.clock()
        days = _observation_days(epoch, observed_at)
        plan = _sample_plan(
            self,
            epoch,
            candidate,
            policy,
            fixed_sample_size,
            authorize_effect,
        )
        if plan is None:
            return _incomplete((), days, "minimum_shadow_window_not_met")
        planned_bindings = _bindings_for_plan(
            self,
            epoch,
            candidate,
            policy,
            plan,
        )
        assignments = _planned_assignments(
            self,
            epoch,
            candidate,
            planned_bindings,
            plan.outcome_maturity_deadline,
            authorize_effect,
        )
        planned_session_ids = tuple(item.session_id for item in plan.members)
        maturity_expired = (
            parse_utc(observed_at)
            >= parse_utc(plan.outcome_maturity_deadline)
        )
        if len(assignments) < fixed_sample_size:
            reason = (
                "shadow_outcome_maturity_expired"
                if maturity_expired
                else "shadow_baseline_outcomes_pending"
            )
            return _incomplete(
                assignments,
                days,
                reason,
                session_ids=planned_session_ids,
            )
        evidence = _read_evidence(
            self.shadow_observations,
            assignments,
            outcome_maturity_deadline=plan.outcome_maturity_deadline,
        )
        if maturity_expired and len(evidence) != len(assignments):
            return _incomplete(
                assignments,
                days,
                "shadow_outcome_maturity_expired",
                session_ids=planned_session_ids,
            )
        if days < self.minimum_days:
            return _incomplete(
                assignments,
                days,
                "minimum_shadow_window_not_met",
                session_ids=planned_session_ids,
            )
        if len(evidence) == len(assignments):
            _verify_lineage(epoch, candidate, assignments, evidence)
            return _complete_result(
                candidate,
                assignments,
                evidence,
                days,
                self.domain_registry,
                policy,
            )
        execute_pending_assignments(
            executor=self.executor,
            observations=self.shadow_observations,
            epoch=epoch,
            candidate=candidate,
            assignments=assignments,
            maximum_provider_calls=maximum_provider_calls,
            authorize_effect=authorize_effect,
        )
        evidence = _read_evidence(
            self.shadow_observations,
            assignments,
            outcome_maturity_deadline=plan.outcome_maturity_deadline,
        )
        if len(evidence) != len(assignments):
            return _incomplete(
                assignments,
                days,
                "shadow_observations_pending",
                session_ids=planned_session_ids,
            )
        _verify_lineage(epoch, candidate, assignments, evidence)
        return _complete_result(
            candidate,
            assignments,
            evidence,
            days,
            self.domain_registry,
            policy,
        )

    def _statistics_policy(
        self,
        epoch: OptimizationEpoch,
    ) -> OptimizationStatisticsPolicy:
        return resolve_statistics_policy(
            epoch.statistics_policy_digest,
            configured_policy=self.statistics_policy,
        )


def _sample_plan(
    port: LocalProspectiveShadowPort,
    epoch: OptimizationEpoch,
    candidate: OptimizationCandidate,
    statistics_policy: OptimizationStatisticsPolicy,
    fixed_sample_size: int,
    authorize_effect: Callable[[], None],
) -> OptimizationShadowSamplePlan | None:
    existing = port.assignments.sample_plan(epoch.epoch_id)
    if existing is not None:
        _verify_sample_plan(
            existing,
            epoch,
            candidate,
            statistics_policy,
            fixed_sample_size,
        )
        return existing
    candidates = tuple(
        item
        for item in port.bindings.read_all()
        if item.control_sequence > epoch.session_sequence_high_watermark
        and port.domain_registry.matches_shadow(item, candidate)
    )
    if len(candidates) < fixed_sample_size:
        return None
    selected = candidates[:fixed_sample_size]
    plan = OptimizationShadowSamplePlan(
        plan_id=stable_id(
            "optimization-shadow-sample-plan",
            epoch.project_id,
            epoch.epoch_id,
        ),
        project_id=epoch.project_id,
        epoch_id=epoch.epoch_id,
        finalist_candidate_digest=candidate.candidate_digest,
        statistics_policy_digest=statistics_policy.policy_digest,
        statistical_alpha=statistics_policy.shadow_alpha,
        fixed_sample_size=fixed_sample_size,
        outcome_maturity_deadline=_outcome_maturity_deadline(
            selected,
            statistics_policy,
        ),
        members=tuple(
            OptimizationShadowSampleMember(
                session_id=item.session_id,
                session_sequence=item.control_sequence,
                binding_digest=item.binding_digest,
            )
            for item in selected
        ),
    )
    return commit_effect(
        authorize_effect,
        lambda: port.assignments.commit_sample_plan(plan),
    )


def _verify_sample_plan(
    plan: OptimizationShadowSamplePlan,
    epoch: OptimizationEpoch,
    candidate: OptimizationCandidate,
    policy: OptimizationStatisticsPolicy,
    fixed_sample_size: int,
) -> None:
    lineage = (
        plan.project_id == epoch.project_id,
        plan.epoch_id == epoch.epoch_id,
        plan.finalist_candidate_digest == candidate.candidate_digest,
        plan.statistics_policy_digest == policy.policy_digest,
        plan.statistical_alpha == policy.shadow_alpha,
        plan.fixed_sample_size == fixed_sample_size,
    )
    if not all(lineage):
        raise SharedStateIntegrityError("shadow sample plan lineage diverged")


def _bindings_for_plan(
    port: LocalProspectiveShadowPort,
    epoch: OptimizationEpoch,
    candidate: OptimizationCandidate,
    policy: OptimizationStatisticsPolicy,
    plan: OptimizationShadowSamplePlan,
) -> tuple[CommittedSessionBinding, ...]:
    by_session = {item.session_id: item for item in port.bindings.read_all()}
    bindings = []
    for member in plan.members:
        binding = by_session.get(member.session_id)
        if binding is None:
            raise SharedStateIntegrityError(
                "shadow sample plan binding is unavailable"
            )
        lineage = (
            binding.binding_digest == member.binding_digest,
            binding.control_sequence == member.session_sequence,
            binding.control_sequence > epoch.session_sequence_high_watermark,
            port.domain_registry.matches_shadow(binding, candidate),
        )
        if not all(lineage):
            raise SharedStateIntegrityError(
                "shadow sample plan binding lineage diverged"
            )
        bindings.append(binding)
    resolved = tuple(bindings)
    if plan.outcome_maturity_deadline != _outcome_maturity_deadline(
        resolved,
        policy,
    ):
        raise SharedStateIntegrityError(
            "shadow sample plan maturity deadline diverged"
        )
    return resolved


def _outcome_maturity_deadline(
    bindings: tuple[CommittedSessionBinding, ...],
    policy: OptimizationStatisticsPolicy,
) -> str:
    latest_commit = max(parse_utc(item.committed_at) for item in bindings)
    return utc_iso(
        latest_commit + timedelta(days=policy.outcome_maturity_days)
    )


def _planned_assignments(
    port: LocalProspectiveShadowPort,
    epoch: OptimizationEpoch,
    candidate: OptimizationCandidate,
    bindings: tuple[CommittedSessionBinding, ...],
    outcome_maturity_deadline: str,
    authorize_effect: Callable[[], None],
) -> tuple[OptimizationShadowAssignment, ...]:
    policy = port.usage_policy_source(epoch.baseline_snapshot_digest)
    return _assignments(
        port.assignments,
        port.observations,
        bindings,
        epoch,
        candidate,
        policy,
        outcome_maturity_deadline,
        authorize_effect,
    )


def _complete_result(
    candidate: OptimizationCandidate,
    assignments: tuple[OptimizationShadowAssignment, ...],
    evidence: tuple[OptimizationShadowObservation, ...],
    days: int,
    domain_registry: CandidateDomainRegistry | None = None,
    statistics_policy: OptimizationStatisticsPolicy | None = None,
) -> PipelineShadowResult:
    if domain_registry is None:
        from ai_sdlc.core.stage_review.optimization.candidate_domain_defaults import (
            default_candidate_domain_registry,
        )

        domain_registry = default_candidate_domain_registry()
    metrics = _comparison_metrics(evidence)
    improved = sum(
        domain_registry.shadow_improved(candidate, item) for item in evidence
    )
    policy = statistics_policy or baseline_statistics_policy()
    _, power, lower = binary_improvement_statistics(
        improved,
        len(evidence),
        alpha=policy.shadow_alpha,
        policy=policy,
    )
    independent = all(
        item.evaluation_binding_id != candidate.generator_identity
        and item.evaluation_provider_id != candidate.generator_provider_id
        for item in evidence
    )
    return PipelineShadowResult(
        complete=True,
        evidence_digest=canonical_digest(evidence, CanonicalizationPolicy()),
        session_ids=tuple(item.session_id for item in assignments),
        observation_days=days,
        quality_confidence_lower=lower,
        metrics=metrics,
        guard_results={
            "assignment_isolated": _assignments_are_isolated(),
            "authority_label_lineage": all(
                item.label_source_digests
                and not item.challenger.unconfirmed_finding
                for item in evidence
            ),
            "candidate_evaluator_independent": independent,
            "minimum_statistical_power": (
                power >= policy.minimum_statistical_power
            ),
            "provider_lineage_complete": _provider_lineage_complete(evidence),
        },
        evaluation_binding_id=stable_id(
            "shadow-evaluation-bindings",
            *sorted({item.evaluation_binding_id for item in evidence}),
        ),
        improved_count=improved,
        sample_count=len(evidence),
        statistics_policy_digest=policy.policy_digest,
        statistical_alpha=policy.shadow_alpha,
        statistical_power=power,
    )


def _incomplete(
    assignments: tuple[OptimizationShadowAssignment, ...],
    days: int,
    reason: str,
    *,
    session_ids: tuple[str, ...] | None = None,
) -> PipelineShadowResult:
    return PipelineShadowResult(
        complete=False,
        reason=reason,
        session_ids=(
            tuple(item.session_id for item in assignments)
            if session_ids is None
            else session_ids
        ),
        observation_days=days,
    )


def _assignments(
    store: OptimizationShadowAssignmentStore,
    observations: OptimizationObservationStore,
    candidates: tuple[CommittedSessionBinding, ...],
    epoch: OptimizationEpoch,
    candidate: OptimizationCandidate,
    policy: ProviderUsageEstimatePolicy,
    outcome_maturity_deadline: str,
    authorize_effect: Callable[[], None],
) -> tuple[OptimizationShadowAssignment, ...]:
    assigned: list[OptimizationShadowAssignment] = []
    for item in candidates:
        if not _has_terminal_observation(
            item,
            observations,
            outcome_maturity_deadline=outcome_maturity_deadline,
        ):
            continue
        assigned.append(
            _commit_assignment(
                store,
                observations,
                item,
                epoch,
                candidate,
                authorize_effect,
                policy,
                outcome_maturity_deadline,
            )
        )
    return tuple(assigned)


def _commit_assignment(
    store: OptimizationShadowAssignmentStore,
    observations: OptimizationObservationStore,
    binding: CommittedSessionBinding,
    epoch: OptimizationEpoch,
    candidate: OptimizationCandidate,
    authorize_effect: Callable[[], None],
    policy: ProviderUsageEstimatePolicy,
    outcome_maturity_deadline: str,
) -> OptimizationShadowAssignment:
    return commit_effect(
        authorize_effect,
        lambda: store.assign(
            epoch_id=epoch.epoch_id,
            finalist_candidate_digest=candidate.candidate_digest,
            session=_shadow_input(
                binding,
                observations,
                baseline_snapshot_digest=epoch.baseline_snapshot_digest,
                usage_policy=policy,
                outcome_maturity_deadline=outcome_maturity_deadline,
            ),
            epoch_session_sequence_high_watermark=(
                epoch.session_sequence_high_watermark
            ),
        ),
    )


def _read_evidence(
    store: OptimizationShadowObservationStore,
    assignments: tuple[OptimizationShadowAssignment, ...],
    *,
    outcome_maturity_deadline: str,
) -> tuple[OptimizationShadowObservation, ...]:
    values = tuple(store.read_assignment(item.assignment_id) for item in assignments)
    cutoff = parse_utc(outcome_maturity_deadline)
    return tuple(
        item
        for item in values
        if item is not None and parse_utc(item.observed_at) <= cutoff
    )


def _verify_lineage(
    epoch: OptimizationEpoch,
    candidate: OptimizationCandidate,
    assignments: tuple[OptimizationShadowAssignment, ...],
    evidence: tuple[OptimizationShadowObservation, ...],
) -> None:
    by_id = {item.assignment_id: item for item in assignments}
    for item in evidence:
        assignment = by_id.get(item.assignment_id)
        expected = () if assignment is None else (
            item.project_id == epoch.project_id,
            item.epoch_id == epoch.epoch_id,
            item.finalist_candidate_digest == candidate.candidate_digest,
            item.assignment_digest == assignment.assignment_digest,
            item.session_id == assignment.session_id,
            item.active_baseline_result_digest
            == assignment.active_baseline_result_digest,
        )
        if not expected or not all(expected):
            raise SharedStateIntegrityError("shadow observation lineage diverged")


def _comparison_metrics(
    evidence: tuple[OptimizationShadowObservation, ...],
) -> ShadowComparisonMetrics:
    count = len(evidence)

    def flag_delta(name: str) -> float:
        return sum(
            int(getattr(item.challenger, name)) - int(getattr(item.baseline, name))
            for item in evidence
        ) / count

    def outcome_delta(name: str) -> float:
        return sum(
            int(item.challenger.terminal_outcome == name)
            - int(item.baseline.terminal_outcome == name)
            for item in evidence
        ) / count

    bad_flags = {name: flag_delta(name) for name in _BAD_FLAGS}
    bad_outcomes = {name: outcome_delta(name) for name in _BAD_OUTCOMES}
    return ShadowComparisonMetrics(
        critical_detection_delta=flag_delta("critical_detected"),
        late_critical_delta=bad_flags["late_critical"],
        reviewer_coverage_leak_delta=bad_flags["reviewer_coverage_leak"],
        false_positive_delta=bad_flags["false_positive"],
        reversal_delta=bad_flags["reversal"],
        stage_reopen_delta=bad_flags["stage_reopened"],
        needs_user_delta=bad_outcomes["needs_user"],
        blocked_delta=bad_outcomes["blocked"],
        timeout_delta=bad_outcomes["timed_out"],
        abandon_delta=bad_outcomes["abandoned"],
        hard_budget_exhausted_delta=bad_outcomes["hard_budget_exhausted"],
        unknown_or_censored_delta=bad_outcomes["unknown_or_censored"],
    )


def _provider_lineage_complete(
    evidence: tuple[OptimizationShadowObservation, ...],
) -> bool:
    return all(
        item.provider_invocation_id
        and item.provider_submission_digest
        and item.validation_digest
        and item.resource_settlement_event_digest
        for item in evidence
    )


def _assignments_are_isolated() -> bool:
    forbidden = {
        "panel_plan_digest",
        "quorum_digest",
        "finding_ledger_digest",
        "certificate_digest",
        "session_budget_digest",
    }
    return not forbidden & set(OptimizationShadowAssignment.model_fields)


def _has_terminal_observation(
    binding: CommittedSessionBinding,
    observations: OptimizationObservationStore,
    *,
    outcome_maturity_deadline: str,
) -> bool:
    cutoff = parse_utc(outcome_maturity_deadline)
    return any(
        item.observation_kind in TERMINAL_OBSERVATION_KINDS
        and parse_utc(item.occurred_at) <= cutoff
        for item in observations.read_session(binding.session_id)
    )


def _shadow_input(
    binding: CommittedSessionBinding,
    observations: OptimizationObservationStore,
    *,
    baseline_snapshot_digest: str,
    usage_policy: ProviderUsageEstimatePolicy,
    outcome_maturity_deadline: str,
) -> ShadowSessionInput:
    cutoff = parse_utc(outcome_maturity_deadline)
    values = tuple(
        item
        for item in observations.read_session(binding.session_id)
        if parse_utc(item.occurred_at) <= cutoff
    )
    terminal = next(
        item
        for item in reversed(values)
        if item.observation_kind in TERMINAL_OBSERVATION_KINDS
    )
    return ShadowSessionInput(
        session_id=binding.session_id,
        session_sequence=binding.control_sequence,
        initial_candidate_digest=binding.initial_candidate_digest,
        risk_profile_digest=stable_id(
            "shadow-risk", binding.stage_key, binding.risk_level
        ),
        visible_evidence_digest=canonical_digest(values, CanonicalizationPolicy()),
        active_baseline_result_digest=terminal.observation_digest,
        baseline_snapshot_digest=baseline_snapshot_digest,
        usage_estimation_policy_version=usage_policy.version,
        usage_estimation_policy_digest=usage_policy.policy_digest,
    )


def _observation_days(epoch: OptimizationEpoch, observed_at: str) -> int:
    if not epoch.started_at:
        return 0
    delta = parse_utc(observed_at) - parse_utc(epoch.started_at)
    return max(0, delta.days)
