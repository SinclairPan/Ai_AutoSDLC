"""唯一 StageCloseAuthorizer 与 Exactly-Once 关闭恢复。"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path

from ai_sdlc.core.stage_review.certificate_models import StageCloseCertificateRequest
from ai_sdlc.core.stage_review.certificates import (
    CertificateInvalidError,
    StageCloseCertificateAuthority,
)
from ai_sdlc.core.stage_review.close_advancement import CloseFactAdvancer
from ai_sdlc.core.stage_review.close_builders import (
    _build_close_claim as build_close_claim,
)
from ai_sdlc.core.stage_review.close_builders import (
    _build_needs_user_authorization as build_needs_user_authorization,
)
from ai_sdlc.core.stage_review.close_builders import (
    build_close_lease_request,
)
from ai_sdlc.core.stage_review.close_governance import (
    StageCloseGovernanceAuthority,
)
from ai_sdlc.core.stage_review.close_governance_models import StageCloseAbortRequest
from ai_sdlc.core.stage_review.close_models import (
    CloseConsumptionClaim,
    CloseConsumptionState,
    StageCloseAuthorization,
    StageCloseContext,
)
from ai_sdlc.core.stage_review.close_recovery import _build_recovery_services
from ai_sdlc.core.stage_review.close_recovery_models import (
    StageCloseReauthorization,
    StageCloseRecoveryRequest,
)
from ai_sdlc.core.stage_review.close_session_coordinator import (
    StageCloseSessionCoordinator,
)
from ai_sdlc.core.stage_review.close_store import (
    CloseStoreConflictError,
    StageCloseStore,
)
from ai_sdlc.core.stage_review.close_validation import (
    CloseClaimConflictError,
    validate_claim_context,
)
from ai_sdlc.core.stage_review.repo_write_lease import (
    RepoWriteLeaseAuthority,
    canonical_worktree_identity,
)
from ai_sdlc.core.stage_review.session_models import StageReviewSession

__all__ = ["CloseClaimConflictError", "StageCloseAuthorizer", "StageCloseAuthorization", "StageCloseContext"]


class StageCloseAuthorizer:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        certificate_authority: StageCloseCertificateAuthority,
        governance_authority: StageCloseGovernanceAuthority,
        clock: Callable[[], str],
        lock_timeout_seconds: float = 2,
    ) -> None:
        if not isinstance(certificate_authority, StageCloseCertificateAuthority):
            raise TypeError("canonical StageCloseCertificateAuthority is required")
        self._certificates = certificate_authority
        self._clock = clock
        self._store = StageCloseStore(
            root,
            project_id=project_id,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        self._repo_leases = RepoWriteLeaseAuthority(
            root,
            project_id=project_id,
            clock=clock,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        self._advancer = CloseFactAdvancer(self._store, clock=clock)
        self._session = StageCloseSessionCoordinator(
            certificate_authority,
            governance_authority,
        )
        self._transaction_authority, self._recovery = _build_recovery_services(
            certificate_authority,
            governance_authority,
            self._store,
            self._repo_leases,
            self._session,
            clock,
            project_id,
        )
        self.worktree_identity = canonical_worktree_identity(root)
        if (
            certificate_authority.project_id != project_id
            or certificate_authority.shared_state_binding_id
            != self._repo_leases.shared_state_binding_id
        ):
            raise ValueError("stage close authorities do not share canonical state")

    def authorize_stage_close(
        self,
        context: StageCloseContext,
        *,
        before_close_artifact: Callable[[], object] | None = None,
    ) -> StageCloseAuthorization:
        trusted, claim, state = self._prepare_claim(context)
        if state.status != "aborted":
            self._session.begin(trusted, claim)
            self._checkpoint("session_consuming")
        with self._store.locked(claim.certificate_id):
            state = self._store.require_consumable_state(claim)
            self._require_abort_fence(claim, state)
            if state.status == "aborted":
                result = self._recover_aborted_locked(trusted, claim, state)
            else:
                result = self._resume_locked(
                    trusted,
                    claim,
                    state,
                    before_close_artifact,
                )
        return self._session.reconcile(
            trusted,
            result,
            self._store.last_event(claim.certificate_id),
        )

    def _prepare_claim(
        self,
        context: StageCloseContext,
    ) -> tuple[StageCloseContext, CloseConsumptionClaim, CloseConsumptionState]:
        certificate = self._certificates.require_persisted(context.certificate)
        existing = self._store.read_claim(certificate.certificate_id)
        if (
            existing is not None
            and existing.command_id != context.certificate_request.intent.command_id
        ):
            raise CloseClaimConflictError(
                "certificate was claimed by another command"
            )
        trusted = StageCloseContext.model_validate(context.model_dump(mode="json"))
        self._require_worktree(trusted)
        claim = existing or self._create_claim(trusted)
        state = self._store.require_consumable_state(claim)
        self._require_abort_fence(claim, state)
        if state.status == "aborted" and self._session.aborted_was_replaced(claim):
            raise CloseClaimConflictError(
                "aborted close certificate is permanently terminal"
            )
        if existing is not None and state.status != "aborted":
            self._certificates.require_recovery_context(
                trusted.certificate,
                trusted.certificate_request,
            )
        validate_claim_context(claim, trusted)
        return trusted, claim, state

    def reauthorize_aborted_close(
        self,
        context: StageCloseContext,
        *,
        recovery_request: StageCloseRecoveryRequest,
        certificate_request: StageCloseCertificateRequest,
    ) -> StageCloseReauthorization:
        trusted = StageCloseContext.model_validate(context.model_dump(mode="json"))
        self._require_worktree(trusted)
        return self._recovery.reauthorize(
            trusted,
            recovery_request=recovery_request,
            certificate_request=certificate_request,
            checkpoint=self._checkpoint,
        )

    def supersede_aborted_close(
        self,
        context: StageCloseContext,
        *,
        recovery_request: StageCloseRecoveryRequest,
    ) -> StageReviewSession:
        trusted = StageCloseContext.model_validate(context.model_dump(mode="json"))
        self._require_worktree(trusted)
        return self._recovery.supersede(
            trusted,
            recovery_request=recovery_request,
        )

    def abort_stage_close(
        self,
        context: StageCloseContext,
        *,
        governance_request: StageCloseAbortRequest,
    ) -> StageCloseAuthorization:
        trusted = StageCloseContext.model_validate(context.model_dump(mode="json"))
        self._require_worktree(trusted)
        self._session.validate_abort_request(governance_request)
        claim = self._store.read_claim(trusted.certificate.certificate_id)
        if claim is None:
            claim = self._create_claim(trusted)
        validate_claim_context(claim, trusted)
        with self._store.locked(claim.certificate_id):
            state = self._store.require_consumable_state(claim)
            if state.status == "closed":
                raise CloseClaimConflictError("committed close cannot be aborted")
            decision = self._session.issue_abort(governance_request, claim)
            if state.status == "aborted":
                last = self._store.last_event(claim.certificate_id)
                if (
                    last is None
                    or last.governance_decision_digest
                    != decision.decision_digest
                ):
                    raise CloseClaimConflictError("abort governance decision diverged")
                result = self._recover_aborted_locked(trusted, claim, state)
            else:
                result = self._abort_locked(
                    trusted,
                    claim,
                    state,
                    decision.decision_digest,
                )
        return self._session.reconcile(
            trusted,
            result,
            self._store.last_event(claim.certificate_id),
        )

    def _abort_locked(
        self,
        context: StageCloseContext,
        claim: CloseConsumptionClaim,
        state: CloseConsumptionState,
        governance_decision_digest: str,
    ) -> StageCloseAuthorization:
        request = build_close_lease_request(context, claim, state)
        with self._repo_leases.acquire(request) as lease_guard:
            authorize_write = partial(
                self._repo_leases.require_current,
                lease_guard.lease,
            )
            state = self._ensure_prepared(claim, state, authorize_write)
            state = self._store.append_event(
                claim,
                state,
                "aborted",
                occurred_at=self._clock(),
                close_artifact_digest=state.close_artifact_digest or None,
                governance_decision_digest=governance_decision_digest,
                authorize_write=authorize_write,
            )
            self._store.materialize(state, authorize_write=authorize_write)
            self._checkpoint("aborted")
            return build_needs_user_authorization(claim, state)

    def _recover_aborted_locked(
        self,
        context: StageCloseContext,
        claim: CloseConsumptionClaim,
        state: CloseConsumptionState,
    ) -> StageCloseAuthorization:
        if self._store.projection_is_current(state):
            return build_needs_user_authorization(claim, state)
        request = build_close_lease_request(context, claim, state)
        with self._repo_leases.acquire(request) as lease_guard:
            authorize_write = partial(
                self._repo_leases.require_current,
                lease_guard.lease,
            )
            self._store.materialize(state, authorize_write=authorize_write)
        return build_needs_user_authorization(claim, state)

    def _create_claim(self, context: StageCloseContext) -> CloseConsumptionClaim:
        claim = build_close_claim(context, prepared_at=self._clock())
        try:
            with self._certificates.hold_current(
                context.certificate,
                context.certificate_request,
            ):
                try:
                    persisted = self._store.create_claim(claim)
                except CloseStoreConflictError as exc:
                    existing = self._store.read_claim(claim.certificate_id)
                    if existing is None:
                        raise CloseClaimConflictError(
                            "certificate claim could not be reconciled"
                        ) from exc
                    validate_claim_context(existing, context)
                    persisted = existing
        except CertificateInvalidError:
            existing = self._store.read_claim(claim.certificate_id)
            if existing is None:
                raise
            validate_claim_context(existing, context)
            persisted = existing
        self._checkpoint("claim_created")
        return persisted

    def _ensure_prepared(
        self,
        claim: CloseConsumptionClaim,
        state: CloseConsumptionState,
        authorize_write: Callable[[], object],
    ) -> CloseConsumptionState:
        if state.revision:
            return state
        prepared = self._store.append_event(
            claim,
            state,
            "prepared",
            occurred_at=claim.prepared_at,
            authorize_write=authorize_write,
        )
        self._checkpoint("prepared")
        return prepared

    def _resume_locked(
        self,
        context: StageCloseContext,
        claim: CloseConsumptionClaim,
        state: CloseConsumptionState,
        before_close_artifact: Callable[[], object] | None,
    ) -> StageCloseAuthorization:
        request = build_close_lease_request(context, claim, state)
        with self._repo_leases.acquire(request) as lease_guard:
            authorize_write = partial(
                self._repo_leases.require_current,
                lease_guard.lease,
            )
            self._certificates.require_recovery_context(
                context.certificate,
                context.certificate_request,
            )
            state = self._store.require_consumable_state(claim)
            state = self._ensure_prepared(claim, state, authorize_write)
            state, receipt = self._advancer.advance(
                context,
                claim,
                state,
                authorize_write,
                self._checkpoint,
                before_close_artifact=before_close_artifact,
            )
            self._store.materialize(state, authorize_write=authorize_write)
            self._checkpoint("state_materialized")
        return StageCloseAuthorization(
            status="closed",
            claim=claim,
            receipt=receipt,
            state=state,
        )

    def _checkpoint(self, phase: str) -> None:
        del phase

    def _require_worktree(self, context: StageCloseContext) -> None:
        if context.worktree_identity != self.worktree_identity:
            raise ValueError("stage close context worktree identity is invalid")

    def _require_abort_fence(
        self,
        claim: CloseConsumptionClaim,
        state: CloseConsumptionState,
    ) -> None:
        if state.status != "aborted" and self._session.find_abort(claim) is not None:
            raise CloseClaimConflictError(
                "governed abort decision requires abort replay"
            )
