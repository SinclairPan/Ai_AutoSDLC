"""每次 reviewer 调用前的 Host 复探测与派发授权。"""

from __future__ import annotations

from datetime import datetime

from ai_sdlc.core.stage_review.binding_lineage import (
    dispatch_assignment_matches_binding,
)
from ai_sdlc.core.stage_review.binding_models import (
    BindingAttemptOperation,
    HostCapabilitySnapshot,
    IsolationExecutionEvidence,
    ProviderBindingDescriptor,
    ReviewerRuntimeAllocation,
)
from ai_sdlc.core.stage_review.binding_ports import (
    HostCapabilityProbe,
    IsolationEvidenceAdapter,
)
from ai_sdlc.core.stage_review.binding_result_builders import (
    build_dispatch_assignment,
    dispatch_assignment_id,
)
from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBinding,
    ReviewerBindingSet,
    ReviewerDispatchAssignment,
    ReviewerDispatchResult,
)
from ai_sdlc.core.stage_review.binding_store import BindingArtifactStore
from ai_sdlc.core.stage_review.binding_validation import (
    BindingRefusal,
    VisibilityBarrierError,
    validate_dispatch_evidence,
    validate_host_snapshot,
    validate_host_transition,
)


class BindingDispatchAuthorizer:
    def __init__(
        self,
        store: BindingArtifactStore,
        host_probe: HostCapabilityProbe,
        isolation_adapter: IsolationEvidenceAdapter,
    ) -> None:
        self._store = store
        self._host_probe = host_probe
        self._isolation_adapter = isolation_adapter

    def authorize(
        self,
        binding_set: ReviewerBindingSet,
        binding: ReviewerBinding,
        operation: BindingAttemptOperation,
        *,
        cohort_id: str,
        expected_pass_head_digest: str,
        now: datetime,
    ) -> ReviewerDispatchResult:
        host = self._probe(binding_set.host_snapshot_digest, now=now)
        if host.snapshot_digest != binding_set.host_snapshot_digest:
            raise BindingRefusal(
                "provider_policy_blocked", "binding.host-capability-changed"
            )
        allocation, descriptor = self._runtime(binding, operation)
        assignment_id = dispatch_assignment_id(
            binding_set,
            binding,
            host,
            cohort_id=cohort_id,
            expected_pass_head_digest=expected_pass_head_digest,
        )
        with self._store.dispatch_lock(assignment_id):
            existing = self._store.get_dispatch_assignment(assignment_id)
            if existing is not None:
                return self._replay(
                    existing,
                    binding_set,
                    binding,
                    operation,
                    descriptor,
                    host,
                    cohort_id=cohort_id,
                    expected_pass_head_digest=expected_pass_head_digest,
                )
            return self._create(
                assignment_id,
                binding_set,
                binding,
                operation,
                allocation,
                descriptor,
                host,
                cohort_id=cohort_id,
                expected_pass_head_digest=expected_pass_head_digest,
            )

    def _create(
        self,
        assignment_id: str,
        binding_set: ReviewerBindingSet,
        binding: ReviewerBinding,
        operation: BindingAttemptOperation,
        allocation: ReviewerRuntimeAllocation,
        descriptor: ProviderBindingDescriptor,
        host: HostCapabilitySnapshot,
        *,
        cohort_id: str,
        expected_pass_head_digest: str,
    ) -> ReviewerDispatchResult:
        evidence = self._store.get_dispatch_evidence(assignment_id)
        if evidence is None:
            evidence = self._evidence(binding, operation, allocation, host)
            self._store.persist_dispatch_evidence(assignment_id, evidence)
        validate_dispatch_evidence(
            operation=operation,
            host_snapshot=host,
            binding=binding,
            descriptor=descriptor,
            evidence=evidence,
        )
        assignment = build_dispatch_assignment(
            binding_set=binding_set,
            binding=binding,
            host_snapshot=host,
            evidence=evidence,
            cohort_id=cohort_id,
            expected_pass_head_digest=expected_pass_head_digest,
        )
        if not dispatch_assignment_matches_binding(binding_set, binding, assignment):
            raise BindingRefusal(
                "provider_policy_blocked",
                "binding.dispatch-assignment-lineage-invalid",
            )
        self._store.persist_dispatch_assignment(assignment)
        return ReviewerDispatchResult(result_code="bound", assignment=assignment)

    def _replay(
        self,
        assignment: ReviewerDispatchAssignment,
        binding_set: ReviewerBindingSet,
        binding: ReviewerBinding,
        operation: BindingAttemptOperation,
        descriptor: ProviderBindingDescriptor,
        host: HostCapabilitySnapshot,
        *,
        cohort_id: str,
        expected_pass_head_digest: str,
    ) -> ReviewerDispatchResult:
        evidence = self._store.get_dispatch_evidence(assignment.assignment_id)
        if evidence is None:
            raise BindingRefusal(
                "provider_policy_blocked", "binding.dispatch-evidence-missing"
            )
        validate_dispatch_evidence(
            operation=operation,
            host_snapshot=host,
            binding=binding,
            descriptor=descriptor,
            evidence=evidence,
        )
        expected = build_dispatch_assignment(
            binding_set=binding_set,
            binding=binding,
            host_snapshot=host,
            evidence=evidence,
            cohort_id=cohort_id,
            expected_pass_head_digest=expected_pass_head_digest,
        )
        if assignment.assignment_digest != expected.assignment_digest:
            raise BindingRefusal(
                "provider_policy_blocked",
                "binding.dispatch-assignment-lineage-invalid",
            )
        return ReviewerDispatchResult(result_code="bound", assignment=assignment)

    def _probe(
        self,
        previous_snapshot_digest: str,
        *,
        now: datetime,
    ) -> HostCapabilitySnapshot:
        host = HostCapabilitySnapshot.model_validate(
            self._host_probe.probe(previous_snapshot_digest).model_dump(mode="json")
        )
        validate_host_snapshot(
            host,
            now=now,
            previous_snapshot_digest=previous_snapshot_digest,
        )
        previous = self._store.find_host_by_digest(previous_snapshot_digest)
        validate_host_transition(host, previous)
        self._store.persist_host(host)
        return host

    def _runtime(
        self,
        binding: ReviewerBinding,
        operation: BindingAttemptOperation,
    ) -> tuple[ReviewerRuntimeAllocation, ProviderBindingDescriptor]:
        allocation = next(
            (
                item
                for item in self._store.allocations(operation.operation_id)
                if item.allocation_digest == binding.allocation_digest
            ),
            None,
        )
        descriptor = next(
            (
                item
                for item in operation.authority_snapshot.provider_descriptors
                if item.descriptor_digest == binding.provider_descriptor_digest
            ),
            None,
        )
        if allocation is None or descriptor is None:
            raise BindingRefusal(
                "provider_policy_blocked", "binding.dispatch-runtime-missing"
            )
        return allocation, descriptor

    def _evidence(
        self,
        binding: ReviewerBinding,
        operation: BindingAttemptOperation,
        allocation: ReviewerRuntimeAllocation,
        host: HostCapabilitySnapshot,
    ) -> IsolationExecutionEvidence:
        try:
            raw = self._isolation_adapter.prepare(
                operation.operation_id,
                (allocation,),
                host,
                binding.visibility_barrier_id,
            )
        except VisibilityBarrierError as exc:
            raise BindingRefusal(
                "visibility_barrier_failed", "binding.dispatch-barrier-failed"
            ) from exc
        if len(raw) != 1:
            raise BindingRefusal(
                "visibility_barrier_failed", "binding.dispatch-evidence-missing"
            )
        return IsolationExecutionEvidence.model_validate(raw[0].model_dump(mode="json"))
