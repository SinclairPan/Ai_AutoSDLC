"""governed abort 后仅允许 supersede 或全新证书恢复。"""

from __future__ import annotations

from contextlib import AbstractContextManager
from functools import partial
from typing import Protocol

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
)
from ai_sdlc.core.stage_review.close_models import CloseConsumptionClaim
from ai_sdlc.core.stage_review.close_recovery_models import StageCloseRecoveryDecision
from ai_sdlc.core.stage_review.session_artifact_models import ArtifactRef
from ai_sdlc.core.stage_review.session_contracts import (
    CloseAbortSupersedeCommand,
    ReconciledCloseCertificateCommand,
    SessionEventKind,
    SessionIntegrityError,
)
from ai_sdlc.core.stage_review.session_models import (
    SessionEvent,
    SessionMutationResult,
    SessionOperation,
    StageReviewSession,
    replace_projection,
)
from ai_sdlc.core.stage_review.session_runtime import SessionRuntime


class RecoveryGovernanceAuthority(Protocol):
    def require_recovery(
        self,
        claim: CloseConsumptionClaim,
        decision: StageCloseRecoveryDecision,
    ) -> StageCloseRecoveryDecision: ...


class ReconciledCertificateGuard(Protocol):
    def require_current(self, session: StageReviewSession) -> None: ...


class RecoveryRepoWriteGuard(Protocol):
    def require_current(self) -> object: ...


class RecoveryCertificateAuthority(Protocol):
    def require_reconciled_artifacts(
        self,
        command: ReconciledCloseCertificateCommand,
    ) -> None: ...

    def hold_reconciled_close_current(
        self,
        command: ReconciledCloseCertificateCommand,
    ) -> AbstractContextManager[ReconciledCertificateGuard]: ...


class RecoveryTransactionAuthority(Protocol):
    def require_reconciled_claim_current(
        self,
        command: ReconciledCloseCertificateCommand,
    ) -> None: ...

    def require_aborted_claim_current(
        self,
        claim: CloseConsumptionClaim,
    ) -> None: ...


