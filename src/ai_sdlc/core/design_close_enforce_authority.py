"""验证 Design 关闭的 enforce certificate 与本地 marker 绑定。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_sdlc.core.design_close_enforce_evidence import (
    _current_project_id,
    _trusted_candidate_artifacts,
    _trusted_enforce_receipt,
)
from ai_sdlc.core.scope_authority_store import ScopeAuthorityIntegrityError
from ai_sdlc.core.stable_file_read import read_stable_bytes, read_stable_text
from ai_sdlc.core.stage_review.activation_models import ActivationSessionRecord
from ai_sdlc.core.stage_review.activation_store import (
    _read_activation_session_records,
)


@dataclass(frozen=True, slots=True)
class _DesignCloseProof:
    kind: Literal["shadow-attestation", "enforce-certificate"]
    proof_id: str
    proof_digest: str
    operation_id: str
    stage_input_digest: str
    stage_key: str
    close_kind: str
    target_status: str
    close_artifact_digest: str
    candidate_manifest_digest: str = ""
    candidate_artifact_digests: tuple[tuple[str, str], ...] = ()
    marker_digest: str = ""


@dataclass(frozen=True, slots=True)
class _ExpectedEnforceProof:
    proof_id: str
    proof_digest: str
    candidate_manifest_digest: str
    marker_digest: str


def _trusted_enforce_proof(
    root: Path,
    *,
    operation_id: str,
    stage_key: str,
    work_item_id: str,
    stage_instance_id: str,
    loop_id: str | None = None,
    close_kind: str,
    target_status: str,
    stage_input_digest: str,
    close_artifact_path: str,
    expected: _ExpectedEnforceProof | None = None,
) -> _DesignCloseProof:
    project_id, candidate_digest, marker_digest, record = _trusted_enforce_identity(
        root,
        operation_id=operation_id,
        stage_key=stage_key,
        work_item_id=work_item_id,
        stage_instance_id=stage_instance_id,
        close_kind=close_kind,
        target_status=target_status,
        stage_input_digest=stage_input_digest,
        close_artifact_path=close_artifact_path,
        expected=expected,
    )
    candidate_artifact_digests, close_artifact_digest = _trusted_enforce_artifacts(
        root,
        record,
        candidate_manifest_digest=candidate_digest,
        stage_key=stage_key,
        work_item_id=work_item_id,
        stage_instance_id=stage_instance_id,
        loop_id=loop_id or stage_instance_id,
        project_id=project_id,
    )
    return _enforce_proof_from_record(
        record,
        operation_id=operation_id,
        stage_input_digest=stage_input_digest,
        stage_key=stage_key,
        close_kind=close_kind,
        target_status=target_status,
        close_artifact_digest=close_artifact_digest,
        candidate_digest=candidate_digest,
        candidate_artifact_digests=candidate_artifact_digests,
        marker_digest=marker_digest,
    )


def _trusted_enforce_identity(
    root: Path,
    *,
    operation_id: str,
    stage_key: str,
    work_item_id: str,
    stage_instance_id: str,
    close_kind: str,
    target_status: str,
    stage_input_digest: str,
    close_artifact_path: str,
    expected: _ExpectedEnforceProof | None,
) -> tuple[str, str, str, ActivationSessionRecord]:
    project_id = _current_project_id(root)
    candidate_digest, marker_digest = _trusted_enforce_marker(
        root,
        operation_id=operation_id,
        stage_key=stage_key,
        close_kind=close_kind,
        target_status=target_status,
        stage_input_digest=stage_input_digest,
        close_artifact_path=close_artifact_path,
        expected=expected,
    )
    record = _trusted_enforce_record(
        root,
        project_id=project_id,
        stage_key=stage_key,
        work_item_id=work_item_id,
        stage_instance_id=stage_instance_id,
        candidate_manifest_digest=candidate_digest,
        expected=expected,
    )
    return project_id, candidate_digest, marker_digest, record


def _trusted_enforce_artifacts(
    root: Path,
    record: ActivationSessionRecord,
    *,
    candidate_manifest_digest: str,
    stage_key: str,
    work_item_id: str,
    stage_instance_id: str,
    loop_id: str,
    project_id: str,
) -> tuple[tuple[tuple[str, str], ...], str]:
    candidate_artifacts = _trusted_candidate_artifacts(
        root,
        record.scope,
        candidate_manifest_digest=candidate_manifest_digest,
        stage_key=stage_key,
        work_item_id=work_item_id,
        stage_instance_id=stage_instance_id,
        loop_id=loop_id,
    )
    return candidate_artifacts, _trusted_enforce_receipt(root, record, project_id)


def _enforce_proof_from_record(
    record: ActivationSessionRecord,
    *,
    operation_id: str,
    stage_input_digest: str,
    stage_key: str,
    close_kind: str,
    target_status: str,
    close_artifact_digest: str,
    candidate_digest: str,
    candidate_artifact_digests: tuple[tuple[str, str], ...],
    marker_digest: str,
) -> _DesignCloseProof:
    return _DesignCloseProof(
        kind="enforce-certificate",
        proof_id=record.close_proof_id,
        proof_digest=record.close_proof_digest,
        operation_id=operation_id,
        stage_input_digest=stage_input_digest,
        stage_key=stage_key,
        close_kind=close_kind,
        target_status=target_status,
        close_artifact_digest=close_artifact_digest,
        candidate_manifest_digest=candidate_digest,
        candidate_artifact_digests=candidate_artifact_digests,
        marker_digest=marker_digest,
    )


def _trusted_enforce_marker(
    root: Path,
    *,
    operation_id: str,
    stage_key: str,
    close_kind: str,
    target_status: str,
    stage_input_digest: str,
    close_artifact_path: str,
    expected: _ExpectedEnforceProof | None,
) -> tuple[str, str]:
    marker_path, marker = _read_enforce_marker(root, operation_id)
    expected_marker = {
        "schema_version": "stage-close-authorization.v1",
        "artifact_kind": "stage-close-authorization",
        "operation_id": operation_id,
        "stage_key": stage_key,
        "close_kind": close_kind,
        "target_status": target_status,
        "stage_input_digest": stage_input_digest,
        "product_close_artifact_path": close_artifact_path,
    }
    if any(marker.get(key) != value for key, value in expected_marker.items()):
        raise ScopeAuthorityIntegrityError(
            "design close enforce marker identity diverged"
        )
    candidate_digest = marker.get("candidate_manifest_digest")
    if not isinstance(candidate_digest, str) or not candidate_digest.strip():
        raise ScopeAuthorityIntegrityError(
            "design close enforce marker lacks candidate authority"
        )
    marker_digest = _marker_digest(root, marker_path)
    if expected is not None and marker_digest != expected.marker_digest:
        raise ScopeAuthorityIntegrityError("design close enforce marker changed")
    return candidate_digest, marker_digest


def _trusted_enforce_record(
    root: Path,
    *,
    project_id: str,
    stage_key: str,
    work_item_id: str,
    stage_instance_id: str,
    candidate_manifest_digest: str,
    expected: _ExpectedEnforceProof | None,
) -> ActivationSessionRecord:
    try:
        records = _read_activation_session_records(root)
    except (OSError, ValueError) as exc:
        raise ScopeAuthorityIntegrityError(
            "design close enforce activation record is unavailable"
        ) from exc
    matches = tuple(
        record
        for record in records
        if _enforce_record_matches(
            record,
            project_id=project_id,
            stage_key=stage_key,
            work_item_id=work_item_id,
            stage_instance_id=stage_instance_id,
            candidate_manifest_digest=candidate_manifest_digest,
            expected=expected,
        )
    )
    if len(matches) != 1:
        raise ScopeAuthorityIntegrityError(
            "design close enforce certificate is unavailable or ambiguous"
        )
    return matches[0]


def _read_enforce_marker(
    root: Path,
    operation_id: str,
) -> tuple[Path, Mapping[str, object]]:
    if not operation_id or Path(operation_id).name != operation_id:
        raise ScopeAuthorityIntegrityError(
            "design close Stage Close operation identity is invalid"
        )
    path = (
        root
        / ".ai-sdlc"
        / "state"
        / "stage-close-authorizations"
        / f"{operation_id}.json"
    )
    try:
        payload = json.loads(read_stable_text(root, path, encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError, ValueError) as exc:
        raise ScopeAuthorityIntegrityError(
            "design close enforce marker is unavailable"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ScopeAuthorityIntegrityError("design close enforce marker is malformed")
    return path, payload


def _enforce_record_matches(
    record: ActivationSessionRecord,
    *,
    project_id: str,
    stage_key: str,
    work_item_id: str,
    stage_instance_id: str,
    candidate_manifest_digest: str,
    expected: _ExpectedEnforceProof | None,
) -> bool:
    if (
        record.close_proof_kind != "enforce-certificate"
        or record.project_id != project_id
        or record.scope.project_id != project_id
        or record.observation.mode != "enforce"
        or record.observation.stage_key != stage_key
        or record.scope.work_item_id != work_item_id
        or record.scope.stage_instance_id != stage_instance_id
        or record.candidate_manifest_digest != candidate_manifest_digest
    ):
        return False
    return expected is None or (
        record.close_proof_id == expected.proof_id
        and record.close_proof_digest == expected.proof_digest
        and record.candidate_manifest_digest == expected.candidate_manifest_digest
    )


def _marker_digest(root: Path, path: Path) -> str:
    content = read_stable_bytes(root, path)
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


__all__: list[str] = []
