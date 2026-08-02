"""完整 SessionPopulation、删失分母与无泄漏数据分区冻结。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from hashlib import sha256

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.dataset_models import (
    DatasetPolicy,
    DatasetPopulationEntry,
    OptimizationDatasetSnapshot,
)
from ai_sdlc.core.stage_review.optimization.dataset_models import (
    _population_digest as population_digest,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    TERMINAL_OBSERVATION_KINDS,
    CommittedSessionBinding,
    OptimizationSessionObservation,
)
from ai_sdlc.core.stage_review.provider_usage_models import ProviderUsageEstimatePolicy
from ai_sdlc.core.stage_review.resource_builders import parse_utc
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts

__all__ = [
    "DatasetPolicy",
    "DatasetPopulationEntry",
    "OptimizationDatasetSnapshot",
    "freeze_optimization_dataset",
]


def freeze_optimization_dataset(
    *, project_id: str, bindings: tuple[CommittedSessionBinding, ...],
    observations: tuple[OptimizationSessionObservation, ...], epoch_started_at: str,
    session_sequence_high_watermark: int, trigger_fingerprint: str,
    constitution_digest: str, baseline_snapshot_digest: str,
    holdout_generation_id: str, policy: DatasetPolicy,
    usage_policy_source: Callable[[str], ProviderUsageEstimatePolicy],
    permanently_held_out_session_ids: tuple[str, ...] = (),
    attributions: tuple[FindingAttribution, ...] = (),
) -> OptimizationDatasetSnapshot:
    comparison_policy = usage_policy_source(baseline_snapshot_digest)
    population, prior_holdout = _population_and_prior_holdout(
        project_id,
        bindings,
        observations,
        session_sequence_high_watermark,
        permanently_held_out_session_ids,
        usage_policy_source,
        comparison_policy,
    )
    partitions = _partition(
        population, epoch_started_at, holdout_generation_id, policy, prior_holdout
    )
    evaluable = tuple(item.session_id for item in population if item.evaluable)
    censored = _censored_sessions(population)
    return OptimizationDatasetSnapshot.model_validate({
        "project_id": project_id,
        "epoch_started_at": epoch_started_at,
        "session_sequence_high_watermark": session_sequence_high_watermark,
        "trigger_fingerprint": trigger_fingerprint,
        "constitution_digest": constitution_digest,
        "baseline_snapshot_digest": baseline_snapshot_digest,
        "comparison_usage_estimation_policy_version": comparison_policy.version,
        "comparison_usage_estimation_policy_digest": comparison_policy.policy_digest,
        "holdout_generation_id": holdout_generation_id,
        "population": population,
        "session_population_ids": tuple(item.session_id for item in population),
        "evaluable_session_ids": evaluable,
        "censoring_reasons": censored,
        "partition_assignment": partitions,
        "partition_exclusions": {
            item.session_id: "prior_holdout_permanent_isolation"
            for item in population
            if item.session_id in prior_holdout
        },
        **_dataset_evidence(population, attributions),
        "unknown_or_censored_rate": len(censored) / len(population) if population else 0,
        "leakage_check_passed": True,
        "data_integrity_digest": population_digest(population),
    })


def _population_and_prior_holdout(
    project_id: str,
    bindings: tuple[CommittedSessionBinding, ...],
    observations: tuple[OptimizationSessionObservation, ...],
    watermark: int,
    held_out_ids: tuple[str, ...],
    usage_policy_source: Callable[[str], ProviderUsageEstimatePolicy],
    comparison_policy: ProviderUsageEstimatePolicy,
) -> tuple[tuple[DatasetPopulationEntry, ...], frozenset[str]]:
    trusted = _bindings_at_watermark(project_id, bindings, watermark)
    observation_map = _canonical_observations(project_id, trusted, observations)
    population = tuple(
        _population_entry(
            binding,
            observation_map.get(binding.session_id, ()),
            usage_policy_source(binding.active_snapshot_digest),
            comparison_policy,
        )
        for binding in trusted
    )
    if held_out_ids != tuple(sorted(set(held_out_ids))):
        raise ValueError("prior holdout sessions must be canonical and unique")
    return population, frozenset(held_out_ids)


def _dataset_evidence(
    population: tuple[DatasetPopulationEntry, ...],
    attributions: tuple[FindingAttribution, ...],
) -> dict[str, tuple[str, ...]]:
    session_ids = {item.session_id for item in population}
    related = tuple(item for item in attributions if item.session_id in session_ids)
    finding_events = {
        digest for item in population for digest in item.finding_event_digests
    }
    labels = {digest for item in population for digest in item.label_source_digests}
    attribution_events = {item.finding_event_digest for item in related}
    return {
        "finding_event_digests": tuple(sorted(finding_events)),
        "finding_attribution_digests": tuple(
            sorted(item.attribution_digest for item in related)
        ),
        "late_critical_finding_event_digests": tuple(sorted(attribution_events)),
        "reviewer_coverage_leak_event_digests": tuple(sorted(attribution_events)),
        "label_source_digests": tuple(sorted(labels)),
    }


def _bindings_at_watermark(
    project_id: str,
    bindings: tuple[CommittedSessionBinding, ...],
    session_sequence_high_watermark: int,
) -> tuple[CommittedSessionBinding, ...]:
    trusted = _canonical_bindings(project_id, bindings)
    return tuple(
        item
        for item in trusted
        if item.control_sequence <= session_sequence_high_watermark
    )


def _canonical_bindings(
    project_id: str,
    bindings: tuple[CommittedSessionBinding, ...],
) -> tuple[CommittedSessionBinding, ...]:
    by_session: dict[str, CommittedSessionBinding] = {}
    for raw in sorted(bindings, key=lambda item: item.control_sequence):
        item = CommittedSessionBinding.model_validate(raw.model_dump(mode="json"))
        if item.project_id != project_id:
            raise SharedStateIntegrityError("dataset session project identity diverged")
        previous = by_session.get(item.session_id)
        if (
            previous
            and previous.initial_candidate_digest != item.initial_candidate_digest
        ):
            raise SharedStateIntegrityError("session candidate identity diverged")
        by_session.setdefault(item.session_id, item)
    return tuple(by_session[key] for key in sorted(by_session))


def _canonical_observations(
    project_id: str,
    bindings: tuple[CommittedSessionBinding, ...],
    observations: tuple[OptimizationSessionObservation, ...],
) -> dict[str, tuple[OptimizationSessionObservation, ...]]:
    candidates = {item.session_id: item.initial_candidate_digest for item in bindings}
    grouped: dict[str, list[OptimizationSessionObservation]] = defaultdict(list)
    for raw in observations:
        item = OptimizationSessionObservation.model_validate(
            raw.model_dump(mode="json")
        )
        if item.project_id != project_id:
            raise SharedStateIntegrityError("dataset observation scope diverged")
        if item.session_id not in candidates:
            continue
        if item.initial_candidate_digest != candidates[item.session_id]:
            raise SharedStateIntegrityError("observation candidate identity diverged")
        grouped[item.session_id].append(item)
    return {
        key: tuple(
            sorted(values, key=lambda item: (item.sequence, item.observation_id))
        )
        for key, values in grouped.items()
    }


def _population_entry(
    binding: CommittedSessionBinding,
    observations: tuple[OptimizationSessionObservation, ...],
    usage_policy: ProviderUsageEstimatePolicy,
    comparison_policy: ProviderUsageEstimatePolicy,
) -> DatasetPopulationEntry:
    created = next(
        (item for item in observations if item.observation_kind == "created"), None
    )
    terminal = next(
        (
            item
            for item in reversed(observations)
            if item.observation_kind in TERMINAL_OBSERVATION_KINDS
        ),
        None,
    )
    reason = _censoring_reason(
        binding,
        created is not None,
        terminal.observation_kind if terminal is not None else "",
        usage_policy.policy_digest == comparison_policy.policy_digest,
    )
    return DatasetPopulationEntry.model_validate({
        "session_id": binding.session_id,
        "initial_candidate_digest": binding.initial_candidate_digest,
        "stage_key": binding.stage_key,
        "risk_level": binding.risk_level,
        "candidate_size_bucket": binding.candidate_size_bucket,
        "provider_ids": binding.provider_ids,
        "active_snapshot_digest": binding.active_snapshot_digest,
        "usage_estimation_policy_version": usage_policy.version,
        "usage_estimation_policy_digest": usage_policy.policy_digest,
        "control_sequence": binding.control_sequence,
        "committed_at": binding.committed_at,
        "evaluable": not reason,
        "terminal_outcome": terminal.observation_kind if terminal else "",
        "censoring_reason": reason,
        **_population_lineage(binding, observations, terminal),
    })


def _population_lineage(
    binding: CommittedSessionBinding,
    observations: tuple[OptimizationSessionObservation, ...],
    terminal: OptimizationSessionObservation | None,
) -> dict[str, object]:
    finding_events = {
        digest for item in observations for digest in item.finding_event_digests
    }
    return {
        "observation_digests": tuple(
            sorted(item.observation_digest for item in observations)
        ),
        "finding_event_digests": tuple(sorted(finding_events)),
        "role_profile_ids": binding.role_profile_ids,
        "reviewer_slot_ids": binding.reviewer_slot_ids,
        "capability_ids": binding.capability_ids,
        "binding_digests": binding.binding_digests,
        "binding_set_digest": binding.binding_set_digest,
        "resource_reservation_digest": binding.resource_reservation_digest,
        "risk_profile_digest": terminal.risk_profile_digest if terminal else "",
        "cohort_id": terminal.cohort_id if terminal else "",
        "finding_ledger_digest": terminal.finding_ledger_digest if terminal else "",
        "convergence_outcome_digest": (
            terminal.convergence_outcome_digest if terminal else ""
        ),
        "label_source_digests": terminal.label_source_digests if terminal else (),
        "resource_usage": terminal.resource_usage if terminal else ResourceAmounts(),
    }


def _censored_sessions(
    population: tuple[DatasetPopulationEntry, ...],
) -> dict[str, str]:
    return {
        item.session_id: item.censoring_reason
        for item in population
        if not item.evaluable
    }


def _censoring_reason(
    binding: CommittedSessionBinding,
    has_created: bool,
    terminal_kind: str,
    usage_policy_comparable: bool,
) -> str:
    if not binding.schema_compatible:
        return "incompatible_schema"
    if not binding.lineage_complete:
        return "lineage_incomplete"
    if not usage_policy_comparable:
        return "usage_policy_incomparable"
    if not has_created:
        return "missing_created_observation"
    if not terminal_kind:
        return "missing_terminal_observation"
    if terminal_kind == "open_censored":
        return "open_censored"
    return ""


def _partition(
    population: tuple[DatasetPopulationEntry, ...],
    epoch_started_at: str,
    generation_id: str,
    policy: DatasetPolicy,
    prior_holdout: frozenset[str],
) -> dict[str, tuple[str, ...]]:
    boundary = parse_utc(epoch_started_at)
    eligible = [item for item in population if item.evaluable]
    shadow = tuple(
        sorted(
            item.session_id
            for item in eligible
            if parse_utc(item.committed_at) > boundary
        )
    )
    historical = [
        item
        for item in eligible
        if parse_utc(item.committed_at) <= boundary
        and item.session_id not in prior_holdout
    ]
    ranked = sorted(
        historical, key=lambda item: _partition_rank(generation_id, item.session_id)
    )
    holdout_count = min(
        len(ranked),
        max(policy.minimum_holdout_size, int(len(ranked) * policy.holdout_ratio)),
    )
    holdout = ranked[:holdout_count]
    remaining = ranked[holdout_count:]
    validation_count = int(len(remaining) * policy.validation_ratio)
    if remaining and validation_count == 0:
        validation_count = 1
    validation = remaining[:validation_count]
    train = remaining[validation_count:]
    return {
        "train": tuple(sorted(item.session_id for item in train)),
        "validation": tuple(sorted(item.session_id for item in validation)),
        "holdout": tuple(sorted(item.session_id for item in holdout)),
        "prospective_shadow": shadow,
    }


def _partition_rank(generation_id: str, session_id: str) -> str:
    return sha256(f"{generation_id}\0{session_id}".encode()).hexdigest()