class SessionCloseRecoveryOps:
    def __init__(self, runtime: SessionRuntime) -> None:
        self._runtime = runtime
        self._certificate: RecoveryCertificateAuthority | None = None
        self._governance: RecoveryGovernanceAuthority | None = None
        self._transaction: RecoveryTransactionAuthority | None = None

    def bind_certificate(self, authority: RecoveryCertificateAuthority) -> None:
        self._certificate = authority

    def bind_governance(self, authority: RecoveryGovernanceAuthority) -> None:
        self._governance = authority

    def bind_transaction(self, authority: RecoveryTransactionAuthority) -> None:
        self._transaction = authority

    def reauthorize(
        self,
        command: ReconciledCloseCertificateCommand,
        *,
        repo_guard: RecoveryRepoWriteGuard | None = None,
    ) -> SessionMutationResult:
        kinds = self._runtime.operation_kinds(
            command,
            ("reconciled_new_certificate_issued",),
        )
        authority = self._require_certificate()
        if self._runtime.store.is_operation_complete(command, kinds):
            return self._replay_completed(command, kinds, authority)
        try:
            with authority.hold_reconciled_close_current(command) as guard:
                session, replay = self._runtime.store.transact(
                    command,
                    kinds,
                    clock=self._runtime.clock,
                    builder=partial(
                        self._build_reauthorize,
                        command,
                        guard,
                        repo_guard,
                    ),
                )
        except (SharedStateIntegrityError, ResourceLockUnavailableError, KeyError):
            if self._runtime.store.is_operation_complete(command, kinds):
                return self._replay_completed(command, kinds, authority)
            raise
        return SessionMutationResult(session=session, idempotent_replay=replay)

    def _replay_completed(
        self,
        command: ReconciledCloseCertificateCommand,
        kinds: tuple[SessionEventKind, ...],
        authority: RecoveryCertificateAuthority,
    ) -> SessionMutationResult:
        self._require_completed_reauthorization(command, authority)
        session, replay = self._runtime.store.transact(
            command,
            kinds,
            clock=self._runtime.clock,
            builder=_reject_incomplete_replay,
        )
        return SessionMutationResult(session=session, idempotent_replay=replay)

    def _require_completed_reauthorization(
        self,
        command: ReconciledCloseCertificateCommand,
        authority: RecoveryCertificateAuthority,
    ) -> None:
        self._require_governance(command.aborted_claim, command.recovery_decision)
        authority.require_reconciled_artifacts(command)
        self._require_transaction().require_reconciled_claim_current(command)

    def supersede(self, command: CloseAbortSupersedeCommand) -> SessionMutationResult:
        kinds = self._runtime.operation_kinds(
            command,
            ("macro_rebaseline_accepted",),
        )
        session, replay = self._runtime.store.transact(
            command,
            kinds,
            clock=self._runtime.clock,
            builder=partial(self._build_supersede, command),
        )
        return SessionMutationResult(session=session, idempotent_replay=replay)

    def _build_reauthorize(
        self,
        command: ReconciledCloseCertificateCommand,
        guard: ReconciledCertificateGuard,
        repo_guard: RecoveryRepoWriteGuard | None,
        base: StageReviewSession | None,
        operation: SessionOperation,
    ) -> tuple[SessionEvent, ...]:
        session = self._runtime.require_base(base)
        self._require_governance(command.aborted_claim, command.recovery_decision)
        guard.require_current(session)
        self._require_transaction().require_reconciled_claim_current(command)
        _require_aborted_session(
            session,
            command.aborted_claim,
            command.recovery_decision,
        )
        if repo_guard is None:
            raise SessionIntegrityError("recovery repo write guard is required")
        repo_guard.require_current()
        projection = replace_projection(
            session.projection,
            state="authorized",
            active_close_certificate_id=command.certificate.certificate_id,
            active_close_certificate_digest=command.certificate.certificate_digest,
            active_close_claim_id=command.claim.claim_id,
            active_close_claim_digest=command.claim.claim_digest,
            close_consumption_receipt_id="",
            close_consumption_receipt_digest="",
            close_governance_decision_digest="",
            close_failure_reason="",
        )
        refs = _reauthorization_refs(command)
        return self._runtime.events(
            session,
            operation,
            (("reconciled_new_certificate_issued", projection, refs),),
        )

    def _build_supersede(
        self,
        command: CloseAbortSupersedeCommand,
        base: StageReviewSession | None,
        operation: SessionOperation,
    ) -> tuple[SessionEvent, ...]:
        session = self._runtime.require_base(base)
        self._require_governance(command.aborted_claim, command.recovery_decision)
        self._require_transaction().require_aborted_claim_current(
            command.aborted_claim
        )
        _require_aborted_session(
            session,
            command.aborted_claim,
            command.recovery_decision,
        )
        projection = replace_projection(
            session.projection,
            state="superseded",
            active_close_certificate_id="",
            active_close_certificate_digest="",
            active_close_claim_id="",
            active_close_claim_digest="",
            close_consumption_receipt_id="",
            close_consumption_receipt_digest="",
            close_governance_decision_digest="",
            close_failure_reason="",
        )
        decision = command.recovery_decision
        ref = ArtifactRef(
            artifact_id=decision.decision_id,
            artifact_digest=decision.decision_digest,
        )
        return self._runtime.events(
            session,
            operation,
            (("macro_rebaseline_accepted", projection, (ref,)),),
        )

    def _require_governance(
        self,
        claim: CloseConsumptionClaim,
        decision: StageCloseRecoveryDecision,
    ) -> None:
        if self._governance is None:
            raise SessionIntegrityError("close recovery governance is not bound")
        if self._governance.require_recovery(claim, decision) != decision:
            raise SessionIntegrityError("close recovery governance decision diverged")

    def _require_certificate(self) -> RecoveryCertificateAuthority:
        if self._certificate is None:
            raise SessionIntegrityError("close recovery certificate authority is not bound")
        return self._certificate

    def _require_transaction(self) -> RecoveryTransactionAuthority:
        if self._transaction is None:
            raise SessionIntegrityError("close recovery transaction authority is not bound")
        return self._transaction


def _reauthorization_refs(
    command: ReconciledCloseCertificateCommand,
) -> tuple[ArtifactRef, ...]:
    return (
        ArtifactRef(
            artifact_id=command.recovery_decision.decision_id,
            artifact_digest=command.recovery_decision.decision_digest,
        ),
        ArtifactRef(
            artifact_id=command.certificate.certificate_id,
            artifact_digest=command.certificate.certificate_digest,
        ),
        ArtifactRef(
            artifact_id=command.claim.claim_id,
            artifact_digest=command.claim.claim_digest,
        ),
    )


def _require_aborted_session(
    session: StageReviewSession,
    claim: CloseConsumptionClaim,
    decision: StageCloseRecoveryDecision,
) -> None:
    projection = session.projection
    checks = (
        projection.state == "needs_user",
        projection.close_failure_reason == "governed_close_abort",
        projection.active_close_certificate_id == claim.certificate_id,
        projection.active_close_certificate_digest == claim.certificate_digest,
        projection.active_close_claim_id == claim.claim_id,
        projection.active_close_claim_digest == claim.claim_digest,
        session.revision == decision.aborted_session_revision,
        session.session_digest == decision.aborted_session_digest,
    )
    if not all(checks):
        raise SessionIntegrityError("aborted close session lineage diverged")


def _reject_incomplete_replay(
    base: StageReviewSession | None,
    operation: SessionOperation,
) -> tuple[SessionEvent, ...]:
    del base, operation
    raise SessionIntegrityError("completed close recovery operation became incomplete")
