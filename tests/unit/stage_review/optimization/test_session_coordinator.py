from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
from tests.unit.stage_review.test_session import (
    NOW,
    PROJECT,
    SNAPSHOT,
    _start_command,
    _unstarted,
)

from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBinding,
    CommittedSessionBindingStore,
    OptimizationObservationStore,
    OptimizationSessionObservation,
)
from ai_sdlc.core.stage_review.optimization.session_coordinator import (
    SessionOptimizationCoordinator,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import (
    SessionSnapshotBindingOperation,
    SnapshotSelectionToken,
)
from ai_sdlc.core.stage_review.optimization.snapshots import SnapshotControlService
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.session import StageReviewSessionService
from ai_sdlc.core.stage_review.session_contracts import SessionIntegrityError

pytestmark = pytest.mark.usefixtures("allow_synthetic_session_authority")


def test_session_start_freezes_snapshot_and_records_created_population(
    tmp_path: Path,
) -> None:
    fixture, risk = _unstarted(tmp_path)
    binding_store = CommittedSessionBindingStore(tmp_path, project_id=PROJECT)
    observations = OptimizationObservationStore(tmp_path, project_id=PROJECT)
    snapshots = _Snapshots(binding_store, observations)
    coordinator = _coordinator(fixture.resolver, snapshots, binding_store, observations)
    service = StageReviewSessionService(
        tmp_path,
        project_id=PROJECT,
        trust_resolver=fixture.resolver,
        finding_ledger_writer=fixture.finding_writer,
        optimization_coordinator=coordinator,
        clock=lambda: NOW,
    )

    result = service.start(_start_command(fixture, risk, suffix="optimization"))

    assert result.session.optimization_snapshot_digest == SNAPSHOT
    assert snapshots.timeline == ["session_binding", "created"]
    assert binding_store.read_all()[0].session_id == fixture.scope.session_id
    assert tuple(item.observation_kind for item in observations.read_all()) == (
        "created",
    )


def test_session_population_changes_refresh_the_optimization_trigger(
    tmp_path: Path,
) -> None:
    fixture, risk = _unstarted(tmp_path)
    binding_store = CommittedSessionBindingStore(tmp_path, project_id=PROJECT)
    observations = OptimizationObservationStore(tmp_path, project_id=PROJECT)
    snapshots = _Snapshots(binding_store, observations)
    refreshes: list[str] = []
    coordinator = _coordinator(
        fixture.resolver,
        snapshots,
        binding_store,
        observations,
        trigger_refresher=lambda: refreshes.append("refresh"),
    )

    coordinator.bind_start(_start_command(fixture, risk, suffix="trigger"))
    started = fixture.service.start(
        _start_command(fixture, risk, suffix="trigger")
    ).session
    terminal = started.model_copy(
        update={
            "projection": started.projection.model_copy(update={"state": "consumed"}),
            "revision": 9,
        }
    )
    coordinator.observe_session(terminal)
    coordinator.observe_session(terminal)

    assert refreshes == ["refresh", "refresh"]


def test_trigger_refresh_failure_never_reverses_committed_population(
    tmp_path: Path,
) -> None:
    fixture, risk = _unstarted(tmp_path)
    binding_store = CommittedSessionBindingStore(tmp_path, project_id=PROJECT)
    observations = OptimizationObservationStore(tmp_path, project_id=PROJECT)
    snapshots = _Snapshots(binding_store, observations)

    def unavailable_trigger() -> None:
        raise RuntimeError("derived trigger unavailable")

    coordinator = _coordinator(
        fixture.resolver,
        snapshots,
        binding_store,
        observations,
        trigger_refresher=unavailable_trigger,
    )

    coordinator.bind_start(_start_command(fixture, risk, suffix="trigger-failure"))

    assert snapshots.timeline == ["session_binding", "created"]
    assert tuple(item.observation_kind for item in observations.read_all()) == (
        "created",
    )


def test_session_start_rejects_stale_snapshot_before_binding(tmp_path: Path) -> None:
    fixture, risk = _unstarted(tmp_path)
    binding_store = CommittedSessionBindingStore(tmp_path, project_id=PROJECT)
    observations = OptimizationObservationStore(tmp_path, project_id=PROJECT)
    snapshots = _Snapshots(binding_store, observations)
    coordinator = _coordinator(fixture.resolver, snapshots, binding_store, observations)
    command = _start_command(fixture, risk, suffix="stale").model_copy(
        update={"optimization_snapshot_digest": "sha256:stale-snapshot"}
    )

    with pytest.raises(SessionIntegrityError, match="snapshot selection is stale"):
        coordinator.bind_start(command)

    assert snapshots.operations == []
    assert binding_store.read_all() == ()
    assert observations.read_all() == ()


def test_terminal_session_observation_is_idempotent(tmp_path: Path) -> None:
    fixture, risk = _unstarted(tmp_path)
    binding_store = CommittedSessionBindingStore(tmp_path, project_id=PROJECT)
    observations = OptimizationObservationStore(tmp_path, project_id=PROJECT)
    snapshots = _Snapshots(binding_store, observations)
    coordinator = _coordinator(fixture.resolver, snapshots, binding_store, observations)
    coordinator.bind_start(_start_command(fixture, risk, suffix="terminal"))
    started = fixture.service.start(
        _start_command(fixture, risk, suffix="terminal")
    ).session
    session = started.model_copy(
        update={
            "projection": started.projection.model_copy(update={"state": "consumed"}),
            "revision": 9,
        }
    )

    coordinator.observe_session(session)
    coordinator.observe_session(session)

    assert tuple(item.observation_kind for item in observations.read_all()) == (
        "created",
        "consumed",
    )


def test_terminal_observation_collects_finding_event_lineage(tmp_path: Path) -> None:
    fixture, risk = _unstarted(tmp_path)
    binding_store = CommittedSessionBindingStore(tmp_path, project_id=PROJECT)
    observations = OptimizationObservationStore(tmp_path, project_id=PROJECT)
    snapshots = _Snapshots(binding_store, observations)
    coordinator = SessionOptimizationCoordinator(
        snapshots=cast(SnapshotControlService, snapshots),
        resolver=fixture.resolver,
        binding_store=binding_store,
        observation_store=observations,
        candidate_size_classifier=lambda _: "small",
        clock=lambda: NOW,
        finding_event_source=lambda _: ("sha256:finding-event.1",),
    )
    coordinator.bind_start(_start_command(fixture, risk, suffix="finding-lineage"))

    coordinator.observe_runtime_outcome(
        fixture.scope.session_id,
        "crashed",
        terminal_reason="review-crashed",
        finding_event_digests=("sha256:finding-event.2",),
    )

    terminal = observations.read_session(fixture.scope.session_id)[-1]
    assert terminal.finding_event_digests == (
        "sha256:finding-event.1",
        "sha256:finding-event.2",
    )


def test_hard_budget_and_integrity_states_keep_distinct_terminal_labels(
    tmp_path: Path,
) -> None:
    fixture, risk = _unstarted(tmp_path)
    binding_store = CommittedSessionBindingStore(tmp_path, project_id=PROJECT)
    observations = OptimizationObservationStore(tmp_path, project_id=PROJECT)
    snapshots = _Snapshots(binding_store, observations)
    coordinator = _coordinator(fixture.resolver, snapshots, binding_store, observations)
    coordinator.bind_start(_start_command(fixture, risk, suffix="terminal-kinds"))
    started = fixture.service.start(
        _start_command(fixture, risk, suffix="terminal-kinds")
    ).session
    hard_budget = started.model_copy(
        update={
            "projection": started.projection.model_copy(
                update={
                    "state": "needs_user",
                    "budget_resume_state": "collecting_initial_reviews",
                }
            ),
            "revision": 9,
        }
    )
    integrity = started.model_copy(
        update={
            "projection": started.projection.model_copy(
                update={
                    "state": "blocked",
                    "budget_grant_failure_code": "reconciliation_state_corrupt",
                }
            ),
            "revision": 10,
        }
    )

    coordinator.observe_session(hard_budget)
    coordinator.observe_session(integrity)

    assert tuple(item.observation_kind for item in observations.read_all()) == (
        "created",
        "hard_budget_exhausted",
        "integrity_failure",
    )


@pytest.mark.parametrize("kind", ["crashed", "timed_out", "abandoned"])
def test_runtime_terminal_outcomes_are_persisted(
    tmp_path: Path,
    kind: str,
) -> None:
    fixture, risk = _unstarted(tmp_path)
    binding_store = CommittedSessionBindingStore(tmp_path, project_id=PROJECT)
    observations = OptimizationObservationStore(tmp_path, project_id=PROJECT)
    snapshots = _Snapshots(binding_store, observations)
    coordinator = _coordinator(fixture.resolver, snapshots, binding_store, observations)
    coordinator.bind_start(_start_command(fixture, risk, suffix=f"runtime-{kind}"))

    coordinator.observe_runtime_outcome(
        fixture.scope.session_id,
        kind,
        terminal_reason=f"review-{kind}",
    )

    terminal = observations.read_session(fixture.scope.session_id)[-1]
    assert terminal.observation_kind == kind
    assert terminal.terminal_reason == f"review-{kind}"


def _coordinator(
    resolver: object,
    snapshots: _Snapshots,
    binding_store: CommittedSessionBindingStore,
    observations: OptimizationObservationStore,
    *,
    trigger_refresher: object | None = None,
) -> SessionOptimizationCoordinator:
    return SessionOptimizationCoordinator(
        snapshots=cast(SnapshotControlService, snapshots),
        resolver=resolver,  # type: ignore[arg-type]
        binding_store=binding_store,
        observation_store=observations,
        candidate_size_classifier=lambda _: "small",
        clock=lambda: NOW,
        trigger_refresher=(
            trigger_refresher if callable(trigger_refresher) else None
        ),
    )


@dataclass
class _Snapshots:
    binding_store: CommittedSessionBindingStore
    observation_store: OptimizationObservationStore
    operations: list[SessionSnapshotBindingOperation] = field(default_factory=list)
    timeline: list[str] = field(default_factory=list)

    def resolve_snapshot(self) -> SnapshotSelectionToken:
        return SnapshotSelectionToken(
            project_id=PROJECT,
            head_sequence=0,
            head_digest="",
            pointer_revision=0,
            revocation_generation=0,
            active_snapshot_digest=SNAPSHOT,
            stable_fallback_digest=SNAPSHOT,
            revoked_snapshot_digests=(),
            control_digest="sha256:baseline-control",
        )

    def bind_session(
        self,
        operation: SessionSnapshotBindingOperation,
        token: SnapshotSelectionToken,
    ) -> None:
        assert token == self.resolve_snapshot()
        self.operations.append(operation)
        self.timeline.append("session_binding")

    def recover_session_population(
        self,
        *,
        binding_store: CommittedSessionBindingStore,
        observation_store: OptimizationObservationStore,
    ) -> tuple[CommittedSessionBinding, ...]:
        assert binding_store is self.binding_store
        assert observation_store is self.observation_store
        operation = self.operations[-1]
        binding = binding_store.append(
            CommittedSessionBinding(
                project_id=operation.project_id,
                session_id=operation.session_id,
                initial_candidate_digest=operation.initial_candidate_digest,
                stage_key=operation.stage_key,
                risk_level=operation.risk_level,
                candidate_size_bucket=operation.candidate_size_bucket,
                provider_ids=operation.provider_ids,
                active_snapshot_digest=operation.target_snapshot_digest,
                control_sequence=1,
                control_event_digest="sha256:session-binding-event",
                committed_at=operation.created_at,
            )
        )
        observation_store.append(_created_observation(binding))
        self.timeline.append("created")
        return (binding,)


def _created_observation(
    binding: CommittedSessionBinding,
) -> OptimizationSessionObservation:
    return OptimizationSessionObservation(
        observation_id=stable_id("session-created-observation", binding.session_id),
        project_id=binding.project_id,
        session_id=binding.session_id,
        initial_candidate_digest=binding.initial_candidate_digest,
        sequence=binding.control_sequence,
        observation_kind="created",
        occurred_at=binding.committed_at,
        stage_key=binding.stage_key,
        risk_level=binding.risk_level,
        candidate_size_bucket=binding.candidate_size_bucket,
        provider_ids=binding.provider_ids,
        active_snapshot_digest=binding.active_snapshot_digest,
    )
