"""Session 启动、ReviewPass 屏障与收敛记录命令。"""

from __future__ import annotations

from functools import partial

from ai_sdlc.core.stage_review.finding_reducer import compare_progress
from ai_sdlc.core.stage_review.finding_support_models import ProgressComparison
from ai_sdlc.core.stage_review.finding_trust_models import InitialReviewSeal
from ai_sdlc.core.stage_review.session_artifact_models import (
    ArtifactRef,
    ProgressRecord,
    ReviewCohort,
    ReviewPass,
)
from ai_sdlc.core.stage_review.session_authority import (
    SessionAuthority,
    resolve_session_authority,
)
from ai_sdlc.core.stage_review.session_builders import (
    build_cohort,
    build_review_pass,
)
from ai_sdlc.core.stage_review.session_change_authority import (
    authority_capability_gap,
    resolve_initial_risk_profile,
)
from ai_sdlc.core.stage_review.session_contracts import (
    ProgressCommand,
    SessionCommand,
    SessionEventKind,
    SessionIntegrityError,
    SessionStartCommand,
    SubmitReviewPassCommand,
)
from ai_sdlc.core.stage_review.session_finding_ops import build_seal_projection
from ai_sdlc.core.stage_review.session_models import (
    SessionEvent,
    SessionMutationResult,
    SessionOperation,
    SessionProjectionData,
    StageReviewSession,
    replace_projection,
)
from ai_sdlc.core.stage_review.session_review_transitions import (
    initial_projection,
    pass_projection,
    pass_spec,
)
from ai_sdlc.core.stage_review.session_runtime import EventSpec, SessionRuntime
from ai_sdlc.core.stage_review.session_transitions import next_no_progress


