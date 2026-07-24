"""完整同候选 Quorum 的唯一关闭证书签发与当前性验证。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from ai_sdlc.core.stage_review.certificate_builder import build_certificate
from ai_sdlc.core.stage_review.certificate_input_guard import (
    CertificateInputGuard,
    StageCloseContextAuthority,
    read_certificate,
    resolve_current_evidence,
)
from ai_sdlc.core.stage_review.certificate_models import (
    StageCloseCertificate,
    StageCloseCertificateRequest,
    StageCloseEvidence,
    StageCloseIntent,
)
from ai_sdlc.core.stage_review.certificate_persistence import (
    persist_certificate_and_proof,
)
from ai_sdlc.core.stage_review.certificate_receipt_store import (
    ReviewReceiptArtifactResolver,
)
from ai_sdlc.core.stage_review.certificate_receipt_store import (
    _require_canonical_receipt_artifact_store as require_canonical_receipt_artifact_store,
)
from ai_sdlc.core.stage_review.certificate_recovery import (
    CertificateRecoveryCoordinator,
    StageCloseRecoveryAuthority,
)
from ai_sdlc.core.stage_review.certificate_validation import (
    CertificateInvalidError,
    validate_reconciled_certificate_inputs,
)
from ai_sdlc.core.stage_review.certificate_validation import (
    _validate_certificate_inputs as validate_certificate_inputs,
)
from ai_sdlc.core.stage_review.close_governance import StageCloseGovernanceAuthority
from ai_sdlc.core.stage_review.close_models import CloseConsumptionClaim
from ai_sdlc.core.stage_review.close_recovery_models import StageCloseRecoveryDecision
from ai_sdlc.core.stage_review.close_transaction_authority import (
    CanonicalCloseTransactionAuthority,
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
from ai_sdlc.core.stage_review.session_close_authorities import (
    SessionCloseTransactionAuthority,
)
from ai_sdlc.core.stage_review.session_close_recovery_ops import (
    ReconciledCertificateGuard,
)
from ai_sdlc.core.stage_review.session_contracts import (
    CloseConsumptionStartCommand,
    ReconciledCloseCertificateCommand,
)

__all__ = ["CertificateInvalidError", "StageCloseCertificate", "StageCloseCertificateAuthority", "StageCloseCertificateRequest", "StageCloseEvidence", "StageCloseIntent"]


class StageCloseCertificateAuthority:
    def __init__(
        self,
        sessions: StageReviewSessionService,
        resources: ResourceGovernor,
        *,
        context_authority: StageCloseContextAuthority,
        receipt_artifact_resolver: ReviewReceiptArtifactResolver | None = None,
        clock: Callable[[], str],
    ) -> None:
        if not isinstance(resources, ResourceGovernor):
            raise TypeError("canonical ResourceGovernor is required")
        receipt_artifact_resolver = require_canonical_receipt_artifact_store(
            receipt_artifact_resolver,
            project_id=sessions.project_id,
            shared_root=sessions.shared_state_root,
        )
        self._sessions = sessions
        self._resources = resources
        self._context_authority = context_authority
        self._receipt_artifact_resolver = receipt_artifact_resolver
        self._clock = clock
        self._require_authority_binding()
        self._recovery = CertificateRecoveryCoordinator(
            sessions,
            resources,
            context_authority,
            receipt_artifact_resolver,
            self.require_persisted,
        )
        self._sessions.bind_close_authority(self)

    @property
    def project_id(self) -> str:
        return self._sessions.project_id

    @property
    def shared_state_binding_id(self) -> str:
        self._require_authority_binding()
        return self._sessions.shared_state_binding_id

    @property
    def session_service(self) -> StageReviewSessionService:
        return self._sessions

    def require_close_start_current(
        self,
        command: CloseConsumptionStartCommand,
    ) -> None:
        trusted = self.require_persisted(command.certificate)
        request = StageCloseCertificateRequest.model_validate(command.certificate_request)
        evidence = resolve_current_evidence(
            self._context_authority,
            request.intent,
        )
        claim = command.claim
        checks = (
            request.evidence == evidence,
            trusted.scope == request.intent.scope == command.scope == claim.scope,
            trusted.command_id == request.intent.command_id == claim.command_id,
            trusted.close_intent_digest
            == request.intent.close_intent_digest
            == claim.close_intent_digest,
            trusted.candidate_manifest_digest
            == request.evidence.candidate_manifest_digest
            == claim.candidate_manifest_digest,
            trusted.certificate_id == claim.certificate_id,
            trusted.certificate_digest == claim.certificate_digest,
            trusted.protected_path_set == claim.protected_path_set,
        )
        if not all(checks):
            raise CertificateInvalidError(
                "stage close claim evidence changed before session consumption"
            )

    def bind_transaction_authority(
        self,
        authority: SessionCloseTransactionAuthority,
    ) -> CanonicalCloseTransactionAuthority:
        if type(authority) is not CanonicalCloseTransactionAuthority:
            raise TypeError("canonical close transaction authority is required")
        return self._sessions._close.bind_transaction_authority(authority)

    def bind_recovery_authority(
        self,
        authority: StageCloseRecoveryAuthority,
    ) -> None:
        if type(authority) is not StageCloseGovernanceAuthority:
            raise TypeError("canonical close governance authority is required")
        self._recovery.bind(authority)

    def issue(self, request: StageCloseCertificateRequest) -> StageCloseCertificate:
        self._require_authority_binding()
        trusted = StageCloseCertificateRequest.model_validate(request)
        self._sessions.get(trusted.intent.scope)
        with self._locked_inputs(trusted) as values:
            session_inputs, final, current, reconciliation = values
            self._validate_inputs(
                trusted, session_inputs, final, current, reconciliation
            )
            return self._persist_certificate(
                trusted, session_inputs, final, current, reconciliation
            )

    def issue_reconciled(
        self,
        request: StageCloseCertificateRequest,
        aborted_claim: CloseConsumptionClaim,
        decision: StageCloseRecoveryDecision,
    ) -> StageCloseCertificate:
        trusted = StageCloseCertificateRequest.model_validate(request)
        self._recovery.require(
            aborted_claim,
            decision,
            trusted.intent.command_id,
        )
        with self._locked_inputs(trusted) as values:
            inputs, final, current, reconciliation = values
            self._validate_reconciled_inputs(
                trusted, inputs, final, current, reconciliation, aborted_claim
            )
            return self._persist_certificate(
                trusted,
                inputs,
                final,
                current,
                reconciliation,
                aborted_claim=aborted_claim,
                recovery_decision=decision,
            )

    def find_reconciled(
        self,
        request: StageCloseCertificateRequest,
        aborted_claim: CloseConsumptionClaim,
        decision: StageCloseRecoveryDecision,
    ) -> StageCloseCertificate | None:
        return self._recovery.find(request, aborted_claim, decision)

    def require_reconciled_artifacts(
        self,
        command: ReconciledCloseCertificateCommand,
    ) -> None:
        self._recovery.require_artifacts(command)

    def hold_reconciled_close_current(
        self,
        command: ReconciledCloseCertificateCommand,
    ) -> AbstractContextManager[ReconciledCertificateGuard]:
        return self._recovery.hold_current(command)

    def require_current(
        self,
        certificate: StageCloseCertificate,
        request: StageCloseCertificateRequest,
    ) -> StageCloseCertificate:
        with self.hold_current(certificate, request) as trusted:
            return trusted

    def require_persisted(
        self,
        certificate: StageCloseCertificate,
    ) -> StageCloseCertificate:
        self._require_authority_binding()
        trusted = StageCloseCertificate.model_validate(
            certificate.model_dump(mode="json")
        )
        if trusted.compatibility_mode != "strict":
            raise CertificateInvalidError("legacy stage close certificate is read-only")
        if read_certificate(self.certificate_path(trusted)) != trusted:
            raise CertificateInvalidError("persisted stage close certificate differs")
        return trusted

    def require_recovery_context(
        self,
        certificate: StageCloseCertificate,
        request: StageCloseCertificateRequest,
    ) -> StageCloseCertificate:
        trusted = self.require_persisted(certificate)
        expected_request = StageCloseCertificateRequest.model_validate(request)
        evidence = resolve_current_evidence(
            self._context_authority,
            expected_request.intent,
        )
        session = self._sessions.get(expected_request.intent.scope)
        checks = (
            evidence == expected_request.evidence,
            trusted.scope == expected_request.intent.scope == session.scope,
            trusted.command_id == expected_request.intent.command_id,
            trusted.close_intent_digest == expected_request.intent.close_intent_digest,
            trusted.candidate_manifest_digest
            == expected_request.evidence.candidate_manifest_digest
            == session.active_candidate_digest,
        )
        if not all(checks):
            raise CertificateInvalidError(
                "stage close recovery candidate or evidence context has changed"
            )
        return trusted

    @contextmanager
    def hold_current(
        self,
        certificate: StageCloseCertificate,
        request: StageCloseCertificateRequest,
    ) -> Iterator[StageCloseCertificate]:
        trusted = self.require_persisted(certificate)
        expected_request = StageCloseCertificateRequest.model_validate(request)
        self._sessions.get(expected_request.intent.scope)
        with self._locked_inputs(expected_request) as values:
            session_inputs, final, current, reconciliation = values
            self._validate_inputs(
                expected_request,
                session_inputs,
                final,
                current,
                reconciliation,
            )
            expected = build_certificate(
                expected_request,
                session_inputs,
                final,
                reconciliation,
                issued_at=trusted.issued_at,
            )
            if trusted != expected:
                raise CertificateInvalidError("stage close certificate is not current")
            yield trusted

    def certificate_path(self, certificate: StageCloseCertificate) -> Path:
        scope = certificate.scope
        return (
            self._sessions.projection_path(scope).parent
            / "certificates"
            / f"{certificate.certificate_id}.json"
        )

    def _locked_inputs(
        self,
        request: StageCloseCertificateRequest,
    ) -> AbstractContextManager[
        tuple[
            SessionCertificateInputs,
            ResourceReservation,
            ResourceReservation,
            ResourceReconciliation,
        ]
    ]:
        return CertificateInputGuard(self._sessions, self._resources, request)

    def _require_authority_binding(self) -> None:
        session_binding = self._sessions.require_shared_state_binding()
        resource_binding = self._resources.require_shared_state_binding()
        if (
            self._sessions.project_id != self._resources.project_id
            or session_binding != resource_binding
        ):
            raise CertificateInvalidError(
                "certificate authorities do not share canonical project state"
            )

    def _validate_inputs(
        self,
        request: StageCloseCertificateRequest,
        inputs: SessionCertificateInputs,
        final: ResourceReservation,
        current: ResourceReservation,
        reconciliation: ResourceReconciliation,
    ) -> None:
        trusted_evidence = resolve_current_evidence(
            self._context_authority,
            request.intent,
        )
        if trusted_evidence != request.evidence:
            raise CertificateInvalidError("protected stage evidence has changed")
        validate_certificate_inputs(
            request,
            inputs,
            final,
            current,
            reconciliation,
            self._receipt_artifact_resolver,
        )

    def _validate_reconciled_inputs(
        self,
        request: StageCloseCertificateRequest,
        inputs: SessionCertificateInputs,
        final: ResourceReservation,
        current: ResourceReservation,
        reconciliation: ResourceReconciliation,
        aborted_claim: CloseConsumptionClaim,
    ) -> None:
        evidence = resolve_current_evidence(self._context_authority, request.intent)
        if evidence != request.evidence:
            raise CertificateInvalidError("protected stage evidence has changed")
        validate_reconciled_certificate_inputs(
            request,
            inputs,
            final,
            current,
            reconciliation,
            aborted_claim,
            self._receipt_artifact_resolver,
        )

    def _persist_certificate(
        self,
        request: StageCloseCertificateRequest,
        inputs: SessionCertificateInputs,
        final: ResourceReservation,
        current: ResourceReservation,
        reconciliation: ResourceReconciliation,
        *,
        aborted_claim: CloseConsumptionClaim | None = None,
        recovery_decision: StageCloseRecoveryDecision | None = None,
    ) -> StageCloseCertificate:
        return persist_certificate_and_proof(
            self._sessions.projection_path(request.intent.scope).parent
            / "certificates",
            request,
            inputs,
            final,
            current,
            reconciliation,
            self._receipt_artifact_resolver,
            self._clock,
            aborted_claim=aborted_claim,
            recovery_decision=recovery_decision,
        )
