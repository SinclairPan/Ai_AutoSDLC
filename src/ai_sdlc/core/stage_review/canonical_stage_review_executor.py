"""把 Binding、Journal、Session 与 FindingLedger 组成唯一评审执行链。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ai_sdlc.core.stage_review.binding_builders import build_binding_attempt_request
from ai_sdlc.core.stage_review.binding_invocations import ReviewerInvocationCoordinator
from ai_sdlc.core.stage_review.binding_models import BindingAuthoritySnapshot
from ai_sdlc.core.stage_review.binding_result_models import ReviewerBindingSet
from ai_sdlc.core.stage_review.bindings import ReviewerBindingService
from ai_sdlc.core.stage_review.candidate import candidate_binding_digest
from ai_sdlc.core.stage_review.canonical_stage_review_optimization import (
    build_session_optimization_coordinator,
)
from ai_sdlc.core.stage_review.canonical_stage_review_slots import (
    CanonicalReviewSlotExecutor,
    ReviewDriverFactory,
)
from ai_sdlc.core.stage_review.canonical_stage_review_support import (
    execution_scope,
    needs_user,
)
from ai_sdlc.core.stage_review.execution_finding_trust import (
    CanonicalFindingLedgerWriter,
    ExecutionFindingTrustResolver,
)
from ai_sdlc.core.stage_review.execution_trust_resolver import ExecutionTrustResolver
from ai_sdlc.core.stage_review.optimization.observations import ObservationKind
from ai_sdlc.core.stage_review.provider_journal import ProviderInvocationJournal
from ai_sdlc.core.stage_review.resource_builders import stable_id, utc_iso
from ai_sdlc.core.stage_review.review_completion import (
    build_review_completion,
    persist_review_completion,
    read_review_completion,
)
from ai_sdlc.core.stage_review.review_input_packet import (
    ReviewInputPacket,
    ReviewInputPacketSet,
    build_review_input_packet,
    persist_review_input_packets,
)
from ai_sdlc.core.stage_review.session import (
    SessionIntegrityError,
    SessionStartCommand,
    StageReviewSessionService,
)
from ai_sdlc.core.stage_review.session_contracts import SessionTrustResolver
from ai_sdlc.core.stage_review.session_models import StageReviewSession
from ai_sdlc.core.stage_review.stage_review_execution import (
    StageReviewExecutionOutcome,
    StageReviewExecutionRequest,
)


class CanonicalStageReviewExecutor:
    def __init__(
        self,
        root: Path,
        *,
        bindings: ReviewerBindingService,
        binding_authority: BindingAuthoritySnapshot,
        journal: ProviderInvocationJournal,
        invocations: ReviewerInvocationCoordinator,
        drivers: ReviewDriverFactory,
        clock: Callable[[], datetime],
        on_authorized: Callable[[StageReviewSessionService], None] | None = None,
    ) -> None:
        self._root = root
        self._bindings = bindings
        self._binding_authority = binding_authority
        self._journal = journal
        self._invocations = invocations
        self._drivers = drivers
        self._clock = clock
        self._on_authorized = on_authorized
        self._slots = CanonicalReviewSlotExecutor(
            bindings=bindings,
            journal=journal,
            invocations=invocations,
            drivers=drivers,
            clock=clock,
        )

    def execute(
        self,
        request: StageReviewExecutionRequest,
    ) -> StageReviewExecutionOutcome:
        packets = tuple(
            build_review_input_packet(
                self._root,
                candidate=request.candidate,
                source_snapshot=request.source_snapshot,
                slot=slot,
            )
            for slot in request.plan.proposal.required_slots
        )
        packet_set = persist_review_input_packets(
            self._root,
            candidate=request.candidate,
            packets=packets,
        )
        binding_set = self._bind(request, packet_set.packet_set_digest)
        if binding_set is None:
            return needs_user("review-binding-unavailable")
        service, findings = self._session_service(request, binding_set)
        session = self._start_session(service, request, binding_set)
        return self._execute_started_session(
            request,
            binding_set,
            service,
            findings,
            packet_set,
            packets,
            session,
        )

    def _execute_started_session(
        self,
        request: StageReviewExecutionRequest,
        binding_set: ReviewerBindingSet,
        service: StageReviewSessionService,
        findings: ExecutionFindingTrustResolver,
        packet_set: ReviewInputPacketSet,
        packets: tuple[ReviewInputPacket, ...],
        session: StageReviewSession,
    ) -> StageReviewExecutionOutcome:
        try:
            return self._continue_session(
                request,
                binding_set,
                service,
                findings,
                packet_set,
                packets,
                session,
            )
        except KeyboardInterrupt:
            self._observe_failure(service, session, "abandoned", "review-abandoned")
            raise
        except TimeoutError:
            self._observe_failure(service, session, "timed_out", "review-timed-out")
            raise
        except SessionIntegrityError:
            self._observe_failure(
                service,
                session,
                "integrity_failure",
                "review-session-integrity-failure",
            )
            raise
        except Exception:
            self._observe_failure(service, session, "crashed", "review-crashed")
            raise

    def _continue_session(
        self,
        request: StageReviewExecutionRequest,
        binding_set: ReviewerBindingSet,
        service: StageReviewSessionService,
        findings: ExecutionFindingTrustResolver,
        packet_set: ReviewInputPacketSet,
        packets: tuple[ReviewInputPacket, ...],
        session: StageReviewSession,
    ) -> StageReviewExecutionOutcome:
        if session.state == "authorized":
            return self._complete(service, session)
        if session.state != "collecting_initial_reviews":
            return self._needs_user(service, session, "review-session-cannot-collect")
        by_slot = {item.slot_id: item for item in packets}
        for slot_id in service.active_cohort(execution_scope(request)).required_slot_ids:
            session = service.get(execution_scope(request))
            if any(item.slot_id == slot_id for item in session.pass_refs):
                continue
            reason = self._slots.execute(
                request,
                binding_set,
                service,
                findings,
                by_slot[slot_id],
                packet_set,
            )
            if reason:
                current = service.get(execution_scope(request))
                return self._needs_user(service, current, reason)
        session = service.get(execution_scope(request))
        if session.state != "authorized":
            return self._needs_user(service, session, "review-remediation-required")
        return self._complete(service, session)

    def _complete(
        self,
        service: StageReviewSessionService,
        session: StageReviewSession,
    ) -> StageReviewExecutionOutcome:
        if self._on_authorized is not None:
            self._on_authorized(service)
        path = service.projection_path(session.scope)
        existing = read_review_completion(path)
        if existing is not None:
            expected = build_review_completion(
                session,
                completed_at=existing.completed_at,
            )
            if existing != expected:
                raise ValueError("review completion lineage fork")
            return _completed_outcome(session, existing.completion_digest)
        completion = build_review_completion(
            session,
            completed_at=utc_iso(self._clock()),
        )
        persist_review_completion(path, completion)
        return _completed_outcome(session, completion.completion_digest)

    def _needs_user(
        self,
        service: StageReviewSessionService,
        session: StageReviewSession,
        reason: str,
    ) -> StageReviewExecutionOutcome:
        self._observe_failure(service, session, "needs_user", reason)
        return needs_user(reason)

    @staticmethod
    def _observe_failure(
        service: StageReviewSessionService,
        session: StageReviewSession,
        observation_kind: ObservationKind,
        terminal_reason: str,
    ) -> None:
        try:
            service.observe_optimization_outcome(
                session.scope,
                observation_kind,
                terminal_reason=terminal_reason,
            )
        except Exception:
            return

    def _bind(
        self,
        request: StageReviewExecutionRequest,
        input_packet_digest: str,
    ) -> ReviewerBindingSet | None:
        final = request.governor.get_reservation(request.plan.final_reservation_id)
        attempt = build_binding_attempt_request(
            plan=request.plan,
            final_reservation=final,
            candidate_manifest_digest=candidate_binding_digest(request.candidate),
            input_packet_digest=input_packet_digest,
            visibility_barrier_id=stable_id(
                "visibility-barrier", request.candidate.review_session_id
            ),
            attempt_index=1,
            previous_binding_set_digest="",
            expected_cohort_id="",
            expected_pass_head_digest="",
            rebind_reason="initial_binding",
            availability_attestation=None,
        )
        result = self._bindings.bind(
            request.plan,
            request=attempt,
            budget_policy=request.budget_policy,
            lease_owner=request.lease_owner,
            now=self._clock(),
        )
        binding = result.binding_set
        if binding is None or binding.execution_mode != "enforce_eligible":
            return None
        return binding

    def _session_service(
        self,
        request: StageReviewExecutionRequest,
        binding_set: ReviewerBindingSet,
    ) -> tuple[StageReviewSessionService, ExecutionFindingTrustResolver]:
        resolver: SessionTrustResolver = ExecutionTrustResolver(
            request=request.proposal.request,
            plan=request.plan,
            risk_profile=request.proposal.risk_profile,
            binding_set=binding_set,
            binding_authority=self._binding_authority,
            resources=request.governor,
            bindings=self._bindings,
            journal=self._journal,
        )
        charged = resolver.resolve_reservation(binding_set.charged_reservation_digest)
        if charged is None:
            raise ValueError("binding charged reservation is unavailable")
        finding_trust = ExecutionFindingTrustResolver(
            self._root,
            project_id=request.candidate.project_id,
            plan=request.plan,
            binding_set=binding_set,
            reservation=charged,
            clock=lambda: utc_iso(self._clock()),
        )
        writer = CanonicalFindingLedgerWriter(
            self._root,
            project_id=request.candidate.project_id,
            resolver=finding_trust,
        )
        optimization = build_session_optimization_coordinator(
            self._root,
            candidate=request.candidate,
            resolver=resolver,
        )
        service = StageReviewSessionService(
            self._root,
            project_id=request.candidate.project_id,
            trust_resolver=resolver,
            finding_ledger_writer=writer,
            optimization_coordinator=optimization,
            clock=lambda: utc_iso(self._clock()),
        )
        return service, finding_trust

    def _start_session(
        self,
        service: StageReviewSessionService,
        request: StageReviewExecutionRequest,
        binding_set: ReviewerBindingSet,
    ) -> StageReviewSession:
        scope = execution_scope(request)
        return service.start(
            SessionStartCommand(
                scope=scope,
                command_id=stable_id("review-session-start", scope.session_id),
                idempotency_key=stable_id("review-session-start-key", scope.session_id),
                expected_revision=0,
                candidate_digest=candidate_binding_digest(request.candidate),
                risk_profile_digest=request.proposal.risk_profile.profile_digest,
                risk_profile_lineage_id=stable_id(
                    "risk-lineage", request.proposal.risk_profile.profile_digest
                ),
                policy_digest=request.plan.proposal.selection_policy_digest,
                optimization_snapshot_digest=(
                    request.plan.proposal.optimization_snapshot_digest
                ),
                plan_digest=request.plan.plan_digest,
                binding_set_digest=binding_set.binding_set_digest,
            )
        ).session

def _completed_outcome(
    session: StageReviewSession,
    completion_digest: str,
) -> StageReviewExecutionOutcome:
    return StageReviewExecutionOutcome(
        status="completed",
        review_session_digest=session.session_digest,
        review_completion_digest=completion_digest,
    )


__all__ = ["CanonicalStageReviewExecutor", "ReviewDriverFactory"]
