"""Compaction 工件与 manifest 的原子提交边界。"""

from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    atomic_write_json,
)
from ai_sdlc.core.stage_review.optimization.commit_fencing import (
    OptimizationCommitLeaseHandle,
)
from ai_sdlc.core.stage_review.optimization.storage_compaction import (
    CompactionBundle,
)
from ai_sdlc.core.stage_review.optimization.storage_compaction import (
    _checkpoint_streams as checkpoint_streams,
)
from ai_sdlc.core.stage_review.optimization.storage_compaction import (
    _create_bytes_idempotent as create_bytes_idempotent,
)
from ai_sdlc.core.stage_review.optimization.storage_compaction import (
    _create_json_idempotent as create_json_idempotent,
)
from ai_sdlc.core.stage_review.optimization.storage_models import (
    OptimizationSegmentDescriptor,
    OptimizationStorageCheckpoint,
    OptimizationStorageManifest,
)


def _persist_segment_bundle(
    root: Path,
    bundle: CompactionBundle,
) -> OptimizationSegmentDescriptor:
    create_bytes_idempotent(root / bundle.segment_relative_path, bundle.segment)
    create_json_idempotent(
        root / bundle.index_relative_path,
        bundle.index.model_dump(mode="json"),
    )
    return bundle.descriptor


def _persist_checkpoint(
    checkpoint_root: Path,
    project_id: str,
    manifest: OptimizationStorageManifest,
    descriptor: OptimizationSegmentDescriptor,
    lease: OptimizationCommitLeaseHandle,
) -> OptimizationStorageCheckpoint:
    checkpoint = OptimizationStorageCheckpoint(
        project_id=project_id,
        sequence=manifest.revision + 1,
        previous_checkpoint_digest=manifest.checkpoint_digest,
        streams=checkpoint_streams((*manifest.segments, descriptor)),
        commit_fencing_high_watermark=lease.claim.fencing_epoch,
        commit_claim_digest=lease.claim.claim_digest,
    )
    path = checkpoint_root / f"{checkpoint.sequence:020d}.json"
    create_json_idempotent(path, checkpoint.model_dump(mode="json"))
    return checkpoint


def _commit_manifest(
    manifest_path: Path,
    project_id: str,
    current: OptimizationStorageManifest,
    before: OptimizationStorageManifest,
    descriptor: OptimizationSegmentDescriptor,
    checkpoint: OptimizationStorageCheckpoint,
    lease: OptimizationCommitLeaseHandle,
) -> OptimizationStorageManifest:
    lease.assert_current()
    if current.manifest_digest != before.manifest_digest:
        raise SharedStateIntegrityError("storage manifest CAS is stale")
    manifest = OptimizationStorageManifest(
        project_id=project_id,
        revision=before.revision + 1,
        previous_manifest_digest=before.manifest_digest,
        checkpoint_digest=checkpoint.checkpoint_digest,
        segments=(*before.segments, descriptor),
        commit_fencing_high_watermark=lease.claim.fencing_epoch,
        commit_claim_digest=lease.claim.claim_digest,
    )
    atomic_write_json(manifest_path, manifest.model_dump(mode="json"))
    return manifest
