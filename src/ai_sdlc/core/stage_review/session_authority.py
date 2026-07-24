"""Session 对 Plan、Binding、Reservation、Assignment 与 Invocation 的可信校验。"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from ai_sdlc.core.stage_review.binding_authority_validation import (
    _validate_binding_authority_snapshot,
)
from ai_sdlc.core.stage_review.binding_models import BindingAuthoritySnapshot
from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBindingSet,
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan
from ai_sdlc.core.stage_review.provider_journal_models import ProviderInvocation
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.session_artifact_models import (
    CohortReviewer,
    ReviewCohort,
)
from ai_sdlc.core.stage_review.session_contracts import (
    SessionIntegrityError,
    SessionTrustResolver,
)
from ai_sdlc.core.stage_review.session_models import StageReviewSession


@dataclass(frozen=True, slots=True)
class SessionAuthority:
    plan: ReviewerPanelPlan
    binding_set: ReviewerBindingSet
    reservation: ResourceReservation


def resolve_session_authority(
    resolver: SessionTrustResolver,
    scope: FindingScope,
    *,
    candidate_digest: str,
    plan_digest: str,
    binding_set_digest: str,
    reservation_digest: str = "",
) -> SessionAuthority:
    plan = resolver.resolve_plan(plan_digest)
    binding_set = resolver.resolve_binding_set(binding_set_digest)
    if plan is None or binding_set is None:
        raise SessionIntegrityError("session plan or binding authority is missing")
    plan = ReviewerPanelPlan.model_validate(plan.model_dump(mode="json"))
    binding_set = ReviewerBindingSet.model_validate(binding_set.model_dump(mode="json"))
    authority = resolver.resolve_binding_authority(
        binding_set.authority_snapshot_digest
    )
    if authority is None:
        raise SessionIntegrityError("session binding authority is missing")
    try:
        authority = BindingAuthoritySnapshot.model_validate(
            authority.model_dump(mode="json")
        )
        _validate_binding_authority_snapshot(plan, authority, binding_set, ())
    except (AttributeError, TypeError, ValidationError, ValueError) as exc:
        raise SessionIntegrityError("session binding authority is invalid") from exc
    reservation = resolver.resolve_reservation(
        reservation_digest or binding_set.charged_reservation_digest
    )
    if reservation is None:
        raise SessionIntegrityError("session charged reservation is missing")
    reservation = ResourceReservation.model_validate(
        reservation.model_dump(mode="json")
    )
    _validate_authority(scope, candidate_digest, plan, binding_set, reservation)
    return SessionAuthority(plan, binding_set, reservation)


def resolve_review_assignment(
    resolver: SessionTrustResolver,
    digest: str,
) -> ReviewerDispatchAssignment:
    assignment = resolver.resolve_assignment(digest)
    if assignment is None:
        raise SessionIntegrityError("review pass assignment is missing")
    try:
        return ReviewerDispatchAssignment.model_validate(
            assignment.model_dump(mode="json")
        )
    except (ValidationError, ValueError, AttributeError) as exc:
        raise SessionIntegrityError("review pass assignment is invalid") from exc


def resolve_review_invocation(
    resolver: SessionTrustResolver,
    invocation_id: str,
) -> ProviderInvocation:
    invocation = resolver.resolve_invocation(invocation_id)
    if invocation is None:
        raise SessionIntegrityError("review pass invocation is missing")
    try:
        return ProviderInvocation.model_validate(invocation.model_dump(mode="json"))
    except (ValidationError, ValueError, AttributeError) as exc:
        raise SessionIntegrityError("review pass invocation is invalid") from exc


def validate_review_authority(
    session: StageReviewSession,
    cohort: ReviewCohort,
    reviewer: CohortReviewer,
    assignment: ReviewerDispatchAssignment,
    invocation: ProviderInvocation,
    payload_digest: str,
) -> None:
    request = invocation.request
    assignment_lineage = (
        assignment.assignment_digest == request.assignment_digest,
        assignment.cohort_id == cohort.cohort_id,
        assignment.expected_pass_head_digest == cohort.initial_pass_head_digest,
        assignment.slot_id == reviewer.slot_id,
        assignment.binding_digest == reviewer.binding_digest,
        assignment.binding_set_digest == cohort.binding_set_digest,
        assignment.candidate_manifest_digest == cohort.candidate_digest,
        assignment.provider_id == reviewer.provider_id,
        assignment.session_id == session.scope.session_id,
    )
    if not all(assignment_lineage):
        raise SessionIntegrityError("review pass assignment lineage is invalid")
    invocation_lineage = (
        invocation.state == "committed",
        request.project_id == session.scope.project_id,
        request.work_item_id == session.scope.work_item_id,
        request.stage_review_session_id == session.scope.session_id,
        request.candidate_digest == cohort.candidate_digest,
        request.provider_id == reviewer.provider_id,
        invocation.validation_digest == payload_digest,
        bool(invocation.isolation_receipt_digests),
        bool(invocation.execution_evidence_root_digest),
    )
    if not all(invocation_lineage):
        raise SessionIntegrityError("review pass provider invocation is invalid")


def validate_resource_advance(
    session: StageReviewSession,
    reservation: ResourceReservation,
    *,
    required_increment: str | None = None,
) -> None:
    lineage = (
        reservation.reservation_id == session.resource_reservation_id,
        reservation.project_id == session.scope.project_id,
        reservation.work_item_id == session.scope.work_item_id,
        reservation.stage_review_session_id == session.scope.session_id,
        reservation.state == "final",
    )
    if not all(lineage):
        raise SessionIntegrityError("session resource reservation lineage is invalid")
    if not session.resource_usage.fits_within(reservation.usage):
        raise SessionIntegrityError("session resource usage cannot move backwards")
    if required_increment is not None and (
        getattr(reservation.usage, required_increment)
        <= getattr(session.resource_usage, required_increment)
    ):
        raise SessionIntegrityError(
            f"session operation lacks required {required_increment} resource charge"
        )


def hard_budget_reached(reservation: ResourceReservation) -> bool:
    return any(
        getattr(reservation.hard_limits, name) > 0
        and getattr(reservation.usage, name) >= getattr(reservation.hard_limits, name)
        for name in ResourceAmounts.ALL_FIELDS
    )


def _validate_authority(
    scope: FindingScope,
    candidate_digest: str,
    plan: ReviewerPanelPlan,
    binding_set: ReviewerBindingSet,
    reservation: ResourceReservation,
) -> None:
    plan_lineage = (
        binding_set.project_id == scope.project_id,
        binding_set.work_item_id == scope.work_item_id,
        binding_set.stage_review_session_id == scope.session_id,
        binding_set.candidate_manifest_digest == candidate_digest,
        binding_set.plan_digest == plan.plan_digest,
        binding_set.plan_finalization_digest == plan.finalization_digest,
        binding_set.final_reservation_id == plan.final_reservation_id,
        binding_set.final_reservation_digest == plan.final_reservation_digest,
        binding_set.execution_mode == "enforce_eligible",
    )
    reservation_lineage = (
        reservation.reservation_id == binding_set.final_reservation_id,
        reservation.project_id == scope.project_id,
        reservation.work_item_id == scope.work_item_id,
        reservation.stage_review_session_id == scope.session_id,
        reservation.state == "final",
    )
    if not all(plan_lineage) or not all(reservation_lineage):
        raise SessionIntegrityError("session plan, binding, or reservation diverged")
    _validate_required_bindings(plan, binding_set)


def _validate_required_bindings(
    plan: ReviewerPanelPlan,
    binding_set: ReviewerBindingSet,
) -> None:
    slots = {item.slot_id: item for item in plan.proposal.required_slots}
    bindings = {item.slot_id: item for item in binding_set.bindings}
    if set(slots) != set(bindings):
        raise SessionIntegrityError("session required slot binding is incomplete")
    for slot_id, slot in slots.items():
        binding = bindings[slot_id]
        valid = (
            binding.slot_kind == "required",
            binding.role_profile_id == slot.role_profile_id,
            binding.role_contract_digest == slot.role_contract_digest,
            binding.capability_ids == slot.capability_ids,
            binding.eligible_for_enforce_quorum,
        )
        if not all(valid):
            raise SessionIntegrityError("session required binding differs from plan")
