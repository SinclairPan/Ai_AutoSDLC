"""SnapshotControl 服务的恢复、资源与投影辅助逻辑。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.commit_fencing import (
    OptimizationCommitLeaseHandle,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBinding,
    CommittedSessionBindingStore,
    OptimizationObservationStore,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import (
    ActiveOptimizationPointer,
    OptimizationSnapshot,
    SnapshotControlEvent,
)
from ai_sdlc.core.stage_review.optimization.snapshot_projection import (
    _rebuild_pointer as rebuild_pointer,
)
from ai_sdlc.core.stage_review.optimization.snapshot_store import SnapshotControlStore
from ai_sdlc.core.stage_review.optimization.storage_models import (
    OptimizationStoragePolicy,
)
from ai_sdlc.core.stage_review.resource_storage_bundles import (
    StorageBundleClass,
    StorageBundleHandle,
)
from ai_sdlc.core.stage_review.resources import ResourceGovernor


class _SnapshotServiceSupportMixin:
    project_id: str
    resources: ResourceGovernor
    storage_policy: OptimizationStoragePolicy
    store: SnapshotControlStore

    def register_snapshot(self, snapshot: OptimizationSnapshot) -> OptimizationSnapshot:
        trusted = OptimizationSnapshot.model_validate(snapshot.model_dump(mode="json"))
        if not trusted.is_baseline:
            if self.store.snapshot(trusted.parent_snapshot_digest) is None:
                raise SharedStateIntegrityError("snapshot parent is unavailable")
            if self.store.snapshot(trusted.stable_fallback_digest) is None:
                raise SharedStateIntegrityError(
                    "snapshot stable fallback is unavailable"
                )
        return self.store.register_snapshot(trusted)

    def events(self) -> tuple[SnapshotControlEvent, ...]:
        return self.store.events()

    def recover_session_population(
        self,
        *,
        binding_store: CommittedSessionBindingStore,
        observation_store: OptimizationObservationStore,
    ) -> tuple[CommittedSessionBinding, ...]:
        from ai_sdlc.core.stage_review.optimization.session_materialization import (
            _recover_session_population as recover_session_population,
        )

        return recover_session_population(
            self.store,
            binding_store=binding_store,
            observation_store=observation_store,
        )

    def _has_pending_safety(self, pointer: ActiveOptimizationPointer) -> bool:
        return any(
            operation.revoked_snapshot_digest not in pointer.revoked_snapshot_digests
            or pointer.active_snapshot_digest == operation.revoked_snapshot_digest
            for operation in self.store.revocation_operations()
        )

    def _reject_pending_safety(self, pointer: ActiveOptimizationPointer) -> None:
        if self._has_pending_safety(pointer):
            raise SharedStateIntegrityError("snapshot_control_safety_pending")

    @contextmanager
    def _storage_bundle(
        self,
        bundle_class: StorageBundleClass,
        operation_id: str,
    ) -> Iterator[StorageBundleHandle]:
        with self.resources.storage_bundle(
            bundle_class=bundle_class,
            bundle_bytes=self.storage_policy.safety_bundle_max_bytes,
            net_reclaim_bytes=0,
            policy=self.storage_policy,
            operation_id=f"snapshot-control.{operation_id}",
        ) as bundle:
            yield bundle

    @contextmanager
    def _write_scope(
        self,
        operation_id: str,
    ) -> Iterator[OptimizationCommitLeaseHandle]:
        expected_head = self._pointer().head_digest or f"snapshot-genesis:{self.project_id}"
        with self.store.commit_leases.acquire(
            owner_id=f"snapshot-writer.{operation_id}",
            scope="snapshot_control",
            expected_head=expected_head,
        ) as lease:
            current_head = self._pointer().head_digest or f"snapshot-genesis:{self.project_id}"
            if current_head != lease.claim.expected_head:
                raise SharedStateIntegrityError("snapshot control expected head is stale")
            yield lease

    def _pointer(self) -> ActiveOptimizationPointer:
        return rebuild_pointer(
            self.project_id,
            self.store.baseline_digest,
            self.store.events(),
        )

    def _safe_fallback(self, pointer: ActiveOptimizationPointer) -> str:
        candidate = pointer.stable_fallback_digest
        visited: set[str] = set()
        while candidate and candidate not in visited:
            visited.add(candidate)
            if candidate not in pointer.revoked_snapshot_digests:
                return candidate
            snapshot = self._require_snapshot(candidate)
            candidate = snapshot.stable_fallback_digest or snapshot.parent_snapshot_digest
        if self.store.baseline_digest in pointer.revoked_snapshot_digests:
            raise SharedStateIntegrityError(
                "all stable optimization snapshots are revoked"
            )
        return self.store.baseline_digest

    def _require_snapshot(self, digest: str) -> OptimizationSnapshot:
        snapshot = self.store.snapshot(digest)
        if snapshot is None:
            raise SharedStateIntegrityError("optimization snapshot is unavailable")
        return snapshot
