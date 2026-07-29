"""读取并核验 Design enforce close 的项目、候选与消费回执证据。"""

from __future__ import annotations

import json
from pathlib import Path

from ai_sdlc.core.scope_authority_store import ScopeAuthorityIntegrityError
from ai_sdlc.core.stable_file_read import read_stable_text
from ai_sdlc.core.stage_review.activation_models import ActivationSessionRecord
from ai_sdlc.core.stage_review.artifacts import (
    read_json_object,
    resolve_canonical_shared_state,
    resolve_repository_project_id,
)
from ai_sdlc.core.stage_review.candidate import (
    CandidateManifest,
    candidate_binding_digest,
)
from ai_sdlc.core.stage_review.close_models import StageCloseConsumptionReceipt
from ai_sdlc.core.stage_review.close_store import StageCloseStore
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.session_contracts import SessionIntegrityError
from ai_sdlc.core.stage_review.session_models import StageReviewSession
from ai_sdlc.core.stage_review.session_reducer import reduce_session_events
from ai_sdlc.core.stage_review.session_store import SessionEventStore


def _trusted_enforce_receipt(
    root: Path,
    record: ActivationSessionRecord,
    project_id: str,
) -> str:
    try:
        session_store = SessionEventStore(root, project_id=project_id)
        session = reduce_session_events(
            record.scope,
            session_store.load_events(record.scope),
        )
        close_store = StageCloseStore(
            root,
            project_id=project_id,
            lock_timeout_seconds=2,
        )
        receipt_path = (
            close_store.receipts_dir / f"{session.active_close_claim_id}.json"
            if session is not None
            else close_store.receipts_dir / "missing.json"
        )
        receipt = StageCloseConsumptionReceipt.model_validate(
            json.loads(
                read_stable_text(
                    close_store.shared_root,
                    receipt_path,
                    encoding="utf-8",
                )
            )
        )
    except (OSError, UnicodeError, ValueError, SessionIntegrityError) as exc:
        raise ScopeAuthorityIntegrityError(
            "design close enforce receipt is unavailable"
        ) from exc
    if session is None or not _enforce_receipt_matches(session, receipt, record):
        raise ScopeAuthorityIntegrityError(
            "design close enforce receipt identity diverged"
        )
    return receipt.close_artifact_digest


def _enforce_receipt_matches(
    session: StageReviewSession,
    receipt: StageCloseConsumptionReceipt,
    record: ActivationSessionRecord,
) -> bool:
    return (
        session.state == "consumed"
        and session.active_close_certificate_id == record.close_proof_id
        and session.active_close_certificate_digest == record.close_proof_digest
        and receipt.receipt_id == session.close_consumption_receipt_id
        and receipt.receipt_digest
        == session.projection.close_consumption_receipt_digest
        and receipt.claim_id == session.active_close_claim_id
        and receipt.claim_digest == session.active_close_claim_digest
        and receipt.certificate_id == record.close_proof_id
        and receipt.certificate_digest == record.close_proof_digest
        and receipt.committed_at == record.observation.completed_at
    )


def _current_project_id(root: Path) -> str:
    try:
        return resolve_repository_project_id(root)
    except (OSError, ValueError) as exc:
        raise ScopeAuthorityIntegrityError(
            "design close repository project identity is unavailable"
        ) from exc


def _trusted_candidate_artifacts(
    root: Path,
    scope: FindingScope,
    *,
    candidate_manifest_digest: str,
    stage_key: str,
    work_item_id: str,
    stage_instance_id: str,
    loop_id: str,
) -> tuple[tuple[str, str], ...]:
    project_id = _current_project_id(root)
    if scope.project_id != project_id:
        raise ScopeAuthorityIntegrityError(
            "design close reviewed candidate belongs to another project"
        )
    try:
        shared = resolve_canonical_shared_state(root, project_id)
        candidate = CandidateManifest.model_validate(
            read_json_object(
                shared / "shadow-planning" / scope.session_id / "candidate.json"
            )
        )
    except (OSError, ValueError) as exc:
        raise ScopeAuthorityIntegrityError(
            "design close reviewed candidate is unavailable"
        ) from exc
    if (
        candidate_binding_digest(candidate) != candidate_manifest_digest
        or candidate.project_id != scope.project_id
        or candidate.review_session_id != scope.session_id
        or candidate.stage_key != stage_key
        or candidate.work_item_id != work_item_id
        or candidate.stage_instance_id != stage_instance_id
        or candidate.loop_id != loop_id
    ):
        raise ScopeAuthorityIntegrityError(
            "design close reviewed candidate identity diverged"
        )
    artifacts = dict(candidate.input_digests)
    for path, digest in candidate.output_digests.items():
        previous = artifacts.setdefault(path, digest)
        if previous != digest:
            raise ScopeAuthorityIntegrityError(
                "design close reviewed candidate artifact binding diverged"
            )
    return tuple(sorted(artifacts.items()))


__all__: list[str] = []
