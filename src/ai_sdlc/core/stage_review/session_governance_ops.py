"""Session 宏观重基线请求与显式 Plan 撤销命令。"""

from __future__ import annotations

from functools import partial

from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.session_artifact_models import (
    ArtifactRef,
    MacroRebaselineRequest,
    ReviewerPlanRevocation,
)
from ai_sdlc.core.stage_review.session_change_authority import (
    require_revocation_target,
    resolve_plan_revocation,
)
from ai_sdlc.core.stage_review.session_contracts import (
    MacroChangeKind,
    MacroRebaselineCommand,
    PlanRevocationCommand,
    SessionCommand,
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


class SessionGovernanceOps:
    def __init__(self, runtime: SessionRuntime) -> None:
        self._runtime = runtime

    def request_macro(
        self,
        command: SessionCommand,
        *,
        change_kind: MacroChangeKind,
        evidence_digest: str,
    ) -> SessionMutationResult:
        completed = self._runtime.store.completed_session(command)
        if completed is not None:
            return SessionMutationResult(
                session=completed,
                macro_rebaseline_request=self._macro_request(command),
                idempotent_replay=True,
            )
        kinds = self._runtime.operation_kinds(command, ("macro_rebaseline_requested",))
        builder = partial(
            self._build_macro,
            command,
            change_kind,
            evidence_digest,
        )
        session, replay = self._runtime.store.transact(
            command,
            kinds,
            clock=self._runtime.clock,
            builder=builder,
        )
        return SessionMutationResult(
            session=session,
            macro_rebaseline_request=session.macro_rebaseline_request,
            idempotent_replay=replay,
        )

    def request_macro_command(
        self,
        command: MacroRebaselineCommand,
    ) -> SessionMutationResult:
        return self.request_macro(
            command,
            change_kind=command.change_kind,
            evidence_digest=command.evidence_digest,
        )

    def revoke_plan(self, command: PlanRevocationCommand) -> SessionMutationResult:
        completed = self._runtime.store.completed_session(command)
        if completed is not None:
            return SessionMutationResult(session=completed, idempotent_replay=True)
        current = self._require_session(command)
        revocation = resolve_plan_revocation(
            self._runtime.resolver,
            command.revocation_digest,
        )
        plan = self._runtime.resolver.resolve_plan(current.active_plan_digest)
        if plan is None:
            raise SessionIntegrityError("active plan authority is missing")
        require_revocation_target(revocation, plan)
        kinds = self._runtime.operation_kinds(command, ("reviewer_plan_revoked",))
        session, replay = self._runtime.store.transact(
            command,
            kinds,
            clock=self._runtime.clock,
            builder=partial(self._build_revocation, command, revocation),
        )
        return SessionMutationResult(session=session, idempotent_replay=replay)

    def _build_macro(
        self,
        command: SessionCommand,
        change_kind: MacroChangeKind,
        evidence_digest: str,
        base: StageReviewSession | None,
        operation: SessionOperation,
    ) -> tuple[SessionEvent, ...]:
        session = self._runtime.require_base(base)
        request = MacroRebaselineRequest(
            request_id=stable_id(
                "macro-rebaseline-request",
                session.scope.session_id,
                command.command_id,
            ),
            scope=session.scope,
            candidate_digest=session.active_candidate_digest,
            change_kind=change_kind,
            evidence_digest=evidence_digest,
            requested_at=operation.prepared_at,
        )
        projection = replace_projection(
            session.projection, macro_rebaseline_request=request
        )
        ref = ArtifactRef(
            artifact_id=request.request_id,
            artifact_digest=request.request_digest,
        )
        return self._runtime.events(
            session,
            operation,
            (("macro_rebaseline_requested", projection, (ref,)),),
        )

    def _build_revocation(
        self,
        command: PlanRevocationCommand,
        revocation: ReviewerPlanRevocation,
        base: StageReviewSession | None,
        operation: SessionOperation,
    ) -> tuple[SessionEvent, ...]:
        session = self._runtime.require_base(base)
        self._runtime.store.mark_operation_effects_started(operation)
        self._runtime.store.persist_revocation(command.scope, revocation)
        projection = replace_projection(
            session.projection,
            state="blocked",
            revoked_plan_digests=tuple(
                sorted({*session.revoked_plan_digests, session.active_plan_digest})
            ),
        )
        ref = ArtifactRef(
            artifact_id=revocation.revocation_id,
            artifact_digest=revocation.revocation_digest,
        )
        return self._runtime.events(
            session,
            operation,
            (("reviewer_plan_revoked", projection, (ref,)),),
        )

    def _require_session(self, command: SessionCommand) -> StageReviewSession:
        session = self._runtime.store.rebuild(command.scope)
        if session is None:
            raise SessionIntegrityError("session does not exist")
        return session

    def _macro_request(self, command: SessionCommand) -> MacroRebaselineRequest:
        request = next(
            (
                item.projection_after.macro_rebaseline_request
                for item in self._runtime.store.load_events(command.scope)
                if item.command_id == command.command_id
                and item.event_kind == "macro_rebaseline_requested"
            ),
            None,
        )
        if request is None:
            raise SessionIntegrityError("macro rebaseline request event is missing")
        return request
