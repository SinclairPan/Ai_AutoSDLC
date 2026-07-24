"""Session BudgetGrant 与 ResourceGovernor 的窄协调端口。"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from ai_sdlc.core.stage_review.resource_grant_models import (
    BudgetGrant,
    BudgetGrantDecisionClaim,
    BudgetGrantDecisionKind,
)
from ai_sdlc.core.stage_review.session_budget_grant_models import (
    BudgetGrantResourceApplication,
)
from ai_sdlc.core.stage_review.session_budget_grant_request import (
    BudgetGrantRequestProof,
)
from ai_sdlc.core.stage_review.session_budget_reconciliation_models import (
    BudgetGrantResourceReconciliation,
)
from ai_sdlc.core.stage_review.session_models import StageReviewSession


class SessionBudgetGrantCoordinator(Protocol):
    def apply(
        self,
        grant: BudgetGrant,
        session: StageReviewSession,
        request_proof: BudgetGrantRequestProof,
    ) -> BudgetGrantResourceApplication: ...

    def verify(
        self,
        application: BudgetGrantResourceApplication,
        session: StageReviewSession,
        request_proof: BudgetGrantRequestProof,
    ) -> BudgetGrantResourceApplication: ...

    def decide(
        self,
        application: BudgetGrantResourceApplication,
        request_proof: BudgetGrantRequestProof,
        desired_kind: BudgetGrantDecisionKind,
    ) -> BudgetGrantDecisionClaim: ...

    def hold_apply_commit(
        self,
        application: BudgetGrantResourceApplication,
        decision: BudgetGrantDecisionClaim,
        session: StageReviewSession,
        request_proof: BudgetGrantRequestProof,
    ) -> AbstractContextManager[None]: ...

    def reconcile(
        self,
        application: BudgetGrantResourceApplication,
        decision: BudgetGrantDecisionClaim,
        request_proof: BudgetGrantRequestProof,
        apply_command_id: str,
    ) -> BudgetGrantResourceReconciliation: ...
