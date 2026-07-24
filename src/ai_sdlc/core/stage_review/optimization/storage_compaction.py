"""OptimizationStorage 的确定性 Segment Bundle 与通用小型原语。"""

from __future__ import annotations

import gzip
import hashlib
import os
import stat as stat_module
from collections.abc import Mapping
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
    create_json_exclusive,
    read_json_object,
    serialized_json_bytes,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.optimization.commit_fencing import (
    LeaseAcquisitionPlan,
)
from ai_sdlc.core.stage_review.optimization.storage_models import (
    OptimizationSegmentDescriptor,
    OptimizationSegmentIndex,
    OptimizationStorageCheckpoint,
    OptimizationStorageManifest,
    OptimizationStoragePolicy,
    OptimizationStorageRecord,
    SegmentIndexEntry,
    StreamCheckpoint,
)

_TREE_SCAN_ATTEMPTS = 3
_CLAIM_DIGEST_PLACEHOLDER = "sha256:" + ("0" * 64)


class CompactionBundle:
    def __init__(
        self,
        *,
        segment: bytes,
        index: OptimizationSegmentIndex,
        descriptor: OptimizationSegmentDescriptor,
        loose_bytes: int,
    ) -> None:
        self.segment = segment
        self.index = index
        self.descriptor = descriptor
        self.loose_bytes = loose_bytes
        self.segment_relative_path = descriptor.segment_relative_path
        self.index_relative_path = descriptor.index_relative_path
        self.temporary_bytes = len(segment) + len(
            json_bytes(index.model_dump(mode="json"))
        )


class PreparedCompaction:
    def __init__(
        self,
        *,
        stream_kind: str,
        before: OptimizationStorageManifest,
        bundle: CompactionBundle,
        required_bundle_bytes: int,
        net_reclaim_bytes: int,
        reclaim_files: tuple[tuple[str, int], ...],
        operation_id: str,
        lease_plan: LeaseAcquisitionPlan,
    ) -> None:
        self.stream_kind = stream_kind
        self.before = before
        self.bundle = bundle
        self.required_bundle_bytes = required_bundle_bytes
        self.net_reclaim_bytes = net_reclaim_bytes
        self.reclaim_files = reclaim_files
        self.operation_id = operation_id
        self.lease_plan = lease_plan


def _prepare_compaction_bundle(
    root: Path,
    stream_kind: str,
    policy: OptimizationStoragePolicy,
    before: OptimizationStorageManifest,
    loose: tuple[OptimizationStorageRecord, ...],
    *,
    operation_id: str,
    lease_plan: LeaseAcquisitionPlan,
) -> PreparedCompaction | None:
    selected = _bounded_records(loose, policy)
    if not selected:
        return None
    bundle = _build_compaction_bundle(root, stream_kind, selected, before)
    required = _required_compaction_bytes(
        root,
        bundle,
        before,
        project_id=selected[0].project_id,
        fencing_epoch=lease_plan.fencing_epoch,
        claim_digest=_CLAIM_DIGEST_PLACEHOLDER,
    )
    reclaim_files = _compaction_reclaim_files(
        root,
        stream_kind,
        compacted_through_sequence=selected[-1].sequence,
        next_revision=before.revision + 1,
    )
    reclaim = sum(size for _path, size in reclaim_files)
    if reclaim <= required:
        return None
    return PreparedCompaction(
        stream_kind=stream_kind,
        before=before,
        bundle=bundle,
        required_bundle_bytes=required,
        net_reclaim_bytes=reclaim,
        reclaim_files=reclaim_files,
        operation_id=operation_id,
        lease_plan=lease_plan,
    )


def _compaction_reclaim_files(
    root: Path,
    stream_kind: str,
    *,
    compacted_through_sequence: int,
    next_revision: int,
) -> tuple[tuple[str, int], ...]:
    candidates = [
        path
        for path in (root / "loose" / stream_kind).glob("*.json")
        if int(path.stem) <= compacted_through_sequence
    ]
    candidates.extend(
        path
        for path in (
            root / "manifest.json",
            root / "checkpoints" / f"{next_revision:020d}.json",
        )
        if path.is_file()
    )
    return tuple(
        (path.relative_to(root).as_posix(), path.stat().st_size)
        for path in sorted(set(candidates))
    )


def _required_compaction_bytes(
    root: Path,
    bundle: CompactionBundle,
    before: OptimizationStorageManifest,
    *,
    project_id: str,
    fencing_epoch: int,
    claim_digest: str,
) -> int:
    checkpoint = _build_compaction_checkpoint(
        project_id,
        before,
        bundle.descriptor,
        fencing_epoch=fencing_epoch,
        claim_digest=claim_digest,
    )
    manifest = _build_compaction_manifest(
        project_id,
        before,
        bundle.descriptor,
        checkpoint,
        fencing_epoch=fencing_epoch,
        claim_digest=claim_digest,
    )
    return _pending_bundle_bytes(root, bundle) + len(
        json_bytes(checkpoint.model_dump(mode="json"))
    ) + len(json_bytes(manifest.model_dump(mode="json")))


