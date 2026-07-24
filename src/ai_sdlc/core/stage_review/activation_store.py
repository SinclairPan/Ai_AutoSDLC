"""由已提交 Stage Close Attestation 派生激活样本的不可变存储。"""

from __future__ import annotations

from pathlib import Path

from ai_sdlc.core.loop_models import utc_now_iso
from ai_sdlc.core.stage_review.activation_artifact_codec import (
    decode_activation_session_record,
    read_activation_session_records,
)
from ai_sdlc.core.stage_review.activation_models import (
    ActivationSessionObservation,
    ActivationSessionRecord,
    RiskLevel,
)
from ai_sdlc.core.stage_review.artifacts import (
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
    resolve_repository_project_id,
)
from ai_sdlc.core.stage_review.candidate import (
    CandidateManifest,
    candidate_binding_digest,
)
from ai_sdlc.core.stage_review.close_gate_models import StageCloseGateAttestation
from ai_sdlc.core.stage_review.close_models import StageCloseAuthorization
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.stage_review_execution import (
    StageReviewExecutionOutcome,
)


def _record_activation_session(
    root: Path,
    attestation: StageCloseGateAttestation,
) -> ActivationSessionRecord | None:
    trusted = StageCloseGateAttestation.model_validate(
        attestation.model_dump(mode="json")
    )
    if not _is_completed_review_session(trusted):
        return None
    project_id = resolve_repository_project_id(root)
    record_id = stable_id(
        "activation-session-record",
        trusted.attestation_digest,
        trusted.planning.panel_plan_digest,
    )
    path = _records_root(root, project_id) / f"{record_id}.json"
    current = _read_record(root, path)
    record = _build_record(root, trusted, project_id, record_id, current)
    if current is not None:
        if current.record_digest != record.record_digest:
            raise ValueError("activation session record lineage diverged")
        return current
    if create_json_exclusive(path, record.model_dump(mode="json")):
        return record
    current = decode_activation_session_record(root, read_json_object(path))
    if current.record_digest != record.record_digest:
        raise ValueError("activation session record content address diverged")
    return current


def _build_record(
    root: Path,
    attestation: StageCloseGateAttestation,
    project_id: str,
    record_id: str,
    current: ActivationSessionRecord | None,
) -> ActivationSessionRecord:
    scope = _validated_scope(root, attestation)
    record = ActivationSessionRecord(
        record_id=record_id,
        project_id=project_id,
        close_proof_kind="shadow-attestation",
        close_proof_id=attestation.attestation_id,
        close_proof_digest=attestation.attestation_digest,
        candidate_manifest_digest=attestation.candidate.candidate_manifest_digest,
        panel_plan_digest=attestation.planning.panel_plan_digest,
        review_session_digest=attestation.review_session_digest,
        review_completion_digest=attestation.review_completion_digest,
        scope=scope,
        observation=ActivationSessionObservation.model_validate(
            {
                "session_id": scope.session_id,
                "stage_key": attestation.stage_key,
                "risk_level": attestation.planning.risk_level,
                "mode": attestation.applicability.mode,
                "completed_at": (
                    current.observation.completed_at if current else utc_now_iso()
                ),
            }
        ),
    )
    return record


def _record_enforced_activation_session(
    root: Path,
    *,
    candidate: CandidateManifest,
    panel_plan_digest: str,
    risk_level: RiskLevel,
    review_outcome: StageReviewExecutionOutcome,
    authorization: StageCloseAuthorization,
) -> ActivationSessionRecord:
    trusted_candidate = CandidateManifest.model_validate(
        candidate.model_dump(mode="json")
    )
    trusted_outcome = StageReviewExecutionOutcome.model_validate(
        review_outcome.model_dump(mode="json")
    )
    trusted_authorization = StageCloseAuthorization.model_validate(
        authorization.model_dump(mode="json")
    )
    receipt = trusted_authorization.receipt
    if trusted_outcome.status != "completed" or receipt is None:
        raise ValueError("enforce activation session lacks committed review facts")
    project_id = resolve_repository_project_id(root)
    candidate_digest = candidate_binding_digest(trusted_candidate)
    claim = trusted_authorization.claim
    if (
        claim.scope.project_id != project_id
        or claim.candidate_manifest_digest != candidate_digest
        or receipt.certificate_id != claim.certificate_id
        or receipt.certificate_digest != claim.certificate_digest
    ):
        raise ValueError("enforce activation close proof lineage diverged")
    scope = _validated_scope_from_candidate(
        trusted_candidate,
        claim.scope,
        candidate_digest=candidate_digest,
        stage_key=trusted_candidate.stage_key,
        work_item_id=trusted_candidate.work_item_id,
        stage_instance_id=trusted_candidate.stage_instance_id,
    )
    record_id = stable_id(
        "activation-session-record",
        claim.certificate_digest,
        panel_plan_digest,
    )
    record = ActivationSessionRecord(
        record_id=record_id,
        project_id=project_id,
        close_proof_kind="enforce-certificate",
        close_proof_id=claim.certificate_id,
        close_proof_digest=claim.certificate_digest,
        candidate_manifest_digest=candidate_digest,
        panel_plan_digest=panel_plan_digest,
        review_session_digest=trusted_outcome.review_session_digest,
        review_completion_digest=trusted_outcome.review_completion_digest,
        scope=scope,
        observation=ActivationSessionObservation(
            session_id=scope.session_id,
            stage_key=trusted_candidate.stage_key,
            risk_level=risk_level,
            mode="enforce",
            completed_at=receipt.committed_at,
        ),
    )
    path = _records_root(root, project_id) / f"{record_id}.json"
    current = _read_record(root, path)
    if current is not None:
        if current != record:
            raise ValueError("enforce activation session lineage diverged")
        return current
    if create_json_exclusive(path, record.model_dump(mode="json")):
        return record
    current = decode_activation_session_record(root, read_json_object(path))
    if current != record:
        raise ValueError("enforce activation session content address diverged")
    return current


