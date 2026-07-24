"""StageCloseAuthorizer 的 governed abort 恢复编排。"""

from __future__ import annotations

from collections.abc import Callable

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.certificate_models import (
    StageCloseCertificate,
    StageCloseCertificateRequest,
)
from ai_sdlc.core.stage_review.certificates import StageCloseCertificateAuthority
from ai_sdlc.core.stage_review.close_builders import (
    _build_close_claim as build_close_claim,
)
from ai_sdlc.core.stage_review.close_builders import (
    build_close_lease_request,
)
from ai_sdlc.core.stage_review.close_governance import (
    StageCloseGovernanceAuthority,
)
from ai_sdlc.core.stage_review.close_models import (
    CloseConsumptionClaim,
    StageCloseContext,
)
from ai_sdlc.core.stage_review.close_recovery_models import (
    StageCloseReauthorization,
    StageCloseRecoveryDecision,
    StageCloseRecoveryRequest,
)
from ai_sdlc.core.stage_review.close_session_coordinator import (
    StageCloseSessionCoordinator,
)
from ai_sdlc.core.stage_review.close_store import StageCloseStore
from ai_sdlc.core.stage_review.close_transaction_authority import (
    CanonicalCloseTransactionAuthority,
)
from ai_sdlc.core.stage_review.close_validation import validate_claim_context
from ai_sdlc.core.stage_review.repo_write_lease import RepoWriteLeaseAuthority
from ai_sdlc.core.stage_review.session_models import StageReviewSession


def _build_recovery_services(
    certificates: StageCloseCertificateAuthority,
    governance: StageCloseGovernanceAuthority,
    store: StageCloseStore,
    repo_leases: RepoWriteLeaseAuthority,
    sessions: StageCloseSessionCoordinator,
    clock: Callable[[], str],
    project_id: str,
) -> tuple[CanonicalCloseTransactionAuthority, StageCloseRecoveryCoordinator]:
    certificates.bind_recovery_authority(governance)
    transaction = CanonicalCloseTransactionAuthority(
        store,
        project_id=project_id,
        shared_state_binding_id=repo_leases.shared_state_binding_id,
    )
    transaction = certificates.bind_transaction_authority(transaction)
    recovery = StageCloseRecoveryCoordinator(
        certificates,
        governance,
        store,
        repo_leases,
        sessions,
        clock,
    )
    return transaction, recovery


class StageCloseRecoveryCoordinator:
    def __init__(
        self,
        certificates: StageCloseCertificateAuthority,
        governance: StageCloseGovernanceAuthority,
        store: StageCloseStore,
        repo_leases: RepoWriteLeaseAuthority,
        sessions: StageCloseSessionCoordinator,
        clock: Callable[[], str],
    ) -> None:
        self._certificates = certificates
        self._governance = governance
        self._store = store
        self._repo_leases = repo_leases
        self._sessions = sessions
        self._clock = clock

    def reauthorize(
        self,
        context: StageCloseContext,
        *,
        recovery_request: StageCloseRecoveryRequest,
        certificate_request: StageCloseCertificateRequest,
        checkpoint: Callable[[str], None],
    ) -> StageCloseReauthorization:
        aborted_claim = self._require_aborted_claim(context)
        session = self._certificates.session_service.get(aborted_claim.scope)
        decision = self._governance.issue_recovery(
            recovery_request,
            aborted_claim,
            session,
        )
        certificate = self._resolve_certificate(
            certificate_request, aborted_claim, decision
        )
        replacement = _replacement_context(
            context,
            certificate_request,
            certificate,
        )
        claim = self._authorize_replacement(
            replacement,
            aborted_claim,
            decision,
            certificate_request,
            checkpoint,
        )
        checkpoint("recovery_session_authorized")
        return StageCloseReauthorization(
            decision=decision,
            certificate=certificate,
            claim=claim,
        )

    def _resolve_certificate(
        self,
        request: StageCloseCertificateRequest,
        claim: CloseConsumptionClaim,
        decision: StageCloseRecoveryDecision,
    ) -> StageCloseCertificate:
        certificate = self._certificates.find_reconciled(
            request,
            claim,
            decision,
        )
        if certificate is not None:
            return certificate
        return self._certificates.issue_reconciled(request, claim, decision)

    def _authorize_replacement(
        self,
        context: StageCloseContext,
        aborted_claim: CloseConsumptionClaim,
        decision: StageCloseRecoveryDecision,
        request: StageCloseCertificateRequest,
        checkpoint: Callable[[str], None],
    ) -> CloseConsumptionClaim:
        claim = self._recovery_claim(context, decision)
        replay = self._sessions.replay_reauthorization_if_complete(
            aborted_claim,
            decision,
            context.certificate,
            request,
            claim,
        )
        if replay is not None:
            return claim
        lease_request = build_close_lease_request(
            context,
            claim,
            self._store.load_state(claim),
        )
        with self._repo_leases.acquire(lease_request) as lease_guard:
            self._repo_leases.require_current(lease_guard.lease)
            claim = self._store.create_claim(claim)
            checkpoint("recovery_claim_created")
            self._repo_leases.require_current(lease_guard.lease)
            self._sessions.reauthorize(
                aborted_claim,
                decision,
                context.certificate,
                request,
                claim,
                lease_guard,
            )
        return claim

    def _recovery_claim(
        self,
        context: StageCloseContext,
        decision: StageCloseRecoveryDecision,
    ) -> CloseConsumptionClaim:
        existing = self._store.read_claim(context.certificate.certificate_id)
        expected_revision = decision.aborted_session_revision + 1
        if existing is not None:
            validate_claim_context(existing, context)
            if existing.session_start_revision != expected_revision:
                raise SharedStateIntegrityError(
                    "reconciled close claim session revision diverged"
                )
            return existing
        return build_close_claim(
            context,
            prepared_at=self._clock(),
            session_start_revision=expected_revision,
        )

    def supersede(
        self,
        context: StageCloseContext,
        *,
        recovery_request: StageCloseRecoveryRequest,
    ) -> StageReviewSession:
        aborted_claim = self._require_aborted_claim(context)
        session = self._certificates.session_service.get(aborted_claim.scope)
        decision = self._governance.issue_recovery(
            recovery_request,
            aborted_claim,
            session,
        )
        if decision.recovery_kind != "supersede_session":
            raise SharedStateIntegrityError("recovery decision does not supersede")
        return self._sessions.supersede(aborted_claim, decision)

    def _require_aborted_claim(
        self,
        context: StageCloseContext,
    ) -> CloseConsumptionClaim:
        self._certificates.require_persisted(context.certificate)
        claim = self._store.read_claim(context.certificate.certificate_id)
        if claim is None:
            raise SharedStateIntegrityError("aborted close claim is missing")
        validate_claim_context(claim, context)
        state = self._store.require_consumable_state(claim)
        if state.status != "aborted":
            raise SharedStateIntegrityError("close claim is not aborted")
        return claim


def _replacement_context(
    context: StageCloseContext,
    request: StageCloseCertificateRequest,
    certificate: StageCloseCertificate,
) -> StageCloseContext:
    payload = certificate.model_dump(mode="json")
    return StageCloseContext.model_validate(
        {
            **context.model_dump(mode="json"),
            "certificate": payload,
            "certificate_request": request.model_dump(mode="json"),
            "context_digest": "",
        }
    )
