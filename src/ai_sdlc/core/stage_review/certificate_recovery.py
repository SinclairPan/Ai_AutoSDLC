"""governed abort 后的新证书查找、绑定与提交守卫。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

from ai_sdlc.core.stage_review.certificate_input_guard import (
    ReconciledCertificateInputGuard,
    StageCloseContextAuthority,
    certificate_matches_request,
    read_certificate,
)
from ai_sdlc.core.stage_review.certificate_models import (
    StageCloseCertificate,
    StageCloseCertificateRequest,
)
from ai_sdlc.core.stage_review.certificate_receipt_store import (
    ReviewReceiptArtifactResolver,
)
from ai_sdlc.core.stage_review.certificate_validation import CertificateInvalidError
from ai_sdlc.core.stage_review.close_models import CloseConsumptionClaim
from ai_sdlc.core.stage_review.close_recovery_models import (
    StageCloseRecoveryDecision,
)
from ai_sdlc.core.stage_review.resources import ResourceGovernor
from ai_sdlc.core.stage_review.session import StageReviewSessionService
from ai_sdlc.core.stage_review.session_close_recovery_ops import (
    ReconciledCertificateGuard,
)
from ai_sdlc.core.stage_review.session_contracts import (
    ReconciledCloseCertificateCommand,
)


class StageCloseRecoveryAuthority(Protocol):
    project_id: str
    shared_state_binding_id: str

    @property
    def authority_id(self) -> str: ...

    @property
    def authority_binding_digest(self) -> str: ...

    def require_recovery(
        self,
        claim: CloseConsumptionClaim,
        decision: StageCloseRecoveryDecision,
    ) -> StageCloseRecoveryDecision: ...


class CertificateRecoveryCoordinator:
    def __init__(
        self,
        sessions: StageReviewSessionService,
        resources: ResourceGovernor,
        context_authority: StageCloseContextAuthority,
        receipt_artifact_resolver: ReviewReceiptArtifactResolver,
        require_persisted: Callable[
            [StageCloseCertificate], StageCloseCertificate
        ],
    ) -> None:
        self._sessions = sessions
        self._resources = resources
        self._context_authority = context_authority
        self._receipt_artifact_resolver = receipt_artifact_resolver
        self._require_persisted = require_persisted
        self._authority: StageCloseRecoveryAuthority | None = None

    def bind(self, authority: StageCloseRecoveryAuthority) -> None:
        identity = _authority_identity(authority)
        current = self._authority
        if current is not None and _authority_identity(current) != identity:
            raise CertificateInvalidError("certificate recovery authority changed")
        expected = (
            self._sessions.project_id,
            self._sessions.shared_state_binding_id,
        )
        if identity[:2] != expected:
            raise CertificateInvalidError("certificate recovery authority is foreign")
        if current is None:
            self._authority = authority

    def require(
        self,
        claim: CloseConsumptionClaim,
        decision: StageCloseRecoveryDecision,
        new_command_id: str,
    ) -> None:
        authority = self._authority
        if authority is None:
            raise CertificateInvalidError("certificate recovery authority is not bound")
        persisted = authority.require_recovery(claim, decision)
        if (
            persisted.recovery_kind != "authorize_new_certificate"
            or persisted.new_command_id != new_command_id
        ):
            raise CertificateInvalidError(
                "recovery decision does not authorize certificate"
            )

    def find(
        self,
        request: StageCloseCertificateRequest,
        aborted_claim: CloseConsumptionClaim,
        decision: StageCloseRecoveryDecision,
    ) -> StageCloseCertificate | None:
        trusted = StageCloseCertificateRequest.model_validate(request)
        self.require(aborted_claim, decision, trusted.intent.command_id)
        directory = self._sessions.projection_path(trusted.intent.scope).parent
        candidates = tuple(
            read_certificate(path)
            for path in sorted((directory / "certificates").glob("*.json"))
            if path.is_file()
        )
        matching = tuple(
            certificate
            for certificate in candidates
            if certificate.command_id == trusted.intent.command_id
        )
        if not matching:
            return None
        if len(matching) != 1 or not certificate_matches_request(
            matching[0], trusted
        ):
            raise CertificateInvalidError(
                "reconciled certificate command identity is already bound"
            )
        return self._require_persisted(matching[0])

    def require_artifacts(
        self,
        command: ReconciledCloseCertificateCommand,
    ) -> None:
        trusted = self._require_persisted(command.certificate)
        request = StageCloseCertificateRequest.model_validate(
            command.certificate_request
        )
        self.require(
            command.aborted_claim,
            command.recovery_decision,
            request.intent.command_id,
        )
        if not certificate_matches_request(trusted, request):
            raise CertificateInvalidError(
                "reconciled certificate command artifacts diverged"
            )

    def hold_current(
        self,
        command: ReconciledCloseCertificateCommand,
    ) -> AbstractContextManager[ReconciledCertificateGuard]:
        trusted = self._require_persisted(command.certificate)
        request = StageCloseCertificateRequest.model_validate(
            command.certificate_request
        )
        self.require(
            command.aborted_claim,
            command.recovery_decision,
            request.intent.command_id,
        )
        return ReconciledCertificateInputGuard(
            self._sessions,
            self._resources,
            self._context_authority,
            self._receipt_artifact_resolver,
            command,
            trusted,
            request,
        )

def _authority_identity(
    authority: StageCloseRecoveryAuthority,
) -> tuple[str, str, str, str]:
    return (
        authority.project_id,
        authority.shared_state_binding_id,
        authority.authority_id,
        authority.authority_binding_digest,
    )
