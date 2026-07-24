"""Binding 可信血缘、隔离、独立性和 Rebind 纯校验。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ai_sdlc.core.stage_review.binding_models import (
    BindingAttemptOperation,
    BindingResultCode,
    HostCapabilitySnapshot,
    IsolationExecutionEvidence,
    ProviderBindingDescriptor,
    ReviewerRuntimeAllocation,
)
from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBinding,
    ReviewerBindingSet,
)
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan, ReviewerSlot
from ai_sdlc.core.stage_review.resource_builders import parse_utc


@dataclass(frozen=True, slots=True)
class BindingRefusal(Exception):  # noqa: N818 - 领域拒绝事实，不是系统异常
    result_code: BindingResultCode
    reason_id: str


class RuntimeSessionCreationError(RuntimeError):
    """Provider runtime broker 未能创建或恢复独立会话。"""


class VisibilityBarrierError(RuntimeError):
    """执行封套无法证明同伴不可见或一次性边界。"""


class BindingRetryableError(RuntimeError):
    """瞬时本地竞争尚未形成 Binding 终态，调用方可重放同一 Attempt。"""


def validate_host_snapshot(
    snapshot: HostCapabilitySnapshot,
    *,
    now: datetime,
    previous_snapshot_digest: str = "",
) -> None:
    if parse_utc(snapshot.expires_at) <= now:
        raise BindingRefusal(
            "provider_policy_blocked", "binding.host-capability-expired"
        )
    if snapshot.capability_source == "environment-only":
        raise BindingRefusal(
            "provider_policy_blocked", "binding.host-capability-untrusted"
        )
    if "agent_execution" not in snapshot.capability_ids:
        raise BindingRefusal(
            "actor_unavailable", "binding.host-agent-execution-unavailable"
        )
    if (
        previous_snapshot_digest
        and snapshot.snapshot_digest != previous_snapshot_digest
        and snapshot.previous_snapshot_digest != previous_snapshot_digest
    ):
        raise BindingRefusal(
            "provider_policy_blocked", "binding.host-snapshot-lineage-invalid"
        )


def validate_host_transition(
    current: HostCapabilitySnapshot,
    previous: HostCapabilitySnapshot | None,
) -> None:
    if previous is not None and current.snapshot_digest == previous.snapshot_digest:
        return
    if not current.previous_snapshot_digest:
        return
    if previous is None or (
        current.previous_snapshot_digest != previous.snapshot_digest
        or parse_utc(current.issued_at) < parse_utc(previous.issued_at)
        or current.authorization_transition == "unverified"
    ):
        raise BindingRefusal(
            "provider_policy_blocked", "binding.host-transition-invalid"
        )


def validate_rebind_authority(
    *,
    operation: BindingAttemptOperation,
    previous: ReviewerBindingSet | None,
    now: datetime,
) -> None:
    request = operation.request
    attestation = operation.availability_attestation
    if previous is None:
        if (
            attestation is not None
            or request.provider_availability_attestation_digest
            or request.unavailable_provider_ids
        ):
            raise BindingRefusal(
                "provider_policy_blocked", "binding.unexpected-availability-evidence"
            )
        return
    if attestation is None or (
        attestation.attestation_digest
        != request.provider_availability_attestation_digest
        or attestation.plan_digest != request.plan_digest
        or attestation.previous_binding_set_digest != previous.binding_set_digest
        or attestation.unavailable_provider_ids != request.unavailable_provider_ids
        or parse_utc(attestation.expires_at) <= now
    ):
        raise BindingRefusal(
            "provider_policy_blocked", "binding.provider-availability-untrusted"
        )


def validate_allocations(
    *,
    plan: ReviewerPanelPlan,
    operation: BindingAttemptOperation,
    allocations: tuple[ReviewerRuntimeAllocation, ...],
) -> tuple[
    tuple[ReviewerSlot, ReviewerRuntimeAllocation, ProviderBindingDescriptor], ...
]:
    by_slot = _unique_allocations(allocations)
    descriptors = {
        item.descriptor_digest: item
        for item in operation.authority_snapshot.provider_descriptors
    }
    pairs = []
    missing_required = []
    for slot in _all_slots(plan):
        allocation = by_slot.get(slot.slot_id)
        if allocation is None:
            if slot.slot_kind == "required":
                missing_required.append(slot.slot_id)
            continue
        descriptor = descriptors.get(allocation.provider_descriptor_digest)
        if (
            descriptor is None
            or allocation.candidate_manifest_digest
            != operation.request.candidate_manifest_digest
            or not _descriptor_matches(slot, allocation, descriptor)
        ):
            raise BindingRefusal(
                "provider_policy_blocked", "binding.provider-policy-mismatch"
            )
        pairs.append((slot, allocation, descriptor))
    if missing_required:
        raise BindingRefusal("actor_unavailable", "binding.required-actor-unavailable")
    _validate_runtime_independence(tuple(item[1] for item in pairs))
    return tuple(pairs)


def validate_evidence(
    *,
    operation: BindingAttemptOperation,
    host_snapshot: HostCapabilitySnapshot,
    pairs: tuple[
        tuple[ReviewerSlot, ReviewerRuntimeAllocation, ProviderBindingDescriptor],
        ...,
    ],
    evidence: tuple[IsolationExecutionEvidence, ...],
) -> dict[str, IsolationExecutionEvidence]:
    by_allocation = {item.allocation_digest: item for item in evidence}
    if len(by_allocation) != len(evidence):
        raise BindingRefusal(
            "visibility_barrier_failed", "binding.isolation-evidence-duplicate"
        )
    for slot, allocation, descriptor in pairs:
        item = by_allocation.get(allocation.allocation_digest)
        if item is None:
            raise BindingRefusal(
                "visibility_barrier_failed", "binding.isolation-evidence-missing"
            )
        _validate_one_evidence(
            operation,
            host_snapshot,
            descriptor,
            item,
            required_enforce=(
                operation.authority_snapshot.enforcement_mode == "enforce"
                and slot.slot_kind in {"required", "optional"}
            ),
        )
    return by_allocation


def validate_rebind(
    *,
    operation: BindingAttemptOperation,
    previous: ReviewerBindingSet | None,
    bindings: tuple[ReviewerBinding, ...],
) -> None:
    request = operation.request
    if not request.previous_binding_set_digest:
        if previous is not None:
            raise BindingRefusal(
                "provider_policy_blocked", "binding.unexpected-previous-binding"
            )
        return
    if previous is None or (
        previous.binding_set_digest != request.previous_binding_set_digest
    ):
        raise BindingRefusal(
            "provider_policy_blocked", "binding.previous-binding-unavailable"
        )
    if previous.authority_snapshot_digest != (
        operation.authority_snapshot.snapshot_digest
    ):
        raise BindingRefusal("provider_policy_blocked", "binding.provider-pool-changed")
    old_by_slot = {item.slot_id: item for item in previous.bindings}
    for binding in bindings:
        old = old_by_slot.get(binding.slot_id)
        if old is None:
            continue
        if binding.equivalence_class_id != old.equivalence_class_id:
            raise BindingRefusal(
                "provider_policy_blocked", "binding.provider-not-equivalent"
            )
        if binding.provider_id != old.provider_id and (
            old.provider_id not in request.unavailable_provider_ids
        ):
            raise BindingRefusal(
                "provider_policy_blocked", "binding.provider-not-unavailable"
            )
        if (
            old.provider_id in request.unavailable_provider_ids
            and binding.provider_id == old.provider_id
        ):
            raise BindingRefusal(
                "actor_unavailable", "binding.unavailable-provider-reused"
            )


def enforce_eligible(
    operation: BindingAttemptOperation,
    slot: ReviewerSlot,
    evidence: IsolationExecutionEvidence,
) -> bool:
    return (
        operation.authority_snapshot.enforcement_mode == "enforce"
        and slot.slot_kind == "required"
        and evidence.isolation_grade == "enforced"
    )


def validate_dispatch_evidence(
    *,
    operation: BindingAttemptOperation,
    host_snapshot: HostCapabilitySnapshot,
    binding: ReviewerBinding,
    descriptor: ProviderBindingDescriptor,
    evidence: IsolationExecutionEvidence,
) -> None:
    if evidence.allocation_digest != binding.allocation_digest:
        raise BindingRefusal(
            "visibility_barrier_failed", "binding.dispatch-evidence-mismatch"
        )
    _validate_one_evidence(
        operation,
        host_snapshot,
        descriptor,
        evidence,
        required_enforce=binding.eligible_for_enforce_quorum,
    )


def _unique_allocations(
    allocations: tuple[ReviewerRuntimeAllocation, ...],
) -> dict[str, ReviewerRuntimeAllocation]:
    by_slot = {item.slot_id: item for item in allocations}
    if len(by_slot) != len(allocations):
        raise BindingRefusal(
            "session_creation_failed", "binding.runtime-slot-duplicate"
        )
    return by_slot


def _descriptor_matches(
    slot: ReviewerSlot,
    allocation: ReviewerRuntimeAllocation,
    descriptor: ProviderBindingDescriptor,
) -> bool:
    return all(
        (
            allocation.provider_id == descriptor.provider_id,
            allocation.model_family == descriptor.model_family,
            allocation.equivalence_class_id == descriptor.equivalence_class_id,
            slot.role_contract_digest in descriptor.role_contract_digests,
            set(slot.capability_ids) <= set(descriptor.capability_ids),
            set(slot.provider_constraints) <= set(descriptor.provider_tags),
            tuple(sorted(slot.tool_permission_ids)) == descriptor.tool_allowlist,
        )
    )


def _validate_runtime_independence(
    allocations: tuple[ReviewerRuntimeAllocation, ...],
) -> None:
    dimensions = (
        "actor_id",
        "session_id",
        "candidate_snapshot_id",
        "working_directory_id",
        "disposable_home_id",
        "disposable_config_id",
        "disposable_credential_view_id",
        "output_directory_id",
    )
    if any(
        len({getattr(item, name) for item in allocations}) != len(allocations)
        for name in dimensions
    ):
        raise BindingRefusal(
            "independence_unproven", "binding.runtime-independence-unproven"
        )


def _validate_one_evidence(
    operation: BindingAttemptOperation,
    host: HostCapabilitySnapshot,
    descriptor: ProviderBindingDescriptor,
    evidence: IsolationExecutionEvidence,
    *,
    required_enforce: bool,
) -> None:
    if (
        evidence.operation_id != operation.operation_id
        or evidence.host_snapshot_digest != host.snapshot_digest
        or evidence.visibility_barrier_id != operation.request.visibility_barrier_id
        or evidence.isolation_backend != descriptor.isolation_backend
    ):
        raise BindingRefusal(
            "visibility_barrier_failed", "binding.isolation-evidence-lineage-invalid"
        )
    disposable = all(
        (
            evidence.candidate_snapshot_isolated,
            evidence.peer_outputs_hidden,
            evidence.disposable_home,
            evidence.disposable_config,
            evidence.disposable_credentials,
            evidence.output_isolated,
            evidence.sentinel_environment_disposable,
        )
    )
    if not disposable or evidence.isolation_grade == "unproven":
        raise BindingRefusal(
            "independence_unproven", "binding.runtime-isolation-unproven"
        )
    if evidence.isolation_grade == "enforced":
        _validate_enforced(host, descriptor, evidence)
    elif required_enforce:
        raise BindingRefusal(
            "independence_unproven", "binding.required-isolation-not-enforced"
        )


def _validate_enforced(
    host: HostCapabilitySnapshot,
    descriptor: ProviderBindingDescriptor,
    evidence: IsolationExecutionEvidence,
) -> None:
    enforced = all(
        (
            evidence.candidate_write_enforced,
            evidence.user_home_protected,
            evidence.global_config_protected,
            evidence.network_policy_enforced,
            descriptor.network_enforcement,
            f"isolation.{descriptor.isolation_backend}" in host.capability_ids,
            f"network_enforcement.{descriptor.isolation_backend}"
            in host.capability_ids,
        )
    )
    if not enforced:
        raise BindingRefusal(
            "independence_unproven", "binding.enforced-isolation-unproven"
        )


def _all_slots(plan: ReviewerPanelPlan) -> tuple[ReviewerSlot, ...]:
    return (
        *plan.proposal.required_slots,
        *plan.proposal.optional_slots,
        *plan.proposal.advisory_slots,
        *plan.proposal.shadow_slots,
    )
