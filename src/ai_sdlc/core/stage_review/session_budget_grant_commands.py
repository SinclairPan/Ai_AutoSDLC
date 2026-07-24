"""BudgetGrant 的确定性 Session 后续命令构造。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_grant_models import (
    BudgetGrantDecisionClaim,
    BudgetGrantResourceError,
)
from ai_sdlc.core.stage_review.session_budget_grant_models import (
    BudgetGrantResourceApplication,
)
from ai_sdlc.core.stage_review.session_budget_reconciliation_models import (
    BudgetGrantResourceReconciliation,
)
from ai_sdlc.core.stage_review.session_contracts import (
    BudgetGrantApplyCommand,
    BudgetGrantFailureCommand,
    BudgetGrantReconcileCommand,
    BudgetGrantRequestCommand,
)
from ai_sdlc.core.stage_review.session_models import SessionEvent, SessionMutationResult


def _merge_replay(
    result: SessionMutationResult,
    request_replay: bool,
) -> SessionMutationResult:
    return result.model_copy(
        update={"idempotent_replay": result.idempotent_replay or request_replay}
    )


def _build_apply_command(
    request: BudgetGrantRequestCommand,
    event: SessionEvent,
    application: BudgetGrantResourceApplication,
    decision: BudgetGrantDecisionClaim,
) -> BudgetGrantApplyCommand:
    command_id = stable_id("budget-grant-session-apply", application.grant.grant_id)
    return BudgetGrantApplyCommand(
        scope=request.scope,
        command_id=command_id,
        idempotency_key=stable_id("budget-grant-session-apply-key", command_id),
        expected_revision=event.sequence,
        request_command_id=request.command_id,
        request_event_digest=event.event_digest,
        application=application,
        decision=decision,
    )


def _build_reconcile_command(
    request: BudgetGrantRequestCommand,
    event: SessionEvent,
    reconciliation: BudgetGrantResourceReconciliation,
    *,
    expected_revision: int | None = None,
) -> BudgetGrantReconcileCommand:
    command_id = stable_id(
        "budget-grant-session-reconcile",
        reconciliation.application.grant.grant_id,
    )
    return BudgetGrantReconcileCommand(
        scope=request.scope,
        command_id=command_id,
        idempotency_key=stable_id("budget-grant-session-reconcile-key", command_id),
        expected_revision=expected_revision or event.sequence,
        request_command_id=request.command_id,
        request_event_digest=event.event_digest,
        reconciliation=reconciliation,
    )


def _build_failure_command(
    request: BudgetGrantRequestCommand,
    event: SessionEvent,
    failure: BudgetGrantResourceError,
) -> BudgetGrantFailureCommand:
    command_id = stable_id(
        "budget-grant-session-failure",
        request.command_id,
        failure.result_code,
    )
    return BudgetGrantFailureCommand(
        scope=request.scope,
        command_id=command_id,
        idempotency_key=stable_id("budget-grant-session-failure-key", command_id),
        expected_revision=event.sequence,
        request_command_id=request.command_id,
        request_event_digest=event.event_digest,
        failure_code=failure.result_code,
        integrity_failure=failure.integrity_failure,
    )
