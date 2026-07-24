"""Snapshot 选择令牌、事件后投影与 Session Binding 校验。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.snapshot_models import (
    ActiveOptimizationPointer,
    SessionSnapshotBindingOperation,
    SnapshotControlEvent,
    SnapshotSelectionToken,
)
from ai_sdlc.core.stage_review.optimization.snapshot_projection import (
    SnapshotEffect,
    project_effect,
)
from ai_sdlc.core.stage_review.optimization.snapshot_projection import (
    _finish_event_projection as finish_event_projection,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id


def _selection_token(pointer: ActiveOptimizationPointer) -> SnapshotSelectionToken:
    return SnapshotSelectionToken(
        project_id=pointer.project_id,
        head_sequence=pointer.head_sequence,
        head_digest=pointer.head_digest,
        pointer_revision=pointer.pointer_revision,
        revocation_generation=pointer.revocation_generation,
        active_snapshot_digest=pointer.active_snapshot_digest,
        stable_fallback_digest=pointer.stable_fallback_digest,
        revoked_snapshot_digests=pointer.revoked_snapshot_digests,
        control_digest=pointer.control_digest,
    )


def _pointer_after(
    pointer: ActiveOptimizationPointer,
    event: SnapshotControlEvent,
) -> ActiveOptimizationPointer:
    projected = project_effect(
        pointer,
        SnapshotEffect(
            event_kind=event.event_kind,
            operation_id=event.operation_id,
            target_snapshot_digest=event.target_snapshot_digest,
            revoked_snapshot_digest=event.revoked_snapshot_digest,
            session_id=event.session_id,
            reason=event.reason,
        ),
    )
    return finish_event_projection(projected, event.event_digest)


def _verify_session_binding(
    operation: SessionSnapshotBindingOperation,
    token: SnapshotSelectionToken,
    pointer: ActiveOptimizationPointer,
) -> None:
    expected = (
        operation.project_id == pointer.project_id == token.project_id,
        operation.expected_head_sequence
        == token.head_sequence
        == pointer.head_sequence,
        operation.expected_head_digest == token.head_digest == pointer.head_digest,
        operation.expected_pointer_revision
        == token.pointer_revision
        == pointer.pointer_revision,
        operation.expected_revocation_generation
        == token.revocation_generation
        == pointer.revocation_generation,
        operation.target_snapshot_digest
        == token.active_snapshot_digest
        == pointer.active_snapshot_digest,
        operation.target_snapshot_digest not in pointer.revoked_snapshot_digests,
    )
    if not all(expected):
        raise SharedStateIntegrityError("session binding expected head is stale")


def _verify_operation_token(
    operation: SessionSnapshotBindingOperation,
    token: SnapshotSelectionToken,
) -> None:
    expected = (
        operation.project_id == token.project_id,
        operation.target_snapshot_digest == token.active_snapshot_digest,
        operation.expected_head_sequence == token.head_sequence,
        operation.expected_head_digest == token.head_digest,
        operation.expected_pointer_revision == token.pointer_revision,
        operation.expected_revocation_generation == token.revocation_generation,
    )
    if not all(expected):
        raise SharedStateIntegrityError("session binding token is invalid")


def _refresh_binding_operation(
    operation: SessionSnapshotBindingOperation,
    token: SnapshotSelectionToken,
) -> SessionSnapshotBindingOperation:
    payload = operation.model_dump(mode="json")
    payload.update(
        {
            "operation_id": stable_id(
                "session-snapshot-binding-attempt",
                operation.project_id,
                operation.session_id,
                token.control_digest,
            ),
            "expected_head_sequence": token.head_sequence,
            "expected_head_digest": token.head_digest,
            "expected_pointer_revision": token.pointer_revision,
            "expected_revocation_generation": token.revocation_generation,
            "operation_digest": "",
        }
    )
    return SessionSnapshotBindingOperation.model_validate(payload)


def _same_binding_identity(
    left: SessionSnapshotBindingOperation,
    right: SessionSnapshotBindingOperation,
) -> bool:
    protected = (
        "project_id",
        "session_id",
        "initial_candidate_digest",
        "stage_key",
        "risk_level",
        "candidate_size_bucket",
        "provider_ids",
        "target_snapshot_digest",
    )
    return all(getattr(left, field) == getattr(right, field) for field in protected)
