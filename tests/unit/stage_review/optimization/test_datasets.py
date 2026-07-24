from __future__ import annotations

from pathlib import Path

import pytest

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.controller_models import OptimizationEpoch
from ai_sdlc.core.stage_review.optimization.datasets import (
    DatasetPolicy,
    freeze_optimization_dataset,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_usage_estimate_policy as baseline_usage_estimate_policy,
)
from ai_sdlc.core.stage_review.optimization.holdout_store import HoldoutCommitmentStore
from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBinding,
    CommittedSessionBindingStore,
    OptimizationObservationStore,
    OptimizationSessionObservation,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    _materialize_open_censored_observations as materialize_open_censored_observations,
)
from ai_sdlc.core.stage_review.optimization.pipeline_effects import allow_effect
from ai_sdlc.core.stage_review.optimization.runtime_dataset import (
    LocalDatasetSnapshotPort,
)
from ai_sdlc.core.stage_review.provider_usage_models import build_usage_estimate_policy


def test_population_keeps_committed_session_without_observation_as_censored() -> None:
    bindings = (
        _binding("session.1", "candidate.1", sequence=1),
        _binding("session.2", "candidate.2", sequence=2),
    )
    observations = (
        _observation("session.1", "candidate.1", "created", sequence=1),
        _observation("session.1", "candidate.1", "consumed", sequence=2),
    )

    snapshot = freeze_optimization_dataset(
        project_id="project.optimization",
        bindings=bindings,
        observations=observations,
        epoch_started_at="2026-07-22T12:00:00Z",
        session_sequence_high_watermark=2,
        trigger_fingerprint="sha256:trigger.1",
        constitution_digest="sha256:constitution.1",
        baseline_snapshot_digest="sha256:baseline.1",
        holdout_generation_id="holdout-generation.1",
        policy=DatasetPolicy(holdout_ratio=0.2, minimum_holdout_size=1),
        usage_policy_source=lambda _: baseline_usage_estimate_policy(),
    )

    assert snapshot.session_population_ids == ("session.1", "session.2")
    assert snapshot.evaluable_session_ids == ("session.1",)
    assert snapshot.censoring_reasons == {"session.2": "missing_created_observation"}
    assert snapshot.unknown_or_censored_rate == 0.5


def test_cost_comparison_censors_other_usage_policy_versions() -> None:
    current = baseline_usage_estimate_policy()
    previous = build_usage_estimate_policy(
        policy_id=current.policy_id,
        version="0.9.0",
        characters_per_token=8,
        estimated_cost_per_token=0.000002,
    )
    bindings = (
        _binding("session.current", "candidate.current", sequence=1),
        _binding(
            "session.previous",
            "candidate.previous",
            sequence=2,
            active_snapshot_digest="sha256:baseline.previous",
        ),
    )
    observations = tuple(
        observation
        for session_id, candidate in (
            ("session.current", "candidate.current"),
            ("session.previous", "candidate.previous"),
        )
        for observation in (
            _observation(session_id, candidate, "created", sequence=1),
            _observation(session_id, candidate, "consumed", sequence=2),
        )
    )

    snapshot = freeze_optimization_dataset(
        project_id="project.optimization",
        bindings=bindings,
        observations=observations,
        epoch_started_at="2026-07-22T12:00:00Z",
        session_sequence_high_watermark=2,
        trigger_fingerprint="sha256:trigger.usage-policy",
        constitution_digest="sha256:constitution.1",
        baseline_snapshot_digest="sha256:baseline.1",
        holdout_generation_id="holdout-generation.usage-policy",
        policy=DatasetPolicy(holdout_ratio=0.2, minimum_holdout_size=1),
        usage_policy_source=lambda digest: (
            previous if digest == "sha256:baseline.previous" else current
        ),
    )

    assert snapshot.evaluable_session_ids == ("session.current",)
    assert snapshot.censoring_reasons == {
        "session.previous": "usage_policy_incomparable"
    }
    assert snapshot.comparison_usage_estimation_policy_digest == (
        current.policy_digest
    )


