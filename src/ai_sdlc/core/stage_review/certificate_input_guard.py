"""证书签发与恢复提交共享的锁内输入快照。"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from types import TracebackType
from typing import Protocol

from pydantic import ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    read_json_object,
)
from ai_sdlc.core.stage_review.certificate_artifact_codec import (
    _decode_certificate_artifact as decode_certificate_artifact,
)
from ai_sdlc.core.stage_review.certificate_builder import build_certificate
from ai_sdlc.core.stage_review.certificate_models import (
    StageCloseCertificate,
    StageCloseCertificateRequest,
    StageCloseEvidence,
    StageCloseIntent,
)
from ai_sdlc.core.stage_review.certificate_receipt_store import (
    ReviewReceiptArtifactResolver,
)
from ai_sdlc.core.stage_review.certificate_validation import (
    CertificateInvalidError,
    validate_reconciled_certificate_inputs,
)
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceReconciliation,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.resources import ResourceGovernor
from ai_sdlc.core.stage_review.session import StageReviewSessionService
from ai_sdlc.core.stage_review.session_certificate_inputs import (
    SessionCertificateInputs,
)
from ai_sdlc.core.stage_review.session_contracts import (
    ReconciledCloseCertificateCommand,
)
from ai_sdlc.core.stage_review.session_models import StageReviewSession


class StageCloseContextAuthority(Protocol):
    def resolve_current(
        self,
        intent: StageCloseIntent,
    ) -> StageCloseEvidence | None: ...


class CertificateInputGuard:
    def __init__(
        self,
        sessions: StageReviewSessionService,
        resources: ResourceGovernor,
        request: StageCloseCertificateRequest,
    ) -> None:
        self._sessions = sessions
        self._resources = resources
        self._request = request
        self._stack = ExitStack()

    def __enter__(
        self,
    ) -> tuple[
        SessionCertificateInputs,
        ResourceReservation,
        ResourceReservation,
        ResourceReconciliation,
    ]:
        session = self._sessions.get(self._request.intent.scope)
        try:
            resources = self._resources.hold_certificate_inputs(
                session.resource_reservation_id,
                session.resource_reservation_digest,
                self._request.resource_reconciliation_digest,
            )
            final, current, reconciliation = self._stack.enter_context(resources)
            inputs = self._stack.enter_context(
                self._sessions.hold_certificate_inputs(self._request.intent.scope)
            )
        except BaseException:
            self._stack.close()
            raise
        return inputs, final, current, reconciliation

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return self._stack.__exit__(exc_type, exc, traceback)


class ReconciledCertificateInputGuard:
    def __init__(
        self,
        sessions: StageReviewSessionService,
        resources: ResourceGovernor,
        context_authority: StageCloseContextAuthority,
        receipt_artifact_resolver: ReviewReceiptArtifactResolver,
        command: ReconciledCloseCertificateCommand,
        certificate: StageCloseCertificate,
        request: StageCloseCertificateRequest,
    ) -> None:
        self._sessions = sessions
        self._resources = resources
        self._context_authority = context_authority
        self._receipt_artifact_resolver = receipt_artifact_resolver
        self._command = command
        self._certificate = certificate
        self._request = request
        self._stack = ExitStack()
        self._resource_inputs: tuple[
            ResourceReservation,
            ResourceReservation,
            ResourceReconciliation,
        ] | None = None

    def __enter__(self) -> ReconciledCertificateInputGuard:
        reconciliation = self._resources.get_reconciliation(
            self._request.resource_reconciliation_digest
        )
        self._resource_inputs = self._stack.enter_context(
            self._resources.hold_certificate_inputs(
                reconciliation.reservation_id,
                self._certificate.final_resource_reservation_digest,
                reconciliation.reconciliation_digest,
            )
        )
        return self

    def require_current(self, session: StageReviewSession) -> None:
        if self._resource_inputs is None:
            raise CertificateInvalidError("reconciled certificate guard is inactive")
        inputs = self._sessions.certificate_inputs_for_operation(
            session,
            self._command.command_id,
        )
        final, current, reconciliation = self._resource_inputs
        evidence = resolve_current_evidence(
            self._context_authority,
            self._request.intent,
        )
        if evidence != self._request.evidence:
            raise CertificateInvalidError("protected stage evidence has changed")
        validate_reconciled_certificate_inputs(
            self._request,
            inputs,
            final,
            current,
            reconciliation,
            self._command.aborted_claim,
            self._receipt_artifact_resolver,
        )
        expected = build_certificate(
            self._request,
            inputs,
            final,
            reconciliation,
            issued_at=self._certificate.issued_at,
        )
        persisted = read_certificate(_certificate_path(self._sessions, expected))
        if self._certificate != expected or persisted != expected:
            raise CertificateInvalidError("reconciled certificate is not current")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return self._stack.__exit__(exc_type, exc, traceback)


def read_certificate(path: Path) -> StageCloseCertificate:
    try:
        return decode_certificate_artifact(read_json_object(path))
    except (OSError, ValidationError, ValueError) as exc:
        raise SharedStateIntegrityError(
            "stage close certificate artifact is invalid"
        ) from exc


def resolve_current_evidence(
    authority: StageCloseContextAuthority,
    intent: StageCloseIntent,
) -> StageCloseEvidence:
    current = authority.resolve_current(intent)
    if current is None:
        raise CertificateInvalidError("protected stage evidence is unavailable")
    try:
        return StageCloseEvidence.model_validate(current.model_dump(mode="json"))
    except (ValidationError, ValueError, AttributeError) as exc:
        raise CertificateInvalidError("protected stage evidence is invalid") from exc


def certificate_matches_request(
    certificate: StageCloseCertificate,
    request: StageCloseCertificateRequest,
) -> bool:
    intent = request.intent
    evidence = request.evidence
    return all(
        (
            certificate.scope == intent.scope,
            certificate.gate_id == intent.gate_id,
            certificate.command_id == intent.command_id,
            certificate.close_intent_digest == intent.close_intent_digest,
            certificate.candidate_manifest_digest
            == evidence.candidate_manifest_digest,
            certificate.evidence_digest == evidence.evidence_digest,
            certificate.protected_path_set == evidence.protected_path_set,
            certificate.resource_reconciliation_digest
            == request.resource_reconciliation_digest,
            certificate.session_revision == request.expected_session_revision,
        )
    )


def _certificate_path(
    sessions: StageReviewSessionService,
    certificate: StageCloseCertificate,
) -> Path:
    return (
        sessions.projection_path(certificate.scope).parent
        / "certificates"
        / f"{certificate.certificate_id}.json"
    )
