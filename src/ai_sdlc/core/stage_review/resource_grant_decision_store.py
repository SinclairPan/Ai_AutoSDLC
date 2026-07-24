"""不可变 BudgetGrant 决策 Claim 的 Resource Store 扩展。"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    create_json_exclusive,
    read_json_object,
)
from ai_sdlc.core.stage_review.resource_grant_models import BudgetGrantDecisionClaim


class _ResourceGrantDecisionStoreMixin:
    root: Path

    @property
    def grant_decisions_dir(self) -> Path:
        return self.root / "budget-grant-decisions"

    def persist_budget_grant_decision(
        self,
        decision: BudgetGrantDecisionClaim,
    ) -> BudgetGrantDecisionClaim:
        path = self._grant_decision_path(decision.decision_id)
        if create_json_exclusive(path, decision.model_dump(mode="json")):
            return decision
        existing = self.get_budget_grant_decision(decision.decision_id)
        if existing is None or existing.decision_digest != decision.decision_digest:
            raise SharedStateIntegrityError("BudgetGrant decision conflict")
        return existing

    def get_budget_grant_decision(
        self,
        decision_id: str,
    ) -> BudgetGrantDecisionClaim | None:
        path = self._grant_decision_path(decision_id)
        if not path.exists():
            return None
        try:
            return BudgetGrantDecisionClaim.model_validate(read_json_object(path))
        except (ValidationError, ValueError) as exc:
            raise SharedStateIntegrityError(
                f"BudgetGrant decision is invalid: {path}"
            ) from exc

    def _grant_decision_path(self, decision_id: str) -> Path:
        return self.grant_decisions_dir / f"{decision_id}.json"
