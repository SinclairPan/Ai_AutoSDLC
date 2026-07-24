"""Role Gap 唯一自动 Replan 预算与精确事件序列。"""

from __future__ import annotations

from functools import partial

from ai_sdlc.core.stage_review.session_authority import (
    SessionAuthority,
    resolve_session_authority,
    validate_resource_advance,
)
from ai_sdlc.core.stage_review.session_change_authority import require_actual_gap
from ai_sdlc.core.stage_review.session_contracts import (
    RiskEnrichmentCommand,
    RoleGapCommand,
    SessionCommand,
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
from ai_sdlc.core.stage_review.session_transitions import (
    cohort_ref,
    invalidate_passes,
    require_gap_coverage,
    role_gap_projections,
    supersede_active,
)

ROLE_GAP_SEQUENCE: tuple[SessionEventKind, ...] = (
    "role_gap_detected",
    "cohort_superseded",
    "old_passes_invalidated",
    "plan_resolution_requested",
    "panel_plan_frozen",
    "reviewer_bindings_validated",
    "new_cohort_activated",
)


class SessionRoleGapOps:
    def __init__(self, runtime: SessionRuntime) -> None:
        self._runtime = runtime

    def handle(self, command: RoleGapCommand) -> SessionMutationResult:
        completed = self._runtime.store.completed_session(command)
        if completed is not None:
            return SessionMutationResult(session=completed, idempotent_replay=True)
        current = self._require_session(command)
        existing = self._runtime.store.get_operation(command.scope, command.command_id)
        if existing is not None:
            return self._replay_gap(command, current, existing.expected_event_kinds)
        self._runtime.require_active(current)
        require_actual_gap(
            self._runtime.resolver,
            current,
            command.missing_capability_ids,
        )
        if self._already_replanned(current):
            return self._needs_user(command, command.missing_capability_ids)
        authority = self._authority(
            current,
            command.plan_digest,
            command.binding_set_digest,
            command.missing_capability_ids,
        )
        kinds = self._runtime.operation_kinds(command, ROLE_GAP_SEQUENCE)
        return self._replace(
            command,
            authority,
            kinds,
            risk_profile_digest=current.active_risk_profile_digest,
            capabilities=command.missing_capability_ids,
            activation_reason="role_gap",
            prepend_enrichment=False,
        )

    def handle_enrichment(
        self,
        command: RiskEnrichmentCommand,
        capabilities: tuple[str, ...],
    ) -> SessionMutationResult:
        completed = self._runtime.store.completed_session(command)
        if completed is not None:
            return SessionMutationResult(session=completed, idempotent_replay=True)
        current = self._require_session(command)
        existing = self._runtime.store.get_operation(command.scope, command.command_id)
        if existing is not None:
            return self._replay_enrichment(
                command,
                current,
                capabilities,
                existing.expected_event_kinds,
            )
        self._runtime.require_active(current)
        if self._already_replanned(current):
            return self._risk_needs_user(command, capabilities)
        authority = self._authority(
            current,
            command.plan_digest,
            command.binding_set_digest,
            capabilities,
        )
        defaults = ("risk_fact_enriched", *ROLE_GAP_SEQUENCE)
        kinds = self._runtime.operation_kinds(command, defaults)
        return self._replace(
            command,
            authority,
            kinds,
            risk_profile_digest=command.risk_profile_digest,
            capabilities=capabilities,
            activation_reason="risk_fact_enriched_uncovered",
            prepend_enrichment=True,
        )

    def _replace(
        self,
        command: SessionCommand,
        authority: SessionAuthority,
        kinds: tuple[SessionEventKind, ...],
        *,
        risk_profile_digest: str,
        capabilities: tuple[str, ...],
        activation_reason: str,
        prepend_enrichment: bool,
    ) -> SessionMutationResult:
        builder = partial(
            self._build_replacement,
            authority,
            kinds,
            risk_profile_digest,
            capabilities,
            activation_reason,
            prepend_enrichment,
        )
        session, replay = self._runtime.store.transact(
            command,
            kinds,
            clock=self._runtime.clock,
            builder=builder,
        )
        return SessionMutationResult(session=session, idempotent_replay=replay)

    def _build_replacement(
        self,
        authority: SessionAuthority,
        kinds: tuple[SessionEventKind, ...],
        risk_profile_digest: str,
        capabilities: tuple[str, ...],
        activation_reason: str,
        prepend_enrichment: bool,
        base: StageReviewSession | None,
        operation: SessionOperation,
    ) -> tuple[SessionEvent, ...]:
        session = self._runtime.require_base(base)
        self._runtime.require_active(session)
        effective = _enriched_session(session, risk_profile_digest, prepend_enrichment)
        cohort = self._runtime.replacement_cohort(
            effective,
            authority,
            candidate_digest=session.active_candidate_digest,
            risk_profile_digest=risk_profile_digest,
            activation_reason=activation_reason,
            created_at=operation.prepared_at,
        )
        self._runtime.store.mark_operation_effects_started(operation)
        self._runtime.store.persist_authority(
            session.scope,
            authority.plan,
            authority.binding_set,
            authority.reservation,
        )
        self._runtime.store.persist_cohort(cohort)
        projections = role_gap_projections(effective, authority, cohort, capabilities)
        if prepend_enrichment:
            projections = (effective.projection, *projections)
        specs = tuple(
            (kind, projection, cohort_ref(kind, cohort))
            for kind, projection in zip(kinds, projections, strict=True)
        )
        return self._runtime.events(session, operation, specs)

    def _authority(
        self,
        current: StageReviewSession,
        plan_digest: str,
        binding_set_digest: str,
        capabilities: tuple[str, ...],
        *,
        validate_charge: bool = True,
    ) -> SessionAuthority:
        authority = resolve_session_authority(
            self._runtime.resolver,
            current.scope,
            candidate_digest=current.active_candidate_digest,
            plan_digest=plan_digest,
            binding_set_digest=binding_set_digest,
        )
        require_gap_coverage(authority, capabilities)
        if validate_charge:
            validate_resource_advance(
                current,
                authority.reservation,
                required_increment="role_replans",
            )
        return authority

    def _replay_gap(
        self,
        command: RoleGapCommand,
        current: StageReviewSession,
        kinds: tuple[SessionEventKind, ...],
    ) -> SessionMutationResult:
        if kinds == ("user_decision_required",):
            return self._needs_user(command, command.missing_capability_ids)
        authority = self._authority(
            current,
            command.plan_digest,
            command.binding_set_digest,
            command.missing_capability_ids,
            validate_charge=False,
        )
        return self._replace(
            command,
            authority,
            kinds,
            risk_profile_digest=current.active_risk_profile_digest,
            capabilities=command.missing_capability_ids,
            activation_reason="role_gap",
            prepend_enrichment=False,
        )

    def _replay_enrichment(
        self,
        command: RiskEnrichmentCommand,
        current: StageReviewSession,
        capabilities: tuple[str, ...],
        kinds: tuple[SessionEventKind, ...],
    ) -> SessionMutationResult:
        if kinds[-1:] == ("user_decision_required",):
            return self._risk_needs_user(command, capabilities)
        authority = self._authority(
            current,
            command.plan_digest,
            command.binding_set_digest,
            capabilities,
            validate_charge=False,
        )
        return self._replace(
            command,
            authority,
            kinds,
            risk_profile_digest=command.risk_profile_digest,
            capabilities=capabilities,
            activation_reason="risk_fact_enriched_uncovered",
            prepend_enrichment=True,
        )

    def _risk_needs_user(
        self,
        command: RiskEnrichmentCommand,
        capabilities: tuple[str, ...],
    ) -> SessionMutationResult:
        defaults: tuple[SessionEventKind, ...] = (
            "risk_fact_enriched",
            "cohort_superseded",
            "old_passes_invalidated",
            "user_decision_required",
        )
        kinds = self._runtime.operation_kinds(command, defaults)
        session, replay = self._runtime.store.transact(
            command,
            kinds,
            clock=self._runtime.clock,
            builder=partial(
                self._build_risk_needs_user,
                command.risk_profile_digest,
                capabilities,
            ),
        )
        return SessionMutationResult(session=session, idempotent_replay=replay)

    def _build_risk_needs_user(
        self,
        risk_profile_digest: str,
        capabilities: tuple[str, ...],
        base: StageReviewSession | None,
        operation: SessionOperation,
    ) -> tuple[SessionEvent, ...]:
        session = self._runtime.require_base(base)
        self._runtime.require_active(session)
        enriched = _enriched_session(session, risk_profile_digest, True)
        superseded = supersede_active(enriched.projection)
        invalidated = invalidate_passes(superseded, session.active_cohort_id)
        pending = replace_projection(
            invalidated,
            state="needs_user",
            pending_role_gap_capability_ids=capabilities,
        )
        projections = enriched.projection, superseded, invalidated, pending
        specs = tuple(
            (kind, projection, ())
            for kind, projection in zip(
                operation.expected_event_kinds,
                projections,
                strict=True,
            )
        )
        return self._runtime.events(session, operation, specs)

    def _needs_user(
        self,
        command: SessionCommand,
        capabilities: tuple[str, ...],
    ) -> SessionMutationResult:
        kinds = self._runtime.operation_kinds(command, ("user_decision_required",))
        builder = partial(self._build_needs_user, capabilities)
        session, replay = self._runtime.store.transact(
            command,
            kinds,
            clock=self._runtime.clock,
            builder=builder,
        )
        return SessionMutationResult(session=session, idempotent_replay=replay)

    def _build_needs_user(
        self,
        capabilities: tuple[str, ...],
        base: StageReviewSession | None,
        operation: SessionOperation,
    ) -> tuple[SessionEvent, ...]:
        session = self._runtime.require_base(base)
        projection = replace_projection(
            session.projection,
            state="needs_user",
            pending_role_gap_capability_ids=capabilities,
        )
        return self._runtime.events(
            session,
            operation,
            (("user_decision_required", projection, ()),),
        )

    @staticmethod
    def _already_replanned(session: StageReviewSession) -> bool:
        return session.role_replan_count(session.risk_profile_lineage_id) >= 1

    def _require_session(self, command: SessionCommand) -> StageReviewSession:
        session = self._runtime.store.rebuild(command.scope)
        if session is None:
            raise SessionIntegrityError("session does not exist")
        return session


def _enriched_session(
    session: StageReviewSession,
    risk_profile_digest: str,
    enabled: bool,
) -> StageReviewSession:
    if not enabled:
        return session
    projection = replace_projection(
        session.projection,
        active_risk_profile_digest=risk_profile_digest,
    )
    return StageReviewSession(
        revision=session.revision,
        head_event_id=session.head_event_id,
        head_event_digest=session.head_event_digest,
        projection=projection,
    )
