"""ReviewerPlanRequest 的可信输入归一化、能力展开与摘要冻结。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from pydantic import BaseModel, ConfigDict

from ai_sdlc.core.stage_review.canonical import normalize_repo_path
from ai_sdlc.core.stage_review.contracts import TaskRiskProfile
from ai_sdlc.core.stage_review.panel_authorization_models import (
    ReviewerPlanningAuthorization,
)
from ai_sdlc.core.stage_review.panel_digests import (
    plan_request_digest,
    planning_authorization_digest,
    planning_context_digest,
    role_option_catalog_digest,
)
from ai_sdlc.core.stage_review.panel_models import (
    CapabilityCoverageRequirement,
    EnforcementMode,
    ReviewerBudgetPolicy,
    ReviewerPlanRequest,
    ReviewerQuorumPolicy,
    ReviewerRoleOption,
)
from ai_sdlc.core.stage_review.registry_models import (
    ReviewerCapabilityRegistry,
    ReviewerSelectionPolicy,
    StageKey,
)


class PlanRequestBuildContext(BaseModel):
    """公共 Builder 参数的内部强类型快照。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    work_item_id: str
    loop_id: str
    loop_round_number: int
    stage_instance_id: str
    candidate_manifest_ref: str
    candidate_manifest_digest: str
    task_risk_profile_ref: str
    task_risk_profile: TaskRiskProfile
    change_surface_digest: str
    registry_ref: str
    registry: ReviewerCapabilityRegistry
    role_catalog_ref: str
    role_options: Sequence[ReviewerRoleOption]
    selection_policy_ref: str
    selection_policy: ReviewerSelectionPolicy
    quorum_policy_ref: str
    quorum_policy: ReviewerQuorumPolicy
    budget_policy_ref: str
    budget_policy: ReviewerBudgetPolicy
    budget_envelope_digest: str
    planning_authorization: ReviewerPlanningAuthorization
    solver_version: str
    optimization_snapshot_ref: str
    optimization_snapshot_digest: str
    enforcement_mode: EnforcementMode


def build_request_from_context(
    context: PlanRequestBuildContext,
) -> ReviewerPlanRequest:
    risk = TaskRiskProfile.model_validate(
        context.task_risk_profile.model_dump(mode="json")
    )
    if risk.work_item_id != context.work_item_id:
        raise ValueError("risk profile work item does not match plan request")
    stage_key = cast(StageKey, risk.stage_key)
    coverage = coverage_requirements(risk, context.selection_policy, stage_key)
    authorization = trusted_planning_authorization(
        context.planning_authorization,
        registry=context.registry,
        role_options=context.role_options,
        selection_policy=context.selection_policy,
        quorum_policy=context.quorum_policy,
        budget_policy=context.budget_policy,
    )
    draft = _draft_request(context, risk, stage_key, coverage, authorization)
    frozen_context = draft.model_copy(
        update={"planning_context_digest": planning_context_digest(draft)}
    )
    payload = frozen_context.model_dump(mode="json", warnings=False)
    payload["request_digest"] = plan_request_digest(frozen_context)
    return ReviewerPlanRequest.model_validate(payload)


