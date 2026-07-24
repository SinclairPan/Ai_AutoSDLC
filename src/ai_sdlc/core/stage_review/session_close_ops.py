"""Session 与 Stage Close Claim/Receipt 的可恢复消费事务。"""

from __future__ import annotations

from functools import partial

from ai_sdlc.core.stage_review.close_governance import StageCloseGovernanceAuthority
from ai_sdlc.core.stage_review.close_models import CloseConsumptionClaim
from ai_sdlc.core.stage_review.close_transaction_authority import (
    CanonicalCloseTransactionAuthority,
)
from ai_sdlc.core.stage_review.session_artifact_models import ArtifactRef
from ai_sdlc.core.stage_review.session_close_authorities import (
    SessionCloseAbortAuthority,
    SessionCloseStartAuthority,
    SessionCloseTransactionAuthority,
)
from ai_sdlc.core.stage_review.session_close_recovery_ops import (
    RecoveryRepoWriteGuard,
    SessionCloseRecoveryOps,
)
from ai_sdlc.core.stage_review.session_contracts import (
    CloseAbortSupersedeCommand,
    CloseConsumptionStartCommand,
    CloseReceiptCommitCommand,
    GovernedCloseAbortCommand,
    ReconciledCloseCertificateCommand,
    SessionEventKind,
    SessionIntegrityError,
)
from ai_sdlc.core.stage_review.session_models import (
    SessionEvent,
    SessionMutationResult,
    SessionOperation,
    SessionProjectionData,
    StageReviewSession,
    replace_projection,
)
from ai_sdlc.core.stage_review.session_runtime import EventSpec, SessionRuntime


