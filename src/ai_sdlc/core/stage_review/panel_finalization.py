"""ReviewerPanelProposal 在 FinalReservation 后的受控冻结构建器。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ai_sdlc.core.stage_review.contracts import TaskRiskProfile
from ai_sdlc.core.stage_review.panel_authorization_models import (
    ReviewerPlanningAuthorization,
)
from ai_sdlc.core.stage_review.panel_digests import (
    panel_proposal_lineage_digest,
    reviewer_panel_finalization_digest,
    reviewer_panel_plan_digest,
)
from ai_sdlc.core.stage_review.panel_models import (
    ReviewerBudgetPolicy,
    ReviewerPlanRequest,
    ReviewerQuorumPolicy,
    ReviewerRoleOption,
)
from ai_sdlc.core.stage_review.panel_plan_models import (
    ReviewerPanelPlan,
    ReviewerPanelProposal,
)
from ai_sdlc.core.stage_review.registry_models import (
    ReviewerCapabilityRegistry,
    ReviewerRoleModule,
    ReviewerSelectionPolicy,
)
from ai_sdlc.core.stage_review.resource_builders import final_requirement
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation


class PanelProposalReplayContext(BaseModel):
    """冻结时重放唯一 Planner 所需的全部不可变输入。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request: ReviewerPlanRequest
    task_risk_profile: TaskRiskProfile
    registry: ReviewerCapabilityRegistry
    selection_policy: ReviewerSelectionPolicy
    quorum_policy: ReviewerQuorumPolicy
    budget_policy: ReviewerBudgetPolicy
    planning_authorization: ReviewerPlanningAuthorization
    role_options: tuple[ReviewerRoleOption, ...]
    module_catalog: tuple[ReviewerRoleModule, ...]

    def replay_inputs(self) -> dict[str, object]:
        return {
            "request": self.request,
            "task_risk_profile": self.task_risk_profile,
            "registry": self.registry,
            "selection_policy": self.selection_policy,
            "quorum_policy": self.quorum_policy,
            "budget_policy": self.budget_policy,
            "planning_authorization": self.planning_authorization,
            "role_options": self.role_options,
            "module_catalog": self.module_catalog,
        }


def _build_reviewer_panel_plan(
    proposal: ReviewerPanelProposal,
    final_reservation: ResourceReservation,
) -> ReviewerPanelPlan:
    trusted_proposal = ReviewerPanelProposal.model_validate(
        proposal.model_dump(mode="json")
    )
    trusted_reservation = ResourceReservation.model_validate(
        final_reservation.model_dump(mode="json")
    )
    _verify_finalization_lineage(trusted_proposal, trusted_reservation)
    draft = ReviewerPanelPlan.model_construct(
        proposal=trusted_proposal,
        proposal_lineage_digest=panel_proposal_lineage_digest(trusted_proposal),
        final_reservation_id=trusted_reservation.reservation_id,
        final_reservation_digest=trusted_reservation.reservation_digest,
        resource_fencing_token=trusted_reservation.fencing_token,
        plan_digest="",
        finalization_digest="",
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["proposal"] = trusted_proposal
    payload["plan_digest"] = reviewer_panel_plan_digest(draft)
    with_plan_digest = ReviewerPanelPlan.model_construct(**payload)
    payload["finalization_digest"] = reviewer_panel_finalization_digest(
        with_plan_digest
    )
    return ReviewerPanelPlan.model_validate(payload)


def _verify_finalization_lineage(
    proposal: ReviewerPanelProposal,
    reservation: ResourceReservation,
) -> None:
    if reservation.state != "final":
        raise ValueError("reviewer panel plan requires FinalReservation")
    if reservation.proposal_digest != proposal.proposal_digest:
        raise ValueError("FinalReservation does not bind reviewer proposal")
    if reservation.proposal_lineage_digest != panel_proposal_lineage_digest(proposal):
        raise ValueError("FinalReservation does not bind exact proposal lineage")
    if reservation.budget_envelope_digest != proposal.budget_envelope_digest:
        raise ValueError("FinalReservation does not bind proposal budget envelope")
    if reservation.budget_policy_digest != proposal.budget_policy_digest:
        raise ValueError("FinalReservation does not bind proposal budget policy")
    expected = final_requirement(proposal.resource_requirement, reservation)
    if reservation.reserved != expected:
        raise ValueError("FinalReservation does not cover the planned resources")
