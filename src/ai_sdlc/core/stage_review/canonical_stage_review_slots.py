"""Canonical Stage Review 的单 Slot 调用与 Pass 提交职责。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from ai_sdlc.core.stage_review.binding_invocations import ReviewerInvocationCoordinator
from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBinding,
    ReviewerBindingSet,
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.bindings import ReviewerBindingService
from ai_sdlc.core.stage_review.canonical_stage_review_support import (
    anticipated_usage,
    build_pass_command,
    execution_scope,
    owner_scope,
    required_binding,
    validated_output_digest,
)
from ai_sdlc.core.stage_review.execution_finding_trust import (
    ExecutionFindingTrustResolver,
)
from ai_sdlc.core.stage_review.provider_journal import ProviderInvocationJournal
from ai_sdlc.core.stage_review.provider_journal_driver import ProviderInvocationDriver
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocationRequest,
    ProviderJournalResult,
)
from ai_sdlc.core.stage_review.provider_transport_models import provider_payload_digest
from ai_sdlc.core.stage_review.remote_review_driver_factory import (
    RemoteReviewDriverUnavailableError,
)
from ai_sdlc.core.stage_review.remote_review_models import RemoteReviewOutput
from ai_sdlc.core.stage_review.resource_builders import stable_id, utc_iso
from ai_sdlc.core.stage_review.review_input_packet import (
    ReviewInputPacket,
    ReviewInputPacketSet,
    review_provider_payload,
)
from ai_sdlc.core.stage_review.session import StageReviewSessionService
from ai_sdlc.core.stage_review.stage_review_execution import StageReviewExecutionRequest


class ReviewDriverFactory(Protocol):
    def build(
        self,
        request: ProviderInvocationRequest,
        *,
        payload: dict[str, object],
        assignment: ReviewerDispatchAssignment,
    ) -> ProviderInvocationDriver: ...


class CanonicalReviewSlotExecutor:
    def __init__(
        self,
        *,
        bindings: ReviewerBindingService,
        journal: ProviderInvocationJournal,
        invocations: ReviewerInvocationCoordinator,
        drivers: ReviewDriverFactory,
        clock: Callable[[], datetime],
    ) -> None:
        self._bindings = bindings
        self._journal = journal
        self._invocations = invocations
        self._drivers = drivers
        self._clock = clock

    def execute(
        self,
        request: StageReviewExecutionRequest,
        binding_set: ReviewerBindingSet,
        service: StageReviewSessionService,
        findings: ExecutionFindingTrustResolver,
        packet: ReviewInputPacket,
        packet_set: ReviewInputPacketSet,
    ) -> str:
        session = service.get(execution_scope(request))
        binding = required_binding(binding_set, packet.slot_id)
        payload = review_provider_payload(packet, packet_set)
        prepared = self._prepare(request, binding_set, service, binding, payload)
        if prepared.invocation is None:
            return f"review-invocation-{prepared.result_code}"
        try:
            result = self._complete(request, prepared, binding, payload)
        except RemoteReviewDriverUnavailableError:
            return "review-provider-unavailable"
        if result is None or result.submission is None:
            suffix = "unknown" if result is None else result.result_code.replace("_", "-")
            return f"review-provider-incomplete-{suffix}"
        submission = result.submission
        output = RemoteReviewOutput.model_validate(submission.output_payload)
        findings.persist_review_evidence(
            session.scope,
            output,
            binding,
            produced_at=utc_iso(self._clock()),
        )
        invocation = self._journal.get(prepared.invocation.invocation_id)
        assignment = self._bindings.get_dispatch_assignment(
            prepared.invocation.request.assignment_digest
        )
        if invocation is None or assignment is None:
            return "review-provider-authority-missing"
        current = service.get(session.scope)
        service.submit_pass(
            build_pass_command(current, binding, assignment, invocation, output)
        )
        return ""

    def _prepare(
        self,
        request: StageReviewExecutionRequest,
        binding_set: ReviewerBindingSet,
        service: StageReviewSessionService,
        binding: ReviewerBinding,
        payload: dict[str, object],
    ) -> ProviderJournalResult:
        session = service.get(execution_scope(request))
        current = request.governor.get_reservation(binding_set.final_reservation_id)
        result = self._invocations.prepare(
            binding_set_id=binding_set.binding_set_id,
            slot_id=binding.slot_id,
            cohort_id=session.active_cohort_id,
            candidate_manifest_digest=session.active_candidate_digest,
            expected_pass_head_digest=session.active_cohort_initial_head_digest,
            owner_scope_id=owner_scope(request, binding.slot_id),
            request_digest=provider_payload_digest(payload),
            expected_reservation_digest=current.reservation_digest,
            anticipated_usage=anticipated_usage(request, binding.slot_id),
            command_id=stable_id(
                "review-provider", session.scope.session_id, binding.slot_id
            ),
            idempotency_key=stable_id(
                "review-provider-key", session.scope.session_id, binding.slot_id
            ),
            lease_owner=request.lease_owner,
            now=self._clock(),
        )
        if not isinstance(result, ProviderJournalResult):
            return ProviderJournalResult(result_code="dispatch_unauthorized")
        return result

    def _complete(
        self,
        request: StageReviewExecutionRequest,
        prepared: ProviderJournalResult,
        binding: ReviewerBinding,
        payload: dict[str, object],
    ) -> ProviderJournalResult | None:
        invocation = prepared.invocation
        if invocation is None:
            return None
        if invocation.state == "committed":
            submission = self._journal.get_submission(invocation.invocation_id)
            return ProviderJournalResult(
                result_code="committed",
                invocation=invocation,
                submission=submission,
            )
        assignment = self._bindings.get_dispatch_assignment(
            invocation.request.assignment_digest
        )
        if assignment is None:
            return None
        driver = self._drivers.build(
            invocation.request,
            payload=payload,
            assignment=assignment,
        )
        result = self._invocations.resume(
            invocation.invocation_id,
            driver=driver,
            validator=lambda item: validated_output_digest(item, binding),
            lease_owner=request.lease_owner,
            now=self._clock(),
        )
        return result if isinstance(result, ProviderJournalResult) else None


__all__ = ["CanonicalReviewSlotExecutor", "ReviewDriverFactory"]
