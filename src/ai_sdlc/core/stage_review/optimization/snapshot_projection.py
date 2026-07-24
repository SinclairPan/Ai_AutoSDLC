"""SnapshotControl 事件链的确定性投影与摘要。"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.optimization.snapshot_models import (
    ActiveOptimizationPointer,
    SnapshotControlEvent,
    SnapshotControlEventKind,
)


@dataclass(frozen=True)
class SnapshotEffect:
    event_kind: SnapshotControlEventKind
    operation_id: str
    target_snapshot_digest: str = ""
    revoked_snapshot_digest: str = ""
    session_id: str = ""
    reason: str = ""


def _initial_pointer(project_id: str, baseline_digest: str) -> ActiveOptimizationPointer:
    return _build_pointer(
        project_id=project_id,
        head_sequence=0,
        head_digest="",
        pointer_revision=0,
        active_snapshot_digest=baseline_digest,
        stable_fallback_digest=baseline_digest,
        revocation_generation=0,
        revoked_snapshot_digests=(),
        session_binding_sequence=0,
    )


def project_effect(
    current: ActiveOptimizationPointer,
    effect: SnapshotEffect,
) -> ActiveOptimizationPointer:
    active = current.active_snapshot_digest
    stable = current.stable_fallback_digest
    revision = current.pointer_revision
    generation = current.revocation_generation
    revoked = current.revoked_snapshot_digests
    binding_sequence = current.session_binding_sequence
    if effect.event_kind == "promotion":
        active, revision = effect.target_snapshot_digest, revision + 1
    elif effect.event_kind == "stability":
        stable, revision = effect.target_snapshot_digest, revision + 1
    elif effect.event_kind == "revocation":
        revoked = tuple(sorted(set((*revoked, effect.revoked_snapshot_digest))))
        generation, revision = generation + 1, revision + 1
    elif effect.event_kind == "rollback":
        active, revision = effect.target_snapshot_digest, revision + 1
    elif effect.event_kind == "session_binding":
        binding_sequence += 1
    return _build_pointer(
        project_id=current.project_id,
        head_sequence=current.head_sequence + 1,
        head_digest="",
        pointer_revision=revision,
        active_snapshot_digest=active,
        stable_fallback_digest=stable,
        revocation_generation=generation,
        revoked_snapshot_digests=revoked,
        session_binding_sequence=binding_sequence,
    )


def _finish_event_projection(
    projected: ActiveOptimizationPointer,
    event_digest: str,
) -> ActiveOptimizationPointer:
    return projected.model_copy(update={"head_digest": event_digest})


def _rebuild_pointer(
    project_id: str,
    baseline_digest: str,
    events: tuple[SnapshotControlEvent, ...],
) -> ActiveOptimizationPointer:
    pointer = _initial_pointer(project_id, baseline_digest)
    operations: set[str] = set()
    for event in events:
        _verify_event_link(pointer, event, operations)
        effect = _effect_from_event(event)
        projected = project_effect(pointer, effect)
        _verify_event_projection(projected, event, effect)
        pointer = _finish_event_projection(projected, event.event_digest)
        operations.add(event.operation_id)
    return pointer


def _effect_from_event(event: SnapshotControlEvent) -> SnapshotEffect:
    return SnapshotEffect(
        event_kind=event.event_kind,
        operation_id=event.operation_id,
        target_snapshot_digest=event.target_snapshot_digest,
        revoked_snapshot_digest=event.revoked_snapshot_digest,
        session_id=event.session_id,
        reason=event.reason,
    )


def _snapshot_effect_digest(effect: SnapshotEffect) -> str:
    return canonical_digest(asdict(effect), CanonicalizationPolicy())


def _verify_event_link(
    pointer: ActiveOptimizationPointer,
    event: SnapshotControlEvent,
    operations: set[str],
) -> None:
    if (
        event.project_id != pointer.project_id
        or event.sequence != pointer.head_sequence + 1
        or event.previous_event_digest != pointer.head_digest
        or event.previous_control_digest != pointer.control_digest
        or event.operation_id in operations
    ):
        raise SharedStateIntegrityError("snapshot control event chain diverged")


def _verify_event_projection(
    projected: ActiveOptimizationPointer,
    event: SnapshotControlEvent,
    effect: SnapshotEffect,
) -> None:
    expected = (
        event.next_control_digest == projected.control_digest,
        event.effect_digest == _snapshot_effect_digest(effect),
        event.pointer_revision == projected.pointer_revision,
        event.revocation_generation == projected.revocation_generation,
        event.session_binding_sequence == projected.session_binding_sequence,
    )
    if not all(expected):
        raise SharedStateIntegrityError("snapshot control event effect diverged")


def _build_pointer(
    *,
    project_id: str,
    head_sequence: int,
    head_digest: str,
    pointer_revision: int,
    active_snapshot_digest: str,
    stable_fallback_digest: str,
    revocation_generation: int,
    revoked_snapshot_digests: tuple[str, ...],
    session_binding_sequence: int,
) -> ActiveOptimizationPointer:
    revoked_digest = _revoked_set_digest(revoked_snapshot_digests)
    control_values = {
        "project_id": project_id,
        "head_sequence": head_sequence,
        "pointer_revision": pointer_revision,
        "active_snapshot_digest": active_snapshot_digest,
        "stable_fallback_digest": stable_fallback_digest,
        "revocation_generation": revocation_generation,
        "revoked_snapshot_digests": revoked_snapshot_digests,
        "revoked_set_digest": revoked_digest,
        "session_binding_sequence": session_binding_sequence,
    }
    return ActiveOptimizationPointer(
        project_id=project_id,
        head_sequence=head_sequence,
        head_digest=head_digest,
        pointer_revision=pointer_revision,
        active_snapshot_digest=active_snapshot_digest,
        stable_fallback_digest=stable_fallback_digest,
        revocation_generation=revocation_generation,
        revoked_snapshot_digests=revoked_snapshot_digests,
        revoked_set_digest=revoked_digest,
        session_binding_sequence=session_binding_sequence,
        control_digest=canonical_digest(control_values, CanonicalizationPolicy()),
    )


def _revoked_set_digest(values: tuple[str, ...]) -> str:
    return canonical_digest(values, CanonicalizationPolicy())
