"""Binding attempt 的计量、隔离运行时校验与不可变提交。"""

from __future__ import annotations

from datetime import datetime

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.binding_lineage import validate_binding_inputs
from ai_sdlc.core.stage_review.binding_models import (
    BindingAttemptOperation,
    BindingResultCode,
    HostCapabilitySnapshot,
    IsolationExecutionEvidence,
    ReviewerRuntimeAllocation,
)
from ai_sdlc.core.stage_review.binding_policy import BindingPolicy
from ai_sdlc.core.stage_review.binding_policy_validation import (
    validate_binding_independence,
)
from ai_sdlc.core.stage_review.binding_ports import (
    HostCapabilityProbe,
    IsolationEvidenceAdapter,
    ReviewerRuntimeBroker,
)
from ai_sdlc.core.stage_review.binding_result_builders import (
    build_binding_result,
    build_binding_set,
    build_independence_proofs,
    build_rebind_directive,
    build_reviewer_binding,
)
from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBinding,
    ReviewerBindingResult,
    ReviewerBindingSet,
)
from ai_sdlc.core.stage_review.binding_store import BindingArtifactStore
from ai_sdlc.core.stage_review.binding_validation import (
    BindingRefusal,
    BindingRetryableError,
    RuntimeSessionCreationError,
    VisibilityBarrierError,
    enforce_eligible,
    validate_allocations,
    validate_evidence,
    validate_host_snapshot,
    validate_host_transition,
    validate_rebind,
    validate_rebind_authority,
)
from ai_sdlc.core.stage_review.panel_models import ReviewerBudgetPolicy
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceLedgerEvent,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.resources import ResourceGovernor


