"""有界 loose/segment 存储、索引查询与原子 Compaction Bundle。"""

from __future__ import annotations

import gzip
import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, cast

from ai_sdlc.core.stage_review.artifact_compat import JsonValue
from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.optimization.commit_fencing import (
    OptimizationCommitLeaseHandle,
    OptimizationCommitLeaseStore,
)
from ai_sdlc.core.stage_review.optimization.storage_commit import (
    _commit_manifest,
    _persist_checkpoint,
    _persist_segment_bundle,
)
from ai_sdlc.core.stage_review.optimization.storage_compaction import (
    PreparedCompaction,
    json_bytes,
)
from ai_sdlc.core.stage_review.optimization.storage_compaction import (
    _compacted_through as compacted_through,
)
from ai_sdlc.core.stage_review.optimization.storage_compaction import (
    _deduplicate_records as deduplicate_records,
)
from ai_sdlc.core.stage_review.optimization.storage_compaction import (
    _lookup_key_digest as lookup_key_digest,
)
from ai_sdlc.core.stage_review.optimization.storage_compaction import (
    _prepare_compaction_bundle as prepare_compaction_bundle,
)
from ai_sdlc.core.stage_review.optimization.storage_compaction import (
    _sha256_digest as sha256_digest,
)
from ai_sdlc.core.stage_review.optimization.storage_compaction import (
    _tree_bytes as tree_bytes,
)
from ai_sdlc.core.stage_review.optimization.storage_compaction import (
    _verify_record_chain as verify_record_chain,
)
from ai_sdlc.core.stage_review.optimization.storage_models import (
    OptimizationSegmentDescriptor,
    OptimizationSegmentIndex,
    OptimizationStorageCheckpoint,
    OptimizationStorageManifest,
    OptimizationStoragePolicy,
    OptimizationStorageRecord,
    SegmentIndexLookupIncompleteError,
    StoragePressureError,
)
from ai_sdlc.core.stage_review.optimization.storage_pressure import (
    _default_disk_probe as default_disk_probe,
)
from ai_sdlc.core.stage_review.optimization.storage_pressure import (
    _require_storage_bundle as require_storage_bundle,
)
from ai_sdlc.core.stage_review.optimization.storage_pressure import (
    _storage_usage_limit as storage_usage_limit,
)
from ai_sdlc.core.stage_review.optimization.storage_pressure import (
    _storage_write_limit as storage_write_limit,
)
from ai_sdlc.core.stage_review.registry_versions import require_machine_id
from ai_sdlc.core.stage_review.resource_storage_bundles import (
    StorageBundleClass,
    StorageBundleHandle,
)

WriteClass = Literal["normal", "critical_recovery", "session_binding", "reclamation"]
CrashPoint = Literal["after_index", "after_checkpoint", "after_manifest"]


