"""BudgetGrant 在 Session 账本中的原子状态转换。"""

from __future__ import annotations

from functools import partial

from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_grant_models import BudgetGrantDecisionClaim
from ai_sdlc.core.stage_review.session_artifact_models import ArtifactRef
from ai_sdlc.core.stage_review.session_budget_grant_authority_contracts import (
    BudgetGrantApprovalResolver,
)
from ai_sdlc.core.stage_review.session_budget_grant_models import (
    BudgetGrantResourceApplication,
)
from ai_sdlc.core.stage_review.session_budget_grant_operation import (
    SessionBudgetGrantOperation,
)
from ai_sdlc.core.stage_review.session_budget_grant_operation import (
    _session_grant_effect_digest as session_grant_effect_digest,
)
from ai_sdlc.core.stage_review.session_budget_grant_validation import (
    _approval_commit_guard as approval_commit_guard,
)
from ai_sdlc.core.stage_review.session_budget_grant_validation import (
    _validate_apply_base as validate_apply_base,
)
from ai_sdlc.core.stage_review.session_budget_grant_validation import (
    _validate_reconcile_base as validate_reconcile_base,
)
from ai_sdlc.core.stage_review.session_budget_reconciliation_models import (
    BudgetGrantResourceReconciliation,
)
from ai_sdlc.core.stage_review.session_contracts import (
    BudgetGrantApplyCommand,
    BudgetGrantFailureCommand,
    BudgetGrantReconcileCommand,
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
from ai_sdlc.core.stage_review.session_runtime import SessionRuntime


class SessionBudgetGrantTransitions:
    def __init__(
        self,
        runtime: SessionRuntime,
        approval_resolver: BudgetGrantApprovalResolver | None,
    ) -> None:
        self._runtime = runtime
        self._approval_resolver = approval_resolver

    def apply(self, command: BudgetGrantApplyCommand) -> SessionMutationResult:
        completed = self._runtime.store.completed_session(command)
        if completed is not None:
            return SessionMutationResult(session=completed, idempotent_replay=True)
        with approval_commit_guard(command, self._approval_resolver):
            session, replay = self._runtime.store.transact(
                command,
                ("budget_grant_applied",),
                clock=self._runtime.clock,
                builder=partial(self._build_apply, command),
            )
        return SessionMutationResult(session=session, idempotent_replay=replay)

    def complete_committed_apply(
        self,
        command: BudgetGrantApplyCommand,
    ) -> SessionMutationResult:
        if not self._runtime.store.operation_events_are_complete(command):
            raise SessionIntegrityError("budget grant apply event is not committed")
        session, replay = self._runtime.store.transact(
            command,
            ("budget_grant_applied",),
            clock=self._runtime.clock,
            builder=partial(self._build_apply, command),
        )
        return SessionMutationResult(session=session, idempotent_replay=replay)

    def reconcile(
        self,
        command: BudgetGrantReconcileCommand,
    ) -> SessionMutationResult:
        completed = self._runtime.store.completed_session(command)
        if completed is not None:
            return SessionMutationResult(session=completed, idempotent_replay=True)
        session, replay = self._runtime.store.transact(
            command,
            ("budget_grant_reconciled",),
            clock=self._runtime.clock,
            builder=partial(self._build_reconcile, command),
        )
        return SessionMutationResult(session=session, idempotent_replay=replay)

    def fail(self, command: BudgetGrantFailureCommand) -> SessionMutationResult:
        completed = self._runtime.store.completed_session(command)
        if completed is not None:
            return SessionMutationResult(session=completed, idempotent_replay=True)
        session, replay = self._runtime.store.transact(
            command,
            ("budget_grant_failed",),
            clock=self._runtime.clock,
            builder=partial(self._build_failure, command),
        )
        return SessionMutationResult(session=session, idempotent_replay=replay)

    def _build_apply(
        self,
        command: BudgetGrantApplyCommand,
        base: StageReviewSession | None,
        operation: SessionOperation,
    ) -> tuple[SessionEvent, ...]:
        session = self._runtime.require_base(base)
        validate_apply_base(session, command)
        return self._build_resource_transition(
            session,
            operation,
            command.request_command_id,
            command.application,
            command.decision,
            None,
        )

    def _build_reconcile(
        self,
        command: BudgetGrantReconcileCommand,
        base: StageReviewSession | None,
        operation: SessionOperation,
    ) -> tuple[SessionEvent, ...]:
        session = self._runtime.require_base(base)
        validate_reconcile_base(session, command)
        return self._build_resource_transition(
            session,
            operation,
            command.request_command_id,
            command.reconciliation.application,
            command.reconciliation.decision,
            command.reconciliation,
        )

    def _build_resource_transition(
        self,
        session: StageReviewSession,
        command_operation: SessionOperation,
        request_command_id: str,
        application: BudgetGrantResourceApplication,
        decision: BudgetGrantDecisionClaim,
        reconciliation: BudgetGrantResourceReconciliation | None,
    ) -> tuple[SessionEvent, ...]:
        operation_id, effect, proof_digest, event_kind = _transition_identity(
            session,
            application,
            decision,
            reconciliation,
        )
        projection = _transition_projection(
            session,
            application,
            decision,
            reconciliation,
            operation_id,
            effect,
        )
        event = self._resource_transition_event(
            session,
            command_operation,
            event_kind,
            projection,
            application,
            proof_digest,
        )
        target = _session_operation(
            session,
            command_operation,
            request_command_id,
            application,
            decision,
            reconciliation,
            operation_id,
            effect,
            event,
        )
        self._persist_resource_transition(
            session,
            command_operation,
            application,
            reconciliation,
            target,
        )
        return (event,)

    def _resource_transition_event(
        self,
        session: StageReviewSession,
        operation: SessionOperation,
        event_kind: SessionEventKind,
        projection: SessionProjectionData,
        application: BudgetGrantResourceApplication,
        proof_digest: str,
    ) -> SessionEvent:
        ref = ArtifactRef(
            artifact_id=application.grant.grant_id,
            artifact_digest=proof_digest,
        )
        return self._runtime.events(
            session,
            operation,
            ((event_kind, projection, (ref,)),),
        )[0]

    def _persist_resource_transition(
        self,
        session: StageReviewSession,
        operation: SessionOperation,
        application: BudgetGrantResourceApplication,
        reconciliation: BudgetGrantResourceReconciliation | None,
        target: SessionBudgetGrantOperation,
    ) -> None:
        self._runtime.store.mark_operation_effects_started(operation)
        self._runtime.store.persist_budget_grant_application(
            session.scope,
            application,
        )
        if reconciliation is not None:
            self._runtime.store.persist_budget_grant_reconciliation(
                session.scope,
                reconciliation,
            )
        self._runtime.store.persist_session_budget_grant_operation(
            session.scope,
            target,
        )

    def _build_failure(
        self,
        command: BudgetGrantFailureCommand,
        base: StageReviewSession | None,
        operation: SessionOperation,
    ) -> tuple[SessionEvent, ...]:
        session = self._runtime.require_base(base)
        if session.pending_budget_grant_command_id != command.request_command_id:
            raise SessionIntegrityError("budget grant failure request diverged")
        projection = replace_projection(
            session.projection,
            state="blocked" if command.integrity_failure else "needs_user",
            budget_resume_state=(
                None if command.integrity_failure else session.budget_resume_state
            ),
            pending_budget_grant_command_id="",
            budget_grant_failure_code=command.failure_code,
        )
        return self._runtime.events(
            session,
            operation,
            (("budget_grant_failed", projection, ()),),
        )


def _transition_identity(
    session: StageReviewSession,
    application: BudgetGrantResourceApplication,
    decision: BudgetGrantDecisionClaim,
    reconciliation: BudgetGrantResourceReconciliation | None,
) -> tuple[str, str, str, SessionEventKind]:
    operation_kind = (
        "session_applied" if reconciliation is None else "reconciled_released"
    )
    proof_digest = (
        decision.decision_digest
        if reconciliation is None
        else reconciliation.reconciliation_digest
    )
    operation_id = stable_id(
        "budget-grant-session-operation",
        application.grant.idempotency_key,
        operation_kind,
    )
    effect = session_grant_effect_digest(
        operation_kind,
        application.grant.grant_digest,
        proof_digest,
        session.session_digest,
    )
    event_kind: SessionEventKind = (
        "budget_grant_applied"
        if reconciliation is None
        else "budget_grant_reconciled"
    )
    return operation_id, effect, proof_digest, event_kind


def _transition_projection(
    session: StageReviewSession,
    application: BudgetGrantResourceApplication,
    decision: BudgetGrantDecisionClaim,
    reconciliation: BudgetGrantResourceReconciliation | None,
    operation_id: str,
    effect: str,
) -> SessionProjectionData:
    grant = application.grant
    reservation = (
        decision.resource_reservation
        if reconciliation is None
        else reconciliation.resource_operation.target_event.reservation
    )
    updates: dict[str, object] = {
        "state": session.budget_resume_state if reconciliation is None else "needs_user",
        "budget_resume_state": (
            None if reconciliation is None else session.budget_resume_state
        ),
        "pending_budget_grant_command_id": "",
        "budget_revision": session.budget_revision + 1,
        "resource_reservation_digest": reservation.reservation_digest,
        "resource_fencing_epoch": reservation.fencing_token,
        "resource_usage": reservation.usage,
        "last_budget_grant_operation_id": operation_id,
        "budget_grant_operation_effect_digest": effect,
        "budget_grant_failure_code": "",
    }
    if reconciliation is None:
        updates["budget_grant_ids"] = tuple(
            sorted((*session.budget_grant_ids, grant.grant_id))
        )
        updates["budget_grant_digests"] = tuple(
            sorted((*session.budget_grant_digests, grant.grant_digest))
        )
    else:
        updates["reconciled_budget_grant_ids"] = tuple(
            sorted((*session.reconciled_budget_grant_ids, grant.grant_id))
        )
        updates["reconciled_budget_grant_digests"] = tuple(
            sorted((*session.reconciled_budget_grant_digests, grant.grant_digest))
        )
    return replace_projection(session.projection, **updates)


def _session_operation(
    session: StageReviewSession,
    command_operation: SessionOperation,
    request_command_id: str,
    application: BudgetGrantResourceApplication,
    decision: BudgetGrantDecisionClaim,
    reconciliation: BudgetGrantResourceReconciliation | None,
    operation_id: str,
    effect: str,
    event: SessionEvent,
) -> SessionBudgetGrantOperation:
    return SessionBudgetGrantOperation(
        operation_id=operation_id,
        operation_kind=(
            "session_applied" if reconciliation is None else "reconciled_released"
        ),
        request_command_id=request_command_id,
        apply_command_id=command_operation.command_id,
        application=application,
        decision=decision,
        reconciliation=reconciliation,
        expected_session_revision=session.revision,
        expected_session_digest=session.session_digest,
        operation_effect_digest=effect,
        target_projection_digest=canonical_digest(
            event.projection_after,
            CanonicalizationPolicy(),
        ),
        target_event_id=event.event_id,
        target_event_digest=event.event_digest,
        target_event=event,
    )
