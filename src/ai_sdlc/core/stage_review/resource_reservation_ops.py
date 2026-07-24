"""Reservation 两阶段输入重放与原子提交操作。"""

from __future__ import annotations

from math import isfinite

from ai_sdlc.core.stage_review.panel_digests import panel_proposal_lineage_digest
from ai_sdlc.core.stage_review.panel_models import ReviewerBudgetPolicy
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelProposal
from ai_sdlc.core.stage_review.resource_builders import (
    build_budget_envelope,
    final_requirement,
    proposal_provider_scope_ids,
)
from ai_sdlc.core.stage_review.resource_digests import (
    resource_operation_effect_digest,
)
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceGovernorState,
    ResourceReservation,
    ResourceReservationResult,
)
from ai_sdlc.core.stage_review.resource_models import BudgetEnvelope
from ai_sdlc.core.stage_review.resource_projection_builder import update_reservation
from ai_sdlc.core.stage_review.resource_runtime import (
    commit_reservation,
    result,
)
from ai_sdlc.core.stage_review.resource_store import ResourceEventStore


class ResourceLineageError(ValueError):
    """预算包与受信策略血缘不一致。"""


def trusted_admission_inputs(
    envelope: BudgetEnvelope,
    policy: ReviewerBudgetPolicy,
    lease_owner: str,
    lease_seconds: float,
    project_id: str,
) -> tuple[BudgetEnvelope, str]:
    trusted = BudgetEnvelope.model_validate(envelope.model_dump(mode="json"))
    trusted_policy = ReviewerBudgetPolicy.model_validate(policy.model_dump(mode="json"))
    expected = build_budget_envelope(
        project_id=trusted.project_id,
        work_item_id=trusted.work_item_id,
        stage_review_session_id=trusted.stage_review_session_id,
        risk_level=trusted.risk_level,
        budget_policy=trusted_policy,
        pool=trusted.pool,
    )
    if (
        trusted.envelope_digest != expected.envelope_digest
        or trusted.project_id != project_id
    ):
        raise ResourceLineageError("budget envelope does not match trusted policy")
    if (
        not isfinite(lease_seconds)
        or lease_seconds <= 0
        or not lease_owner.strip()
        or lease_owner != lease_owner.strip()
    ):
        raise ValueError("reservation lease requires owner and positive duration")
    effect = resource_operation_effect_digest(
        "reserve_admission",
        {
            "envelope_digest": trusted.envelope_digest,
            "lease_owner": lease_owner,
            "lease_seconds": lease_seconds,
        },
    )
    return trusted, effect


def trusted_finalization_inputs(
    proposal: ReviewerPanelProposal,
    reservation_id: str,
    lease_owner: str,
    expected_fencing_token: int,
) -> tuple[ReviewerPanelProposal, str]:
    trusted = ReviewerPanelProposal.model_validate(proposal.model_dump(mode="json"))
    if not lease_owner.strip() or lease_owner != lease_owner.strip():
        raise ValueError("reservation finalization requires lease owner")
    effect = resource_operation_effect_digest(
        "finalize_reservation",
        {
            "reservation_id": reservation_id,
            "proposal_digest": trusted.proposal_digest,
            "lease_owner": lease_owner,
            "expected_fencing_token": expected_fencing_token,
        },
    )
    return trusted, effect


def _offline_finalization_effect(
    reservation_id: str,
    lease_owner: str,
    expected_fencing_token: int,
) -> str:
    if not lease_owner.strip() or lease_owner != lease_owner.strip():
        raise ValueError("offline reservation finalization requires lease owner")
    return resource_operation_effect_digest(
        "finalize_offline_reservation",
        {
            "reservation_id": reservation_id,
            "lease_owner": lease_owner,
            "expected_fencing_token": expected_fencing_token,
        },
    )


def _commit_offline_final_reservation(
    store: ResourceEventStore,
    state: ResourceGovernorState,
    current: ResourceReservation,
    effect_digest: str,
    operation_id: str,
) -> ResourceReservationResult:
    if current.pool != "offline_optimization":
        return result("invalid_reservation", current)
    finalized = update_reservation(
        current,
        operation_id=operation_id,
        operation_effect_digest=effect_digest,
        state="final",
        fencing_token=state.next_fencing_token,
    )
    return commit_reservation(
        store, state, "reservation_finalized", operation_id, finalized
    )


def commit_final_reservation(
    store: ResourceEventStore,
    state: ResourceGovernorState,
    current: ResourceReservation,
    proposal: ReviewerPanelProposal,
    effect_digest: str,
    operation_id: str,
) -> ResourceReservationResult:
    if (
        proposal.budget_envelope_digest != current.budget_envelope_digest
        or proposal.budget_policy_digest != current.budget_policy_digest
    ):
        return result("invalid_reservation", current)
    required = final_requirement(proposal.resource_requirement, current)
    if not required.fits_within(current.reserved):
        return result("requirement_exceeds_admission")
    finalized = update_reservation(
        current,
        operation_id=operation_id,
        operation_effect_digest=effect_digest,
        state="final",
        proposal_digest=proposal.proposal_digest,
        proposal_lineage_digest=panel_proposal_lineage_digest(proposal),
        provider_scope_ids=proposal_provider_scope_ids(proposal),
        reserved=required,
        hard_limits=required,
        fencing_token=state.next_fencing_token,
    )
    return commit_reservation(
        store, state, "reservation_finalized", operation_id, finalized
    )
