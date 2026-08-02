"""验证并恢复已开始关闭的 canonical Review Session。"""

from __future__ import annotations

from collections.abc import Callable

from ai_sdlc.core.stage_review.binding_result_models import ReviewerBindingSet
from ai_sdlc.core.stage_review.candidate import candidate_binding_digest
from ai_sdlc.core.stage_review.canonical_stage_review_support import execution_scope
from ai_sdlc.core.stage_review.review_completion import (
    ReviewSessionCompletion,
    build_review_completion,
    read_review_completion,
)
from ai_sdlc.core.stage_review.session import (
    SessionIntegrityError,
    StageReviewSessionService,
)
from ai_sdlc.core.stage_review.session_contracts import CloseConsumptionStartCommand
from ai_sdlc.core.stage_review.session_models import StageReviewSession
from ai_sdlc.core.stage_review.stage_review_execution import (
    StageReviewExecutionOutcome,
    StageReviewExecutionRequest,
)


def _recover_consuming_review_session(
    request: StageReviewExecutionRequest,
    binding_set: ReviewerBindingSet,
    service: StageReviewSessionService,
    session: StageReviewSession,
    on_authorized: Callable[[StageReviewSessionService], None] | None,
) -> StageReviewExecutionOutcome:
    recovered = service._recover_close_start_context(execution_scope(request))
    completion = read_review_completion(service.projection_path(session.scope))
    if recovered is None or completion is None:
        raise SessionIntegrityError(
            "consuming review session recovery evidence is unavailable"
        )
    command, authorized, completed_at = recovered
    expected = build_review_completion(authorized, completed_at=completed_at)
    if not _completion_lineage_matches(
        request=request,
        binding_set=binding_set,
        session=session,
        command=command,
        completion=completion,
        expected=expected,
    ):
        raise SessionIntegrityError(
            "consuming review session completion lineage diverged"
        )
    if on_authorized is not None:
        on_authorized(service)
    return StageReviewExecutionOutcome(
        status="completed",
        review_session_digest=completion.session_digest,
        review_completion_digest=completion.completion_digest,
    )


def _completion_lineage_matches(
    *,
    request: StageReviewExecutionRequest,
    binding_set: ReviewerBindingSet,
    session: StageReviewSession,
    command: CloseConsumptionStartCommand,
    completion: ReviewSessionCompletion,
    expected: ReviewSessionCompletion,
) -> bool:
    certificate = command.certificate
    return all(
        (
            completion == expected,
            expected.scope == session.scope == command.scope,
            expected.session_digest == certificate.session_digest,
            expected.candidate_manifest_digest
            == certificate.candidate_manifest_digest
            == candidate_binding_digest(request.candidate),
            expected.panel_plan_digest
            == certificate.panel_plan_digest
            == request.plan.plan_digest,
            expected.binding_set_digest
            == certificate.binding_digest
            == binding_set.binding_set_digest
            == session.active_binding_set_digest,
            expected.finding_ledger_digest
            == certificate.finding_ledger_digest
            == session.finding_ledger_digest,
            certificate.task_risk_profile_digest
            == request.proposal.risk_profile.profile_digest,
            certificate.selection_policy_digest
            == request.plan.proposal.selection_policy_digest,
            certificate.budget_policy_digest
            == request.plan.proposal.budget_policy_digest,
        )
    )