def build_planning_authorization(
    *,
    registry: ReviewerCapabilityRegistry,
    role_options: Sequence[ReviewerRoleOption],
    selection_policy: ReviewerSelectionPolicy,
    quorum_policy: ReviewerQuorumPolicy,
    budget_policy: ReviewerBudgetPolicy,
) -> ReviewerPlanningAuthorization:
    """由已冻结治理输入构建单一内容寻址授权。"""

    values = _authorization_values(
        registry, role_options, selection_policy, quorum_policy, budget_policy
    )
    draft = ReviewerPlanningAuthorization.model_construct(
        _fields_set=None, **values, authorization_digest=""
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["authorization_digest"] = planning_authorization_digest(draft)
    return ReviewerPlanningAuthorization.model_validate(payload)


def trusted_planning_authorization(
    authorization: ReviewerPlanningAuthorization,
    *,
    registry: ReviewerCapabilityRegistry,
    role_options: Sequence[ReviewerRoleOption],
    selection_policy: ReviewerSelectionPolicy,
    quorum_policy: ReviewerQuorumPolicy,
    budget_policy: ReviewerBudgetPolicy,
) -> ReviewerPlanningAuthorization:
    """重验授权本身及其指向的全部规划治理输入。"""

    trusted = ReviewerPlanningAuthorization.model_validate(
        authorization.model_dump(mode="json")
    )
    expected = _authorization_values(
        registry, role_options, selection_policy, quorum_policy, budget_policy
    )
    if any(getattr(trusted, name) != value for name, value in expected.items()):
        raise ValueError("planning authorization does not match governance inputs")
    return trusted


def coverage_requirements(
    risk: TaskRiskProfile,
    policy: ReviewerSelectionPolicy,
    stage_key: StageKey,
) -> tuple[CapabilityCoverageRequirement, ...]:
    counts = {item: 1 for item in risk.required_capability_ids}
    if risk.risk_level in policy.double_coverage_risk_levels:
        counts = {item: 2 for item in counts}
    for rule in policy.capability_requirement_rules:
        if stage_key in rule.stage_keys and risk.risk_level in rule.risk_levels:
            for capability_id in rule.capability_ids:
                counts[capability_id] = max(
                    counts.get(capability_id, 0), rule.coverage_count
                )
    return tuple(
        CapabilityCoverageRequirement(
            capability_id=capability_id,
            minimum_required_slots=counts[capability_id],
        )
        for capability_id in sorted(counts)
    )


def _draft_request(
    context: PlanRequestBuildContext,
    risk: TaskRiskProfile,
    stage_key: StageKey,
    coverage: tuple[CapabilityCoverageRequirement, ...],
    authorization: ReviewerPlanningAuthorization,
) -> ReviewerPlanRequest:
    required = tuple(item.capability_id for item in coverage)
    blocking = blocking_capability_ids(required, context.selection_policy)
    return ReviewerPlanRequest.model_construct(
        request_id=context.request_id,
        work_item_id=context.work_item_id,
        loop_id=context.loop_id,
        loop_round_number=context.loop_round_number,
        stage_key=stage_key,
        stage_instance_id=context.stage_instance_id,
        risk_level=risk.risk_level,
        required_capability_ids=required,
        coverage_requirements=coverage,
        blocking_capability_ids=blocking,
        planning_context_digest="",
        candidate_manifest_ref=normalize_repo_path(context.candidate_manifest_ref),
        candidate_manifest_digest=context.candidate_manifest_digest,
        task_risk_profile_ref=normalize_repo_path(context.task_risk_profile_ref),
        task_risk_profile_digest=risk.profile_digest,
        change_surface_digest=context.change_surface_digest,
        registry_ref=normalize_repo_path(context.registry_ref),
        registry_digest=context.registry.registry_digest,
        registry_version=context.registry.registry_version,
        role_catalog_ref=normalize_repo_path(context.role_catalog_ref),
        role_catalog_digest=role_option_catalog_digest(tuple(context.role_options)),
        selection_policy_ref=normalize_repo_path(context.selection_policy_ref),
        selection_policy_digest=context.selection_policy.policy_digest,
        selection_policy_version=context.selection_policy.version,
        quorum_policy_ref=normalize_repo_path(context.quorum_policy_ref),
        quorum_policy_digest=context.quorum_policy.policy_digest,
        quorum_policy_version=context.quorum_policy.version,
        budget_policy_ref=normalize_repo_path(context.budget_policy_ref),
        budget_policy_digest=context.budget_policy.policy_digest,
        budget_envelope_digest=context.budget_envelope_digest,
        planning_authorization_digest=authorization.authorization_digest,
        solver_version=context.solver_version,
        optimization_snapshot_ref=normalize_repo_path(
            context.optimization_snapshot_ref
        ),
        optimization_snapshot_digest=context.optimization_snapshot_digest,
        enforcement_mode=context.enforcement_mode,
        request_digest="",
    )


def blocking_capability_ids(
    required: tuple[str, ...],
    policy: ReviewerSelectionPolicy,
) -> tuple[str, ...]:
    return tuple(sorted(set(required) & set(policy.allowed_blocking_authority_ids)))


def _authorization_values(
    registry: ReviewerCapabilityRegistry,
    role_options: Sequence[ReviewerRoleOption],
    selection_policy: ReviewerSelectionPolicy,
    quorum_policy: ReviewerQuorumPolicy,
    budget_policy: ReviewerBudgetPolicy,
) -> dict[str, str]:
    return {
        "registry_digest": registry.registry_digest,
        "role_catalog_digest": role_option_catalog_digest(tuple(role_options)),
        "selection_policy_digest": selection_policy.policy_digest,
        "quorum_policy_digest": quorum_policy.policy_digest,
        "budget_policy_digest": budget_policy.policy_digest,
    }
