"""从冻结报告构造 Challenger，并由确定性 Gate 签发晋升决定。"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.candidate_domain_registry import (
    CandidateDomainRegistry,
)
from ai_sdlc.core.stage_review.optimization.candidate_policy import (
    CandidatePolicyApplier,
)
from ai_sdlc.core.stage_review.optimization.controller_models import OptimizationEpoch
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationEvaluationReport,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    PipelinePromotionPackage,
    PipelineShadowResult,
)
from ai_sdlc.core.stage_review.optimization.promotion import (
    AutoPromotionEvidence,
    AutoPromotionGate,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import OptimizationSnapshot
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts


class LocalPromotionEvaluationPort:
    def __init__(
        self,
        *,
        snapshot_source: Callable[[str], OptimizationSnapshot],
        attribution_source: Callable[[], tuple[FindingAttribution, ...]],
        gate: AutoPromotionGate,
        resource_capacity: ResourceAmounts,
        clock: Callable[[], str],
        domain_registry: CandidateDomainRegistry | None = None,
    ) -> None:
        self.snapshot_source = snapshot_source
        self.attribution_source = attribution_source
        self.gate = gate
        self.resource_capacity = resource_capacity
        self.clock = clock
        if domain_registry is None:
            from ai_sdlc.core.stage_review.optimization.candidate_domain_defaults import (
                default_candidate_domain_registry,
            )

            domain_registry = default_candidate_domain_registry()
        self.domain_registry = domain_registry
        self.applier = CandidatePolicyApplier(domain_registry)

    def evaluate(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        reports: tuple[OptimizationEvaluationReport, ...],
        shadow: PipelineShadowResult,
    ) -> PipelinePromotionPackage:
        digests = tuple(sorted(item.report_digest for item in reports))
        attributions = _candidate_attributions(candidate, self.attribution_source())
        baseline = self.snapshot_source(epoch.baseline_snapshot_digest)
        snapshot = self.applier.apply(
            candidate,
            base_snapshot=baseline,
            attributions=attributions,
            evaluation_report_digests=digests,
            created_at=self.clock(),
        )
        domain_guards = self.domain_registry.promotion_guards(
            candidate, reports, shadow
        )
        evidence = _promotion_evidence(
            epoch,
            candidate,
            reports,
            shadow,
            snapshot,
            self.resource_capacity,
            domain_guards,
        )
        decision = self.gate.evaluate(
            evidence,
            decision_id=stable_id("auto-promotion", candidate.candidate_digest),
        )
        return PipelinePromotionPackage(decision=decision, snapshot=snapshot)


def _candidate_attributions(
    candidate: OptimizationCandidate,
    values: tuple[FindingAttribution, ...],
) -> tuple[FindingAttribution, ...]:
    by_digest = {item.attribution_digest: item for item in values}
    try:
        return tuple(by_digest[digest] for digest in candidate.attribution_digests)
    except KeyError as exc:
        raise ValueError("candidate attribution is unavailable") from exc


def _promotion_evidence(
    epoch: OptimizationEpoch,
    candidate: OptimizationCandidate,
    reports: tuple[OptimizationEvaluationReport, ...],
    shadow: PipelineShadowResult,
    snapshot: OptimizationSnapshot,
    resource_capacity: ResourceAmounts,
    domain_guards: Mapping[str, bool],
) -> AutoPromotionEvidence:
    holdout = next(item for item in reports if item.partition == "holdout")
    guards = {
        f"{item.evaluator_kind}.{key}": value
        for item in reports
        for key, value in item.guard_results.items()
    }
    guards.update({f"shadow.{key}": value for key, value in shadow.guard_results.items()})
    guards.update({f"domain.{key}": value for key, value in domain_guards.items()})
    return AutoPromotionEvidence.model_validate({
        "baseline_snapshot_digest": candidate.base_snapshot_digest,
        "challenger_snapshot_digest": snapshot.snapshot_digest,
        "candidate_digest": candidate.candidate_digest,
        "evaluation_report_digests": tuple(
            sorted(item.report_digest for item in reports)
        ),
        "invariant_results": guards,
        **_quality_evidence(holdout, shadow),
        "holdout_session_count": len(holdout.comparison_session_ids),
        "shadow_session_count": len(shadow.session_ids),
        "shadow_observation_days": shadow.observation_days,
        "resources_within_constitution": _resources_within_constitution(
            epoch, candidate, resource_capacity
        ),
        "duties_independent": _duties_independent(candidate, reports, shadow),
    })


def _quality_evidence(
    holdout: OptimizationEvaluationReport, shadow: PipelineShadowResult
) -> dict[str, float]:
    if shadow.metrics is None:
        raise ValueError("complete shadow metrics are required for promotion")
    metrics = shadow.metrics
    return {
        "critical_detection_delta": _minimum_quality_delta(holdout, shadow),
        "late_critical_delta": metrics.late_critical_delta,
        "reviewer_coverage_leak_delta": metrics.reviewer_coverage_leak_delta,
        "false_positive_delta": metrics.false_positive_delta,
        "reversal_delta": metrics.reversal_delta,
        "stage_reopen_delta": metrics.stage_reopen_delta,
        "needs_user_delta": metrics.needs_user_delta,
        "blocked_delta": metrics.blocked_delta,
        "timeout_delta": metrics.timeout_delta,
        "abandon_delta": metrics.abandon_delta,
        "hard_budget_exhausted_delta": metrics.hard_budget_exhausted_delta,
        "unknown_or_censored_delta": max(
            holdout.censoring_metrics.get("unknown_or_censored", 0),
            metrics.unknown_or_censored_delta,
        ),
        "quality_confidence_lower": min(
            holdout.effect_confidence_lower, shadow.quality_confidence_lower
        ),
    }


def _minimum_quality_delta(
    holdout: OptimizationEvaluationReport, shadow: PipelineShadowResult
) -> float:
    return min(
        holdout.quality_deltas.get("critical_detection", 0),
        0 if shadow.metrics is None else shadow.metrics.critical_detection_delta,
    )


def _resources_within_constitution(
    epoch: OptimizationEpoch,
    candidate: OptimizationCandidate,
    capacity: ResourceAmounts,
) -> bool:
    estimate = ResourceAmounts(
        provider_calls=candidate.estimated_provider_calls,
        tokens=candidate.estimated_tokens,
        cost=candidate.estimated_cost,
        active_wall_clock=candidate.estimated_active_wall_clock,
    )
    projected = epoch.cumulative_usage + estimate
    return all(
        getattr(projected, name) <= getattr(capacity, name)
        for name in ResourceAmounts.ALL_FIELDS
    )


def _duties_independent(
    candidate: OptimizationCandidate,
    reports: tuple[OptimizationEvaluationReport, ...],
    shadow: PipelineShadowResult,
) -> bool:
    identities = {
        *(item.evaluation_binding_id for item in reports),
        shadow.evaluation_binding_id,
        "promotion-gate.deterministic-v1",
    }
    return (
        candidate.generator_identity not in identities
        and candidate.generator_provider_id not in identities
        and len(identities) == len(reports) + 2
    )
