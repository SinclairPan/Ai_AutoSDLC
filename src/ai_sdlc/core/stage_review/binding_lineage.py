"""Binding 输入与 DispatchAssignment 的单一完整血缘判定。"""

from __future__ import annotations

from datetime import datetime

from ai_sdlc.core.stage_review.binding_models import (
    BindingAttemptOperation,
    BindingAuthoritySnapshot,
)
from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBinding,
    ReviewerBindingSet,
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.binding_validation import BindingRefusal
from ai_sdlc.core.stage_review.panel_models import ReviewerBudgetPolicy
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan
from ai_sdlc.core.stage_review.provider_authority_registry import (
    _validate_registered_provider_authority,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation


def validate_binding_inputs(
    *,
    plan: ReviewerPanelPlan,
    operation: BindingAttemptOperation,
    budget_policy: ReviewerBudgetPolicy,
    reservation: ResourceReservation | None,
    frozen_reservation: ResourceReservation | None,
    now: datetime,
) -> None:
    _validate_current_reservation(plan, operation, reservation, now=now)
    _validate_frozen_reservation(operation, reservation, frozen_reservation)
    _validate_plan(plan, operation, budget_policy)
    _validate_authority(plan, operation)


def _validate_current_reservation(
    plan: ReviewerPanelPlan,
    operation: BindingAttemptOperation,
    reservation: ResourceReservation | None,
    *,
    now: datetime,
) -> None:
    request = operation.request
    if reservation is None or (
        reservation.reservation_id != request.final_reservation_id
        or reservation.project_id != request.project_id
        or reservation.work_item_id != request.work_item_id
        or reservation.stage_review_session_id != request.stage_review_session_id
        or reservation.state != "final"
        or reservation.fencing_token != request.resource_fencing_token
        or reservation.proposal_digest != plan.proposal.proposal_digest
        or reservation.budget_policy_digest != request.budget_policy_digest
    ):
        raise BindingRefusal(
            "provider_policy_blocked", "binding.resource-lineage-invalid"
        )
    if parse_utc(reservation.lease_expires_at) <= now:
        raise BindingRefusal(
            "provider_policy_blocked", "binding.resource-lease-expired"
        )


def _validate_frozen_reservation(
    operation: BindingAttemptOperation,
    current: ResourceReservation | None,
    frozen: ResourceReservation | None,
) -> None:
    request = operation.request
    if (
        current is None
        or frozen is None
        or frozen.reservation_id != current.reservation_id
        or frozen.reservation_digest != request.final_reservation_digest
        or frozen.project_id != current.project_id
        or frozen.work_item_id != current.work_item_id
        or frozen.stage_review_session_id != current.stage_review_session_id
        or frozen.state != "final"
        or frozen.fencing_token != current.fencing_token
        or frozen.revision > current.revision
    ):
        raise BindingRefusal(
            "provider_policy_blocked", "binding.resource-ancestor-invalid"
        )


def _validate_plan(
    plan: ReviewerPanelPlan,
    operation: BindingAttemptOperation,
    budget_policy: ReviewerBudgetPolicy,
) -> None:
    request = operation.request
    if (
        plan.plan_digest != request.plan_digest
        or plan.finalization_digest != request.plan_finalization_digest
        or plan.final_reservation_id != request.final_reservation_id
        or plan.final_reservation_digest != request.final_reservation_digest
        or plan.resource_fencing_token != request.resource_fencing_token
        or plan.proposal.budget_policy_digest != budget_policy.policy_digest
    ):
        raise BindingRefusal("provider_policy_blocked", "binding.plan-lineage-invalid")


def _validate_authority(
    plan: ReviewerPanelPlan,
    operation: BindingAttemptOperation,
) -> None:
    authority = operation.authority_snapshot
    _validate_binding_authority(plan, authority)
    if (
        authority.plan_digest != plan.plan_digest
        or authority.plan_finalization_digest != plan.finalization_digest
        or authority.request_digest != plan.proposal.request_digest
        or authority.optimization_snapshot_digest
        != plan.proposal.optimization_snapshot_digest
        or authority.registry_digest != plan.proposal.registry_digest
        or authority.selection_policy_digest != plan.proposal.selection_policy_digest
    ):
        raise BindingRefusal(
            "provider_policy_blocked", "binding.authority-lineage-invalid"
        )


def _validate_binding_authority(
    plan: ReviewerPanelPlan,
    authority: BindingAuthoritySnapshot,
) -> None:
    try:
        _validate_registered_provider_authority(authority, plan)
    except (AttributeError, TypeError, ValueError) as exc:
        raise BindingRefusal(
            "provider_policy_blocked",
            "binding.authority-untrusted",
        ) from exc


def dispatch_assignment_matches_binding(
    binding_set: ReviewerBindingSet,
    binding: ReviewerBinding,
    assignment: ReviewerDispatchAssignment,
) -> bool:
    return all(
        (
            assignment.binding_set_digest == binding_set.binding_set_digest,
            assignment.binding_digest == binding.binding_digest,
            assignment.slot_id == binding.slot_id,
            assignment.candidate_manifest_digest
            == binding_set.candidate_manifest_digest,
            assignment.provider_id == binding.provider_id,
            assignment.provider_descriptor_digest == binding.provider_descriptor_digest,
            assignment.provider_execution_identity_digest
            == binding.execution_identity.identity_digest,
            assignment.physical_provider_id == binding.physical_provider_id,
            assignment.physical_equivalence_class_id
            == binding.physical_equivalence_class_id,
            assignment.transport_profile_digest == binding.transport_profile_digest,
            assignment.transport_contract_digest == binding.transport_contract_digest,
            assignment.transport_authority_digest == binding.transport_authority_digest,
            assignment.model_family == binding.model_family,
            assignment.recovery_capabilities == binding.recovery_capabilities,
            assignment.session_id == binding_set.stage_review_session_id,
            assignment.host_snapshot_digest == binding_set.host_snapshot_digest,
        )
    )


__all__ = ["dispatch_assignment_matches_binding", "validate_binding_inputs"]