def test_partitions_are_disjoint_and_post_epoch_sessions_only_enter_shadow() -> None:
    bindings = tuple(
        _binding(
            f"session.{index}",
            f"candidate.{index}",
            sequence=index,
            committed_at=(
                "2026-07-22T13:00:00Z" if index == 6 else f"2026-07-21T0{index}:00:00Z"
            ),
        )
        for index in range(1, 7)
    )
    observations = tuple(
        event
        for index in range(1, 7)
        for event in (
            _observation(
                f"session.{index}",
                f"candidate.{index}",
                "created",
                sequence=index * 2 - 1,
            ),
            _observation(
                f"session.{index}",
                f"candidate.{index}",
                "consumed",
                sequence=index * 2,
            ),
        )
    )

    snapshot = freeze_optimization_dataset(
        project_id="project.optimization",
        bindings=bindings,
        observations=observations,
        epoch_started_at="2026-07-22T12:00:00Z",
        session_sequence_high_watermark=6,
        trigger_fingerprint="sha256:trigger.2",
        constitution_digest="sha256:constitution.1",
        baseline_snapshot_digest="sha256:baseline.1",
        holdout_generation_id="holdout-generation.2",
        policy=DatasetPolicy(
            holdout_ratio=0.2,
            minimum_holdout_size=1,
            validation_ratio=0.25,
        ),
        usage_policy_source=lambda _: baseline_usage_estimate_policy(),
    )

    partitions = snapshot.partition_assignment
    assigned = [session_id for values in partitions.values() for session_id in values]
    assert len(assigned) == len(set(assigned))
    assert partitions["prospective_shadow"] == ("session.6",)
    assert "session.6" not in (
        *partitions["train"],
        *partitions["validation"],
        *partitions["holdout"],
    )
    assert snapshot.leakage_check_passed is True


def test_sessions_committed_after_trigger_watermark_are_not_backfilled() -> None:
    bindings = (
        _binding("session.1", "candidate.1", sequence=1),
        _binding("session.2", "candidate.2", sequence=2),
        _binding("session.3", "candidate.3", sequence=3),
    )
    observations = tuple(
        _observation(
            f"session.{index}",
            f"candidate.{index}",
            "created",
            sequence=index,
        )
        for index in range(1, 4)
    )

    snapshot = freeze_optimization_dataset(
        project_id="project.optimization",
        bindings=bindings,
        observations=observations,
        epoch_started_at="2026-07-22T12:00:00Z",
        session_sequence_high_watermark=2,
        trigger_fingerprint="sha256:trigger.watermark",
        constitution_digest="sha256:constitution.1",
        baseline_snapshot_digest="sha256:baseline.1",
        holdout_generation_id="holdout-generation.watermark",
        policy=DatasetPolicy(holdout_ratio=0.2, minimum_holdout_size=1),
        usage_policy_source=lambda _: baseline_usage_estimate_policy(),
    )

    assert snapshot.session_population_ids == ("session.1", "session.2")
    assert "session.3" not in snapshot.session_population_ids


def test_prior_holdout_session_never_reenters_a_later_epoch_partition() -> None:
    bindings = tuple(
        _binding(f"session.{index}", f"candidate.{index}", sequence=index)
        for index in range(1, 5)
    )
    observations = tuple(
        event
        for index in range(1, 5)
        for event in (
            _observation(
                f"session.{index}",
                f"candidate.{index}",
                "created",
                sequence=index * 2 - 1,
            ),
            _observation(
                f"session.{index}",
                f"candidate.{index}",
                "consumed",
                sequence=index * 2,
            ),
        )
    )

    snapshot = freeze_optimization_dataset(
        project_id="project.optimization",
        bindings=bindings,
        observations=observations,
        epoch_started_at="2026-07-22T12:00:00Z",
        session_sequence_high_watermark=4,
        trigger_fingerprint="sha256:trigger.prior-holdout",
        constitution_digest="sha256:constitution.1",
        baseline_snapshot_digest="sha256:baseline.1",
        holdout_generation_id="holdout-generation.later",
        policy=DatasetPolicy(holdout_ratio=0.2, minimum_holdout_size=1),
        usage_policy_source=lambda _: baseline_usage_estimate_policy(),
        permanently_held_out_session_ids=("session.1",),
    )

    assigned = {
        session_id
        for values in snapshot.partition_assignment.values()
        for session_id in values
    }
    assert "session.1" not in assigned
    assert snapshot.partition_exclusions == {
        "session.1": "prior_holdout_permanent_isolation"
    }


