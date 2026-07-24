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
    json_bytes,
)
from ai_sdlc.core.stage_review.optimization.storage_compaction import (
    _build_compaction_checkpoint as build_compaction_checkpoint,
)
from ai_sdlc.core.stage_review.optimization.storage_compaction import (
    _build_compaction_manifest as build_compaction_manifest,
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
from ai_sdlc.core.stage_review.resource_storage_bundles import StorageBundleHandle


def _persist_segment_bundle(
    root: Path,
    bundle: CompactionBundle,
    resource_bundle: StorageBundleHandle,
) -> OptimizationSegmentDescriptor:
    segment_path = root / bundle.segment_relative_path
    if segment_path.is_file():
        create_bytes_idempotent(segment_path, bundle.segment)
        resource_bundle.confirm_artifact(segment_path, bundle.segment)
    else:
        with resource_bundle.authorize_artifact(segment_path, bundle.segment):
            create_bytes_idempotent(segment_path, bundle.segment)
    index_path = root / bundle.index_relative_path
    index_payload = bundle.index.model_dump(mode="json")
    if index_path.is_file():
        create_json_idempotent(index_path, index_payload)
        resource_bundle.confirm_artifact(index_path, json_bytes(index_payload))
    else:
        with resource_bundle.authorize_artifact(
            index_path,
            json_bytes(index_payload),
        ):
            create_json_idempotent(index_path, index_payload)
    return bundle.descriptor


def _persist_checkpoint(
    checkpoint_root: Path,
    project_id: str,
    manifest: OptimizationStorageManifest,
    descriptor: OptimizationSegmentDescriptor,
    lease: OptimizationCommitLeaseHandle,
    resource_bundle: StorageBundleHandle,
) -> OptimizationStorageCheckpoint:
    checkpoint = build_compaction_checkpoint(
        project_id,
        manifest,
        descriptor,
        fencing_epoch=lease.claim.fencing_epoch,
        claim_digest=lease.claim.claim_digest,
    )
    path = checkpoint_root / f"{checkpoint.sequence:020d}.json"
    payload = checkpoint.model_dump(mode="json")
    serialized = json_bytes(payload)
    if path.is_file() and path.read_bytes() == serialized:
        resource_bundle.confirm_artifact(path, serialized)
        return checkpoint
    with resource_bundle.authorize_artifact(
        path,
        serialized,
        allow_replacement=True,
    ):
        atomic_write_json(path, payload)
    return checkpoint


def _commit_manifest(
    manifest_path: Path,
    project_id: str,
    current: OptimizationStorageManifest,
    before: OptimizationStorageManifest,
    descriptor: OptimizationSegmentDescriptor,
    checkpoint: OptimizationStorageCheckpoint,
    lease: OptimizationCommitLeaseHandle,
    resource_bundle: StorageBundleHandle,
) -> OptimizationStorageManifest:
    lease.assert_current()
    if current.manifest_digest != before.manifest_digest:
        raise SharedStateIntegrityError("storage manifest CAS is stale")
    manifest = build_compaction_manifest(
        project_id,
        before,
        descriptor,
        checkpoint,
        fencing_epoch=lease.claim.fencing_epoch,
        claim_digest=lease.claim.claim_digest,
    )
    payload = manifest.model_dump(mode="json")
    serialized = json_bytes(payload)
    if manifest_path.is_file() and manifest_path.read_bytes() == serialized:
        resource_bundle.confirm_artifact(manifest_path, serialized)
        return manifest
    with resource_bundle.authorize_artifact(
        manifest_path,
        serialized,
        allow_replacement=True,
    ):
        atomic_write_json(manifest_path, payload)
    return manifest
