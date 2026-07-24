"""ResourceGovernor 对 Session BudgetGrant 请求证明的边界校验。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.resource_grant_models import (
    BudgetGrant,
    BudgetGrantResourceError,
)
from ai_sdlc.core.stage_review.session_budget_grant_request import (
    BudgetGrantRequestProof,
)


def validate_budget_grant_request(
    grant: BudgetGrant,
    proof: BudgetGrantRequestProof,
) -> None:
    approval = proof.approval
    projection = proof.requested_event.projection_after
    valid = (
        grant.requested_event_digest == proof.requested_event.event_digest,
        grant.project_id == approval.scope.project_id,
        grant.work_item_id == approval.scope.work_item_id,
        grant.stage_review_session_id == approval.scope.session_id,
        grant.final_reservation_id == approval.final_reservation_id,
        projection.resource_reservation_id == approval.final_reservation_id,
        projection.resource_reservation_digest == approval.final_reservation_digest,
        projection.resource_fencing_epoch == approval.final_fencing_token,
        projection.budget_revision == approval.expected_budget_revision,
        grant.expected_budget_revision == approval.expected_budget_revision,
        grant.increment == approval.increment,
    )
    if not all(valid):
        raise BudgetGrantResourceError("invalid_input")
