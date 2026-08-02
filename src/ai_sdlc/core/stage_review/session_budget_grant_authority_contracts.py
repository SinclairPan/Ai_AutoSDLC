"""BudgetGrant 审批治理的无运行时依赖端口。"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from ai_sdlc.core.stage_review.session_budget_approval_models import (
    BudgetGrantApproval,
    BudgetGrantApprovalState,
)


class BudgetGrantApprovalResolver(Protocol):
    authority_id: str

    def resolve(self, approval_digest: str) -> BudgetGrantApproval | None: ...

    def approval_state(
        self,
        approval_digest: str,
    ) -> BudgetGrantApprovalState | None: ...

    def hold_session_apply(
        self,
        expected: BudgetGrantApprovalState,
        *,
        decision_digest: str,
        command_id: str,
    ) -> AbstractContextManager[None]: ...