class SessionCloseOps:
    def __init__(self, runtime: SessionRuntime) -> None:
        self._runtime = runtime
        self._authority: SessionCloseStartAuthority | None = None
        self._abort_authority: SessionCloseAbortAuthority | None = None
        self._transaction_authority: SessionCloseTransactionAuthority | None = None
        self._recovery = SessionCloseRecoveryOps(runtime)

    def bind_authority(self, authority: SessionCloseStartAuthority) -> None:
        if self._authority is not None and self._authority is not authority:
            raise SessionIntegrityError("session close authority is already bound")
        self._authority = authority
        self._recovery.bind_certificate(authority)

    def bind_abort_authority(self, authority: SessionCloseAbortAuthority) -> None:
        if type(authority) is not StageCloseGovernanceAuthority:
            raise TypeError("canonical close governance authority is required")
        current = self._abort_authority
        if current is not None and _abort_authority_identity(
            current
        ) != _abort_authority_identity(authority):
            raise SessionIntegrityError("session close abort authority is already bound")
        if self._abort_authority is None:
            self._abort_authority = authority
            self._recovery.bind_governance(authority)

    def bind_transaction_authority(
        self,
        authority: SessionCloseTransactionAuthority,
    ) -> CanonicalCloseTransactionAuthority:
        if type(authority) is not CanonicalCloseTransactionAuthority:
            raise TypeError("canonical close transaction authority is required")
        current = self._transaction_authority
        if current is not None:
            if (
                type(current) is not CanonicalCloseTransactionAuthority
                or current.canonical_binding != authority.canonical_binding
            ):
                raise SessionIntegrityError("session close transaction authority is bound")
            return current
        self._transaction_authority = authority
        self._recovery.bind_transaction(authority)
        return authority

    def start(self, command: CloseConsumptionStartCommand) -> SessionMutationResult:
        kinds = self._runtime.operation_kinds(
            command,
            ("close_consumption_started",),
        )
        session, replay = self._runtime.store.transact(
            command,
            kinds,
            clock=self._runtime.clock,
            builder=partial(self._build_start, command),
        )
        return SessionMutationResult(session=session, idempotent_replay=replay)

    def commit(self, command: CloseReceiptCommitCommand) -> SessionMutationResult:
        kinds = self._runtime.operation_kinds(command, ("close_receipt_committed",))
        session, replay = self._runtime.store.transact(
            command,
            kinds,
            clock=self._runtime.clock,
            builder=partial(self._build_commit, command),
        )
        return SessionMutationResult(session=session, idempotent_replay=replay)

    def abort(self, command: GovernedCloseAbortCommand) -> SessionMutationResult:
        current = self._runtime.store.rebuild(command.scope)
        session = self._runtime.require_base(current)
        defaults: tuple[SessionEventKind, ...] = ("governed_close_abort",)
        if session.state == "authorized":
            defaults = ("close_consumption_started", "governed_close_abort")
        kinds = self._runtime.operation_kinds(command, defaults)
        session, replay = self._runtime.store.transact(
            command,
            kinds,
            clock=self._runtime.clock,
            builder=partial(self._build_abort, command, kinds),
        )
        return SessionMutationResult(session=session, idempotent_replay=replay)

    def reauthorize(
        self,
        command: ReconciledCloseCertificateCommand,
        *,
        repo_guard: RecoveryRepoWriteGuard | None = None,
    ) -> SessionMutationResult:
        return self._recovery.reauthorize(command, repo_guard=repo_guard)

    def supersede(
        self,
        command: CloseAbortSupersedeCommand,
    ) -> SessionMutationResult:
        return self._recovery.supersede(command)

    def dispatch(self, command: object) -> SessionMutationResult | None:
        if isinstance(command, CloseConsumptionStartCommand):
            return self.start(command)
        if isinstance(command, CloseReceiptCommitCommand):
            return self.commit(command)
        if isinstance(command, GovernedCloseAbortCommand):
            return self.abort(command)
        if isinstance(command, ReconciledCloseCertificateCommand):
            return self.reauthorize(command)
        if isinstance(command, CloseAbortSupersedeCommand):
            return self.supersede(command)
        return None

    def _build_start(
        self,
        command: CloseConsumptionStartCommand,
        base: StageReviewSession | None,
        operation: SessionOperation,
    ) -> tuple[SessionEvent, ...]:
        session = self._runtime.require_base(base)
        self._require_start_binding(session, command)
        self._require_authority().require_close_start_current(command)
        self._require_transaction_authority().require_close_claim_current(command)
        projection = _start_projection(session.projection, command)
        return self._runtime.events(
            session,
            operation,
            (("close_consumption_started", projection, _start_refs(command)),),
        )

    def _build_commit(
        self,
        command: CloseReceiptCommitCommand,
        base: StageReviewSession | None,
        operation: SessionOperation,
    ) -> tuple[SessionEvent, ...]:
        session = self._runtime.require_base(base)
        _require_active_claim(session.projection, command.claim)
        self._require_transaction_authority().require_close_receipt_current(command)
        receipt = command.receipt
        if receipt.claim_digest != command.claim.claim_digest:
            raise SessionIntegrityError("close receipt does not belong to active claim")
        projection = replace_projection(
            session.projection,
            state="consumed",
            close_consumption_receipt_id=receipt.receipt_id,
            close_consumption_receipt_digest=receipt.receipt_digest,
        )
        ref = ArtifactRef(
            artifact_id=receipt.receipt_id,
            artifact_digest=receipt.receipt_digest,
        )
        return self._runtime.events(
            session,
            operation,
            (("close_receipt_committed", projection, (ref,)),),
        )

    def _build_abort(
        self,
        command: GovernedCloseAbortCommand,
        kinds: tuple[SessionEventKind, ...],
        base: StageReviewSession | None,
        operation: SessionOperation,
    ) -> tuple[SessionEvent, ...]:
        session = self._runtime.require_base(base)
        decision = command.governance_decision
        persisted = self._require_abort_authority().require_abort(
            command.claim,
            decision.decision_digest,
        )
        if persisted != decision:
            raise SessionIntegrityError("session close abort decision diverged")
        specs: list[EventSpec] = []
        projection = session.projection
        if kinds[0] == "close_consumption_started":
            if session.state != "authorized":
                raise SessionIntegrityError("governed abort cannot start consumption")
            projection = _abort_start_projection(projection, command)
            specs.append((kinds[0], projection, _claim_refs(command.claim)))
        else:
            _require_active_claim(projection, command.claim)
        projection = replace_projection(
            projection,
            state="needs_user",
            close_governance_decision_digest=decision.decision_digest,
            close_failure_reason="governed_close_abort",
        )
        specs.append(("governed_close_abort", projection, ()))
        return self._runtime.events(session, operation, tuple(specs))

    def _require_start_binding(
        self,
        session: StageReviewSession,
        command: CloseConsumptionStartCommand,
    ) -> None:
        certificate = command.certificate
        claim = command.claim
        normal = (
            session.session_digest == certificate.session_digest
            and session.revision == certificate.session_revision
            and claim.session_start_revision == certificate.session_revision
        )
        recovered = (
            session.active_close_certificate_id == certificate.certificate_id
            and session.active_close_certificate_digest
            == certificate.certificate_digest
            and session.active_close_claim_id == claim.claim_id
            and session.active_close_claim_digest == claim.claim_digest
            and session.revision == certificate.session_revision + 1
            and claim.session_start_revision == certificate.session_revision + 1
        )
        checks = (
            session.state == "authorized",
            normal or recovered,
            session.revision == claim.session_start_revision,
            session.active_candidate_digest == claim.candidate_manifest_digest,
            certificate.scope == command.scope == claim.scope,
            certificate.certificate_id == claim.certificate_id,
            certificate.certificate_digest == claim.certificate_digest,
            certificate.close_intent_digest == claim.close_intent_digest,
            certificate.protected_path_set == claim.protected_path_set,
        )
        if not all(checks):
            raise SessionIntegrityError("session close start binding is stale")

    def _require_authority(self) -> SessionCloseStartAuthority:
        if self._authority is None:
            raise SessionIntegrityError("session close authority is not bound")
        return self._authority

    def _require_abort_authority(self) -> SessionCloseAbortAuthority:
        if self._abort_authority is None:
            raise SessionIntegrityError("session close abort authority is not bound")
        return self._abort_authority

    def _require_transaction_authority(self) -> SessionCloseTransactionAuthority:
        if self._transaction_authority is None:
            raise SessionIntegrityError("session close transaction authority is not bound")
        return self._transaction_authority


