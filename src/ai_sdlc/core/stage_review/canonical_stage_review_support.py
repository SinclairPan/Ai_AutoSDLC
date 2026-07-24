"""CanonicalStageReviewExecutor 的纯构造与校验辅助。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBinding,
    ReviewerBindingSet,
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.finding_command_models import FindingInitialDraft
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocation,
    ProviderSubmission,
)
from ai_sdlc.core.stage_review.remote_review_models import RemoteReviewOutput
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.session import (
    SubmitReviewPassCommand,
    review_submission_digest,
)
from ai_sdlc.core.stage_review.session_models import StageReviewSession
from ai_sdlc.core.stage_review.stage_review_execution import (
    StageReviewExecutionOutcome,
    StageReviewExecutionRequest,
)


def execution_scope(request: StageReviewExecutionRequest) -> FindingScope:
    candidate = request.candidate
    return FindingScope(
        project_id=candidate.project_id,
        work_item_id=candidate.work_item_id,
        stage_instance_id=candidate.stage_instance_id,
        session_id=candidate.review_session_id,
    )


def required_binding(
    binding_set: ReviewerBindingSet,
    slot_id: str,
) -> ReviewerBinding:
    match = next((item for item in binding_set.bindings if item.slot_id == slot_id), None)
    if match is None:
        raise ValueError("required review binding is unavailable")
    return match


def build_pass_command(
    session: StageReviewSession,
    binding: ReviewerBinding,
    assignment: ReviewerDispatchAssignment,
    invocation: ProviderInvocation,
    output: RemoteReviewOutput,
) -> SubmitReviewPassCommand:
    return SubmitReviewPassCommand(
        scope=session.scope,
        command_id=stable_id(
            "review-pass", session.scope.session_id, binding.slot_id
        ),
        idempotency_key=stable_id(
            "review-pass-key", session.scope.session_id, binding.slot_id
        ),
        expected_revision=session.revision,
        cohort_id=session.active_cohort_id,
        slot_id=binding.slot_id,
        assignment_digest=assignment.assignment_digest,
        invocation_id=invocation.invocation_id,
        verdict=output.verdict,
        coverage=output.coverage,
        findings=_review_findings(output, binding),
        evidence_digests=output.evidence_digests,
        observed_peer_pass_ids=(),
    )


def _review_findings(
    output: RemoteReviewOutput,
    binding: ReviewerBinding,
) -> tuple[FindingInitialDraft, ...]:
    return tuple(
        FindingInitialDraft(
            identity=item.identity,
            severity=item.severity,
            evidence_bundle_digest=item.evidence_bundle_digest,
            actor_id=binding.actor_id,
            slot_id=binding.slot_id,
            capability_id=item.capability_id,
        )
        for item in output.findings
    )


def validated_output_digest(
    submission: ProviderSubmission,
    binding: ReviewerBinding,
) -> str:
    output = RemoteReviewOutput.model_validate(submission.output_payload)
    return review_submission_digest(
        verdict=output.verdict,
        coverage=output.coverage,
        findings=_review_findings(output, binding),
        evidence_digests=output.evidence_digests,
        observed_peer_pass_ids=(),
    )


def owner_scope(request: StageReviewExecutionRequest, slot_id: str) -> str:
    slot = next(
        item for item in request.plan.proposal.required_slots if item.slot_id == slot_id
    )
    if not slot.provider_constraints:
        raise ValueError("reviewer provider scope is unavailable")
    return slot.provider_constraints[0]


def anticipated_usage(
    request: StageReviewExecutionRequest,
    slot_id: str,
) -> ResourceAmounts:
    slot = next(
        item for item in request.plan.proposal.required_slots if item.slot_id == slot_id
    )
    return ResourceAmounts(
        provider_calls=1,
        review_passes=1,
        tokens=slot.estimated_tokens,
        cost=slot.estimated_cost,
        active_wall_clock=slot.estimated_wall_clock,
        parallelism=1,
    )


def needs_user(reason: str) -> StageReviewExecutionOutcome:
    return StageReviewExecutionOutcome(status="needs_user", reason_code=reason)


def blocked(reason: str) -> StageReviewExecutionOutcome:
    return StageReviewExecutionOutcome(status="blocked", reason_code=reason)


__all__ = [
    "anticipated_usage",
    "blocked",
    "build_pass_command",
    "execution_scope",
    "needs_user",
    "owner_scope",
    "required_binding",
    "validated_output_digest",
]
