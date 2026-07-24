"""Stage Close Gateway 的项目身份与本地持久化边界。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ai_sdlc.core.stage_review.activation_artifact_codec import (
    LegacyActivationArtifactUnavailableError,
    decode_stage_close_gate_attestation,
    quarantined_stage_close_attestation_children,
    quarantined_stage_close_attestation_ids,
    read_stage_close_gate_attestations,
)
from ai_sdlc.core.stage_review.artifacts import (
    ShortFileLock,
    atomic_write_json,
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
    resolve_repository_project_id,
)
from ai_sdlc.core.stage_review.close_gate_models import (
    StageCloseGateAttestation,
    StageCloseGateOperation,
)

_LOCK_TIMEOUT_SECONDS = 5.0
_STATE_RANK = {"prepared": 0, "original_completed": 1, "shadow_observed": 2}


@dataclass(frozen=True, slots=True)
class _GatePaths:
    operations: Path
    attestations: Path


def _read_gate_attestations(root: Path) -> tuple[StageCloseGateAttestation, ...]:
    paths = _gate_paths(root.resolve())
    if not paths.attestations.is_dir():
        return ()
    return read_stage_close_gate_attestations(
        root,
        tuple(sorted(paths.attestations.glob("*.json"))),
    )


def _read_gate_operation(
    root: Path,
    operation_id: str,
) -> StageCloseGateOperation | None:
    path = _gate_paths(root).operations / f"{operation_id}.json"
    if not path.is_file():
        return None
    return StageCloseGateOperation.model_validate(read_json_object(path))


def _gate_execution_lock(root: Path, operation_id: str) -> ShortFileLock:
    paths = _gate_paths(root)
    return ShortFileLock(
        paths.operations / "execution-locks" / f"{operation_id}.lock",
        timeout_seconds=_LOCK_TIMEOUT_SECONDS,
    )


def _prepare_gate_operation(
    root: Path,
    operation: StageCloseGateOperation,
) -> StageCloseGateOperation:
    paths = _gate_paths(root)
    with _operation_lock(paths, operation.operation_id):
        current = _read_operation_path(paths, operation.operation_id)
        if current is not None:
            _require_same_operation(current, operation)
            return _reconcile_prepared_input(paths, current, operation)
        _write_operation_path(paths, operation)
        return operation


def advance_gate_operation(
    root: Path,
    operation: StageCloseGateOperation,
) -> StageCloseGateOperation:
    paths = _gate_paths(root)
    with _operation_lock(paths, operation.operation_id):
        current = _read_operation_path(paths, operation.operation_id)
        if current is None:
            _write_operation_path(paths, operation)
            return operation
        _require_same_operation(current, operation)
        if _STATE_RANK[current.state] > _STATE_RANK[operation.state]:
            return current
        _require_same_completion(current, operation)
        _write_operation_path(paths, operation)
        return operation


def _persist_gate_attestation(
    root: Path,
    attestation: StageCloseGateAttestation,
) -> None:
    path = _gate_paths(root).attestations / f"{attestation.attestation_id}.json"
    payload = attestation.model_dump(mode="json")
    if create_json_exclusive(path, payload):
        return
    current = decode_stage_close_gate_attestation(root, read_json_object(path))
    if current.attestation_digest != attestation.attestation_digest:
        raise ValueError("stage close attestation content address diverged")


def _gate_attestation_is_current(
    root: Path,
    operation: StageCloseGateOperation,
    close_artifact_path: Path,
) -> bool:
    if not operation.attestation_id or not operation.attestation_digest:
        return False
    path = _gate_paths(root).attestations / f"{operation.attestation_id}.json"
    try:
        current = decode_stage_close_gate_attestation(root, read_json_object(path))
    except (FileNotFoundError, OSError, ValueError):
        return False
    return (
        current.operation_id == operation.operation_id
        and current.attestation_digest == operation.attestation_digest
        and current.close_artifact_digest == operation.close_artifact_digest
        and close_artifact_path.is_file()
        and _file_digest(close_artifact_path) == operation.close_artifact_digest
    )


def _latest_gate_attestation_id(root: Path, starting_id: str) -> str:
    current = starting_id
    visited: set[str] = set()
    attestations = _read_gate_attestations(root)
    quarantined = quarantined_stage_close_attestation_ids(root)
    quarantined_children = quarantined_stage_close_attestation_children(root)
    while True:
        if current in quarantined or quarantined_children.get(current):
            raise LegacyActivationArtifactUnavailableError(
                "stage close supersession lineage references quarantined attestation"
            )
        if current in visited:
            raise ValueError("stage close attestation supersession cycle detected")
        visited.add(current)
        children = [
            item.attestation_id
            for item in attestations
            if item.supersedes_attestation_id == current
        ]
        if not children:
            return current
        if len(children) != 1:
            raise ValueError("stage close attestation supersession fork detected")
        current = children[0]


def _file_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _gate_paths(root: Path) -> _GatePaths:
    project_id = _canonical_project_id(root)
    shared_root = resolve_canonical_shared_state(root, project_id)
    bind_repository_project(shared_root, project_id)
    gate_root = shared_root / "stage-close-gate"
    return _GatePaths(
        operations=gate_root / "operations",
        attestations=gate_root / "attestations",
    )


def _operation_lock(paths: _GatePaths, operation_id: str) -> ShortFileLock:
    return ShortFileLock(
        paths.operations / "locks" / f"{operation_id}.lock",
        timeout_seconds=_LOCK_TIMEOUT_SECONDS,
    )


def _read_operation_path(
    paths: _GatePaths,
    operation_id: str,
) -> StageCloseGateOperation | None:
    path = paths.operations / f"{operation_id}.json"
    if not path.is_file():
        return None
    return StageCloseGateOperation.model_validate(read_json_object(path))


def _write_operation_path(
    paths: _GatePaths,
    operation: StageCloseGateOperation,
) -> None:
    path = paths.operations / f"{operation.operation_id}.json"
    atomic_write_json(path, operation.model_dump(mode="json"))


def _require_same_operation(
    current: StageCloseGateOperation,
    proposed: StageCloseGateOperation,
) -> None:
    current_identity = (current.stage_key, current.loop_id, current.close_kind)
    proposed_identity = (proposed.stage_key, proposed.loop_id, proposed.close_kind)
    if current_identity != proposed_identity:
        raise ValueError("stage close operation identity diverged")


def _require_same_completion(
    current: StageCloseGateOperation,
    proposed: StageCloseGateOperation,
) -> None:
    if current.state == "prepared":
        return
    current_completion = (
        current.result_digest,
        current.result_status,
        current.result_loop_status,
        current.close_artifact_digest,
    )
    proposed_completion = (
        proposed.result_digest,
        proposed.result_status,
        proposed.result_loop_status,
        proposed.close_artifact_digest,
    )
    if current_completion != proposed_completion:
        raise ValueError("stage close operation completion diverged")


def _reconcile_prepared_input(
    paths: _GatePaths,
    current: StageCloseGateOperation,
    proposed: StageCloseGateOperation,
) -> StageCloseGateOperation:
    if current.state != "prepared":
        return current
    if proposed.artifact_existed_before:
        reconciled = current.model_copy(update={"artifact_existed_before": True})
        _write_operation_path(paths, reconciled)
        return reconciled
    if current.stage_input_digest == proposed.stage_input_digest:
        return current
    _write_operation_path(paths, proposed)
    return proposed


def _canonical_project_id(root: Path) -> str:
    return resolve_repository_project_id(root)