def _pending_bundle_bytes(root: Path, bundle: CompactionBundle) -> int:
    artifacts = (
        (root / bundle.segment_relative_path, bundle.segment),
        (
            root / bundle.index_relative_path,
            json_bytes(bundle.index.model_dump(mode="json")),
        ),
    )
    pending = 0
    for path, content in artifacts:
        if not path.is_file():
            pending += len(content)
            continue
        if path.read_bytes() != content:
            raise SharedStateIntegrityError("immutable storage artifact diverged")
    return pending


def _build_compaction_checkpoint(
    project_id: str,
    before: OptimizationStorageManifest,
    descriptor: OptimizationSegmentDescriptor,
    *,
    fencing_epoch: int,
    claim_digest: str,
) -> OptimizationStorageCheckpoint:
    return OptimizationStorageCheckpoint(
        project_id=project_id,
        sequence=before.revision + 1,
        previous_checkpoint_digest=before.checkpoint_digest,
        streams=_checkpoint_streams((*before.segments, descriptor)),
        commit_fencing_high_watermark=fencing_epoch,
        commit_claim_digest=claim_digest,
    )


def _build_compaction_manifest(
    project_id: str,
    before: OptimizationStorageManifest,
    descriptor: OptimizationSegmentDescriptor,
    checkpoint: OptimizationStorageCheckpoint,
    *,
    fencing_epoch: int,
    claim_digest: str,
) -> OptimizationStorageManifest:
    return OptimizationStorageManifest(
        project_id=project_id,
        revision=before.revision + 1,
        previous_manifest_digest=before.manifest_digest,
        checkpoint_digest=checkpoint.checkpoint_digest,
        segments=(*before.segments, descriptor),
        commit_fencing_high_watermark=fencing_epoch,
        commit_claim_digest=claim_digest,
    )


def _build_compaction_bundle(
    root: Path,
    stream_kind: str,
    records: tuple[OptimizationStorageRecord, ...],
    manifest: OptimizationStorageManifest,
) -> CompactionBundle:
    header = {
        "format": "canonical-jsonl-gzip.v1",
        "stream_kind": stream_kind,
        "first_sequence": records[0].sequence,
        "last_sequence": records[-1].sequence,
        "previous_head_digest": _previous_stream_head(manifest, stream_kind),
    }
    plain = _segment_plaintext(header, records)
    segment = gzip.compress(plain, compresslevel=9, mtime=0)
    segment_digest = _sha256_digest(segment)
    index = _build_index(stream_kind, records, segment_digest)
    descriptor = _build_descriptor(stream_kind, records, segment_digest, index)
    loose_bytes = sum(
        (root / "loose" / stream_kind / f"{item.sequence:020d}.json").stat().st_size
        for item in records
    )
    return CompactionBundle(
        segment=segment,
        index=index,
        descriptor=descriptor,
        loose_bytes=loose_bytes,
    )


def _segment_plaintext(
    header: dict[str, object], records: tuple[OptimizationStorageRecord, ...]
) -> bytes:
    lines = (json_bytes(header).rstrip(b"\n"),) + tuple(
        json_bytes(item.model_dump(mode="json")).rstrip(b"\n") for item in records
    )
    return b"\n".join(lines) + b"\n"


def _build_descriptor(
    stream_kind: str,
    records: tuple[OptimizationStorageRecord, ...],
    segment_digest: str,
    index: OptimizationSegmentIndex,
) -> OptimizationSegmentDescriptor:
    first, last = records[0].sequence, records[-1].sequence
    return OptimizationSegmentDescriptor(
        stream_kind=stream_kind,
        first_sequence=first,
        last_sequence=last,
        record_count=len(records),
        previous_head_digest=records[0].previous_record_digest,
        head_digest=records[-1].record_digest,
        segment_relative_path=(
            f"segments/{stream_kind}/{first:020d}-{last:020d}.jsonl.gz"
        ),
        segment_digest=segment_digest,
        index_relative_path=(
            f"segment-indexes/{stream_kind}/{first:020d}-{last:020d}.index.json"
        ),
        index_digest=index.index_digest,
    )


