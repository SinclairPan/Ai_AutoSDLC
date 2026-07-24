"""Resource 锁域内的 BudgetGrant 最终决策线性化。"""

from __future__ import annotations

from datetime import datetime

from ai_sdlc.core.stage_review.resource_builders import stable_id, utc_iso
from ai_sdlc.core.stage_review.resource_grant_models import (
    BudgetGrantDecisionClaim,
    BudgetGrantDecisionKind,
    BudgetGrantOperation,
    BudgetGrantResourceError,
)
from ai_sdlc.core.stage_review.resource_runtime import prepare_state, utc_now
from ai_sdlc.core.stage_review.resource_store import ResourceEventStore
from ai_sdlc.core.stage_review.session_budget_approval_models import (
    BudgetGrantApprovalState,
)
from ai_sdlc.core.stage_review.session_budget_grant_request import (
    BudgetGrantRequestProof,
)


def _decide_budget_grant(
    store: ResourceEventStore,
    application_operation: BudgetGrantOperation,
    request_proof: BudgetGrantRequestProof,
    approval_state: BudgetGrantApprovalState,
    desired_kind: BudgetGrantDecisionKind,
    *,
    now: datetime | None,
) -> BudgetGrantDecisionClaim:
    if desired_kind not in {"session_apply", "reconcile"}:
        raise BudgetGrantResourceError("invalid_input")
    grant = application_operation.grant
    decision_id = stable_id("budget-grant-decision", grant.idempotency_key)
    current_time = utc_now(now)
    with store.locked():
        existing = store.get_budget_grant_decision(decision_id)
        if existing is not None:
            if (
                existing.grant.grant_digest != grant.grant_digest
                or existing.request_proof_digest != request_proof.proof_digest
                or existing.approval_state.authority_id
                != approval_state.authority_id
            ):
                raise BudgetGrantResourceError("state_corrupt")
            return existing
        state = prepare_state(store, current_time)
        current = state.reservations.get(grant.final_reservation_id)
        if current is None:
            raise BudgetGrantResourceError("invalid_reservation")
        valid = (
            grant.grant_id in current.budget_grant_ids,
            grant.grant_id not in current.reconciled_budget_grant_ids,
            current.state == "final",
        )
        if not all(valid):
            raise BudgetGrantResourceError("grant_not_current")
        claim = BudgetGrantDecisionClaim(
            decision_id=decision_id,
            decision_kind=desired_kind,
            grant=grant,
            request_proof_digest=request_proof.proof_digest,
            approval_state=approval_state,
            resource_reservation_revision=current.revision,
            resource_reservation_digest=current.reservation_digest,
            resource_fencing_token=current.fencing_token,
            resource_reservation=current,
            claimed_at=utc_iso(current_time),
        )
        return store.persist_budget_grant_decision(claim)
