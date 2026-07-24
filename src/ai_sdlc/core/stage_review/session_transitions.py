"""Session 投影的纯状态转换函数。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.session_artifact_models import (
    ArtifactRef,
    ReviewCohort,
    RoleReplanCounter,
)
from ai_sdlc.core.stage_review.session_authority import (
    SessionAuthority,
    hard_budget_reached,
)
from ai_sdlc.core.stage_review.session_contracts import (
    ProgressOutcome,
    SessionEventKind,
    SessionIntegrityError,
    SessionState,
)
from ai_sdlc.core.stage_review.session_models import (
    SessionProjectionData,
    StageReviewSession,
    replace_projection,
)


def next_no_progress(current: int, outcome: ProgressOutcome | None) -> int:
    if outcome == "improved" or outcome is None:
        return 0
    return current + 1


def stop_for_hard_budget(
    projection: SessionProjectionData,
    resume_state: SessionState,
) -> SessionProjectionData:
    if resume_state == "needs_user":
        raise SessionIntegrityError("hard budget resume state cannot be needs_user")
    return replace_projection(
        projection,
        state="needs_user",
        budget_resume_state=resume_state,
    )


def supersede_active(projection: SessionProjectionData) -> SessionProjectionData:
    return replace_projection(
        projection,
        superseded_cohort_ids=tuple(
            sorted({*projection.superseded_cohort_ids, projection.active_cohort_id})
        ),
    )


def invalidate_passes(
    projection: SessionProjectionData,
    cohort_id: str,
) -> SessionProjectionData:
    invalidated = {
        item.pass_id for item in projection.pass_refs if item.cohort_id == cohort_id
    }
    return replace_projection(
        projection,
        invalidated_pass_ids=tuple(
            sorted({*projection.invalidated_pass_ids, *invalidated})
        ),
    )


def activate_cohort(
    projection: SessionProjectionData,
    cohort: ReviewCohort,
    *,
    state: SessionState = "collecting_initial_reviews",
) -> SessionProjectionData:
    return replace_projection(
        projection,
        state=state,
        active_cohort_id=cohort.cohort_id,
        active_cohort_initial_head_digest=cohort.initial_pass_head_digest,
        cohort_refs=(
            *projection.cohort_refs,
            ArtifactRef(
                artifact_id=cohort.cohort_id,
                artifact_digest=cohort.cohort_digest,
            ),
        ),
        pending_role_gap_capability_ids=(),
    )


def cohort_ref(
    kind: SessionEventKind,
    cohort: ReviewCohort,
) -> tuple[ArtifactRef, ...]:
    if kind != "new_cohort_activated":
        return ()
    return (
        ArtifactRef(
            artifact_id=cohort.cohort_id,
            artifact_digest=cohort.cohort_digest,
        ),
    )


def role_gap_projections(
    session: StageReviewSession,
    authority: SessionAuthority,
    cohort: ReviewCohort,
    capabilities: tuple[str, ...],
) -> tuple[SessionProjectionData, ...]:
    projection = replace_projection(
        session.projection,
        state="replanning",
        pending_role_gap_capability_ids=capabilities,
    )
    values = [projection]
    projection = supersede_active(projection)
    values.append(projection)
    projection = invalidate_passes(projection, session.active_cohort_id)
    values.extend((projection, projection))
    projection = _freeze_replan(session, projection, authority)
    values.append(projection)
    projection = replace_projection(
        projection,
        state="binding",
        active_binding_set_digest=authority.binding_set.binding_set_digest,
    )
    values.append(projection)
    activated = activate_cohort(
        projection,
        cohort,
        state="collecting_initial_reviews",
    )
    if hard_budget_reached(authority.reservation):
        activated = stop_for_hard_budget(activated, "collecting_initial_reviews")
    values.append(activated)
    return tuple(values)


def require_gap_coverage(
    authority: SessionAuthority,
    capabilities: tuple[str, ...],
) -> None:
    covered = {item.capability_id for item in authority.plan.proposal.coverage_proof}
    if not set(capabilities) <= covered:
        raise SessionIntegrityError(
            "role gap replacement plan lacks required capability"
        )


def _freeze_replan(
    session: StageReviewSession,
    projection: SessionProjectionData,
    authority: SessionAuthority,
) -> SessionProjectionData:
    counters = {
        item.risk_profile_lineage_id: item.count
        for item in projection.role_replan_counts
    }
    lineage = session.risk_profile_lineage_id
    counters[lineage] = counters.get(lineage, 0) + 1
    return replace_projection(
        projection,
        active_plan_digest=authority.plan.plan_digest,
        resource_reservation_digest=authority.reservation.reservation_digest,
        resource_usage=authority.reservation.usage,
        role_replan_counts=tuple(
            RoleReplanCounter(risk_profile_lineage_id=key, count=value)
            for key, value in sorted(counters.items())
        ),
    )
