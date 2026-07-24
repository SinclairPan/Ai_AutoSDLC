"""Candidate、Provider Rebind 与已覆盖 Risk Enrichment 的 Cohort 替换。"""

from __future__ import annotations

from functools import partial

from ai_sdlc.core.stage_review.session_artifact_models import ReviewCohort
from ai_sdlc.core.stage_review.session_authority import (
    SessionAuthority,
    hard_budget_reached,
    resolve_session_authority,
    validate_resource_advance,
)
from ai_sdlc.core.stage_review.session_change_authority import (
    capability_gap,
    resolve_rebind_directive,
    resolve_risk_profile,
    validate_rebind,
)
from ai_sdlc.core.stage_review.session_contracts import (
    CandidateUpdateCommand,
    ProviderRebindCommand,
    RiskEnrichmentCommand,
    SessionCommand,
    SessionEventKind,
    SessionIntegrityError,
    SessionState,
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
from ai_sdlc.core.stage_review.session_transitions import (
    activate_cohort,
    cohort_ref,
    invalidate_passes,
    stop_for_hard_budget,
    supersede_active,
)


class SessionReplacementOps:
    def __init__(self, runtime: SessionRuntime) -> None:
        self._runtime = runtime

    def update_candidate(
        self,
        command: CandidateUpdateCommand,
    ) -> SessionMutationResult:
        completed = self._runtime.store.completed_session(command)
        if completed is not None:
            return SessionMutationResult(session=completed, idempotent_replay=True)
        current = self._require_session(command)
        if not current.initial_seal_refs:
            raise SessionIntegrityError(
                "candidate update requires the initial review barrier"
            )
        authority = self._authority(
            current,
            candidate_digest=command.candidate_digest,
            plan_digest=current.active_plan_digest,
            binding_set_digest=command.binding_set_digest,
        )
        kinds: tuple[SessionEventKind, ...] = (
            "candidate_updated",
            "cohort_superseded",
            "old_passes_invalidated",
            "reviewer_bindings_validated",
            "new_cohort_activated",
        )
        return self._replace(
            command,
            authority,
            kinds,
            candidate_digest=command.candidate_digest,
            risk_profile_digest=current.active_risk_profile_digest,
            activation_reason="candidate_updated",
            final_state="awaiting_verification",
        )

    def rebind_provider(
        self,
        command: ProviderRebindCommand,
    ) -> SessionMutationResult:
        completed = self._runtime.store.completed_session(command)
        if completed is not None:
            return SessionMutationResult(session=completed, idempotent_replay=True)
        current = self._require_session(command)
        existing = self._runtime.store.get_operation(command.scope, command.command_id)
        _require_rebindable(current, existing)
        authority = self._authority(
            current,
            candidate_digest=current.active_candidate_digest,
            plan_digest=current.active_plan_digest,
            binding_set_digest=command.binding_set_digest,
        )
        if existing is None:
            current_binding = self._runtime.resolver.resolve_binding_set(
                current.active_binding_set_digest
            )
            if current_binding is None:
                raise SessionIntegrityError("active binding authority is missing")
            directive = resolve_rebind_directive(
                self._runtime.resolver,
                command.rebind_directive_digest,
            )
            validate_rebind(
                current,
                current_binding,
                authority.binding_set,
                directive,
                reservation=authority.reservation,
            )
        kinds: tuple[SessionEventKind, ...] = (
            "provider_rebind_required",
            "cohort_superseded",
            "old_passes_invalidated",
            "reviewer_bindings_validated",
            "new_cohort_activated",
        )
        return self._replace(
            command,
            authority,
            kinds,
            candidate_digest=current.active_candidate_digest,
            risk_profile_digest=current.active_risk_profile_digest,
            activation_reason="provider_unavailable",
            final_state=_rebind_state(authority),
        )

    def enrich_covered(
        self,
        command: RiskEnrichmentCommand,
    ) -> SessionMutationResult:
        completed = self._runtime.store.completed_session(command)
        if completed is not None:
            return SessionMutationResult(session=completed, idempotent_replay=True)
        current = self._require_session(command)
        profile = resolve_risk_profile(
            self._runtime.resolver,
            current,
            command.risk_profile_digest,
        )
        missing = capability_gap(
            self._runtime.resolver,
            current,
            tuple(profile.required_capability_ids),
        )
        if missing:
            raise SessionIntegrityError("risk profile capabilities require role gap")
        authority = self._authority(
            current,
            candidate_digest=current.active_candidate_digest,
            plan_digest=current.active_plan_digest,
            binding_set_digest=current.active_binding_set_digest,
            reservation_digest=current.resource_reservation_digest,
        )
        kinds: tuple[SessionEventKind, ...] = (
            "risk_fact_enriched",
            "cohort_superseded",
            "old_passes_invalidated",
            "new_cohort_activated",
        )
        return self._replace(
            command,
            authority,
            kinds,
            candidate_digest=current.active_candidate_digest,
            risk_profile_digest=command.risk_profile_digest,
            activation_reason="risk_fact_enriched_covered",
            final_state="collecting_initial_reviews",
        )

    def _replace(
        self,
        command: SessionCommand,
        authority: SessionAuthority,
        defaults: tuple[SessionEventKind, ...],
        *,
        candidate_digest: str,
        risk_profile_digest: str,
        activation_reason: str,
        final_state: SessionState,
    ) -> SessionMutationResult:
        kinds = self._runtime.operation_kinds(command, defaults)
        builder = partial(
            self._build_replacement,
            authority,
            kinds,
            candidate_digest,
            risk_profile_digest,
            activation_reason,
            final_state,
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
        candidate_digest: str,
        risk_profile_digest: str,
        activation_reason: str,
        final_state: SessionState,
        base: StageReviewSession | None,
        operation: SessionOperation,
    ) -> tuple[SessionEvent, ...]:
        session = self._runtime.require_base(base)
        self._runtime.require_active(session)
        validate_resource_advance(session, authority.reservation)
        cohort = self._runtime.replacement_cohort(
            session,
            authority,
            candidate_digest=candidate_digest,
            risk_profile_digest=risk_profile_digest,
            activation_reason=activation_reason,
            created_at=operation.prepared_at,
        )
        self._runtime.store.mark_operation_effects_started(operation)
        self._persist(session, authority, cohort)
        specs = _replacement_specs(
            session,
            authority,
            cohort,
            kinds,
            candidate_digest=candidate_digest,
            risk_profile_digest=risk_profile_digest,
            final_state=final_state,
        )
        return self._runtime.events(session, operation, specs)

    def _authority(
        self,
        current: StageReviewSession,
        *,
        candidate_digest: str,
        plan_digest: str,
        binding_set_digest: str,
        reservation_digest: str = "",
    ) -> SessionAuthority:
        return resolve_session_authority(
            self._runtime.resolver,
            current.scope,
            candidate_digest=candidate_digest,
            plan_digest=plan_digest,
            binding_set_digest=binding_set_digest,
            reservation_digest=reservation_digest,
        )

    def _persist(
        self,
        session: StageReviewSession,
        authority: SessionAuthority,
        cohort: ReviewCohort,
    ) -> None:
        self._runtime.store.persist_authority(
            session.scope,
            authority.plan,
            authority.binding_set,
            authority.reservation,
        )
        self._runtime.store.persist_cohort(cohort)

    def _require_session(self, command: SessionCommand) -> StageReviewSession:
        session = self._runtime.store.rebuild(command.scope)
        if session is None:
            raise SessionIntegrityError("session does not exist")
        return session


def _replacement_specs(
    session: StageReviewSession,
    authority: SessionAuthority,
    cohort: ReviewCohort,
    kinds: tuple[SessionEventKind, ...],
    *,
    candidate_digest: str,
    risk_profile_digest: str,
    final_state: SessionState,
) -> tuple[EventSpec, ...]:
    projection = session.projection
    values: list[EventSpec] = []
    for kind in kinds:
        projection = _apply_replacement_event(
            projection,
            session,
            authority,
            cohort,
            kind,
            candidate_digest,
            risk_profile_digest,
            final_state,
        )
        values.append((kind, projection, cohort_ref(kind, cohort)))
    return tuple(values)


def _rebind_state(authority: SessionAuthority) -> SessionState:
    return (
        "needs_user"
        if hard_budget_reached(authority.reservation)
        else "collecting_initial_reviews"
    )


def _require_rebindable(
    session: StageReviewSession,
    operation: SessionOperation | None,
) -> None:
    if operation is None and session.pending_role_gap_capability_ids:
        raise SessionIntegrityError("provider rebind cannot resolve an active role gap")


def _apply_replacement_event(
    projection: SessionProjectionData,
    session: StageReviewSession,
    authority: SessionAuthority,
    cohort: ReviewCohort,
    kind: SessionEventKind,
    candidate_digest: str,
    risk_profile_digest: str,
    final_state: SessionState,
) -> SessionProjectionData:
    if kind == "candidate_updated":
        return replace_projection(
            projection,
            state="awaiting_verification",
            active_candidate_digest=candidate_digest,
        )
    if kind == "risk_fact_enriched":
        return replace_projection(
            projection, active_risk_profile_digest=risk_profile_digest
        )
    if kind == "provider_rebind_required":
        return replace_projection(projection, state="binding")
    if kind == "cohort_superseded":
        return supersede_active(projection)
    if kind == "old_passes_invalidated":
        return invalidate_passes(projection, session.active_cohort_id)
    if kind == "reviewer_bindings_validated":
        return replace_projection(
            projection,
            active_binding_set_digest=authority.binding_set.binding_set_digest,
            resource_reservation_digest=authority.reservation.reservation_digest,
            resource_usage=authority.reservation.usage,
        )
    if kind == "new_cohort_activated":
        state = (
            "collecting_initial_reviews"
            if final_state == "needs_user"
            else final_state
        )
        activated = activate_cohort(projection, cohort, state=state)
        if final_state == "needs_user":
            return stop_for_hard_budget(activated, state)
        return activated
    return projection
