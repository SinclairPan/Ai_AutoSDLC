"""验证 Design 关闭的 Shadow attestation 与候选绑定。"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Protocol

from ai_sdlc.core.design_close_enforce_authority import _DesignCloseProof
from ai_sdlc.core.design_close_enforce_evidence import _trusted_candidate_artifacts
from ai_sdlc.core.scope_authority_store import ScopeAuthorityIntegrityError
from ai_sdlc.core.stage_review.artifacts import resolve_repository_project_id
from ai_sdlc.core.stage_review.close_gate_models import (
    PreparedStageClose,
    StageCloseGateAttestation,
    StageCloseGateOperation,
)
from ai_sdlc.core.stage_review.close_gate_store import (
    _gate_attestation_is_current,
    _read_gate_attestations,
    _read_gate_operation,
)
from ai_sdlc.core.stage_review.finding_models import FindingScope


class _AnchoredShadowAuthority(Protocol):
    stage_close_operation_id: str
    stage_key: str
    close_kind: str
    stage_input_digest: str
    stage_close_proof_id: str
    stage_close_proof_digest: str
    candidate_manifest_digest: str
    work_item_id: str
    loop_id: str
    target_status: str


def _trusted_shadow_close_proof(
    root: Path,
    prepared: PreparedStageClose,
    close_path: Path,
    operation: StageCloseGateOperation | None,
) -> _DesignCloseProof | None:
    if operation is None or operation.state != "shadow_observed":
        return None
    attestations = _matching_attestations(root, operation)
    if (
        operation.stage_input_digest != prepared.stage_input_digest
        or not _gate_attestation_is_current(root, operation, close_path)
        or len(attestations) != 1
    ):
        raise ScopeAuthorityIntegrityError(
            "design close Stage Close attestation is unavailable or stale"
        )
    attestation = attestations[0]
    candidate_digest, candidate_artifacts = _shadow_candidate_artifacts(
        root,
        prepared,
        attestation,
    )
    return _DesignCloseProof(
        kind="shadow-attestation",
        proof_id=operation.attestation_id,
        proof_digest=operation.attestation_digest,
        operation_id=operation.operation_id,
        stage_input_digest=operation.stage_input_digest,
        stage_key=prepared.stage_key,
        close_kind=prepared.close_kind,
        target_status=prepared.target_status,
        close_artifact_digest=operation.close_artifact_digest,
        candidate_manifest_digest=candidate_digest,
        candidate_artifact_digests=candidate_artifacts,
    )


def _shadow_candidate_artifacts(
    root: Path,
    prepared: PreparedStageClose,
    attestation: StageCloseGateAttestation,
) -> tuple[str, tuple[tuple[str, str], ...]]:
    if attestation.candidate.status != "materialized":
        return "", ()
    candidate_digest = attestation.candidate.candidate_manifest_digest
    scope = _candidate_scope(
        root,
        attestation.candidate.candidate_ref,
        attestation.review_scope,
        work_item_id=prepared.work_item_id,
        stage_instance_id=prepared.stage_instance_id,
    )
    artifacts = _trusted_candidate_artifacts(
        root,
        scope,
        candidate_manifest_digest=candidate_digest,
        stage_key=prepared.stage_key,
        work_item_id=prepared.work_item_id,
        stage_instance_id=prepared.stage_instance_id,
        loop_id=prepared.loop_id,
    )
    return candidate_digest, artifacts


def _trusted_anchored_shadow_proof(
    root: Path,
    anchor: _AnchoredShadowAuthority,
    close_path: Path,
) -> _DesignCloseProof:
    operation = _trusted_anchored_shadow_operation(root, anchor, close_path)
    attestations = _matching_attestations(root, operation)
    if len(attestations) != 1:
        raise ScopeAuthorityIntegrityError(
            "design close reviewed candidate is unavailable or stale"
        )
    candidate_artifacts = _trusted_anchored_candidate_artifacts(
        root,
        anchor,
        attestations[0],
    )
    return _DesignCloseProof(
        kind="shadow-attestation",
        proof_id=operation.attestation_id,
        proof_digest=operation.attestation_digest,
        operation_id=operation.operation_id,
        stage_input_digest=operation.stage_input_digest,
        stage_key=anchor.stage_key,
        close_kind=anchor.close_kind,
        target_status=anchor.target_status,
        close_artifact_digest=operation.close_artifact_digest,
        candidate_manifest_digest=anchor.candidate_manifest_digest,
        candidate_artifact_digests=candidate_artifacts,
    )


def _trusted_anchored_shadow_operation(
    root: Path,
    anchor: _AnchoredShadowAuthority,
    close_path: Path,
) -> StageCloseGateOperation:
    try:
        operation = _read_gate_operation(root, anchor.stage_close_operation_id)
    except (OSError, ValueError) as exc:
        raise ScopeAuthorityIntegrityError(
            "design close Stage Close operation is unavailable"
        ) from exc
    if (
        operation is None
        or operation.state != "shadow_observed"
        or operation.stage_key != anchor.stage_key
        or operation.close_kind != anchor.close_kind
        or operation.stage_input_digest != anchor.stage_input_digest
        or operation.attestation_id != anchor.stage_close_proof_id
        or operation.attestation_digest != anchor.stage_close_proof_digest
        or not _gate_attestation_is_current(root, operation, close_path)
    ):
        raise ScopeAuthorityIntegrityError(
            "design close Stage Close attestation is unavailable or stale"
        )
    return operation


def _trusted_anchored_candidate_artifacts(
    root: Path,
    anchor: _AnchoredShadowAuthority,
    attestation: StageCloseGateAttestation,
) -> tuple[tuple[str, str], ...]:
    if anchor.candidate_manifest_digest:
        if (
            attestation.candidate.status != "materialized"
            or attestation.candidate.candidate_manifest_digest
            != anchor.candidate_manifest_digest
        ):
            raise ScopeAuthorityIntegrityError(
                "design close reviewed candidate is unavailable or stale"
            )
        scope = _candidate_scope(
            root,
            attestation.candidate.candidate_ref,
            attestation.review_scope,
            work_item_id=anchor.work_item_id,
            stage_instance_id=anchor.loop_id,
        )
        return _trusted_candidate_artifacts(
            root,
            scope,
            candidate_manifest_digest=anchor.candidate_manifest_digest,
            stage_key=anchor.stage_key,
            work_item_id=anchor.work_item_id,
            stage_instance_id=anchor.loop_id,
            loop_id=anchor.loop_id,
        )
    elif attestation.candidate.status != "not_materialized":
        raise ScopeAuthorityIntegrityError(
            "design close reviewed candidate binding diverged"
        )
    return ()


def _candidate_scope(
    root: Path,
    candidate_ref: str,
    review_scope: FindingScope | None,
    *,
    work_item_id: str,
    stage_instance_id: str,
) -> FindingScope:
    if review_scope is not None:
        return review_scope
    session_id = PurePosixPath(candidate_ref).parent.name
    if not session_id or session_id in {".", ".."}:
        raise ScopeAuthorityIntegrityError(
            "design close reviewed candidate scope is unavailable"
        )
    try:
        project_id = resolve_repository_project_id(root)
    except (OSError, ValueError) as exc:
        raise ScopeAuthorityIntegrityError(
            "design close reviewed candidate scope is unavailable"
        ) from exc
    return FindingScope(
        project_id=project_id,
        work_item_id=work_item_id,
        stage_instance_id=stage_instance_id,
        session_id=session_id,
    )


def _matching_attestations(
    root: Path,
    operation: StageCloseGateOperation,
) -> tuple[StageCloseGateAttestation, ...]:
    try:
        return tuple(
            item
            for item in _read_gate_attestations(root)
            if item.attestation_id == operation.attestation_id
            and item.attestation_digest == operation.attestation_digest
        )
    except (OSError, ValueError) as exc:
        raise ScopeAuthorityIntegrityError(
            "design close Stage Close attestation is unavailable"
        ) from exc


__all__: list[str] = []
