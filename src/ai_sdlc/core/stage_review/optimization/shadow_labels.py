"""用现有 Finding Authority 与终态事实标注 Shadow 对照结果。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.finding_models import FindingEvent
from ai_sdlc.core.stage_review.optimization.models import OptimizationCandidate
from ai_sdlc.core.stage_review.optimization.observations import (
    OptimizationSessionObservation,
)
from ai_sdlc.core.stage_review.optimization.shadow_observations import (
    ShadowOutcome,
    ShadowTerminalOutcome,
)
from ai_sdlc.core.stage_review.remote_review_models import (
    RemoteReviewFinding,
    RemoteReviewOutput,
)


def labeled_shadow_outcomes(
    *,
    candidate: OptimizationCandidate,
    baseline_observation: OptimizationSessionObservation,
    review: RemoteReviewOutput,
    finding_events: tuple[FindingEvent, ...],
) -> tuple[ShadowOutcome, ShadowOutcome, tuple[str, ...]]:
    late, initial = _critical_events(finding_events)
    matched = {
        event.event_digest
        for event in late
        if any(_matches(finding, event) for finding in review.findings)
    }
    unmatched = _unmatched_findings(review, (*late, *initial))
    baseline = ShadowOutcome(
        critical_detected=bool(initial),
        late_critical=bool(late),
        reviewer_coverage_leak=any(
            item.reviewer_coverage_leak is not None for item in late
        ),
        reversal=_reversal(finding_events),
        terminal_outcome=_terminal_outcome(baseline_observation.observation_kind),
    )
    challenger = _challenger_outcome(
        candidate, baseline_observation, baseline, late, matched, unmatched
    )
    return baseline, challenger, _label_digests(baseline_observation, finding_events)


def _matches(finding: RemoteReviewFinding, event: FindingEvent) -> bool:
    identity = event.identity
    return bool(
        identity is not None
        and finding.identity.identity_digest == identity.identity_digest
        and finding.capability_id == event.capability_id
        and finding.severity in {"P0", "P1"}
    )


def _critical_events(
    events: tuple[FindingEvent, ...],
) -> tuple[tuple[FindingEvent, ...], tuple[FindingEvent, ...]]:
    return (
        tuple(item for item in events if _is_late_critical(item)),
        tuple(item for item in events if _is_initial_critical(item)),
    )


def _unmatched_findings(
    review: RemoteReviewOutput,
    authority_events: tuple[FindingEvent, ...],
) -> tuple[RemoteReviewFinding, ...]:
    return tuple(
        finding
        for finding in review.findings
        if not any(_matches(finding, event) for event in authority_events)
    )


def _challenger_outcome(
    candidate: OptimizationCandidate,
    baseline_observation: OptimizationSessionObservation,
    baseline: ShadowOutcome,
    late: tuple[FindingEvent, ...],
    matched: set[str],
    unmatched: tuple[RemoteReviewFinding, ...],
) -> ShadowOutcome:
    unresolved = bool(unmatched)
    return ShadowOutcome(
        critical_detected=bool(baseline.critical_detected or matched),
        late_critical=any(item.event_digest not in matched for item in late),
        reviewer_coverage_leak=any(
            item.event_digest not in matched
            and item.reviewer_coverage_leak is not None
            for item in late
        ),
        reversal=baseline.reversal,
        unconfirmed_finding=unresolved,
        terminal_outcome=(
            "unknown_or_censored"
            if unresolved
            else _challenger_terminal(candidate, baseline_observation)
        ),
    )


def _label_digests(
    baseline: OptimizationSessionObservation,
    events: tuple[FindingEvent, ...],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                baseline.observation_digest,
                *baseline.label_source_digests,
                *(item.event_digest for item in events),
            }
        )
    )


def _is_late_critical(event: FindingEvent) -> bool:
    return event.late_critical_finding is not None and event.severity in {"P0", "P1"}


def _is_initial_critical(event: FindingEvent) -> bool:
    return (
        event.event_type == "initial_discovered"
        and event.late_critical_finding is None
        and event.severity in {"P0", "P1"}
    )


def _reversal(events: tuple[FindingEvent, ...]) -> bool:
    return any(item.event_type in {"regressed", "verification_failed"} for item in events)


def _terminal_outcome(kind: str) -> ShadowTerminalOutcome:
    mapping: dict[str, ShadowTerminalOutcome] = {
        "abandoned": "abandoned",
        "blocked": "blocked",
        "consumed": "consumed",
        "hard_budget_exhausted": "hard_budget_exhausted",
        "needs_user": "needs_user",
        "timed_out": "timed_out",
    }
    return mapping.get(kind, "unknown_or_censored")


def _challenger_terminal(
    candidate: OptimizationCandidate,
    baseline: OptimizationSessionObservation,
) -> ShadowTerminalOutcome:
    current = _terminal_outcome(baseline.observation_kind)
    if (
        candidate.candidate_domain == "budget"
        and current == "hard_budget_exhausted"
        and _budget_covers(candidate, baseline)
    ):
        return "consumed"
    return current


def _budget_covers(
    candidate: OptimizationCandidate,
    observation: OptimizationSessionObservation,
) -> bool:
    usage = observation.resource_usage
    values = _numeric_patch_values(candidate)
    actual = {
        "hard_provider_calls": usage.provider_calls,
        "hard_review_passes": usage.review_passes,
        "hard_tokens": usage.tokens,
        "hard_wall_clock": usage.active_wall_clock,
        "maximum_slots": usage.slots,
    }
    return all(
        values.get(name, float("-inf")) >= amount
        for name, amount in actual.items()
    )


def _numeric_patch_values(candidate: OptimizationCandidate) -> dict[str, float]:
    values: dict[str, float] = {}
    for item in candidate.patch_operations:
        if isinstance(item.value, (int, float)) and not isinstance(item.value, bool):
            values[item.field_path.rsplit(".", 1)[-1]] = float(item.value)
    return values


__all__ = ["labeled_shadow_outcomes"]