class SessionReviewOps:
    def __init__(self, runtime: SessionRuntime) -> None:
        self._runtime = runtime

    def start(self, command: SessionStartCommand) -> SessionMutationResult:
        profile = resolve_initial_risk_profile(
            self._runtime.resolver,
            command.scope,
            command.risk_profile_digest,
            command.plan_digest,
        )
        authority = resolve_session_authority(
            self._runtime.resolver,
            command.scope,
            candidate_digest=command.candidate_digest,
            plan_digest=command.plan_digest,
            binding_set_digest=command.binding_set_digest,
        )
        missing_capabilities = authority_capability_gap(
            authority.plan,
            authority.binding_set,
            tuple(profile.required_capability_ids),
        )
        kinds = self._runtime.operation_kinds(command, ("session_started",))
        builder = partial(
            self._build_start,
            command,
            authority,
            missing_capabilities,
        )
        session, replay = self._runtime.store.transact(
            command,
            kinds,
            clock=self._runtime.clock,
            builder=builder,
        )
        return SessionMutationResult(session=session, idempotent_replay=replay)

    def submit_pass(
        self,
        command: SubmitReviewPassCommand,
    ) -> SessionMutationResult:
        current = self._require_session(command)
        existing = self._runtime.store.get_operation(command.scope, command.command_id)
        if existing is None:
            self._require_new_pass_target(current, command)
            defaults = self._pass_event_kinds(current, command)
        else:
            defaults = existing.expected_event_kinds
        kinds = self._runtime.operation_kinds(
            command,
            defaults,
        )
        builder = partial(self._build_pass, command, kinds)
        session, replay = self._runtime.store.transact(
            command,
            kinds,
            clock=self._runtime.clock,
            builder=builder,
        )
        review_pass = self._pass_for_command(command)
        return SessionMutationResult(
            session=session,
            review_pass=review_pass,
            initial_review_seal=self._seal_for_command(command),
            idempotent_replay=replay,
        )

    def record_progress(self, command: ProgressCommand) -> SessionMutationResult:
        kinds = self._runtime.operation_kinds(command, ("progress_recorded",))
        builder = partial(self._build_progress, command)
        session, replay = self._runtime.store.transact(
            command,
            kinds,
            clock=self._runtime.clock,
            builder=builder,
        )
        return SessionMutationResult(session=session, idempotent_replay=replay)

    def _build_start(
        self,
        command: SessionStartCommand,
        authority: SessionAuthority,
        missing_capabilities: tuple[str, ...],
        base: StageReviewSession | None,
        operation: SessionOperation,
    ) -> tuple[SessionEvent, ...]:
        if base is not None:
            raise SessionIntegrityError("session already exists")
        cohort = self._initial_cohort(command, authority, operation.prepared_at)
        self._runtime.store.mark_operation_effects_started(operation)
        self._persist_authority(command, authority, cohort)
        projection = initial_projection(
            command,
            authority,
            cohort,
            missing_capabilities=missing_capabilities,
        )
        ref = ArtifactRef(
            artifact_id=cohort.cohort_id,
            artifact_digest=cohort.cohort_digest,
        )
        return self._runtime.events(
            None,
            operation,
            (("session_started", projection, (ref,)),),
        )

    def _build_pass(
        self,
        command: SubmitReviewPassCommand,
        kinds: tuple[SessionEventKind, ...],
        base: StageReviewSession | None,
        operation: SessionOperation,
    ) -> tuple[SessionEvent, ...]:
        session = self._runtime.require_base(base)
        cohort = self._runtime.store.get_cohort(command.scope, session.active_cohort_id)
        review_pass, reservation = build_review_pass(
            self._runtime.resolver,
            session,
            cohort,
            command,
            submitted_at=operation.prepared_at,
        )
        self._reject_peer_visibility(session, cohort, review_pass)
        self._runtime.store.mark_operation_effects_started(operation)
        self._runtime.store.persist_pass(review_pass)
        projection = pass_projection(session, review_pass, reservation)
        specs: list[EventSpec] = [pass_spec(review_pass, projection)]
        if len(kinds) == 2:
            specs.append(
                self._seal_spec(
                    session,
                    cohort,
                    review_pass,
                    projection,
                    operation,
                    kinds[1],
                )
            )
        return self._runtime.events(session, operation, tuple(specs))

    def _build_progress(
        self,
        command: ProgressCommand,
        base: StageReviewSession | None,
        operation: SessionOperation,
    ) -> tuple[SessionEvent, ...]:
        session = self._runtime.require_base(base)
        self._runtime.require_active(session)
        comparison = self._compare_progress(session, command)
        outcome = comparison.outcome if comparison is not None else None
        streak = next_no_progress(session.no_progress_streak, outcome)
        state = (
            "needs_user" if outcome == "uncomparable" or streak >= 2 else session.state
        )
        record = ProgressRecord(
            snapshot_digest=command.snapshot.snapshot_digest,
            outcome=outcome,
            decisive_dimension=comparison.decisive_dimension or ""
            if comparison is not None
            else "",
        )
        self._runtime.store.mark_operation_effects_started(operation)
        artifact_id = self._runtime.store.persist_progress(
            command.scope, command.snapshot
        )
        projection = replace_projection(
            session.projection,
            state=state,
            progress_records=(*session.progress_records, record),
            no_progress_streak=streak,
        )
        ref = ArtifactRef(
            artifact_id=artifact_id, artifact_digest=record.snapshot_digest
        )
        return self._runtime.events(
            session,
            operation,
            (("progress_recorded", projection, (ref,)),),
        )

    def _initial_cohort(
        self,
        command: SessionStartCommand,
        authority: SessionAuthority,
        created_at: str,
    ) -> ReviewCohort:
        return build_cohort(
            command.scope,
            authority,
            candidate_digest=command.candidate_digest,
            risk_profile_digest=command.risk_profile_digest,
            risk_profile_lineage_id=command.risk_profile_lineage_id,
            policy_digest=command.policy_digest,
            optimization_snapshot_digest=command.optimization_snapshot_digest,
            ordinal=1,
            initial_pass_head_digest="",
            predecessor_cohort_id="",
            activation_reason="session_started",
            created_at=created_at,
        )

    def _persist_authority(
        self,
        command: SessionStartCommand,
        authority: SessionAuthority,
        cohort: ReviewCohort,
    ) -> None:
        self._runtime.store.persist_authority(
            command.scope,
            authority.plan,
            authority.binding_set,
            authority.reservation,
        )
        self._runtime.store.persist_cohort(cohort)

    def _require_new_pass_target(
        self,
        current: StageReviewSession,
        command: SubmitReviewPassCommand,
    ) -> None:
        self._runtime.require_active(current)
        cohort = self._runtime.store.get_cohort(command.scope, current.active_cohort_id)
        if command.cohort_id != cohort.cohort_id:
            raise SessionIntegrityError("review pass cohort is not active")
        if cohort.cohort_id in current.sealed_cohort_ids:
            raise SessionIntegrityError("review cohort is already sealed")
        if current.state == "replanning" or current.pending_role_gap_capability_ids:
            raise SessionIntegrityError("review cohort has an unresolved role gap")
        if cohort.cohort_id not in current.sealed_cohort_ids and (
            command.observed_peer_pass_ids
        ):
            raise SessionIntegrityError("initial review pass observed peer output")

    def _pass_event_kinds(
        self,
        session: StageReviewSession,
        command: SubmitReviewPassCommand,
    ) -> tuple[SessionEventKind, ...]:
        cohort = self._runtime.store.get_cohort(command.scope, session.active_cohort_id)
        slots = {
            item.slot_id
            for item in session.pass_refs
            if item.cohort_id == cohort.cohort_id and item.is_first_cohort_pass
        }
        if command.slot_id in slots or slots | {command.slot_id} != set(
            cohort.required_slot_ids
        ):
            return ("review_pass_committed",)
        seal_kind: SessionEventKind = (
            "initial_reviews_sealed"
            if not session.initial_seal_refs
            else "cohort_reviews_sealed"
        )
        return "review_pass_committed", seal_kind

    def _seal_spec(
        self,
        session: StageReviewSession,
        cohort: ReviewCohort,
        review_pass: ReviewPass,
        projection: SessionProjectionData,
        operation: SessionOperation,
        kind: SessionEventKind,
    ) -> EventSpec:
        projection, refs = build_seal_projection(
            self._runtime,
            session,
            cohort,
            review_pass,
            projection,
            operation,
            initial=kind == "initial_reviews_sealed",
        )
        return kind, projection, refs

    def _compare_progress(
        self,
        session: StageReviewSession,
        command: ProgressCommand,
    ) -> ProgressComparison | None:
        if not session.progress_records:
            return None
        previous = self._runtime.store.get_progress(
            command.scope, session.progress_records[-1].snapshot_digest
        )
        return compare_progress(previous, command.snapshot)

    def _pass_for_command(self, command: SubmitReviewPassCommand) -> ReviewPass:
        event = next(
            item
            for item in self._runtime.store.load_events(command.scope)
            if item.command_id == command.command_id
            and item.event_kind == "review_pass_committed"
        )
        return self._runtime.store.get_pass(
            command.scope, event.artifact_refs[0].artifact_id
        )

    def _seal_for_command(
        self,
        command: SubmitReviewPassCommand,
    ) -> InitialReviewSeal | None:
        event = next(
            (
                item
                for item in self._runtime.store.load_events(command.scope)
                if item.command_id == command.command_id
                and item.event_kind == "initial_reviews_sealed"
            ),
            None,
        )
        if event is None:
            return None
        return self._runtime.store.get_initial_seal(
            command.scope, event.artifact_refs[0].artifact_id
        )

    def _require_session(self, command: SessionCommand) -> StageReviewSession:
        session = self._runtime.store.rebuild(command.scope)
        if session is None:
            raise SessionIntegrityError("session does not exist")
        return session

    @staticmethod
    def _reject_peer_visibility(
        session: StageReviewSession,
        cohort: ReviewCohort,
        review_pass: ReviewPass,
    ) -> None:
        if cohort.cohort_id not in session.sealed_cohort_ids and (
            review_pass.observed_peer_pass_ids
        ):
            raise SessionIntegrityError("initial review pass observed peer output")
