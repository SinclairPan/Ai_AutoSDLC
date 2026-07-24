"""BudgetGrant 的授权、双账本提交、补偿与确定性失败编排。"""

from __future__ import annotations

from functools import partial

from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_grant_models import (
    BudgetGrant,
    BudgetGrantDecisionClaim,
    BudgetGrantDecisionKind,
    BudgetGrantResourceError,
)
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.session_artifact_models import ArtifactRef
from ai_sdlc.core.stage_review.session_authority import hard_budget_reached
from ai_sdlc.core.stage_review.session_budget_grant_authority import (
    _approval_is_active as approval_is_active,
)
from ai_sdlc.core.stage_review.session_budget_grant_authority import (
    _build_request_proof as build_request_proof,
)
from ai_sdlc.core.stage_review.session_budget_grant_authority import (
    _build_session_budget_grant as build_session_budget_grant,
)
from ai_sdlc.core.stage_review.session_budget_grant_authority import (
    _require_budget_grant_approval as require_budget_grant_approval,
)
from ai_sdlc.core.stage_review.session_budget_grant_authority import (
    _require_sufficient_increment as require_sufficient_increment,
)
from ai_sdlc.core.stage_review.session_budget_grant_authority_contracts import (
    BudgetGrantApprovalResolver,
)
from ai_sdlc.core.stage_review.session_budget_grant_commands import (
    _build_apply_command as build_apply_command,
)
from ai_sdlc.core.stage_review.session_budget_grant_commands import (
    _build_failure_command as build_failure_command,
)
from ai_sdlc.core.stage_review.session_budget_grant_commands import (
    _build_reconcile_command as build_reconcile_command,
)
from ai_sdlc.core.stage_review.session_budget_grant_commands import _merge_replay
from ai_sdlc.core.stage_review.session_budget_grant_coordinator_contracts import (
    SessionBudgetGrantCoordinator,
)
from ai_sdlc.core.stage_review.session_budget_grant_models import (
    BudgetGrantResourceApplication,
)
from ai_sdlc.core.stage_review.session_budget_grant_recovery import (
    _SessionBudgetGrantRecoveryMixin as SessionBudgetGrantRecoveryMixin,
)
from ai_sdlc.core.stage_review.session_budget_grant_request import (
    BudgetGrantRequestProof,
)
from ai_sdlc.core.stage_review.session_budget_grant_transitions import (
    SessionBudgetGrantTransitions,
)
from ai_sdlc.core.stage_review.session_contracts import (
    BudgetGrantApplyCommand,
    BudgetGrantFailureCommand,
    BudgetGrantReconcileCommand,
    BudgetGrantRequestCommand,
    SessionCasConflictError,
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


class SessionBudgetGrantOps(SessionBudgetGrantRecoveryMixin):
    def __init__(
        self,
        runtime: SessionRuntime,
        coordinator: SessionBudgetGrantCoordinator | None,
        approval_resolver: BudgetGrantApprovalResolver | None,
    ) -> None:
        self._runtime = runtime
        self._coordinator = coordinator
        self._approval_resolver = approval_resolver
        self._transitions = SessionBudgetGrantTransitions(runtime, approval_resolver)

    def extend(self, command: BudgetGrantRequestCommand) -> SessionMutationResult:
        requested, request_replay = self._request(command)
        if request_replay and not requested.pending_budget_grant_command_id:
            return SessionMutationResult(session=requested, idempotent_replay=True)
        event = self._request_event(command)
        proof = build_request_proof(self._runtime, command, event)
        grant = build_session_budget_grant(command, requested, event)
        reservation = self._request_reservation(requested, proof)
        require_sufficient_increment(reservation, grant.increment)
        existing = self._runtime.store.get_budget_grant_application(
            requested.scope,
            grant.grant_id,
        )
        if existing is None and not approval_is_active(
            self._approval_resolver,
            proof.approval.approval_digest,
        ):
            failure = BudgetGrantResourceError("approval_revoked")
            return self._terminal_failure(command, event, failure, request_replay)
        try:
            application = existing or self._apply_resource(grant, requested, proof)
            return self._finish(
                command,
                event,
                requested,
                proof,
                application,
                request_replay,
            )
        except BudgetGrantResourceError as failure:
            if failure.retryable:
                raise
            return self._terminal_failure(command, event, failure, request_replay)

    def apply(self, command: BudgetGrantApplyCommand) -> SessionMutationResult:
        return self._transitions.apply(command)

    def reconcile(
        self,
        command: BudgetGrantReconcileCommand,
    ) -> SessionMutationResult:
        return self._transitions.reconcile(command)

    def fail(self, command: BudgetGrantFailureCommand) -> SessionMutationResult:
        return self._transitions.fail(command)

    def _request(
        self,
        command: BudgetGrantRequestCommand,
    ) -> tuple[StageReviewSession, bool]:
        completed = self._runtime.store.completed_session(command)
        if completed is not None:
            return completed, True
        session, replay = self._runtime.store.transact(
            command,
            ("budget_grant_requested",),
            clock=self._runtime.clock,
            builder=partial(self._build_request, command),
        )
        return session, replay

    def _build_request(
        self,
        command: BudgetGrantRequestCommand,
        base: StageReviewSession | None,
        operation: SessionOperation,
    ) -> tuple[SessionEvent, ...]:
        session = self._runtime.require_base(base)
        approval, _ = require_budget_grant_approval(
            self._runtime,
            self._approval_resolver,
            command,
            session,
        )
        projection = replace_projection(
            session.projection,
            pending_budget_grant_command_id=operation.command_id,
            budget_grant_failure_code="",
        )
        self._runtime.store.persist_budget_grant_approval(approval)
        self._runtime.store.mark_operation_effects_started(operation)
        ref = ArtifactRef(
            artifact_id=approval.approval_id,
            artifact_digest=approval.approval_digest,
        )
        return self._runtime.events(
            session,
            operation,
            (("budget_grant_requested", projection, (ref,)),),
        )

    def _finish(
        self,
        request: BudgetGrantRequestCommand,
        event: SessionEvent,
        session: StageReviewSession,
        proof: BudgetGrantRequestProof,
        application: BudgetGrantResourceApplication,
        request_replay: bool,
    ) -> SessionMutationResult:
        decision, desired = self._final_decision(session, proof, application)
        apply_command = build_apply_command(request, event, application, decision)
        if desired == "reconcile" or decision.decision_kind == "reconcile":
            return self._reconcile(
                request,
                event,
                application,
                decision,
                proof,
                request_replay,
                apply_command=apply_command,
            )
        try:
            with self._require_coordinator().hold_apply_commit(
                application,
                decision,
                session,
                proof,
            ):
                result = self.apply(apply_command)
        except (SessionCasConflictError, BudgetGrantResourceError):
            return self._reconcile(
                request,
                event,
                application,
                decision,
                proof,
                request_replay,
                apply_command=apply_command,
            )
        return _merge_replay(result, request_replay)

    def _final_decision(
        self,
        session: StageReviewSession,
        proof: BudgetGrantRequestProof,
        application: BudgetGrantResourceApplication,
    ) -> tuple[BudgetGrantDecisionClaim, BudgetGrantDecisionKind]:
        active = approval_is_active(
            self._approval_resolver,
            proof.approval.approval_digest,
        )
        if active:
            try:
                application = self._require_coordinator().verify(
                    application,
                    session,
                    proof,
                )
            except BudgetGrantResourceError:
                active = False
        active = active and approval_is_active(
            self._approval_resolver,
            proof.approval.approval_digest,
        )
        desired: BudgetGrantDecisionKind = (
            "session_apply"
            if active and not hard_budget_reached(application.reservation)
            else "reconcile"
        )
        decision = self._require_coordinator().decide(
            application,
            proof,
            desired,
        )
        if self._decision_requires_reconcile(decision, proof, application):
            desired = "reconcile"
        return decision, desired

    def _decision_requires_reconcile(
        self,
        decision: BudgetGrantDecisionClaim,
        proof: BudgetGrantRequestProof,
        application: BudgetGrantResourceApplication,
    ) -> bool:
        current_state = (
            self._approval_resolver.approval_state(
                proof.approval.approval_digest
            )
            if self._approval_resolver is not None
            else None
        )
        apply_command_id = stable_id(
            "budget-grant-session-apply",
            application.grant.grant_id,
        )
        return (
            decision.decision_kind == "reconcile"
            or current_state != decision.approval_state
            or self._runtime.store.operation_was_rejected(
                proof.approval.scope,
                apply_command_id,
            )
        )

    def _apply_resource(
        self,
        grant: BudgetGrant,
        session: StageReviewSession,
        proof: BudgetGrantRequestProof,
    ) -> BudgetGrantResourceApplication:
        application = self._require_coordinator().apply(
            grant,
            session,
            proof,
        )
        if (
            application.grant.grant_digest != grant.grant_digest
            or application.request_proof_digest != proof.proof_digest
        ):
            raise BudgetGrantResourceError("state_corrupt")
        self._runtime.store.persist_budget_grant_application(
            session.scope,
            application,
        )
        return application

    def _reconcile(
        self,
        request: BudgetGrantRequestCommand,
        event: SessionEvent,
        application: BudgetGrantResourceApplication,
        decision: BudgetGrantDecisionClaim,
        proof: BudgetGrantRequestProof,
        request_replay: bool,
        *,
        apply_command: BudgetGrantApplyCommand,
    ) -> SessionMutationResult:
        self._runtime.store.abandon_budget_grant_apply(
            apply_command,
            self._runtime.clock(),
        )
        try:
            reconciliation = self._require_coordinator().reconcile(
                application,
                decision,
                proof,
                apply_command.command_id,
            )
        except BudgetGrantResourceError as failure:
            if failure.retryable:
                raise
            blocked = BudgetGrantResourceError(
                f"reconciliation_{failure.result_code}"
            )
            return self._terminal_failure(request, event, blocked, request_replay)
        current = self._runtime.store.rebuild(request.scope)
        if current is None:
            raise SessionIntegrityError("budget grant reconciliation lost session")
        if current.pending_budget_grant_command_id != request.command_id:
            return SessionMutationResult(
                session=current,
                idempotent_replay=request_replay,
            )
        result = self.reconcile(
            build_reconcile_command(
                request,
                event,
                reconciliation,
                expected_revision=current.revision,
            )
        )
        return _merge_replay(result, request_replay)

    def _terminal_failure(
        self,
        request: BudgetGrantRequestCommand,
        event: SessionEvent,
        failure: BudgetGrantResourceError,
        request_replay: bool,
    ) -> SessionMutationResult:
        result = self.fail(build_failure_command(request, event, failure))
        return _merge_replay(result, request_replay)

    def _request_event(self, command: BudgetGrantRequestCommand) -> SessionEvent:
        matches = tuple(
            event
            for event in self._runtime.store.load_events(command.scope)
            if event.command_id == command.command_id
            and event.event_kind == "budget_grant_requested"
        )
        if len(matches) != 1:
            raise SessionIntegrityError("budget grant request event is missing")
        return matches[0]

    def _request_reservation(
        self,
        session: StageReviewSession,
        proof: BudgetGrantRequestProof,
    ) -> ResourceReservation:
        reservation = self._runtime.resolver.resolve_reservation(
            proof.approval.final_reservation_digest
        )
        if (
            reservation is None
            or reservation.reservation_digest != session.resource_reservation_digest
        ):
            raise SessionIntegrityError("budget grant request reservation is missing")
        return reservation

    def _require_coordinator(self) -> SessionBudgetGrantCoordinator:
        if self._coordinator is None:
            raise SessionIntegrityError("budget grant resource coordinator is missing")
        return self._coordinator
