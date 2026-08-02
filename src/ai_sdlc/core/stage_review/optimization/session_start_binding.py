"""构造并校验 Session start 的不可变 Snapshot Binding 合同。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.binding_result_models import ReviewerBindingSet
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.contracts import TaskRiskProfile
from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBinding,
    OptimizationSessionObservation,
)
from ai_sdlc.core.stage_review.optimization.session_materialization import (
    _created_observation as created_observation,
)
from ai_sdlc.core.stage_review.optimization.session_materialization import (
    _materialized_binding as materialized_binding,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import (
    SessionSnapshotBindingOperation,
    SnapshotControlEvent,
    SnapshotSelectionToken,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.session_contracts import (
    SessionIntegrityError,
    SessionStartCommand,
)


def _binding_operation(
    command: SessionStartCommand,
    token: SnapshotSelectionToken,
    profile: TaskRiskProfile,
    binding_set: ReviewerBindingSet,
    *,
    candidate_size: str,
    created_at: str,
) -> SessionSnapshotBindingOperation:
    bindings = binding_set.bindings
    command_digest = canonical_digest(command, CanonicalizationPolicy())
    capabilities = {
        capability for item in bindings for capability in item.capability_ids
    }
    return SessionSnapshotBindingOperation(
        operation_id=stable_id(
            "session-snapshot-binding-v2",
            command.scope.project_id,
            command.scope.session_id,
            command_digest,
        ),
        project_id=command.scope.project_id,
        session_id=command.scope.session_id,
        command_id=command.command_id,
        idempotency_key=command.idempotency_key,
        command_digest=command_digest,
        initial_candidate_digest=command.candidate_digest,
        stage_key=profile.stage_key,
        risk_level=profile.risk_level,
        candidate_size_bucket=candidate_size,
        provider_ids=tuple(sorted({item.provider_id for item in bindings})),
        binding_set_digest=binding_set.binding_set_digest,
        role_profile_ids=tuple(sorted({item.role_profile_id for item in bindings})),
        reviewer_slot_ids=tuple(sorted({item.slot_id for item in bindings})),
        capability_ids=tuple(sorted(capabilities)),
        binding_digests=tuple(sorted({item.binding_digest for item in bindings})),
        resource_reservation_digest=binding_set.final_reservation_digest,
        risk_profile_digest=profile.profile_digest,
        created_at=created_at,
        target_snapshot_digest=token.active_snapshot_digest,
        expected_head_sequence=token.head_sequence,
        expected_head_digest=token.head_digest,
        expected_pointer_revision=token.pointer_revision,
        expected_revocation_generation=token.revocation_generation,
    )


def _binding_for(
    bindings: tuple[CommittedSessionBinding, ...],
    session_id: str,
) -> CommittedSessionBinding:
    matches = tuple(item for item in bindings if item.session_id == session_id)
    if len(matches) != 1:
        raise SharedStateIntegrityError("session optimization binding is unavailable")
    return matches[0]


def _operation_token(
    operation: SessionSnapshotBindingOperation,
) -> SnapshotSelectionToken:
    return SnapshotSelectionToken(
        project_id=operation.project_id,
        head_sequence=operation.expected_head_sequence,
        head_digest=operation.expected_head_digest,
        pointer_revision=operation.expected_pointer_revision,
        revocation_generation=operation.expected_revocation_generation,
        active_snapshot_digest=operation.target_snapshot_digest,
        stable_fallback_digest=operation.target_snapshot_digest,
        revoked_snapshot_digests=(),
        control_digest="",
    )


def _verify_recovered_operation(
    command: SessionStartCommand,
    operation: SessionSnapshotBindingOperation,
    expected_operation: SessionSnapshotBindingOperation,
) -> None:
    operation_payload = operation.model_dump(
        mode="json",
        exclude={"operation_id", "operation_digest"},
    )
    expected_payload = expected_operation.model_dump(
        mode="json",
        exclude={"operation_id", "operation_digest"},
    )
    if (
        operation_payload != expected_payload
        or operation.target_snapshot_digest != command.optimization_snapshot_digest
    ):
        raise SessionIntegrityError("session optimization binding lineage diverged")


def _verify_recovered_population(
    operation: SessionSnapshotBindingOperation,
    event: SnapshotControlEvent,
    binding: CommittedSessionBinding,
    created: tuple[OptimizationSessionObservation, ...],
) -> None:
    expected_binding = materialized_binding(operation, event)
    expected_created = created_observation(operation, event)
    if binding != expected_binding or created != (expected_created,):
        raise SessionIntegrityError("session optimization binding lineage diverged")
