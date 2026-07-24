"""StageCloseAuthorizer 与 canonical Session Close Operation 的窄适配。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.certificate_models import (
    StageCloseCertificate,
    StageCloseCertificateRequest,
)
from ai_sdlc.core.stage_review.certificates import StageCloseCertificateAuthority
from ai_sdlc.core.stage_review.close_governance import (
    StageCloseGovernanceAuthority,
)
from ai_sdlc.core.stage_review.close_governance_models import (
    StageCloseAbortRequest,
    StageCloseGovernanceDecision,
)
from ai_sdlc.core.stage_review.close_models import (
    CloseConsumptionClaim,
    CloseConsumptionEvent,
    StageCloseAuthorization,
    StageCloseConsumptionReceipt,
    StageCloseContext,
)
from ai_sdlc.core.stage_review.close_recovery_models import (
    StageCloseRecoveryDecision,
)
from ai_sdlc.core.stage_review.repo_write_lease import RepoWriteLeaseGuard
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.session_contracts import (
    CloseAbortSupersedeCommand,
    CloseConsumptionStartCommand,
    CloseReceiptCommitCommand,
    GovernedCloseAbortCommand,
    ReconciledCloseCertificateCommand,
    SessionIntegrityError,
)
from ai_sdlc.core.stage_review.session_models import StageReviewSession


class StageCloseSessionCoordinator:
    def __init__(
        self,
        authority: StageCloseCertificateAuthority,
        governance: StageCloseGovernanceAuthority,
    ) -> None:
        if not isinstance(governance, StageCloseGovernanceAuthority):
            raise TypeError("canonical StageCloseGovernanceAuthority is required")
        self._authority = authority
        self._sessions = authority.session_service
        self._governance = governance
        if (
            governance.project_id != authority.project_id
            or governance.shared_state_binding_id
            != authority.shared_state_binding_id
        ):
            raise ValueError("stage close governance does not share canonical state")
        self._sessions.bind_close_abort_authority(governance)

    def validate_abort_request(self, request: StageCloseAbortRequest) -> None:
        self._governance.validate_request(request)

    def aborted_is_current(self, claim: CloseConsumptionClaim) -> bool:
        session = self._sessions.get(claim.scope)
        projection = session.projection
        return all(
            (
                session.state == "needs_user",
                projection.close_failure_reason == "governed_close_abort",
                projection.active_close_certificate_id == claim.certificate_id,
                projection.active_close_certificate_digest
                == claim.certificate_digest,
                projection.active_close_claim_id == claim.claim_id,
                projection.active_close_claim_digest == claim.claim_digest,
            )
        )

    def aborted_was_replaced(self, claim: CloseConsumptionClaim) -> bool:
        session = self._sessions.get(claim.scope)
        active_id = session.projection.active_close_certificate_id
        return session.state == "superseded" or bool(
            active_id and active_id != claim.certificate_id
        )

    def issue_abort(
        self,
        request: StageCloseAbortRequest,
        claim: CloseConsumptionClaim,
    ) -> StageCloseGovernanceDecision:
        return self._governance.issue_abort(request, claim)

    def find_abort(
        self,
        claim: CloseConsumptionClaim,
    ) -> StageCloseGovernanceDecision | None:
        return self._governance.find_abort(claim)

    def begin(
        self,
        context: StageCloseContext,
        claim: CloseConsumptionClaim,
    ) -> StageReviewSession:
        command_id = stable_id("session-close-start", claim.claim_digest)
        self._sessions.get(claim.scope)
        result = self._sessions.begin_close(
            CloseConsumptionStartCommand(
                scope=claim.scope,
                command_id=command_id,
                idempotency_key=command_id,
                expected_revision=claim.session_start_revision,
                certificate=context.certificate,
                certificate_request=context.certificate_request,
                claim=claim,
            )
        )
        self._require_identity(result.session, claim)
        if result.session.state not in {"consuming", "consumed"}:
            raise SessionIntegrityError("session close consumption did not start")
        return result.session

    def commit(
        self,
        claim: CloseConsumptionClaim,
        receipt: StageCloseConsumptionReceipt,
    ) -> StageReviewSession:
        command_id = stable_id("session-close-commit", claim.claim_digest)
        self._sessions.get(claim.scope)
        result = self._sessions.commit_close(
            CloseReceiptCommitCommand(
                scope=claim.scope,
                command_id=command_id,
                idempotency_key=command_id,
                expected_revision=claim.session_start_revision + 1,
                claim=claim,
                receipt=receipt,
            )
        )
        self._require_identity(result.session, claim)
        if result.session.state != "consumed":
            raise SessionIntegrityError("session close receipt was not committed")
        return result.session

    def reauthorize(
        self,
        aborted_claim: CloseConsumptionClaim,
        decision: StageCloseRecoveryDecision,
        certificate: StageCloseCertificate,
        certificate_request: StageCloseCertificateRequest,
        claim: CloseConsumptionClaim,
        repo_guard: RepoWriteLeaseGuard | None = None,
    ) -> StageReviewSession:
        self._sessions.get(aborted_claim.scope)
        command_id = stable_id("session-close-reauthorize", decision.decision_digest)
        command = ReconciledCloseCertificateCommand(
            scope=aborted_claim.scope,
            command_id=command_id,
            idempotency_key=command_id,
            expected_revision=decision.aborted_session_revision,
            aborted_claim=aborted_claim,
            recovery_decision=decision,
            certificate=certificate,
            certificate_request=certificate_request,
            claim=claim,
        )
        self._sessions._resume_pending(command.scope, command.command_id)
        return self._sessions._close.reauthorize(
            command,
            repo_guard=repo_guard,
        ).session

    def replay_reauthorization_if_complete(
        self,
        aborted_claim: CloseConsumptionClaim,
        decision: StageCloseRecoveryDecision,
        certificate: StageCloseCertificate,
        certificate_request: StageCloseCertificateRequest,
        claim: CloseConsumptionClaim,
    ) -> StageReviewSession | None:
        session = self._sessions.get(aborted_claim.scope)
        projection = session.projection
        completed = (
            session.state == "authorized"
            and projection.active_close_certificate_id == certificate.certificate_id
            and projection.active_close_certificate_digest
            == certificate.certificate_digest
            and projection.active_close_claim_id == claim.claim_id
            and projection.active_close_claim_digest == claim.claim_digest
        )
        if not completed:
            return None
        return self.reauthorize(
            aborted_claim,
            decision,
            certificate,
            certificate_request,
            claim,
        )

    def supersede(
        self,
        aborted_claim: CloseConsumptionClaim,
        decision: StageCloseRecoveryDecision,
    ) -> StageReviewSession:
        self._sessions.get(aborted_claim.scope)
        command_id = stable_id("session-close-supersede", decision.decision_digest)
        command = CloseAbortSupersedeCommand(
            scope=aborted_claim.scope,
            command_id=command_id,
            idempotency_key=command_id,
            expected_revision=decision.aborted_session_revision,
            aborted_claim=aborted_claim,
            recovery_decision=decision,
        )
        self._sessions._resume_pending(command.scope, command.command_id)
        return self._sessions._close.supersede(command).session

    def abort(
        self,
        claim: CloseConsumptionClaim,
        governance_decision: StageCloseGovernanceDecision,
    ) -> StageReviewSession:
        current = self._sessions.get(claim.scope)
        if current.state == "needs_user":
            self._require_aborted(current, claim, governance_decision.decision_digest)
            return current
        if current.state == "consumed":
            raise SessionIntegrityError("consumed session close cannot be aborted")
        command_id = stable_id("session-close-abort", claim.claim_digest)
        result = self._sessions.abort_close(
            GovernedCloseAbortCommand(
                scope=claim.scope,
                command_id=command_id,
                idempotency_key=command_id,
                expected_revision=current.revision,
                claim=claim,
                governance_decision=governance_decision,
            )
        )
        self._require_aborted(result.session, claim, governance_decision.decision_digest)
        return result.session

    def reconcile(
        self,
        context: StageCloseContext,
        result: StageCloseAuthorization,
        terminal_event: CloseConsumptionEvent | None,
    ) -> StageCloseAuthorization:
        if result.status == "closed":
            if result.receipt is None:
                raise SessionIntegrityError("closed stage lost its receipt")
            self.commit(
                result.claim,
                result.receipt,
            )
            return result
        if terminal_event is None or not terminal_event.governance_decision_digest:
            raise SessionIntegrityError("aborted stage lost governance evidence")
        decision = self._governance.require_abort(
            result.claim,
            terminal_event.governance_decision_digest,
        )
        self.abort(result.claim, decision)
        return result

    @staticmethod
    def _require_identity(
        session: StageReviewSession,
        claim: CloseConsumptionClaim,
    ) -> None:
        projection = session.projection
        checks = (
            projection.active_close_certificate_id == claim.certificate_id,
            projection.active_close_certificate_digest == claim.certificate_digest,
            projection.active_close_claim_id == claim.claim_id,
            projection.active_close_claim_digest == claim.claim_digest,
        )
        if not all(checks):
            raise SessionIntegrityError("session close identity diverged")

    @classmethod
    def _require_aborted(
        cls,
        session: StageReviewSession,
        claim: CloseConsumptionClaim,
        decision_digest: str,
    ) -> None:
        cls._require_identity(session, claim)
        projection = session.projection
        if (
            session.state != "needs_user"
            or projection.close_failure_reason != "governed_close_abort"
            or projection.close_governance_decision_digest != decision_digest
        ):
            raise SessionIntegrityError("session close abort decision diverged")
