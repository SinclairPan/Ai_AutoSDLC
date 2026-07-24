"""从已提交 SnapshotControl Session Binding 恢复优化人口事实。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBinding,
    CommittedSessionBindingStore,
    OptimizationObservationStore,
    OptimizationSessionObservation,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import (
    SessionSnapshotBindingOperation,
    SnapshotControlEvent,
)
from ai_sdlc.core.stage_review.optimization.snapshot_store import SnapshotControlStore
from ai_sdlc.core.stage_review.resource_builders import stable_id


def _recover_session_population(
    store: SnapshotControlStore,
    *,
    binding_store: CommittedSessionBindingStore,
    observation_store: OptimizationObservationStore,
) -> tuple[CommittedSessionBinding, ...]:
    events = {item.operation_id: item for item in store.events()}
    for operation in store.binding_operations():
        event = events.get(operation.operation_id)
        if event is None:
            continue
        _verify_binding_event(operation, event)
        binding_store.append(_materialized_binding(operation, event))
        observation_store.append(_created_observation(operation, event))
    return binding_store.read_all()


def _verify_binding_event(
    operation: SessionSnapshotBindingOperation,
    event: SnapshotControlEvent,
) -> None:
    if (
        event.event_kind != "session_binding"
        or event.session_id != operation.session_id
        or event.target_snapshot_digest != operation.target_snapshot_digest
    ):
        raise SharedStateIntegrityError("session binding event lineage diverged")


def _materialized_binding(
    operation: SessionSnapshotBindingOperation,
    event: SnapshotControlEvent,
) -> CommittedSessionBinding:
    return CommittedSessionBinding(
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


def _created_observation(
    operation: SessionSnapshotBindingOperation,
    event: SnapshotControlEvent,
) -> OptimizationSessionObservation:
    return OptimizationSessionObservation(
        observation_id=stable_id("session-created-observation", operation.session_id),
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
        active_snapshot_digest=operation.target_snapshot_digest,
    )
