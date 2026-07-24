"""Reviewer Provider 调用的唯一 prepare/resume 组合入口。"""

from __future__ import annotations

from datetime import datetime

from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerDispatchAssignment,
    ReviewerDispatchResult,
)
from ai_sdlc.core.stage_review.bindings import ReviewerBindingService
from ai_sdlc.core.stage_review.isolation_launcher import (
    IsolationLaunchContext,
    ReviewerIsolationLauncher,
)
from ai_sdlc.core.stage_review.isolation_runtime_layout import AllocationPathResolver
from ai_sdlc.core.stage_review.provider_journal import (
    ProviderInvocationDriver,
    ProviderInvocationJournal,
    build_provider_invocation_request,
)
from ai_sdlc.core.stage_review.provider_journal_driver import ProviderOutputValidator
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocationRequest,
    ProviderJournalResult,
)
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.reviewer_execution_gate import (
    ReviewerExecutionGate,
    TrustedTransportStatus,
)

ReviewerInvocationResult = ProviderJournalResult | ReviewerDispatchResult


class ReviewerInvocationCoordinator:
    """强制每次 reviewer 首调与恢复先经过当前 Host/Binding 授权。"""

    def __init__(
        self,
        bindings: ReviewerBindingService,
        journal: ProviderInvocationJournal,
        isolation_launcher: ReviewerIsolationLauncher | None = None,
        allocation_path_resolver: AllocationPathResolver | None = None,
        trusted_egress_provider_ids: tuple[str, ...] = (),
        trusted_transport: TrustedTransportStatus | None = None,
    ) -> None:
        self._bindings = bindings
        self._journal = journal
        self._isolation_launcher = isolation_launcher
        self._allocation_path_resolver = allocation_path_resolver
        self._execution_gate = ReviewerExecutionGate(
            authorize=self._authorize_resume,
            prepare_isolated_driver=self._prepare_reviewer_driver,
            requires_reviewer_gate=self._requires_reviewer_gate,
            trusted_egress_provider_ids=trusted_egress_provider_ids,
            trusted_transport=trusted_transport,
        )
        self._journal.register_reviewer_driver_preparer(self._execution_gate.prepare)

    def _requires_reviewer_gate(self, request: ProviderInvocationRequest) -> bool:
        return bool(
            self._bindings.get_dispatch_assignment(request.assignment_digest)
            or self._bindings.has_reviewer_provider(request.provider_id)
        )

    def prepare(
        self,
        *,
        binding_set_id: str,
        slot_id: str,
        cohort_id: str,
        candidate_manifest_digest: str,
        expected_pass_head_digest: str,
        owner_scope_id: str,
        request_digest: str,
        expected_reservation_digest: str,
        anticipated_usage: ResourceAmounts,
        command_id: str,
        idempotency_key: str,
        lease_owner: str,
        now: datetime,
    ) -> ReviewerInvocationResult:
        binding_set = self._bindings.get_binding_set(binding_set_id)
        dispatch = self._bindings.authorize_dispatch(
            binding_set_id=binding_set_id,
            slot_id=slot_id,
            cohort_id=cohort_id,
            candidate_manifest_digest=candidate_manifest_digest,
            expected_pass_head_digest=expected_pass_head_digest,
            now=now,
        )
        if dispatch.assignment is None or binding_set is None:
            return dispatch
        assignment = dispatch.assignment
        request = build_provider_invocation_request(
            project_id=binding_set.project_id,
            work_item_id=binding_set.work_item_id,
            stage_review_session_id=binding_set.stage_review_session_id,
            owner_scope_id=owner_scope_id,
            candidate_digest=assignment.candidate_manifest_digest,
            assignment_digest=assignment.assignment_digest,
            epoch_id="",
            provider_id=assignment.provider_id,
            request_digest=request_digest,
            reservation_id=binding_set.final_reservation_id,
            expected_reservation_digest=expected_reservation_digest,
            expected_fencing_token=binding_set.resource_fencing_token,
            anticipated_usage=anticipated_usage,
            capabilities=assignment.recovery_capabilities,
            command_id=command_id,
            idempotency_key=idempotency_key,
            authorization_scope="reviewer_binding",
        )
        return self._journal.prepare(request, lease_owner=lease_owner, now=now)

    def resume(
        self,
        invocation_id: str,
        *,
        driver: ProviderInvocationDriver,
        validator: ProviderOutputValidator,
        lease_owner: str,
        now: datetime,
    ) -> ReviewerInvocationResult:
        return self._journal.resume(
            invocation_id,
            driver=driver,
            validator=validator,
            lease_owner=lease_owner,
            now=now,
        )

    def _prepare_reviewer_driver(
        self,
        request: ProviderInvocationRequest,
        driver: ProviderInvocationDriver,
        now: datetime,
    ) -> ProviderInvocationDriver | None:
        if self._isolation_launcher is None or self._allocation_path_resolver is None:
            return None
        resolved = self._bindings.get_dispatch_isolation_context(
            request.assignment_digest
        )
        if resolved is None:
            return None
        allocation, peers, evidence, host = resolved
        try:
            layout = self._allocation_path_resolver.resolve(
                allocation,
                peer_allocations=peers,
                assignment_digest=request.assignment_digest,
            )
        except (OSError, ValueError):
            return None
        context = IsolationLaunchContext.from_layout(
            layout,
            host_snapshot=host,
            adapter_grade=evidence.isolation_grade,
        )
        return self._isolation_launcher.prepare_driver(
            driver,
            context=context,
            now=now,
        )

    def _authorize_resume(
        self,
        request: ProviderInvocationRequest,
        now: datetime,
    ) -> bool:
        assignment = self._bindings.get_dispatch_assignment(request.assignment_digest)
        if assignment is None or not _invocation_matches_assignment(
            request, assignment
        ):
            return False
        authorized = self._bindings.reauthorize_dispatch(
            assignment.assignment_digest, now=now
        )
        return bool(
            authorized.assignment is not None
            and authorized.assignment.provider_descriptor_digest
            == assignment.provider_descriptor_digest
            and authorized.assignment.recovery_capabilities
            == assignment.recovery_capabilities
        )


def _invocation_matches_assignment(
    request: ProviderInvocationRequest,
    assignment: ReviewerDispatchAssignment,
) -> bool:
    return all(
        (
            request.assignment_digest == assignment.assignment_digest,
            request.candidate_digest == assignment.candidate_manifest_digest,
            request.epoch_id == "",
            request.provider_id == assignment.provider_id,
            request.capabilities == assignment.recovery_capabilities,
            request.authorization_scope == "reviewer_binding",
        )
    )
