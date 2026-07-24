"""SnapshotControl 的唯一发布、撤销、回滚与 Session Freeze 服务。"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.commit_fencing import (
    OptimizationCommitLeaseHandle,
)
from ai_sdlc.core.stage_review.optimization.promotion import AutoPromotionDecision
from ai_sdlc.core.stage_review.optimization.snapshot_binding import (
    _pointer_after as pointer_after,
)
from ai_sdlc.core.stage_review.optimization.snapshot_binding import (
    _refresh_binding_operation as refresh_binding_operation,
)
from ai_sdlc.core.stage_review.optimization.snapshot_binding import (
    _same_binding_identity as same_binding_identity,
)
from ai_sdlc.core.stage_review.optimization.snapshot_binding import (
    _selection_token as selection_token,
)
from ai_sdlc.core.stage_review.optimization.snapshot_binding import (
    _verify_operation_token as verify_operation_token,
)
from ai_sdlc.core.stage_review.optimization.snapshot_binding import (
    _verify_session_binding as verify_session_binding,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import (
    ActiveOptimizationPointer,
    OptimizationSnapshot,
    SessionSnapshotBindingOperation,
    SnapshotControlEvent,
    SnapshotRevocationOperation,
    SnapshotSelectionToken,
)
from ai_sdlc.core.stage_review.optimization.snapshot_projection import (
    SnapshotEffect,
    project_effect,
)
from ai_sdlc.core.stage_review.optimization.snapshot_projection import (
    _snapshot_effect_digest as snapshot_effect_digest,
)
from ai_sdlc.core.stage_review.optimization.snapshot_retry import (
    SnapshotControlBusyError,
    SnapshotControlRetryExecutor,
    SnapshotControlRetryPolicy,
)
from ai_sdlc.core.stage_review.optimization.snapshot_service_support import (
    _SnapshotServiceSupportMixin as SnapshotServiceSupportMixin,
)
from ai_sdlc.core.stage_review.optimization.snapshot_store import SnapshotControlStore
from ai_sdlc.core.stage_review.optimization.storage_models import (
    OptimizationStoragePolicy,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_storage_bundles import (
    StorageBundleHandle,
    StorageBundleUnavailableError,
)
from ai_sdlc.core.stage_review.resources import ResourceGovernor


class SnapshotControlService(SnapshotServiceSupportMixin):
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        baseline_snapshot: OptimizationSnapshot,
        resource_governor: ResourceGovernor,
        storage_policy: OptimizationStoragePolicy | None = None,
        lock_timeout_seconds: float = 2,
        retry_policy: SnapshotControlRetryPolicy | None = None,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.project_id = project_id
        self.resources = resource_governor
        self.storage_policy = storage_policy or OptimizationStoragePolicy()
        self.store = SnapshotControlStore(
            root,
            project_id=project_id,
            baseline_snapshot=baseline_snapshot,
            lock_timeout_seconds=min(lock_timeout_seconds, 0.05),
            storage_policy=self.storage_policy,
        )
        self.retry = SnapshotControlRetryExecutor(
            retry_policy,
            monotonic=monotonic or time.monotonic,
            sleeper=sleeper or time.sleep,
        )

    def resolve_snapshot(self) -> SnapshotSelectionToken:
        try:
            return self.retry.run(
                "snapshot-resolver",
                lambda _: self._resolve_snapshot_once(),
            )
        except (SnapshotControlBusyError, StorageBundleUnavailableError) as exc:
            raise SharedStateIntegrityError("snapshot_control_safety_pending") from exc

    def _resolve_snapshot_once(self) -> SnapshotSelectionToken:
        pointer = self._pointer()
        if not self._has_pending_safety(pointer):
            return selection_token(pointer)
        with self._storage_bundle(
            "critical_recovery", "snapshot-resolver"
        ) as bundle, self._write_scope("snapshot-resolver") as lease:
            return selection_token(self._recover_safety_locked(lease, bundle))

    def promote(
        self,
        snapshot_digest: str,
        *,
        decision: AutoPromotionDecision,
        operation_id: str,
    ) -> SnapshotControlEvent | None:
        trusted = AutoPromotionDecision.model_validate(decision.model_dump(mode="json"))
        try:
            return self.retry.run(
                operation_id,
                lambda _: self._promote_once(snapshot_digest, trusted, operation_id),
            )
        except (SnapshotControlBusyError, StorageBundleUnavailableError):
            return None

    def _promote_once(
        self,
        snapshot_digest: str,
        decision: AutoPromotionDecision,
        operation_id: str,
    ) -> SnapshotControlEvent | None:
        self._resolve_snapshot_once()
        with self._write_scope(operation_id) as lease:
            pointer = self._pointer()
            self._reject_pending_safety(pointer)
            existing = self.store.event_for_operation(operation_id)
            if existing is not None:
                return existing
            snapshot = self._require_snapshot(snapshot_digest)
            expected = (
                decision.approved,
                decision.challenger_snapshot_digest == snapshot_digest,
                decision.baseline_snapshot_digest == pointer.active_snapshot_digest,
                decision.candidate_digest == snapshot.candidate_digest,
                snapshot_digest not in pointer.revoked_snapshot_digests,
            )
            if not all(expected):
                return None
            return self._commit_effect(
                pointer,
                SnapshotEffect(
                    event_kind="promotion",
                    operation_id=operation_id,
                    target_snapshot_digest=snapshot_digest,
                ),
                lease,
                None,
            )

    def mark_stable(
        self, snapshot_digest: str, *, operation_id: str
    ) -> SnapshotControlEvent | None:
        try:
            return self.retry.run(
                operation_id,
                lambda _: self._mark_stable_once(snapshot_digest, operation_id),
            )
        except (SnapshotControlBusyError, StorageBundleUnavailableError):
            return None

    def _mark_stable_once(
        self,
        snapshot_digest: str,
        operation_id: str,
    ) -> SnapshotControlEvent | None:
        self._resolve_snapshot_once()
        with self._write_scope(operation_id) as lease:
            pointer = self._pointer()
            self._reject_pending_safety(pointer)
            existing = self.store.event_for_operation(operation_id)
            if existing is not None:
                return existing
            if (
                snapshot_digest != pointer.active_snapshot_digest
                or snapshot_digest in pointer.revoked_snapshot_digests
            ):
                return None
            return self._commit_effect(
                pointer,
                SnapshotEffect(
                    event_kind="stability",
                    operation_id=operation_id,
                    target_snapshot_digest=snapshot_digest,
                ),
                lease,
                None,
            )

    def request_revocation(
        self,
        snapshot_digest: str,
        *,
        reason: str,
        operation_id: str,
    ) -> SnapshotRevocationOperation:
        self._require_snapshot(snapshot_digest)
        try:
            with self._storage_bundle("critical_recovery", operation_id):
                return self.store.persist_revocation(
                    SnapshotRevocationOperation(
                        operation_id=operation_id,
                        project_id=self.project_id,
                        revoked_snapshot_digest=snapshot_digest,
                        reason=reason,
                    )
                )
        except StorageBundleUnavailableError as exc:
            raise SharedStateIntegrityError("snapshot_control_safety_pending") from exc

    def revoke_and_rollback(
        self,
        snapshot_digest: str,
        *,
        reason: str,
        operation_id: str,
    ) -> SnapshotSelectionToken:
        self.request_revocation(
            snapshot_digest, reason=reason, operation_id=operation_id
        )
        return self.resolve_snapshot()

    def bind_session(
        self,
        operation: SessionSnapshotBindingOperation,
        token: SnapshotSelectionToken,
    ) -> SnapshotControlEvent:
        verify_operation_token(operation, token)
        try:
            return self.retry.run(
                operation.operation_id,
                lambda _: self._bind_session_attempt(operation),
            )
        except StorageBundleUnavailableError as exc:
            raise SnapshotControlBusyError("snapshot_control_busy") from exc

    def _bind_session_attempt(
        self,
        operation: SessionSnapshotBindingOperation,
    ) -> SnapshotControlEvent:
        token = self._resolve_snapshot_once()
        if token.active_snapshot_digest != operation.target_snapshot_digest:
            raise SharedStateIntegrityError(
                "session start snapshot selection is stale after head change"
            )
        refreshed = refresh_binding_operation(operation, token)
        with self._storage_bundle("session_binding", refreshed.operation_id) as bundle:
            trusted = self.store.persist_binding_operation(refreshed)
            return self._bind_session_locked(trusted, token, bundle)

    def _bind_session_locked(
        self,
        trusted: SessionSnapshotBindingOperation,
        token: SnapshotSelectionToken,
        bundle: StorageBundleHandle,
    ) -> SnapshotControlEvent:
        with self._write_scope(trusted.operation_id) as lease:
            pointer = self._pointer()
            self._reject_pending_safety(pointer)
            existing_session = next(
                (
                    item
                    for item in self.store.events()
                    if item.event_kind == "session_binding"
                    and item.session_id == trusted.session_id
                ),
                None,
            )
            if existing_session is not None:
                original = next(
                    (
                        item
                        for item in self.store.binding_operations()
                        if item.operation_id == existing_session.operation_id
                    ),
                    None,
                )
                if original is None or not same_binding_identity(original, trusted):
                    raise SharedStateIntegrityError("session binding identity diverged")
                return existing_session
            existing = self.store.event_for_operation(trusted.operation_id)
            if existing is not None:
                return existing
            verify_session_binding(trusted, token, pointer)
            return self._commit_effect(
                pointer,
                SnapshotEffect(
                    event_kind="session_binding",
                    operation_id=trusted.operation_id,
                    target_snapshot_digest=trusted.target_snapshot_digest,
                    session_id=trusted.session_id,
                ),
                lease,
                bundle,
            )

    def _recover_safety_locked(
        self,
        lease: OptimizationCommitLeaseHandle,
        bundle: StorageBundleHandle,
    ) -> ActiveOptimizationPointer:
        pointer = self._pointer()
        for operation in self.store.revocation_operations():
            if (
                operation.revoked_snapshot_digest
                not in pointer.revoked_snapshot_digests
            ):
                event = self._commit_effect(
                    pointer,
                    SnapshotEffect(
                        event_kind="revocation",
                        operation_id=operation.operation_id,
                        revoked_snapshot_digest=operation.revoked_snapshot_digest,
                        reason=operation.reason,
                    ),
                    lease,
                    bundle,
                )
                pointer = pointer_after(pointer, event)
            if pointer.active_snapshot_digest == operation.revoked_snapshot_digest:
                fallback = self._safe_fallback(pointer)
                event = self._commit_effect(
                    pointer,
                    SnapshotEffect(
                        event_kind="rollback",
                        operation_id=stable_id(
                            "snapshot-rollback", operation.operation_id
                        ),
                        target_snapshot_digest=fallback,
                        reason=operation.reason,
                    ),
                    lease,
                    bundle,
                )
                pointer = pointer_after(pointer, event)
        return pointer

    def _commit_effect(
        self,
        pointer: ActiveOptimizationPointer,
        effect: SnapshotEffect,
        lease: OptimizationCommitLeaseHandle,
        resource_bundle: StorageBundleHandle | None,
    ) -> SnapshotControlEvent:
        existing = self.store.event_for_operation(effect.operation_id)
        if existing is not None:
            return existing
        projected = project_effect(pointer, effect)
        event = SnapshotControlEvent(
            project_id=self.project_id,
            sequence=projected.head_sequence,
            event_kind=effect.event_kind,
            operation_id=effect.operation_id,
            previous_event_digest=pointer.head_digest,
            previous_control_digest=pointer.control_digest,
            next_control_digest=projected.control_digest,
            effect_digest=snapshot_effect_digest(effect),
            target_snapshot_digest=effect.target_snapshot_digest,
            revoked_snapshot_digest=effect.revoked_snapshot_digest,
            session_id=effect.session_id,
            reason=effect.reason,
            pointer_revision=projected.pointer_revision,
            revocation_generation=projected.revocation_generation,
            session_binding_sequence=projected.session_binding_sequence,
            commit_fencing_epoch=lease.claim.fencing_epoch,
            commit_claim_digest=lease.claim.claim_digest,
        )
        return self.store.append_event(
            event,
            lease=lease,
            resource_bundle=resource_bundle,
        )