def _start_projection(
    projection: SessionProjectionData,
    command: CloseConsumptionStartCommand,
) -> SessionProjectionData:
    certificate = command.certificate
    claim = command.claim
    return replace_projection(
        projection,
        state="consuming",
        active_close_certificate_id=certificate.certificate_id,
        active_close_certificate_digest=certificate.certificate_digest,
        active_close_claim_id=claim.claim_id,
        active_close_claim_digest=claim.claim_digest,
    )


def _abort_start_projection(
    projection: SessionProjectionData,
    command: GovernedCloseAbortCommand,
) -> SessionProjectionData:
    claim = command.claim
    return replace_projection(
        projection,
        state="consuming",
        active_close_certificate_id=claim.certificate_id,
        active_close_certificate_digest=claim.certificate_digest,
        active_close_claim_id=claim.claim_id,
        active_close_claim_digest=claim.claim_digest,
    )


def _start_refs(command: CloseConsumptionStartCommand) -> tuple[ArtifactRef, ...]:
    return _claim_refs(command.claim)


def _claim_refs(claim: CloseConsumptionClaim) -> tuple[ArtifactRef, ...]:
    return (
        ArtifactRef(
            artifact_id=claim.certificate_id,
            artifact_digest=claim.certificate_digest,
        ),
        ArtifactRef(artifact_id=claim.claim_id, artifact_digest=claim.claim_digest),
    )


def _abort_authority_identity(
    authority: SessionCloseAbortAuthority,
) -> tuple[str, str, str, str]:
    return (
        authority.project_id,
        authority.shared_state_binding_id,
        authority.authority_id,
        authority.authority_binding_digest,
    )


def _require_active_claim(
    projection: SessionProjectionData,
    claim: CloseConsumptionClaim,
) -> None:
    checks = (
        projection.state == "consuming",
        projection.active_close_certificate_id == claim.certificate_id,
        projection.active_close_certificate_digest
        == claim.certificate_digest,
        projection.active_close_claim_id == claim.claim_id,
        projection.active_close_claim_digest == claim.claim_digest,
    )
    if not all(checks):
        raise SessionIntegrityError("session active close claim diverged")