def _build_index(
    stream_kind: str,
    records: tuple[OptimizationStorageRecord, ...],
    segment_digest: str,
) -> OptimizationSegmentIndex:
    entries = tuple(
        SegmentIndexEntry(
            key_kind=kind,
            key_digest=_lookup_key_digest(kind, key),
            sequence=record.sequence,
            record_offset=offset,
            record_digest=record.record_digest,
        )
        for offset, record in enumerate(records)
        for kind, key in record.keys.items()
    )
    return OptimizationSegmentIndex(
        project_id=records[0].project_id,
        stream_kind=stream_kind,
        first_sequence=records[0].sequence,
        last_sequence=records[-1].sequence,
        segment_digest=segment_digest,
        entries=entries,
    )


def _checkpoint_streams(
    descriptors: tuple[OptimizationSegmentDescriptor, ...],
) -> tuple[StreamCheckpoint, ...]:
    names = sorted({item.stream_kind for item in descriptors})
    return tuple(_stream_checkpoint(name, descriptors) for name in names)


def _stream_checkpoint(
    name: str, descriptors: tuple[OptimizationSegmentDescriptor, ...]
) -> StreamCheckpoint:
    selected = tuple(item for item in descriptors if item.stream_kind == name)
    return StreamCheckpoint(
        stream_kind=name,
        compacted_through_sequence=max(item.last_sequence for item in selected),
        head_digest=selected[-1].head_digest,
        segment_digests=tuple(item.segment_digest for item in selected),
        index_digests=tuple(item.index_digest for item in selected),
    )


def _bounded_records(
    records: tuple[OptimizationStorageRecord, ...],
    policy: OptimizationStoragePolicy,
) -> tuple[OptimizationStorageRecord, ...]:
    selected: list[OptimizationStorageRecord] = []
    size = 0
    for record in records:
        encoded = json_bytes(record.model_dump(mode="json"))
        if selected and (
            len(selected) >= policy.maximum_segment_records
            or size + len(encoded) > policy.maximum_segment_bytes
        ):
            break
        selected.append(record)
        size += len(encoded)
    return tuple(selected)


def _compacted_through(manifest: OptimizationStorageManifest, stream: str) -> int:
    return max(
        (
            item.last_sequence
            for item in manifest.segments
            if item.stream_kind == stream
        ),
        default=0,
    )


def _previous_stream_head(manifest: OptimizationStorageManifest, stream: str) -> str:
    return next(
        (
            item.head_digest
            for item in reversed(manifest.segments)
            if item.stream_kind == stream
        ),
        "",
    )


def _verify_record_chain(records: tuple[OptimizationStorageRecord, ...]) -> None:
    previous = ""
    for sequence, record in enumerate(records, start=1):
        if record.sequence != sequence or record.previous_record_digest != previous:
            raise SharedStateIntegrityError(
                "optimization storage record chain diverged"
            )
        previous = record.record_digest


def _deduplicate_records(
    records: tuple[OptimizationStorageRecord, ...],
) -> tuple[OptimizationStorageRecord, ...]:
    by_sequence: dict[int, OptimizationStorageRecord] = {}
    for record in records:
        existing = by_sequence.get(record.sequence)
        if existing is not None and existing.record_digest != record.record_digest:
            raise SharedStateIntegrityError("storage sequence digest forked")
        by_sequence[record.sequence] = record
    return tuple(by_sequence[key] for key in sorted(by_sequence))


def _lookup_key_digest(kind: str, key: str) -> str:
    return canonical_digest({"kind": kind, "key": key}, CanonicalizationPolicy())


def _tree_bytes(root: Path) -> int:
    previous: tuple[tuple[str, int, int], ...] | None = None
    for _attempt in range(_TREE_SCAN_ATTEMPTS):
        current, unstable = _tree_snapshot(root)
        if not unstable and current == previous:
            return sum(size for _path, size, _modified in current)
        previous = None if unstable else current
    raise ResourceLockUnavailableError("optimization storage accounting is unstable")


def _tree_snapshot(root: Path) -> tuple[tuple[tuple[str, int, int], ...], bool]:
    entries: list[tuple[str, int, int]] = []
    unstable = False
    for path in root.rglob("*"):
        try:
            metadata = path.stat()
        except FileNotFoundError:
            unstable = True
            continue
        if stat_module.S_ISREG(metadata.st_mode):
            entries.append(
                (
                    path.relative_to(root).as_posix(),
                    metadata.st_size,
                    metadata.st_mtime_ns,
                )
            )
    return tuple(sorted(entries)), unstable


def json_bytes(value: Mapping[str, object]) -> bytes:
    return serialized_json_bytes(value)


def _sha256_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _create_bytes_idempotent(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if path.read_bytes() != content:
            raise SharedStateIntegrityError(
                "immutable segment content diverged"
            ) from None
        return
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _create_json_idempotent(path: Path, payload: dict[str, object]) -> None:
    if create_json_exclusive(path, payload):
        return
    if read_json_object(path) != payload:
        raise SharedStateIntegrityError("immutable storage artifact diverged")
