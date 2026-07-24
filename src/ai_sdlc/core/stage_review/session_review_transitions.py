"""Session 启动与 ReviewPass 提交的纯投影构建。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.session_artifact_models import (
    ArtifactRef,
    ReviewCohort,
    ReviewPass,
    ReviewPassRef,
)
from ai_sdlc.core.stage_review.session_authority import (
    SessionAuthority,
    hard_budget_reached,
)
from ai_sdlc.core.stage_review.session_contracts import SessionStartCommand
from ai_sdlc.core.stage_review.session_models import (
    SessionProjectionData,
    StageReviewSession,
    replace_projection,
)
from ai_sdlc.core.stage_review.session_runtime import EventSpec
from ai_sdlc.core.stage_review.session_transitions import stop_for_hard_budget


def initial_projection(
    command: SessionStartCommand,
    authority: SessionAuthority,
    cohort: ReviewCohort,
    *,
    missing_capabilities: tuple[str, ...] = (),
) -> SessionProjectionData:
    cohort_ref = ArtifactRef(
        artifact_id=cohort.cohort_id,
        artifact_digest=cohort.cohort_digest,
    )
    return SessionProjectionData(
        scope=command.scope,
        state="replanning" if missing_capabilities else "collecting_initial_reviews",
        policy_digest=command.policy_digest,
        optimization_snapshot_digest=command.optimization_snapshot_digest,
        risk_profile_lineage_id=command.risk_profile_lineage_id,
        active_candidate_digest=command.candidate_digest,
        active_risk_profile_digest=command.risk_profile_digest,
        active_plan_digest=authority.plan.plan_digest,
        active_binding_set_digest=authority.binding_set.binding_set_digest,
        active_cohort_id=cohort.cohort_id,
        active_cohort_initial_head_digest=cohort.initial_pass_head_digest,
        resource_reservation_id=authority.reservation.reservation_id,
        resource_reservation_digest=authority.reservation.reservation_digest,
        resource_fencing_epoch=authority.reservation.fencing_token,
        resource_usage=authority.reservation.usage,
        finding_ledger_digest="",
        cohort_refs=(cohort_ref,),
        pending_role_gap_capability_ids=missing_capabilities,
    )


def pass_projection(
    session: StageReviewSession,
    review_pass: ReviewPass,
    reservation: ResourceReservation,
) -> SessionProjectionData:
    ref = ReviewPassRef(
        pass_id=review_pass.pass_id,
        pass_digest=review_pass.pass_digest,
        cohort_id=review_pass.cohort_id,
        slot_id=review_pass.slot_id,
        is_first_cohort_pass=review_pass.is_first_cohort_pass,
    )
    projection = replace_projection(
        session.projection,
        pass_refs=(*session.pass_refs, ref),
        resource_reservation_digest=reservation.reservation_digest,
        resource_usage=reservation.usage,
    )
    if hard_budget_reached(reservation):
        return stop_for_hard_budget(projection, session.state)
    return projection


def pass_spec(
    review_pass: ReviewPass,
    projection: SessionProjectionData,
) -> EventSpec:
    ref = ArtifactRef(
        artifact_id=review_pass.pass_id,
        artifact_digest=review_pass.pass_digest,
    )
    return "review_pass_committed", projection, (ref,)