class BindingAttemptExecutor:
    def __init__(
        self,
        store: BindingArtifactStore,
        governor: ResourceGovernor,
        host_probe: HostCapabilityProbe,
        runtime_broker: ReviewerRuntimeBroker,
        isolation_adapter: IsolationEvidenceAdapter,
        binding_policy: BindingPolicy,
    ) -> None:
        self._store = store
        self._governor = governor
        self._host_probe = host_probe
        self._runtime_broker = runtime_broker
        self._isolation_adapter = isolation_adapter
        self._binding_policy = BindingPolicy.model_validate(
            binding_policy.model_dump(mode="json")
        )

    def execute(
        self,
        plan: ReviewerPanelPlan,
        operation: BindingAttemptOperation,
        budget_policy: ReviewerBudgetPolicy,
        lease_owner: str,
        now: datetime,
    ) -> ReviewerBindingResult:
        request = operation.request
        reservation = self._reservation(request.final_reservation_id)
        frozen = self._governor.get_reservation_ancestor(
            request.final_reservation_id, request.final_reservation_digest
        )
        validate_binding_inputs(
            plan=plan,
            operation=operation,
            budget_policy=budget_policy,
            reservation=reservation,
            frozen_reservation=frozen,
            now=now,
        )
        previous = self._previous_binding(operation)
        _validate_frozen_authority(operation, previous)
        validate_rebind_authority(operation=operation, previous=previous, now=now)
        self._store.persist_authority(operation.authority_snapshot)
        if operation.availability_attestation is not None:
            self._store.persist_availability(operation.availability_attestation)
        charged, event = self._charge(operation, lease_owner=lease_owner, now=now)
        host, bindings = self._runtime_bindings(
            plan,
            operation,
            previous_host_digest=(previous.host_snapshot_digest if previous else ""),
            now=now,
        )
        validate_binding_independence(
            required_slot_ids=tuple(
                item.slot_id for item in plan.proposal.required_slots
            ),
            bindings=bindings,
            proofs=build_independence_proofs(bindings),
            policy=self._binding_policy,
        )
        validate_rebind(operation=operation, previous=previous, bindings=bindings)
        return self._commit(plan, operation, host, charged, event, bindings)

    def persist_refusal(
        self,
        operation: BindingAttemptOperation,
        refusal: BindingRefusal,
    ) -> ReviewerBindingResult:
        evidence = self._store.evidence(operation.operation_id)
        result = build_binding_result(
            result_code=refusal.result_code,
            operation_id=operation.operation_id,
            reason_ids=(refusal.reason_id,),
            diagnostic_evidence_digests=tuple(
                item.isolation_evidence_digest for item in evidence
            ),
        )
        self._store.persist_result(result)
        return result

    def persist_bound_result(
        self,
        operation: BindingAttemptOperation,
        binding_set: ReviewerBindingSet,
    ) -> ReviewerBindingResult:
        result = build_binding_result(
            result_code="bound",
            operation_id=operation.operation_id,
            binding_set=binding_set,
            rebind_directive=build_rebind_directive(operation, binding_set),
        )
        self._store.persist_result(result)
        return result

    def _runtime_bindings(
        self,
        plan: ReviewerPanelPlan,
        operation: BindingAttemptOperation,
        *,
        previous_host_digest: str,
        now: datetime,
    ) -> tuple[HostCapabilitySnapshot, tuple[ReviewerBinding, ...]]:
        persisted = self._store.persisted_evidence(operation.operation_id)
        if persisted is None:
            host = self._probe_host(previous_host_digest, now=now)
            allocations = self._allocate(operation, plan)
            evidence = self._prepare_evidence(operation, allocations, host)
        else:
            host = self._recover_evidence_host(
                persisted, previous_host_digest=previous_host_digest, now=now
            )
            allocations = self._store.allocations(operation.operation_id)
            evidence = persisted
        pairs = validate_allocations(
            plan=plan, operation=operation, allocations=allocations
        )
        by_allocation = validate_evidence(
            operation=operation,
            host_snapshot=host,
            pairs=pairs,
            evidence=evidence,
        )
        bindings = tuple(
            build_reviewer_binding(
                slot=slot,
                allocation=allocation,
                descriptor=descriptor,
                evidence=by_allocation[allocation.allocation_digest],
                input_packet_digest=operation.request.input_packet_digest,
                enforce_eligible=enforce_eligible(
                    operation, slot, by_allocation[allocation.allocation_digest]
                ),
            )
            for slot, allocation, descriptor in pairs
        )
        return host, bindings

    def _recover_evidence_host(
        self,
        evidence: tuple[IsolationExecutionEvidence, ...],
        *,
        previous_host_digest: str,
        now: datetime,
    ) -> HostCapabilitySnapshot:
        digests = {item.host_snapshot_digest for item in evidence}
        if len(digests) != 1:
            raise BindingRefusal(
                "visibility_barrier_failed", "binding.evidence-host-lineage-invalid"
            )
        host = self._store.find_host_by_digest(digests.pop())
        if host is None:
            raise BindingRefusal(
                "provider_policy_blocked", "binding.evidence-host-missing"
            )
        validate_host_snapshot(
            host,
            now=now,
            previous_snapshot_digest=previous_host_digest,
        )
        previous = (
            self._store.find_host_by_digest(previous_host_digest)
            if previous_host_digest
            else None
        )
        validate_host_transition(host, previous)
        current = self._probe_host(host.snapshot_digest, now=now)
        if current.snapshot_digest != host.snapshot_digest:
            raise BindingRefusal(
                "provider_policy_blocked", "binding.host-capability-changed"
            )
        return host

    def _commit(
        self,
        plan: ReviewerPanelPlan,
        operation: BindingAttemptOperation,
        host: HostCapabilitySnapshot,
        reservation: ResourceReservation,
        event: ResourceLedgerEvent,
        bindings: tuple[ReviewerBinding, ...],
    ) -> ReviewerBindingResult:
        binding_set = build_binding_set(
            operation=operation,
            host_snapshot=host,
            reservation=reservation,
            resource_event=event,
            bindings=bindings,
            unbound_slot_ids=_unbound_slots(plan, bindings),
        )
        self._store.persist_binding_set(binding_set)
        return self.persist_bound_result(operation, binding_set)

    def _charge(
        self,
        operation: BindingAttemptOperation,
        *,
        lease_owner: str,
        now: datetime,
    ) -> tuple[ResourceReservation, ResourceLedgerEvent]:
        result = self._governor.record_usage(
            operation.request.final_reservation_id,
            delta=operation.resource_delta,
            lease_owner=lease_owner,
            expected_fencing_token=operation.request.resource_fencing_token,
            operation_id=operation.resource_operation_id,
            now=now,
        )
        if result.result_code != "recorded" or result.operation_reservation is None:
            if result.result_code == "lock_unavailable":
                raise BindingRetryableError("binding resource lock is unavailable")
            reason = (
                "binding.budget-exhausted"
                if result.result_code == "hard_limit_exceeded"
                else "binding.resource-charge-rejected"
            )
            code: BindingResultCode = "provider_policy_blocked"
            raise BindingRefusal(code, reason)
        event = self._governor.get_operation_event(operation.resource_operation_id)
        if event is None or event.actual_usage != operation.resource_delta:
            raise SharedStateIntegrityError("binding resource event is inconsistent")
        return result.operation_reservation, event

    def _allocate(
        self,
        operation: BindingAttemptOperation,
        plan: ReviewerPanelPlan,
    ) -> tuple[ReviewerRuntimeAllocation, ...]:
        existing = self._store.allocations(operation.operation_id)
        if existing:
            return existing
        try:
            raw = self._runtime_broker.allocate(
                operation.operation_id, plan, operation.authority_snapshot
            )
        except RuntimeSessionCreationError as exc:
            raise BindingRefusal(
                "session_creation_failed", "binding.session-creation-failed"
            ) from exc
        allocations = tuple(
            ReviewerRuntimeAllocation.model_validate(item.model_dump(mode="json"))
            for item in raw
        )
        return self._store.persist_allocations(operation.operation_id, allocations)

    def _prepare_evidence(
        self,
        operation: BindingAttemptOperation,
        allocations: tuple[ReviewerRuntimeAllocation, ...],
        host: HostCapabilitySnapshot,
    ) -> tuple[IsolationExecutionEvidence, ...]:
        try:
            raw = self._isolation_adapter.prepare(
                operation.operation_id,
                allocations,
                host,
                operation.request.visibility_barrier_id,
            )
        except VisibilityBarrierError as exc:
            raise BindingRefusal(
                "visibility_barrier_failed", "binding.visibility-barrier-failed"
            ) from exc
        evidence = tuple(
            IsolationExecutionEvidence.model_validate(item.model_dump(mode="json"))
            for item in raw
        )
        return self._store.persist_evidence(operation.operation_id, evidence)

    def _probe_host(
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
        previous = (
            self._store.find_host_by_digest(previous_snapshot_digest)
            if previous_snapshot_digest
            else None
        )
        validate_host_transition(host, previous)
        self._store.persist_host(host)
        return host

    def _reservation(self, reservation_id: str) -> ResourceReservation | None:
        try:
            return self._governor.get_reservation(reservation_id)
        except KeyError:
            return None

    def _previous_binding(
        self,
        operation: BindingAttemptOperation,
    ) -> ReviewerBindingSet | None:
        digest = operation.request.previous_binding_set_digest
        return self._store.find_binding_set_by_digest(digest) if digest else None


def _validate_frozen_authority(
    operation: BindingAttemptOperation,
    previous: ReviewerBindingSet | None,
) -> None:
    if previous is not None and previous.authority_snapshot_digest != (
        operation.authority_snapshot.snapshot_digest
    ):
        raise BindingRefusal("provider_policy_blocked", "binding.provider-pool-changed")


def _unbound_slots(
    plan: ReviewerPanelPlan,
    bindings: tuple[ReviewerBinding, ...],
) -> tuple[str, ...]:
    bound = {item.slot_id for item in bindings}
    all_ids = {
        item.slot_id
        for values in (
            plan.proposal.required_slots,
            plan.proposal.optional_slots,
            plan.proposal.advisory_slots,
            plan.proposal.shadow_slots,
        )
        for item in values
    }
    return tuple(sorted(all_ids - bound))