def _validated_scope(
    root: Path,
    attestation: StageCloseGateAttestation,
) -> FindingScope:
    if attestation.review_scope is None:
        raise ValueError("activation session lacks authoritative review scope")
    return _validated_candidate_scope(
        root,
        attestation.review_scope,
        candidate_digest=attestation.candidate.candidate_manifest_digest,
        stage_key=attestation.stage_key,
        work_item_id=attestation.work_item_id,
        stage_instance_id=attestation.stage_instance_id,
    )


def _validated_candidate_scope(
    root: Path,
    expected: FindingScope,
    *,
    candidate_digest: str,
    stage_key: str,
    work_item_id: str,
    stage_instance_id: str,
) -> FindingScope:
    shared = resolve_canonical_shared_state(root, expected.project_id)
    target = shared / "shadow-planning" / expected.session_id / "candidate.json"
    candidate = CandidateManifest.model_validate(read_json_object(target))
    if candidate_binding_digest(candidate) != candidate_digest:
        raise ValueError("activation candidate manifest digest diverged")
    actual = FindingScope(
        project_id=candidate.project_id,
        work_item_id=candidate.work_item_id,
        stage_instance_id=candidate.stage_instance_id,
        session_id=candidate.review_session_id,
    )
    if (
        actual != expected
        or candidate.work_item_id != work_item_id
        or candidate.stage_instance_id != stage_instance_id
        or candidate.stage_key != stage_key
    ):
        raise ValueError("activation review scope lineage diverged")
    return actual


def _validated_scope_from_candidate(
    candidate: CandidateManifest,
    expected: FindingScope,
    *,
    candidate_digest: str,
    stage_key: str,
    work_item_id: str,
    stage_instance_id: str,
) -> FindingScope:
    if candidate_binding_digest(candidate) != candidate_digest:
        raise ValueError("activation candidate manifest digest diverged")
    actual = FindingScope(
        project_id=candidate.project_id,
        work_item_id=candidate.work_item_id,
        stage_instance_id=candidate.stage_instance_id,
        session_id=candidate.review_session_id,
    )
    if (
        actual != expected
        or candidate.work_item_id != work_item_id
        or candidate.stage_instance_id != stage_instance_id
        or candidate.stage_key != stage_key
    ):
        raise ValueError("activation review scope lineage diverged")
    return actual


def _read_record(
    root: Path,
    path: Path,
) -> ActivationSessionRecord | None:
    if not path.is_file():
        return None
    return decode_activation_session_record(root, read_json_object(path))


def _read_activation_session_records(
    root: Path,
) -> tuple[ActivationSessionRecord, ...]:
    project_id = resolve_repository_project_id(root)
    records_root = _records_root(root, project_id)
    if not records_root.is_dir():
        return ()
    return read_activation_session_records(
        root,
        tuple(sorted(records_root.glob("*.json"))),
    )


def _is_completed_review_session(attestation: StageCloseGateAttestation) -> bool:
    return (
        attestation.applicability.mode in {"shadow", "enforce"}
        and attestation.candidate.status == "materialized"
        and attestation.planning.status == "resolved"
        and bool(attestation.review_session_digest)
        and bool(attestation.review_completion_digest)
    )


def _records_root(root: Path, project_id: str) -> Path:
    shared = resolve_canonical_shared_state(root, project_id)
    bind_repository_project(shared, project_id)
    return shared / "activation" / "session-records"
