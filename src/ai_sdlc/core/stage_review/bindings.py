"""Reviewer Binding 唯一 facade；不拥有 Cohort/Pass 状态。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.binding_availability_builders import (
    build_provider_availability_attestation,
)
from ai_sdlc.core.stage_review.binding_availability_models import (
    ProviderAvailabilityAttestation,
)
from ai_sdlc.core.stage_review.binding_builders import (
    build_binding_attempt_operation,
    build_binding_attempt_request,
    build_binding_authority_snapshot,
    build_host_capability_snapshot,
    build_isolation_execution_evidence,
    build_provider_binding_descriptor,
    build_runtime_allocation,
)
from ai_sdlc.core.stage_review.binding_dispatch import BindingDispatchAuthorizer
from ai_sdlc.core.stage_review.binding_execution import BindingAttemptExecutor
from ai_sdlc.core.stage_review.binding_lineage import _validate_binding_authority
from ai_sdlc.core.stage_review.binding_models import (
    BindingAttemptOperation,
    BindingAttemptRequest,
    BindingAuthoritySnapshot,
    BindingResultCode,
    HostCapabilitySnapshot,
    IsolationExecutionEvidence,
    IsolationGrade,
    ProviderBindingDescriptor,
    ReviewerRuntimeAllocation,
)
from ai_sdlc.core.stage_review.binding_policy import (
    BindingPolicy,
    baseline_binding_policy,
)
from ai_sdlc.core.stage_review.binding_ports import (
    BindingAuthorityResolver,
    HostCapabilityProbe,
    IsolationEvidenceAdapter,
    ProviderAvailabilityResolver,
    ReviewerRuntimeBroker,
)
from ai_sdlc.core.stage_review.binding_result_builders import build_binding_result
from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBindingResult,
    ReviewerBindingSet,
    ReviewerDispatchAssignment,
    ReviewerDispatchResult,
)
from ai_sdlc.core.stage_review.binding_store import BindingArtifactStore
from ai_sdlc.core.stage_review.binding_validation import (
    BindingRefusal,
    BindingRetryableError,
    RuntimeSessionCreationError,
    VisibilityBarrierError,
)
from ai_sdlc.core.stage_review.panel_models import ReviewerBudgetPolicy
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan
from ai_sdlc.core.stage_review.resources import ResourceGovernor


class ReviewerBindingService:
    """冻结 Binding 事实，并为每次 reviewer 调用签发 assignment。"""

    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        resource_governor: ResourceGovernor,
        authority_resolver: BindingAuthorityResolver,
        host_probe: HostCapabilityProbe,
        runtime_broker: ReviewerRuntimeBroker,
        isolation_adapter: IsolationEvidenceAdapter,
        availability_resolver: ProviderAvailabilityResolver | None = None,
        binding_policy: BindingPolicy | None = None,
        lock_timeout_seconds: float = 2,
    ) -> None:
        self._authority_resolver = authority_resolver
        self._availability_resolver = availability_resolver
        self._store = BindingArtifactStore(
            root,
            project_id=project_id,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        self._executor = BindingAttemptExecutor(
            self._store,
            resource_governor,
            host_probe,
            runtime_broker,
            isolation_adapter,
            binding_policy or baseline_binding_policy(),
        )
        self._dispatch = BindingDispatchAuthorizer(
            self._store, host_probe, isolation_adapter
        )

    def bind(
        self,
        plan: ReviewerPanelPlan,
        *,
        request: BindingAttemptRequest,
        budget_policy: ReviewerBudgetPolicy,
        lease_owner: str,
        now: datetime,
    ) -> ReviewerBindingResult:
        trusted_plan = ReviewerPanelPlan.model_validate(plan.model_dump(mode="json"))
        trusted_request = BindingAttemptRequest.model_validate(
            request.model_dump(mode="json")
        )
        trusted_policy = ReviewerBudgetPolicy.model_validate(
            budget_policy.model_dump(mode="json")
        )
        try:
            operation = self._load_or_prepare_operation(trusted_plan, trusted_request)
        except BindingRefusal as refusal:
            return build_binding_result(
                result_code=refusal.result_code,
                operation_id=trusted_request.operation_id,
                reason_ids=(refusal.reason_id,),
            )
        with self._store.attempt_execution_lock(operation):
            existing = self._store.get_attempt_result(operation.operation_id)
            if existing is not None:
                return existing
            committed = self._store.get_binding_set_for_operation(operation)
            if committed is not None:
                return self._executor.persist_bound_result(operation, committed)
            try:
                return self._executor.execute(
                    trusted_plan, operation, trusted_policy, lease_owner, now
                )
            except BindingRefusal as refusal:
                return self._executor.persist_refusal(operation, refusal)

    def authorize_dispatch(
        self,
        *,
        binding_set_id: str,
        slot_id: str,
        cohort_id: str,
        candidate_manifest_digest: str,
        expected_pass_head_digest: str,
        now: datetime,
    ) -> ReviewerDispatchResult:
        binding_set = self._store.get_binding_set(binding_set_id)
        if binding_set is None or (
            binding_set.candidate_manifest_digest != candidate_manifest_digest
        ):
            return _dispatch_refusal(
                "provider_policy_blocked", "binding.dispatch-lineage-invalid"
            )
        binding = next(
            (item for item in binding_set.bindings if item.slot_id == slot_id), None
        )
        operation = self._store.get_operation(binding_set.attempt_operation_id)
        if binding is None or operation is None:
            return _dispatch_refusal(
                "provider_policy_blocked", "binding.dispatch-slot-invalid"
            )
        try:
            return self._dispatch.authorize(
                binding_set,
                binding,
                operation,
                cohort_id=cohort_id,
                expected_pass_head_digest=expected_pass_head_digest,
                now=now,
            )
        except BindingRefusal as refusal:
            return _dispatch_refusal(
                refusal.result_code,
                refusal.reason_id,
                requires_rebind=refusal.reason_id.startswith("binding.host-"),
            )

    def get_binding_set(self, binding_set_id: str) -> ReviewerBindingSet | None:
        return self._store.get_binding_set(binding_set_id)

    def get_attempt_result(self, operation_id: str) -> ReviewerBindingResult | None:
        return self._store.get_attempt_result(operation_id)

    def get_dispatch_assignment(
        self,
        assignment_digest: str,
    ) -> ReviewerDispatchAssignment | None:
        return self._store.find_dispatch_assignment(assignment_digest)

    def has_reviewer_provider(self, provider_id: str) -> bool:
        return self._store.has_reviewer_provider(provider_id)

    def get_dispatch_isolation_context(
        self,
        assignment_digest: str,
    ) -> (
        tuple[
            ReviewerRuntimeAllocation,
            tuple[ReviewerRuntimeAllocation, ...],
            IsolationExecutionEvidence,
            HostCapabilitySnapshot,
        ]
        | None
    ):
        assignment = self._store.find_dispatch_assignment(assignment_digest)
        if assignment is None:
            return None
        evidence = self._store.get_dispatch_evidence(assignment.assignment_id)
        host = self._store.find_host_by_digest(assignment.host_snapshot_digest)
        binding_set = self._store.find_binding_set_by_digest(
            assignment.binding_set_digest
        )
        if evidence is None or host is None or binding_set is None:
            return None
        allocation = next(
            (
                item
                for item in self._store.allocations(binding_set.attempt_operation_id)
                if item.allocation_digest == evidence.allocation_digest
            ),
            None,
        )
        if allocation is None:
            return None
        peers = tuple(
            item
            for item in self._store.allocations(binding_set.attempt_operation_id)
            if item.allocation_digest != allocation.allocation_digest
        )
        return allocation, peers, evidence, host

    def reauthorize_dispatch(
        self,
        assignment_digest: str,
        *,
        now: datetime,
    ) -> ReviewerDispatchResult:
        assignment = self._store.find_dispatch_assignment(assignment_digest)
        if assignment is None:
            return _dispatch_refusal(
                "provider_policy_blocked", "binding.dispatch-assignment-missing"
            )
        binding_set = self._store.find_binding_set_by_digest(
            assignment.binding_set_digest
        )
        if binding_set is None:
            return _dispatch_refusal(
                "provider_policy_blocked", "binding.dispatch-lineage-invalid"
            )
        return self.authorize_dispatch(
            binding_set_id=binding_set.binding_set_id,
            slot_id=assignment.slot_id,
            cohort_id=assignment.cohort_id,
            candidate_manifest_digest=assignment.candidate_manifest_digest,
            expected_pass_head_digest=assignment.expected_pass_head_digest,
            now=now,
        )

    def _load_or_prepare_operation(
        self,
        plan: ReviewerPanelPlan,
        request: BindingAttemptRequest,
    ) -> BindingAttemptOperation:
        existing = self._store.get_operation(request.operation_id)
        if existing is not None:
            if existing.request.request_digest != request.request_digest:
                raise SharedStateIntegrityError("binding attempt lineage fork")
            _validate_binding_authority(plan, existing.authority_snapshot)
            return existing
        authority = BindingAuthoritySnapshot.model_validate(
            self._authority_resolver.resolve(plan).model_dump(mode="json")
        )
        _validate_binding_authority(plan, authority)
        availability: ProviderAvailabilityAttestation | None = None
        if request.previous_binding_set_digest and self._availability_resolver:
            resolved = self._availability_resolver.resolve(
                plan, request.previous_binding_set_digest
            )
            if resolved is not None:
                availability = ProviderAvailabilityAttestation.model_validate(
                    resolved.model_dump(mode="json")
                )
        return self._store.prepare_operation(
            build_binding_attempt_operation(request, authority, availability)
        )


def _dispatch_refusal(
    code: BindingResultCode,
    reason: str,
    *,
    requires_rebind: bool = False,
) -> ReviewerDispatchResult:
    return ReviewerDispatchResult(
        result_code=code,
        reason_ids=(reason,),
        requires_rebind=requires_rebind,
    )


__all__ = [
    "BindingAuthoritySnapshot",
    "HostCapabilitySnapshot",
    "IsolationGrade",
    "ProviderBindingDescriptor",
    "ProviderAvailabilityAttestation",
    "ReviewerBindingService",
    "ReviewerRuntimeAllocation",
    "RuntimeSessionCreationError",
    "BindingRetryableError",
    "VisibilityBarrierError",
    "build_binding_attempt_request",
    "build_binding_authority_snapshot",
    "build_host_capability_snapshot",
    "build_isolation_execution_evidence",
    "build_provider_availability_attestation",
    "build_provider_binding_descriptor",
    "build_runtime_allocation",
]