def test_open_censored_observation_stays_in_non_evaluable_denominator() -> None:
    binding = _binding("session.1", "candidate.1", sequence=1)
    observations = (
        _observation("session.1", "candidate.1", "created", sequence=1),
        _observation("session.1", "candidate.1", "open_censored", sequence=2),
    )

    snapshot = freeze_optimization_dataset(
        project_id="project.optimization",
        bindings=(binding,),
        observations=observations,
        epoch_started_at="2026-07-22T12:00:00Z",
        session_sequence_high_watermark=1,
        trigger_fingerprint="sha256:trigger.open",
        constitution_digest="sha256:constitution.1",
        baseline_snapshot_digest="sha256:baseline.1",
        holdout_generation_id="holdout-generation.open",
        policy=DatasetPolicy(holdout_ratio=0.2, minimum_holdout_size=1),
        usage_policy_source=lambda _: baseline_usage_estimate_policy(),
    )

    assert snapshot.evaluable_session_ids == ()
    assert snapshot.censoring_reasons == {"session.1": "open_censored"}
    assert snapshot.unknown_or_censored_rate == 1


def test_dataset_boundary_materializes_missing_terminal_as_open_censored(
    tmp_path: Path,
) -> None:
    store = OptimizationObservationStore(tmp_path, project_id="project.optimization")
    binding = _binding("session.1", "candidate.1", sequence=1)
    store.append(_observation("session.1", "candidate.1", "created", sequence=1))

    materialize_open_censored_observations(
        (binding,),
        store,
        sequence_high_watermark=1,
        occurred_at="2026-07-22T12:00:00Z",
    )

    assert tuple(item.observation_kind for item in store.read_all()) == (
        "created",
        "open_censored",
    )


def test_runtime_dataset_uses_epoch_start_not_late_freeze_time(
    tmp_path: Path,
) -> None:
    project_id = "project.optimization"
    bindings = CommittedSessionBindingStore(tmp_path, project_id=project_id)
    observations = OptimizationObservationStore(tmp_path, project_id=project_id)
    binding = _binding(
        "session.after-epoch",
        "candidate.after-epoch",
        sequence=1,
        committed_at="2026-07-22T13:00:00Z",
    )
    bindings.append(binding)
    observations.append(
        _observation(
            "session.after-epoch",
            "candidate.after-epoch",
            "created",
            sequence=1,
        )
    )
    observations.append(
        _observation(
            "session.after-epoch",
            "candidate.after-epoch",
            "consumed",
            sequence=2,
        )
    )
    port = LocalDatasetSnapshotPort(
        tmp_path,
        project_id=project_id,
        snapshots=_NoopSnapshotPopulationRecovery(),  # type: ignore[arg-type]
        bindings=bindings,
        observations=observations,
        holdout_commitments=HoldoutCommitmentStore(
            tmp_path,
            project_id=project_id,
            familywise_alpha=0.05,
        ),
        policy=DatasetPolicy(holdout_ratio=0.2, minimum_holdout_size=1),
        clock=lambda: "2026-07-22T14:00:00Z",
        usage_policy_source=lambda _: baseline_usage_estimate_policy(),
    )
    epoch = OptimizationEpoch(
        epoch_id="optimization-epoch.boundary",
        project_id=project_id,
        trigger_fingerprint="sha256:trigger.boundary",
        trigger_digest="sha256:trigger-event.boundary",
        constitution_digest="sha256:constitution.boundary",
        baseline_snapshot_digest="sha256:baseline.boundary",
        candidate_domain_registry_digest="sha256:registry",
        session_sequence_high_watermark=1,
        new_session_count=1,
        state="snapshotting",
        revision=1,
        started_at="2026-07-22T12:00:00Z",
    )

    port.freeze(epoch, allow_effect)
    snapshot = port.load(epoch.epoch_id)

    assert snapshot.epoch_started_at == epoch.started_at
    assert snapshot.partition_assignment["prospective_shadow"] == (
        "session.after-epoch",
    )


