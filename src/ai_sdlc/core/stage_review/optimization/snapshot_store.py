"""Snapshot Registry、Control Event 与安全 Operation 的共享存储。"""

from __future__ import annotations

from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    bind_repository_project,
    create_json_exclusive,
    portable_content_digest_name,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.optimization.commit_fencing import (
    OptimizationCommitLeaseHandle,
    OptimizationCommitLeaseStore,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import (
    OptimizationSnapshot,
    SessionSnapshotBindingOperation,
    SnapshotControlEvent,
    SnapshotRevocationOperation,
)
from ai_sdlc.core.stage_review.optimization.snapshot_projection import (
    _rebuild_pointer as rebuild_pointer,
)
from ai_sdlc.core.stage_review.optimization.storage import (
    OptimizationStorage,
    WriteClass,
)
from ai_sdlc.core.stage_review.optimization.storage_models import (
    OptimizationStoragePolicy,
    OptimizationStorageRecord,
)
from ai_sdlc.core.stage_review.registry_versions import require_machine_id
from ai_sdlc.core.stage_review.resource_storage_bundles import StorageBundleHandle


class SnapshotControlStore:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        baseline_snapshot: OptimizationSnapshot,
        lock_timeout_seconds: float = 2,
        storage_policy: OptimizationStoragePolicy | None = None,
    ) -> None:
        self.project_id = require_machine_id(project_id, "project_id")
        shared = resolve_canonical_shared_state(root, self.project_id)
        bind_repository_project(shared, self.project_id)
        self.root = shared / "offline-optimization" / "snapshot-control"
        self.lock_timeout_seconds = lock_timeout_seconds
        self.commit_leases = OptimizationCommitLeaseStore(
            root,
            project_id=self.project_id,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        self.storage = OptimizationStorage(
            root,
            project_id=self.project_id,
            policy=storage_policy or OptimizationStoragePolicy(),
            commit_leases=self.commit_leases,
        )
        trusted = OptimizationSnapshot.model_validate(
            baseline_snapshot.model_dump(mode="json")
        )
        if trusted.project_id != self.project_id or not trusted.is_baseline:
            raise ValueError("snapshot control baseline is invalid")
        self.baseline_digest = self._bind_baseline(trusted)

    def register_snapshot(self, snapshot: OptimizationSnapshot) -> OptimizationSnapshot:
        trusted = OptimizationSnapshot.model_validate(snapshot.model_dump(mode="json"))
        if trusted.project_id != self.project_id:
            raise SharedStateIntegrityError("snapshot project identity diverged")
        path = self._snapshot_path(trusted.snapshot_digest)
        if create_json_exclusive(path, trusted.model_dump(mode="json")):
            return trusted
        existing = OptimizationSnapshot.model_validate(read_json_object(path))
        if existing != trusted:
            raise SharedStateIntegrityError("snapshot digest content diverged")
        return existing

    def snapshot(self, digest: str) -> OptimizationSnapshot | None:
        path = self._snapshot_path(digest)
        if not path.is_file():
            return None
        return OptimizationSnapshot.model_validate(read_json_object(path))

    def _snapshot_path(self, digest: str) -> Path:
        name = portable_content_digest_name(digest)
        return self.root / "snapshots" / f"{name}.json"

    def events(self) -> tuple[SnapshotControlEvent, ...]:
        events = tuple(
            _snapshot_event(record)
            for record in self.storage.read_stream("snapshot-control")
        )
        rebuild_pointer(self.project_id, self.baseline_digest, events)
        return events

    def append_event(
        self,
        event: SnapshotControlEvent,
        *,
        lease: OptimizationCommitLeaseHandle,
        resource_bundle: StorageBundleHandle | None = None,
    ) -> SnapshotControlEvent:
        trusted = SnapshotControlEvent.model_validate(event.model_dump(mode="json"))
        lease.assert_current()
        if (
            trusted.commit_fencing_epoch != lease.claim.fencing_epoch
            or trusted.commit_claim_digest != lease.claim.claim_digest
        ):
            raise SharedStateIntegrityError("snapshot control fencing claim diverged")
        existing = self.event_for_operation(trusted.operation_id)
        if existing is not None:
            if existing != trusted:
                raise SharedStateIntegrityError("snapshot operation content diverged")
            return existing
        record = self.storage.append(
            "snapshot-control",
            trusted.model_dump(mode="json"),
            keys={"operation_id": trusted.operation_id},
            lease=lease,
            write_class=_event_write_class(trusted),
            resource_bundle=resource_bundle,
        )
        if record.sequence != trusted.sequence:
            raise SharedStateIntegrityError("snapshot control sequence collided")
        self.events()
        return _snapshot_event(record)

    def event_for_operation(self, operation_id: str) -> SnapshotControlEvent | None:
        record = self.storage.lookup(
            "snapshot-control",
            key_kind="operation_id",
            key=operation_id,
        )
        return None if record is None else _snapshot_event(record)

    def persist_revocation(
        self, operation: SnapshotRevocationOperation
    ) -> SnapshotRevocationOperation:
        trusted = SnapshotRevocationOperation.model_validate(
            operation.model_dump(mode="json")
        )
        path = self.root / "revocation-operations" / f"{trusted.operation_id}.json"
        if create_json_exclusive(path, trusted.model_dump(mode="json")):
            return trusted
        existing = SnapshotRevocationOperation.model_validate(read_json_object(path))
        if existing != trusted:
            raise SharedStateIntegrityError("revocation operation content diverged")
        return existing

    def revocation_operations(self) -> tuple[SnapshotRevocationOperation, ...]:
        directory = self.root / "revocation-operations"
        if not directory.is_dir():
            return ()
        return tuple(
            SnapshotRevocationOperation.model_validate(read_json_object(path))
            for path in sorted(directory.glob("*.json"))
        )

    def persist_binding_operation(
        self, operation: SessionSnapshotBindingOperation
    ) -> SessionSnapshotBindingOperation:
        trusted = SessionSnapshotBindingOperation.model_validate(
            operation.model_dump(mode="json")
        )
        path = self.root / "session-binding-operations" / f"{trusted.operation_id}.json"
        if create_json_exclusive(path, trusted.model_dump(mode="json")):
            return trusted
        existing = SessionSnapshotBindingOperation.model_validate(
            read_json_object(path)
        )
        if existing != trusted:
            raise SharedStateIntegrityError(
                "session binding operation content diverged"
            )
        return existing

    def binding_operations(self) -> tuple[SessionSnapshotBindingOperation, ...]:
        directory = self.root / "session-binding-operations"
        if not directory.is_dir():
            return ()
        return tuple(
            SessionSnapshotBindingOperation.model_validate(read_json_object(path))
            for path in sorted(directory.glob("*.json"))
        )

    def _bind_baseline(self, baseline: OptimizationSnapshot) -> str:
        identity = {
            "project_id": self.project_id,
            "baseline_snapshot_digest": baseline.snapshot_digest,
        }
        path = self.root / "baseline.json"
        if create_json_exclusive(path, identity):
            self.register_snapshot(baseline)
            return baseline.snapshot_digest
        persisted = read_json_object(path)
        digest = str(persisted.get("baseline_snapshot_digest", ""))
        if persisted.get("project_id") != self.project_id or not digest:
            raise SharedStateIntegrityError("snapshot baseline identity diverged")
        if digest == baseline.snapshot_digest:
            self.register_snapshot(baseline)
            return digest
        existing = self.snapshot(digest)
        if existing is None or not existing.is_baseline:
            raise SharedStateIntegrityError("snapshot baseline artifact is unavailable")
        return digest


def _snapshot_event(record: OptimizationStorageRecord) -> SnapshotControlEvent:
    event = SnapshotControlEvent.model_validate(record.payload)
    if event.sequence != record.sequence:
        raise SharedStateIntegrityError("snapshot control record sequence diverged")
    return event


def _event_write_class(event: SnapshotControlEvent) -> WriteClass:
    if event.event_kind in {"revocation", "rollback"}:
        return "critical_recovery"
    if event.event_kind == "session_binding":
        return "session_binding"
    return "normal"