class OptimizationStorage:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        policy: OptimizationStoragePolicy,
        commit_leases: OptimizationCommitLeaseStore,
        disk_probe: Callable[[], tuple[int, int]] | None = None,
    ) -> None:
        self.project_id = require_machine_id(project_id, "project_id")
        self.policy = OptimizationStoragePolicy.model_validate(policy.model_dump())
        shared = resolve_canonical_shared_state(root, self.project_id)
        bind_repository_project(shared, self.project_id)
        self.accounting_root = shared / "offline-optimization"
        self.root = self.accounting_root / "storage"
        self.loose_root = self.root / "loose"
        self.segment_root = self.root / "segments"
        self.index_root = self.root / "segment-indexes"
        self.checkpoint_root = self.root / "checkpoints"
        self.manifest_path = self.root / "manifest.json"
        self.commit_leases = commit_leases
        self._disk_probe = disk_probe or (lambda: default_disk_probe(self.root))

    def append(
        self,
        stream_kind: str,
        payload: Mapping[str, object],
        *,
        keys: Mapping[str, str],
        lease: OptimizationCommitLeaseHandle,
        write_class: WriteClass = "normal",
        resource_bundle: StorageBundleHandle | None = None,
    ) -> OptimizationStorageRecord:
        require_storage_bundle(write_class, resource_bundle)
        lease.assert_current()
        records = self.read_stream(stream_kind)
        sequence = len(records) + 1
        previous = "" if not records else records[-1].record_digest
        record = OptimizationStorageRecord(
            project_id=self.project_id,
            stream_kind=stream_kind,
            sequence=sequence,
            previous_record_digest=previous,
            payload=cast(dict[str, JsonValue], dict(payload)),
            keys=dict(keys),
        )
        serialized = json_bytes(record.model_dump(mode="json"))
        self.reserve_bundle(
            write_class=write_class,
            bundle_bytes=len(serialized),
            net_reclaim_bytes=0,
            resource_bundle=resource_bundle,
        )
        lease.assert_current()
        path = self._loose_path(stream_kind, sequence)
        if not create_json_exclusive(path, record.model_dump(mode="json")):
            existing = OptimizationStorageRecord.model_validate(read_json_object(path))
            if existing != record:
                raise SharedStateIntegrityError("storage sequence collided")
            return existing
        return record

    def read_stream(self, stream_kind: str) -> tuple[OptimizationStorageRecord, ...]:
        manifest = self.manifest()
        committed = tuple(
            record
            for descriptor in manifest.segments
            if descriptor.stream_kind == stream_kind
            for record in self._read_segment(descriptor)
        )
        compacted = max((item.sequence for item in committed), default=0)
        loose = self._read_loose(stream_kind, after_sequence=compacted)
        records = deduplicate_records((*committed, *loose))
        verify_record_chain(records)
        return records

    def lookup(
        self, stream_kind: str, *, key_kind: str, key: str
    ) -> OptimizationStorageRecord | None:
        started = time.monotonic()
        scanned = 0
        target = lookup_key_digest(key_kind, key)
        manifest = self.manifest()
        for descriptor in manifest.segments:
            if descriptor.stream_kind != stream_kind:
                continue
            index = self._read_index(descriptor)
            for entry in index.entries:
                scanned = self._consume_scan(scanned, started)
                if entry.key_kind == key_kind and entry.key_digest == target:
                    return self._record_from_segment(descriptor, entry.sequence)
        compacted = max(
            (
                item.last_sequence
                for item in manifest.segments
                if item.stream_kind == stream_kind
            ),
            default=0,
        )
        for record in self._read_loose(stream_kind, after_sequence=compacted):
            for candidate_kind, candidate in record.keys.items():
                scanned = self._consume_scan(scanned, started)
                if candidate_kind == key_kind and candidate == key:
                    return record
        return None

    def _prepare_compaction(self, stream_kind: str) -> PreparedCompaction | None:
        before = self.manifest()
        compacted = compacted_through(before, stream_kind)
        loose = self._read_loose(stream_kind, after_sequence=compacted)
        return prepare_compaction_bundle(
            self.root, stream_kind, self.policy, before, loose
        )

    def _commit_compaction(
        self,
        prepared: PreparedCompaction,
        *,
        lease: OptimizationCommitLeaseHandle,
        resource_bundle: StorageBundleHandle,
        crash_point: CrashPoint | None = None,
    ) -> OptimizationStorageManifest:
        before = self._require_compaction_authority(
            prepared, lease=lease, resource_bundle=resource_bundle
        )
        bundle = prepared.bundle
        self.reserve_bundle(
            write_class="reclamation",
            bundle_bytes=prepared.required_bundle_bytes,
            net_reclaim_bytes=prepared.net_reclaim_bytes,
            resource_bundle=resource_bundle,
        )
        lease.assert_current()
        descriptor = _persist_segment_bundle(self.root, bundle)
        if crash_point == "after_index":
            raise RuntimeError("injected compaction crash after index")
        checkpoint = _persist_checkpoint(
            self.checkpoint_root,
            self.project_id,
            before,
            descriptor,
            lease,
        )
        if crash_point == "after_checkpoint":
            raise RuntimeError("injected compaction crash after checkpoint")
        manifest = _commit_manifest(
            self.manifest_path,
            self.project_id,
            self.manifest(),
            before,
            descriptor,
            checkpoint,
            lease,
        )
        if crash_point == "after_manifest":
            raise RuntimeError("injected compaction crash after manifest")
        self.cleanup_committed_loose(prepared.stream_kind)
        return manifest

    def _require_compaction_authority(
        self,
        prepared: PreparedCompaction,
        *,
        lease: OptimizationCommitLeaseHandle,
        resource_bundle: StorageBundleHandle,
    ) -> OptimizationStorageManifest:
        resource_bundle.assert_active("reclamation")
        if (
            resource_bundle.reservation.bundle_bytes
            < prepared.required_bundle_bytes
            or resource_bundle.reservation.net_reclaim_bytes
            < prepared.net_reclaim_bytes
        ):
            raise StoragePressureError("reclamation transaction bundle is incomplete")
        lease.assert_current()
        before = prepared.before
        if self.manifest().manifest_digest != before.manifest_digest:
            raise SharedStateIntegrityError("prepared compaction head is stale")
        return before

    def cleanup_committed_loose(self, stream_kind: str) -> None:
        compacted = compacted_through(self.manifest(), stream_kind)
        directory = self.loose_root / stream_kind
        for path in directory.glob("*.json") if directory.is_dir() else ():
            sequence = int(path.stem)
            if sequence <= compacted:
                path.unlink(missing_ok=True)

    def manifest(self) -> OptimizationStorageManifest:
        if not self.manifest_path.is_file():
            return OptimizationStorageManifest(project_id=self.project_id, revision=0)
        manifest = OptimizationStorageManifest.model_validate(
            read_json_object(self.manifest_path)
        )
        if manifest.project_id != self.project_id:
            raise SharedStateIntegrityError("storage manifest project diverged")
        if manifest.revision:
            checkpoint = self._read_checkpoint(manifest.revision)
            if checkpoint.checkpoint_digest != manifest.checkpoint_digest:
                raise SharedStateIntegrityError("storage checkpoint digest diverged")
            for descriptor in manifest.segments:
                self._read_index(descriptor)
                self._read_segment(descriptor)
        return manifest

    def reserve_bundle(
        self,
        *,
        write_class: WriteClass,
        bundle_bytes: int,
        net_reclaim_bytes: int,
        resource_bundle: StorageBundleHandle | None = None,
    ) -> None:
        if bundle_bytes < 0 or net_reclaim_bytes < 0:
            raise ValueError("storage bundle sizes cannot be negative")
        limits = storage_write_limit(self.policy, write_class)
        usage_limit = storage_usage_limit(self.policy, write_class)
        usage = tree_bytes(self.accounting_root)
        if (
            bundle_bytes > limits
            or usage + bundle_bytes > usage_limit
        ):
            raise StoragePressureError("storage bundle exceeds its dedicated reserve")
        if write_class == "reclamation" and net_reclaim_bytes <= bundle_bytes:
            raise StoragePressureError("reclamation bundle must release net space")
        free, total = self._disk_probe()
        minimum = max(
            self.policy.minimum_free_bytes,
            int(total * self.policy.minimum_free_ratio),
        )
        reserved_class: StorageBundleClass | None = None
        if write_class == "critical_recovery":
            reserved_class = "critical_recovery"
        elif write_class == "session_binding":
            reserved_class = "session_binding"
        reserved_safety = reserved_class is not None and (
            resource_bundle is not None
            and resource_bundle.reservation.bundle_bytes >= bundle_bytes
        )
        if reserved_safety:
            assert resource_bundle is not None
            assert reserved_class is not None
            resource_bundle.assert_active(reserved_class)
        if free < bundle_bytes or (
            not reserved_safety and free - bundle_bytes < minimum
        ):
            raise StoragePressureError("filesystem free-space floor reached")

    def _consume_scan(self, scanned: int, started: float) -> int:
        next_count = scanned + 1
        if (
            next_count > self.policy.maximum_index_scan_items
            or time.monotonic() - started > self.policy.maximum_index_scan_seconds
        ):
            raise SegmentIndexLookupIncompleteError("segment_index_lookup_incomplete")
        return next_count

    def _read_loose(
        self, stream_kind: str, *, after_sequence: int
    ) -> tuple[OptimizationStorageRecord, ...]:
        directory = self.loose_root / stream_kind
        if not directory.is_dir():
            return ()
        return tuple(
            OptimizationStorageRecord.model_validate(read_json_object(path))
            for path in sorted(directory.glob("*.json"))
            if int(path.stem) > after_sequence
        )

    def _read_segment(
        self, descriptor: OptimizationSegmentDescriptor
    ) -> tuple[OptimizationStorageRecord, ...]:
        path = self.root / descriptor.segment_relative_path
        raw = path.read_bytes()
        if sha256_digest(raw) != descriptor.segment_digest:
            raise SharedStateIntegrityError("optimization segment digest diverged")
        lines = gzip.decompress(raw).decode("utf-8").splitlines()
        if not lines:
            raise SharedStateIntegrityError("optimization segment is empty")
        records = tuple(
            OptimizationStorageRecord.model_validate(json.loads(line))
            for line in lines[1:]
        )
        if (
            len(records) != descriptor.record_count
            or records[0].sequence != descriptor.first_sequence
            or records[-1].sequence != descriptor.last_sequence
        ):
            raise SharedStateIntegrityError("optimization segment range diverged")
        return records

    def _read_index(
        self, descriptor: OptimizationSegmentDescriptor
    ) -> OptimizationSegmentIndex:
        path = self.root / descriptor.index_relative_path
        index = OptimizationSegmentIndex.model_validate(read_json_object(path))
        if index.index_digest != descriptor.index_digest:
            raise SharedStateIntegrityError("optimization segment index diverged")
        return index

    def _record_from_segment(
        self, descriptor: OptimizationSegmentDescriptor, sequence: int
    ) -> OptimizationStorageRecord:
        return next(
            item for item in self._read_segment(descriptor) if item.sequence == sequence
        )

    def _loose_path(self, stream_kind: str, sequence: int) -> Path:
        return self.loose_root / stream_kind / f"{sequence:020d}.json"

    def _read_checkpoint(self, revision: int) -> OptimizationStorageCheckpoint:
        path = self.checkpoint_root / f"{revision:020d}.json"
        return OptimizationStorageCheckpoint.model_validate(read_json_object(path))