def test_conflicting_candidate_for_same_session_is_rejected() -> None:
    bindings = (
        _binding("session.1", "candidate.1", sequence=1),
        _binding("session.1", "candidate.other", sequence=2),
    )

    with pytest.raises(SharedStateIntegrityError, match="candidate identity"):
        freeze_optimization_dataset(
            project_id="project.optimization",
            bindings=bindings,
            observations=(),
            epoch_started_at="2026-07-22T12:00:00Z",
            session_sequence_high_watermark=2,
            trigger_fingerprint="sha256:trigger.3",
            constitution_digest="sha256:constitution.1",
            baseline_snapshot_digest="sha256:baseline.1",
            holdout_generation_id="holdout-generation.3",
            policy=DatasetPolicy(holdout_ratio=0.2, minimum_holdout_size=1),
            usage_policy_source=lambda _: baseline_usage_estimate_policy(),
        )


def test_observation_store_is_append_only_and_idempotent(tmp_path: Path) -> None:
    store = OptimizationObservationStore(
        tmp_path,
        project_id="project.optimization",
    )
    observation = _observation(
        "session.1",
        "candidate.1",
        "created",
        sequence=1,
    )

    first = store.append(observation)
    replay = store.append(observation)

    assert first == replay == observation
    assert store.read_session("session.1") == (observation,)
    conflicting = observation.model_copy(
        update={"terminal_reason": "tampered", "observation_digest": ""}
    )
    with pytest.raises(SharedStateIntegrityError, match="observation identity"):
        store.append(conflicting)


def _binding(
    session_id: str,
    candidate_digest: str,
    *,
    sequence: int,
    committed_at: str = "2026-07-21T10:00:00Z",
    active_snapshot_digest: str = "sha256:baseline.1",
) -> CommittedSessionBinding:
    return CommittedSessionBinding(
        project_id="project.optimization",
        session_id=session_id,
        initial_candidate_digest=candidate_digest,
        stage_key="implementation",
        risk_level="high",
        candidate_size_bucket="medium",
        provider_ids=("provider.codex",),
        active_snapshot_digest=active_snapshot_digest,
        control_sequence=sequence,
        control_event_digest=f"sha256:control.{sequence}",
        committed_at=committed_at,
    )


def _observation(
    session_id: str,
    candidate_digest: str,
    kind: str,
    *,
    sequence: int,
) -> OptimizationSessionObservation:
    return OptimizationSessionObservation(
        observation_id=f"observation.{session_id}.{sequence}",
        project_id="project.optimization",
        session_id=session_id,
        initial_candidate_digest=candidate_digest,
        sequence=sequence,
        observation_kind=kind,
        occurred_at=f"2026-07-21T10:{sequence:02d}:00Z",
        stage_key="implementation",
        risk_level="high",
        candidate_size_bucket="medium",
        provider_ids=("provider.codex",),
        active_snapshot_digest="sha256:baseline.1",
        terminal_reason="" if kind == "created" else kind,
    )


class _NoopSnapshotPopulationRecovery:
    def recover_session_population(self, **_kwargs: object) -> None:
        return None
