"""Risk、Rebind 与 Plan Revocation 的可信事实解析和前置校验。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.binding_result_models import (
    RebindDirective,
    ReviewerBindingSet,
)
from ai_sdlc.core.stage_review.contracts import TaskRiskProfile
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.panel_models import ReviewerPlanRequest
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.session_artifact_models import ReviewerPlanRevocation
from ai_sdlc.core.stage_review.session_authority import validate_resource_advance
from ai_sdlc.core.stage_review.session_contracts import (
    SessionIntegrityError,
    SessionTrustResolver,
)
from ai_sdlc.core.stage_review.session_models import StageReviewSession


def resolve_risk_profile(
    resolver: SessionTrustResolver,
    session: StageReviewSession,
    digest: str,
) -> TaskRiskProfile:
    profile = resolver.resolve_risk_profile(digest)
    if profile is None:
        raise SessionIntegrityError("risk profile is not trusted")
    profile = TaskRiskProfile.model_validate(profile.model_dump(mode="json"))
    request = _resolve_stage_request(
        resolver,
        session.scope,
        session.active_plan_digest,
    )
    lineage = (
        profile.profile_digest == digest,
        profile.work_item_id == session.scope.work_item_id,
        profile.stage_key == request.stage_key,
    )
    if not all(lineage):
        raise SessionIntegrityError("risk profile lineage is invalid")
    return profile


def resolve_initial_risk_profile(
    resolver: SessionTrustResolver,
    scope: FindingScope,
    digest: str,
    plan_digest: str,
) -> TaskRiskProfile:
    profile = resolver.resolve_risk_profile(digest)
    if profile is None:
        raise SessionIntegrityError("risk profile is not trusted")
    profile = TaskRiskProfile.model_validate(profile.model_dump(mode="json"))
    request = _resolve_stage_request(resolver, scope, plan_digest)
    if (
        profile.profile_digest != digest
        or profile.work_item_id != scope.work_item_id
        or profile.stage_key != request.stage_key
        or request.task_risk_profile_digest != digest
    ):
        raise SessionIntegrityError("risk profile lineage is invalid")
    return profile


def _resolve_stage_request(
    resolver: SessionTrustResolver,
    scope: FindingScope,
    plan_digest: str,
) -> ReviewerPlanRequest:
    plan = resolver.resolve_plan(plan_digest)
    if plan is None:
        raise SessionIntegrityError("risk profile plan is not trusted")
    plan = ReviewerPanelPlan.model_validate(plan.model_dump(mode="json"))
    request = resolver.resolve_plan_request(plan.proposal.request_digest)
    if request is None:
        raise SessionIntegrityError("risk profile plan request is not trusted")
    request = ReviewerPlanRequest.model_validate(request.model_dump(mode="json"))
    lineage = (
        request.request_digest == plan.proposal.request_digest,
        request.work_item_id == scope.work_item_id,
        request.stage_instance_id == scope.stage_instance_id,
    )
    if not all(lineage):
        raise SessionIntegrityError("risk profile plan request lineage is invalid")
    return request


def capability_gap(
    resolver: SessionTrustResolver,
    session: StageReviewSession,
    required: tuple[str, ...],
) -> tuple[str, ...]:
    plan = resolver.resolve_plan(session.active_plan_digest)
    binding = resolver.resolve_binding_set(session.active_binding_set_digest)
    if plan is None or binding is None:
        raise SessionIntegrityError("active plan or binding authority is missing")
    plan = ReviewerPanelPlan.model_validate(plan.model_dump(mode="json"))
    binding = ReviewerBindingSet.model_validate(binding.model_dump(mode="json"))
    return authority_capability_gap(plan, binding, required)


def authority_capability_gap(
    plan: ReviewerPanelPlan,
    binding: ReviewerBindingSet,
    required: tuple[str, ...],
) -> tuple[str, ...]:
    planned = {item.capability_id for item in plan.proposal.coverage_proof}
    bound = {
        capability for item in binding.bindings for capability in item.capability_ids
    }
    return tuple(sorted(set(required) - (planned & bound)))


def require_actual_gap(
    resolver: SessionTrustResolver,
    session: StageReviewSession,
    capabilities: tuple[str, ...],
) -> None:
    profile = resolve_risk_profile(
        resolver,
        session,
        session.active_risk_profile_digest,
    )
    actual = capability_gap(
        resolver,
        session,
        tuple(profile.required_capability_ids),
    )
    if capabilities != actual or not actual:
        raise SessionIntegrityError("role gap differs from active risk profile")


def resolve_rebind_directive(
    resolver: SessionTrustResolver,
    digest: str,
) -> RebindDirective:
    directive = resolver.resolve_rebind_directive(digest)
    if directive is None:
        raise SessionIntegrityError("provider rebind directive is not trusted")
    directive = RebindDirective.model_validate(directive.model_dump(mode="json"))
    if directive.directive_digest != digest:
        raise SessionIntegrityError("provider rebind directive digest is invalid")
    return directive


def validate_rebind(
    session: StageReviewSession,
    current_binding: ReviewerBindingSet,
    binding: ReviewerBindingSet,
    directive: RebindDirective,
    *,
    reservation: ResourceReservation,
) -> None:
    if binding.binding_set_digest == session.active_binding_set_digest:
        raise SessionIntegrityError("provider rebind must change binding")
    lineage = (
        directive.previous_binding_set_digest == session.active_binding_set_digest,
        directive.new_binding_set_digest == binding.binding_set_digest,
        directive.expected_cohort_id == session.active_cohort_id,
        directive.expected_pass_head_digest
        == session.active_cohort_initial_head_digest,
        binding.previous_binding_set_digest == session.active_binding_set_digest,
    )
    if not all(lineage):
        raise SessionIntegrityError("provider rebind directive lineage is invalid")
    unavailable = set(directive.unavailable_provider_ids)
    if not unavailable:
        raise SessionIntegrityError("provider rebind lacks unavailable provider facts")
    if unavailable & {item.provider_id for item in binding.bindings}:
        raise SessionIntegrityError("provider rebind retained an unavailable provider")
    prior = {item.provider_id for item in current_binding.bindings}
    if not unavailable & prior:
        raise SessionIntegrityError(
            "provider rebind does not replace an active provider"
        )
    for counter in ("provider_retries", "binding_attempts"):
        validate_resource_advance(
            session,
            reservation,
            required_increment=counter,
        )


def resolve_plan_revocation(
    resolver: SessionTrustResolver,
    digest: str,
) -> ReviewerPlanRevocation:
    revocation = resolver.resolve_plan_revocation(digest)
    if revocation is None:
        raise SessionIntegrityError("plan revocation is not trusted")
    revocation = ReviewerPlanRevocation.model_validate(
        revocation.model_dump(mode="json")
    )
    if revocation.revocation_digest != digest:
        raise SessionIntegrityError("plan revocation digest is invalid")
    return revocation


def require_revocation_target(
    revocation: ReviewerPlanRevocation,
    plan: ReviewerPanelPlan,
) -> None:
    profiles = {item.role_profile_id for item in plan.proposal.required_slots}
    capabilities = {item.capability_id for item in plan.proposal.coverage_proof}
    matches = {
        "plan": revocation.plan_digest == plan.plan_digest,
        "profile": bool(set(revocation.profile_ids) & profiles),
        "capability": bool(set(revocation.capability_ids) & capabilities),
    }
    if not matches[revocation.target_kind]:
        raise SessionIntegrityError("plan revocation does not target active plan")
