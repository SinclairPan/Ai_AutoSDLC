"""Session 预算投影的局部结构不变量。"""

from __future__ import annotations

from typing import Protocol

from ai_sdlc.core.stage_review.session_contracts import SessionState


class SessionBudgetProjection(Protocol):
    state: SessionState
    budget_revision: int
    budget_grant_ids: tuple[str, ...]
    budget_grant_digests: tuple[str, ...]
    reconciled_budget_grant_ids: tuple[str, ...]
    reconciled_budget_grant_digests: tuple[str, ...]
    pending_budget_grant_command_id: str
    budget_resume_state: SessionState | None
    last_budget_grant_operation_id: str
    budget_grant_operation_effect_digest: str


def _validate_session_budget_projection(value: SessionBudgetProjection) -> None:
    groups = (
        value.budget_grant_ids,
        value.budget_grant_digests,
        value.reconciled_budget_grant_ids,
        value.reconciled_budget_grant_digests,
    )
    if any(group != tuple(sorted(set(group))) for group in groups):
        raise ValueError("session budget projection values must be canonical")
    waiting = value.budget_resume_state is not None
    if value.pending_budget_grant_command_id and not waiting:
        raise ValueError("session pending budget grant state is incomplete")
    if waiting and (value.state != "needs_user" or value.budget_resume_state == "needs_user"):
        raise ValueError("session budget resume state is invalid")
    applied = bool(value.last_budget_grant_operation_id)
    if applied != bool(value.budget_grant_operation_effect_digest):
        raise ValueError("session budget grant operation lineage is incomplete")
    if len(value.budget_grant_ids) != len(value.budget_grant_digests):
        raise ValueError("session budget grant facts are incomplete")
    if len(value.reconciled_budget_grant_ids) != len(
        value.reconciled_budget_grant_digests
    ):
        raise ValueError("session reconciled budget grant facts are incomplete")
    revision_ids = {*value.budget_grant_ids, *value.reconciled_budget_grant_ids}
    if value.budget_revision != len(revision_ids):
        raise ValueError("session budget revision differs from applied grants")
