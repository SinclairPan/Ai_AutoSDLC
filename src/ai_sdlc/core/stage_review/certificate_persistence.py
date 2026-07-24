"""原子持久化关闭证书及其可离线重放的 Authority Proof。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    create_json_exclusive,
    read_json_object,
)
from ai_sdlc.core.stage_review.certificate_builder import build_certificate
from ai_sdlc.core.stage_review.certificate_models import (
    StageCloseCertificate,
    StageCloseCertificateRequest,
)
from ai_sdlc.core.stage_review.certificate_receipt_store import (
    ReviewReceiptArtifactResolver,
)
from ai_sdlc.core.stage_review.certificate_validation import CertificateInvalidError
from ai_sdlc.core.stage_review.ci_certificate_evidence import (
    CiCertificateAuthorityProof,
    capture_ci_certificate_authority_evidence,
)
from ai_sdlc.core.stage_review.close_models import CloseConsumptionClaim
from ai_sdlc.core.stage_review.close_recovery_models import StageCloseRecoveryDecision
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceReconciliation,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.session_certificate_inputs import (
    SessionCertificateInputs,
)


def persist_certificate_and_proof(
    directory: Path,
    request: StageCloseCertificateRequest,
    inputs: SessionCertificateInputs,
    final: ResourceReservation,
    current: ResourceReservation,
    reconciliation: ResourceReconciliation,
    receipt_resolver: ReviewReceiptArtifactResolver,
    clock: Callable[[], str],
    *,
    aborted_claim: CloseConsumptionClaim | None = None,
    recovery_decision: StageCloseRecoveryDecision | None = None,
) -> StageCloseCertificate:
    certificate = build_certificate(
        request,
        inputs,
        final,
        reconciliation,
        issued_at=clock(),
    )
    path = directory / f"{certificate.certificate_id}.json"
    if not create_json_exclusive(path, certificate.model_dump(mode="json")):
        certificate = _require_existing(path, request, inputs, final, reconciliation)
    evidence = capture_ci_certificate_authority_evidence(
        inputs,
        final,
        current,
        reconciliation,
        receipt_resolver,
    )
    proof = CiCertificateAuthorityProof(
        certificate=certificate,
        certificate_request=request,
        authority_evidence=evidence,
        aborted_claim=aborted_claim,
        recovery_decision=recovery_decision,
    )
    _persist_proof(path, proof)
    return certificate


def _require_existing(
    path: Path,
    request: StageCloseCertificateRequest,
    inputs: SessionCertificateInputs,
    final: ResourceReservation,
    reconciliation: ResourceReconciliation,
) -> StageCloseCertificate:
    existing = StageCloseCertificate.model_validate(read_json_object(path))
    expected = build_certificate(
        request,
        inputs,
        final,
        reconciliation,
        issued_at=existing.issued_at,
    )
    if existing != expected:
        raise CertificateInvalidError(
            "stage close certificate identity is already bound"
        )
    return existing


def _persist_proof(path: Path, proof: CiCertificateAuthorityProof) -> None:
    proof_path = (
        path.parent.parent
        / "certificate-proofs"
        / f"{proof.certificate.certificate_id}.json"
    )
    payload = proof.model_dump(mode="json")
    if create_json_exclusive(proof_path, payload):
        return
    existing = CiCertificateAuthorityProof.model_validate(read_json_object(proof_path))
    if existing != proof:
        raise CertificateInvalidError("stage close certificate proof already differs")


__all__ = ["persist_certificate_and_proof"]
