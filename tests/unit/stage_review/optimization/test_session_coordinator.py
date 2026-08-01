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
    _binding_operation,
)
from ai_sdlc.core.stage_review.optimization.session_materialization import (
    _verify_binding_event as verify_binding_event,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import (
    SessionSnapshotBindingOperation,
    SnapshotControlEvent,
    SnapshotSelectionToken,
)
from ai_sdlc.core.stage_review.optimization.snapshots import SnapshotControlService
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.session import StageReviewSessionService
from ai_sdlc.core.stage_review.session_contracts import (
    SessionIntegrityError,
    SessionStartCommand,
)
from ai_sdlc.core.stage_review.session_models import SessionOperation
from ai_sdlc.core.stage_review.session_operation_registry import prepare_operation

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


def test_session_start_replay_keeps_frozen_snapshot_after_active_promotion(
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
    command = _start_command(fixture, risk, suffix="promoted-replay")
    started = service.start(command)
    snapshots.active_snapshot_digest = "sha256:promoted-snapshot"

    replay = service.start(command)

    assert replay.idempotent_replay is True
    assert replay.session == started.session
    assert snapshots.timeline == ["session_binding", "created"]


def test_session_start_recovers_frozen_binding_before_operation_exists(
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
    command = _start_command(fixture, risk, suffix="binding-before-operation")
    coordinator.bind_start(command)
    snapshots.active_snapshot_digest = "sha256:promoted-snapshot"

    started = service.start(command)

    assert started.session.optimization_snapshot_digest == SNAPSHOT
    assert snapshots.timeline == ["session_binding", "created"]


def test_session_start_rejects_orphan_operation_without_snapshot_lineage(
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
    command = _start_command(fixture, risk, suffix="orphan-operation")
    store = service._store
    _prepare_start_operation(service, command)
    snapshots.active_snapshot_digest = "sha256:promoted-snapshot"

    with pytest.raises(
        SessionIntegrityError,
        match="session optimization binding lineage is unavailable",
    ):
        service.start(command)

    assert store.rebuild(command.scope) is None
    assert binding_store.read_all() == ()
    assert observations.read_all() == ()


def test_pending_session_start_recovery_requires_snapshot_lineage(
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
    pending = _start_command(fixture, risk, suffix="pending-orphan")
    incoming = _start_command(fixture, risk, suffix="different-incoming")
    _prepare_start_operation(service, pending)
    snapshots.active_snapshot_digest = "sha256:promoted-snapshot"

    with pytest.raises(
        SessionIntegrityError,
        match="session optimization binding lineage is unavailable",
    ):
        service.start(incoming)

    assert service._store.rebuild(pending.scope) is None
    assert binding_store.read_all() == ()
    assert observations.read_all() == ()


def test_session_start_rejects_tampered_frozen_binding_operation(
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
    command = _start_command(fixture, risk, suffix="tampered-binding")
    coordinator.bind_start(command)
    payload = snapshots.operations[0].model_dump(mode="json")
    payload.update(
        {
            "binding_set_digest": "sha256:other-binding",
            "operation_digest": "",
        }
    )
    snapshots.operations[0] = SessionSnapshotBindingOperation.model_validate(payload)

    with pytest.raises(
        SessionIntegrityError,
        match="session optimization binding lineage is unavailable",
    ):
        service.start(command)

    assert service._store.rebuild(command.scope) is None


def test_session_start_rejects_rehashed_binding_operation_after_event_commit(
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
    command = _start_command(fixture, risk, suffix="rehashed-binding")
    _commit_start_binding(coordinator, snapshots, command)
    payload = snapshots.operations[0].model_dump(mode="json")
    payload.update(
        {
            "created_at": "2035-12-31T23:59:59Z",
            "operation_digest": "",
        }
    )
    snapshots.operations[0] = SessionSnapshotBindingOperation.model_validate(payload)

    with pytest.raises(
        SessionIntegrityError,
        match="session optimization binding lineage is unavailable",
    ):
        service.start(command)

    assert service._store.rebuild(command.scope) is None
    assert binding_store.read_all() == ()
    assert observations.read_all() == ()


def test_session_start_rejects_replacement_command_before_population(
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
    original = _start_command(fixture, risk, suffix="original-command")
    _commit_start_binding(coordinator, snapshots, original)
    replacement = original.model_copy(
        update={
            "command_id": "command.replacement",
            "idempotency_key": "idempotency.replacement",
        }
    )

    with pytest.raises(SessionIntegrityError, match="optimization binding lineage"):
        service.start(replacement)

    assert service._store.rebuild(original.scope) is None
    assert binding_store.read_all() == ()
    assert observations.read_all() == ()


def test_session_start_rejects_divergent_candidate_before_population(
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
    original = _start_command(fixture, risk, suffix="original-candidate")
    _commit_start_binding(coordinator, snapshots, original)
    replacement = original.model_copy(
        update={
            "command_id": "command.divergent",
            "idempotency_key": "idempotency.divergent",
            "candidate_digest": "sha256:divergent-candidate",
        }
    )

    with pytest.raises(SessionIntegrityError, match="optimization binding lineage"):
        service.start(replacement)

    assert service._store.rebuild(original.scope) is None
    assert binding_store.read_all() == ()
    assert observations.read_all() == ()


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
        trigger_refresher=(trigger_refresher if callable(trigger_refresher) else None),
    )


def _commit_start_binding(
    coordinator: SessionOptimizationCoordinator,
    snapshots: _Snapshots,
    command: SessionStartCommand,
) -> None:
    profile = coordinator.resolver.resolve_risk_profile(command.risk_profile_digest)
    binding_set = coordinator.resolver.resolve_binding_set(command.binding_set_digest)
    assert profile is not None
    assert binding_set is not None
    token = snapshots.resolve_snapshot()
    snapshots.bind_session(
        _binding_operation(
            command,
            token,
            profile,
            binding_set,
            candidate_size="small",
            created_at=NOW,
        ),
        token,
    )


def _prepare_start_operation(
    service: StageReviewSessionService,
    command: SessionStartCommand,
) -> None:
    store = service._store
    prepare_operation(
        command,
        ("session_started",),
        NOW,
        store._operation_path(command.scope, command.command_id),
        lambda path: store._require_model(
            path,
            SessionOperation,
            "session operation",
        ),
    )


@dataclass
class _Snapshots:
    binding_store: CommittedSessionBindingStore
    observation_store: OptimizationObservationStore
    operations: list[SessionSnapshotBindingOperation] = field(default_factory=list)
    events: list[SnapshotControlEvent] = field(default_factory=list)
    timeline: list[str] = field(default_factory=list)
    active_snapshot_digest: str = SNAPSHOT

    def resolve_snapshot(self) -> SnapshotSelectionToken:
        return SnapshotSelectionToken(
            project_id=PROJECT,
            head_sequence=0,
            head_digest="",
            pointer_revision=0,
            revocation_generation=0,
            active_snapshot_digest=self.active_snapshot_digest,
            stable_fallback_digest=self.active_snapshot_digest,
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
        self.events.append(
            SnapshotControlEvent(
                project_id=operation.project_id,
                sequence=1,
                event_kind="session_binding",
                operation_id=operation.operation_id,
                previous_event_digest="",
                previous_control_digest="sha256:baseline-control",
                next_control_digest="sha256:session-control",
                effect_digest="sha256:session-effect",
                target_snapshot_digest=operation.target_snapshot_digest,
                session_id=operation.session_id,
                pointer_revision=0,
                revocation_generation=0,
                session_binding_sequence=1,
                commit_fencing_epoch=1,
                commit_claim_digest="sha256:session-claim",
                extensions={
                    "session_binding_operation_digest": operation.operation_digest
                },
            )
        )
        self.timeline.append("session_binding")

    def session_binding_lineage(
        self,
        session_id: str,
    ) -> tuple[SessionSnapshotBindingOperation, SnapshotControlEvent] | None:
        events = tuple(item for item in self.events if item.session_id == session_id)
        if not events:
            return None
        event = events[0]
        operation = next(
            item for item in self.operations if item.operation_id == event.operation_id
        )
        verify_binding_event(operation, event)
        return operation, event

    def recover_session_population(
        self,
        *,
        binding_store: CommittedSessionBindingStore,
        observation_store: OptimizationObservationStore,
    ) -> tuple[CommittedSessionBinding, ...]:
        assert binding_store is self.binding_store
        assert observation_store is self.observation_store
        operation = self.operations[-1]
        event = self.events[-1]
        materialized = bool(binding_store.read_all())
        binding = binding_store.append(
            CommittedSessionBinding(
                project_id=operation.project_id,
                session_id=operation.session_id,
                initial_candidate_digest=operation.initial_candidate_digest,
                stage_key=operation.stage_key,
                risk_level=operation.risk_level,
                candidate_size_bucket=operation.candidate_size_bucket,
                provider_ids=operation.provider_ids,
                binding_set_digest=operation.binding_set_digest,
                role_profile_ids=operation.role_profile_ids,
                reviewer_slot_ids=operation.reviewer_slot_ids,
                capability_ids=operation.capability_ids,
                binding_digests=operation.binding_digests,
                resource_reservation_digest=operation.resource_reservation_digest,
                active_snapshot_digest=operation.target_snapshot_digest,
                control_sequence=event.sequence,
                control_event_digest=event.event_digest,
                committed_at=operation.created_at,
            )
        )
        observation_store.append(_created_observation(operation, event))
        if not materialized:
            self.timeline.append("created")
        return (binding,)


def _created_observation(
    operation: SessionSnapshotBindingOperation,
    event: SnapshotControlEvent,
) -> OptimizationSessionObservation:
    return OptimizationSessionObservation(
        observation_id=stable_id(
            "session-created-observation",
            operation.session_id,
        ),
        project_id=operation.project_id,
        session_id=operation.session_id,
        initial_candidate_digest=operation.initial_candidate_digest,
        sequence=event.sequence,
        observation_kind="created",
        occurred_at=operation.created_at,
        stage_key=operation.stage_key,
        risk_level=operation.risk_level,
        candidate_size_bucket=operation.candidate_size_bucket,
        provider_ids=operation.provider_ids,
        active_snapshot_digest=operation.target_snapshot_digest,
        binding_set_digest=operation.binding_set_digest,
        risk_profile_digest=operation.risk_profile_digest,
        label_source_digests=tuple(
            sorted(
                {
                    operation.binding_set_digest,
                    operation.operation_digest,
                    event.event_digest,
                }
                - {""}
            )
        ),
    )
