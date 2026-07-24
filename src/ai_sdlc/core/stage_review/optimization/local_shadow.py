"""在未来 Session 上汇总独立、不可变的 Prospective Shadow 对照证据。"""

from __future__ import annotations

from collections.abc import Callable

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.optimization.candidate_domain_registry import (
    CandidateDomainRegistry,
)
from ai_sdlc.core.stage_review.optimization.controller_models import OptimizationEpoch
from ai_sdlc.core.stage_review.optimization.models import OptimizationCandidate
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
from ai_sdlc.core.stage_review.provider_usage_models import ProviderUsageEstimatePolicy
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id

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

    def observe(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        authorize_effect: Callable[[], None],
        maximum_provider_calls: int = 0,
    ) -> PipelineShadowResult:
        assignments = _eligible_assignments(
            self, epoch, candidate, authorize_effect
        )
        days = _observation_days(epoch, self.clock())
        if len(assignments) < self.minimum_sessions or days < self.minimum_days:
            return _incomplete(assignments, days, "minimum_shadow_window_not_met")
        execute_pending_assignments(
            executor=self.executor,
            observations=self.shadow_observations,
            epoch=epoch,
            candidate=candidate,
            assignments=assignments,
            maximum_provider_calls=maximum_provider_calls,
            authorize_effect=authorize_effect,
        )
        evidence = _read_evidence(self.shadow_observations, assignments)
        if len(evidence) != len(assignments):
            return _incomplete(assignments, days, "shadow_observations_pending")
        _verify_lineage(epoch, candidate, assignments, evidence)
        return _complete_result(
            candidate, assignments, evidence, days, self.domain_registry
        )


def _eligible_assignments(
    port: LocalProspectiveShadowPort,
    epoch: OptimizationEpoch,
    candidate: OptimizationCandidate,
    authorize_effect: Callable[[], None],
) -> tuple[OptimizationShadowAssignment, ...]:
    candidates = tuple(
        item
        for item in port.bindings.read_all()
        if item.control_sequence > epoch.session_sequence_high_watermark
        and port.domain_registry.matches_shadow(item, candidate)
    )
    policy = port.usage_policy_source(epoch.baseline_snapshot_digest)
    return _assignments(
        port.assignments,
        port.observations,
        candidates,
        epoch,
        candidate,
        policy,
        authorize_effect,
    )


def _complete_result(
    candidate: OptimizationCandidate,
    assignments: tuple[OptimizationShadowAssignment, ...],
    evidence: tuple[OptimizationShadowObservation, ...],
    days: int,
    domain_registry: CandidateDomainRegistry | None = None,
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
    _, power, lower = binary_improvement_statistics(improved, len(evidence))
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
            "minimum_statistical_power": power >= 0.8,
            "provider_lineage_complete": _provider_lineage_complete(evidence),
        },
        evaluation_binding_id=stable_id(
            "shadow-evaluation-bindings",
            *sorted({item.evaluation_binding_id for item in evidence}),
        ),
    )


def _incomplete(
    assignments: tuple[OptimizationShadowAssignment, ...],
    days: int,
    reason: str,
) -> PipelineShadowResult:
    return PipelineShadowResult(
        complete=False,
        reason=reason,
        session_ids=tuple(item.session_id for item in assignments),
        observation_days=days,
    )


def _assignments(
    store: OptimizationShadowAssignmentStore,
    observations: OptimizationObservationStore,
    candidates: tuple[CommittedSessionBinding, ...],
    epoch: OptimizationEpoch,
    candidate: OptimizationCandidate,
    policy: ProviderUsageEstimatePolicy,
    authorize_effect: Callable[[], None],
) -> tuple[OptimizationShadowAssignment, ...]:
    assigned: list[OptimizationShadowAssignment] = []
    for item in candidates:
        if not _has_terminal_observation(item, observations):
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
            ),
            epoch_session_sequence_high_watermark=(
                epoch.session_sequence_high_watermark
            ),
        ),
    )


def _read_evidence(
    store: OptimizationShadowObservationStore,
    assignments: tuple[OptimizationShadowAssignment, ...],
) -> tuple[OptimizationShadowObservation, ...]:
    values = tuple(store.read_assignment(item.assignment_id) for item in assignments)
    return tuple(item for item in values if item is not None)


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
    binding: CommittedSessionBinding, observations: OptimizationObservationStore
) -> bool:
    return any(
        item.observation_kind in TERMINAL_OBSERVATION_KINDS
        for item in observations.read_session(binding.session_id)
    )


def _shadow_input(
    binding: CommittedSessionBinding,
    observations: OptimizationObservationStore,
    *,
    baseline_snapshot_digest: str,
    usage_policy: ProviderUsageEstimatePolicy,
) -> ShadowSessionInput:
    values = observations.read_session(binding.session_id)
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
