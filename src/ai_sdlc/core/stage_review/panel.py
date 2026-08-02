"""动态 Reviewer Panel 的唯一公共规划与重放校验入口。"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import ValidationError

from ai_sdlc.core.stage_review.contracts import TaskRiskProfile
from ai_sdlc.core.stage_review.panel_authorization_models import (
    ReviewerPlanningAuthorization,
)
from ai_sdlc.core.stage_review.panel_digests import (
    budget_policy_digest,
    panel_proposal_digest,
    panel_proposal_semantic_payload,
    quorum_policy_digest,
    role_option_catalog_digest,
    role_option_independence_key,
)
from ai_sdlc.core.stage_review.panel_models import (
    EnforcementMode,
    PanelPlanningError,
    PlannerResultCode,
    ReviewerBudgetPolicy,
    ReviewerPlanRequest,
    ReviewerQuorumPolicy,
    ReviewerRoleOption,
)
from ai_sdlc.core.stage_review.panel_plan_models import (
    ReviewerPanelProposal,
    ReviewerPanelResolution,
)
from ai_sdlc.core.stage_review.panel_request_builder import (
    PlanRequestBuildContext,
    blocking_capability_ids,
    build_planning_authorization,
    build_request_from_context,
    coverage_requirements,
    trusted_planning_authorization,
)
from ai_sdlc.core.stage_review.panel_solver import solve_reviewer_panel
from ai_sdlc.core.stage_review.registry_models import (
    ReviewerCapabilityRegistry,
    ReviewerRoleModule,
    ReviewerSelectionPolicy,
)
from ai_sdlc.core.stage_review.role_contract_models import ReviewerRoleContract


def build_budget_policy(**values: object) -> ReviewerBudgetPolicy:
    """构建内容寻址的 Hard Budget Policy。"""

    draft = ReviewerBudgetPolicy.model_construct(
        _fields_set=None, **values, policy_digest=""
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["policy_digest"] = budget_policy_digest(draft)
    return ReviewerBudgetPolicy.model_validate(payload)


def build_quorum_policy(**values: object) -> ReviewerQuorumPolicy:
    """构建内容寻址的动态 Quorum Policy。"""

    prepared = dict(values)
    for field_name in (
        "veto_authorities",
        "allowed_abstentions",
        "substitutable_required_role_groups",
    ):
        if field_name in prepared:
            prepared[field_name] = tuple(prepared[field_name])  # type: ignore[arg-type]
    draft = ReviewerQuorumPolicy.model_construct(
        _fields_set=None, **prepared, policy_digest=""
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["policy_digest"] = quorum_policy_digest(draft)
    return ReviewerQuorumPolicy.model_validate(payload)


def build_role_option(
    *,
    role_contract: ReviewerRoleContract,
    eligible_slot_kinds: Sequence[str],
    prompt_template_digest: str,
    tool_permission_ids: Sequence[str],
    evidence_source_ids: Sequence[str],
    independence_key: str | None = None,
    estimated_provider_calls: int,
    estimated_review_passes: int,
    estimated_tokens: int,
    estimated_cost: float,
    estimated_wall_clock: float,
) -> ReviewerRoleOption:
    """把冻结 Role 合同包装为纯规划候选，不执行 Provider 绑定。"""

    draft = ReviewerRoleOption.model_construct(
        role_contract=role_contract,
        eligible_slot_kinds=tuple(eligible_slot_kinds),
        prompt_template_digest=prompt_template_digest,
        tool_permission_ids=tuple(tool_permission_ids),
        evidence_source_ids=tuple(evidence_source_ids),
        independence_key="",
        estimated_provider_calls=estimated_provider_calls,
        estimated_review_passes=estimated_review_passes,
        estimated_tokens=estimated_tokens,
        estimated_cost=estimated_cost,
        estimated_wall_clock=estimated_wall_clock,
    )
    computed = role_option_independence_key(draft)
    if independence_key is not None and independence_key != computed:
        raise ValueError("independence_key must be derived from role option semantics")
    payload = draft.model_dump(mode="json", warnings=False)
    payload["role_contract"] = role_contract
    payload["independence_key"] = computed
    return ReviewerRoleOption.model_validate(payload)


def build_plan_request(
    *,
    request_id: str,
    work_item_id: str,
    loop_id: str,
    loop_round_number: int,
    stage_instance_id: str,
    candidate_manifest_ref: str,
    candidate_manifest_digest: str,
    task_risk_profile_ref: str,
    task_risk_profile: TaskRiskProfile,
    change_surface_digest: str,
    registry_ref: str,
    registry: ReviewerCapabilityRegistry,
    role_catalog_ref: str,
    role_options: Sequence[ReviewerRoleOption],
    selection_policy_ref: str,
    selection_policy: ReviewerSelectionPolicy,
    quorum_policy_ref: str,
    quorum_policy: ReviewerQuorumPolicy,
    budget_policy_ref: str,
    budget_policy: ReviewerBudgetPolicy,
    budget_envelope_digest: str,
    planning_authorization: ReviewerPlanningAuthorization,
    solver_version: str,
    optimization_snapshot_ref: str,
    optimization_snapshot_digest: str,
    enforcement_mode: EnforcementMode,
) -> ReviewerPlanRequest:
    """冻结规划语义；请求实例身份不参与计划语义摘要。"""

    return build_request_from_context(PlanRequestBuildContext.model_validate(locals()))


def plan_reviewer_panel(
    *,
    request: ReviewerPlanRequest,
    task_risk_profile: TaskRiskProfile,
    registry: ReviewerCapabilityRegistry,
    selection_policy: ReviewerSelectionPolicy,
    quorum_policy: ReviewerQuorumPolicy,
    budget_policy: ReviewerBudgetPolicy,
    planning_authorization: ReviewerPlanningAuthorization,
    role_options: Sequence[ReviewerRoleOption],
    module_catalog: Sequence[ReviewerRoleModule],
) -> ReviewerPanelResolution:
    """运行唯一 Planner；输入错误以稳定结果码返回。"""

    try:
        trusted_registry, trusted_selection, trusted_quorum, trusted_budget = (
            _trusted_governance(
                registry, selection_policy, quorum_policy, budget_policy
            )
        )
        trusted_request = _trusted_request(
            request,
            task_risk_profile,
            trusted_registry,
            trusted_selection,
            trusted_quorum,
            trusted_budget,
            planning_authorization,
            role_options,
        )
        return solve_reviewer_panel(
            request=trusted_request,
            registry=trusted_registry,
            selection_policy=trusted_selection,
            quorum_policy=trusted_quorum,
            budget_policy=trusted_budget,
            role_options=role_options,
            module_catalog=module_catalog,
        )
    except PanelPlanningError as exc:
        return ReviewerPanelResolution(
            result_code=exc.result_code,
            reason_ids=exc.reason_ids,
            missing_capability_ids=exc.missing,
        )
    except (ValidationError, ValueError):
        return ReviewerPanelResolution(
            result_code="role_contract_conflict",
            reason_ids=("panel.registry-or-role-invalid",),
        )


def validate_panel_proposal(
    proposal: ReviewerPanelProposal,
    **inputs: object,
) -> None:
    """用相同冻结输入重放唯一 Planner，拒绝摘要自洽但语义漂移的计划。"""

    result = plan_reviewer_panel(**inputs)  # type: ignore[arg-type]
    if result.result_code != "resolved" or result.proposal is None:
        raise ValueError("panel proposal replay could not resolve")
    request = inputs.get("request")
    if not isinstance(request, ReviewerPlanRequest):
        raise ValueError("panel proposal replay requires plan request")
    if proposal.request_digest != request.request_digest:
        raise ValueError("panel proposal request lineage mismatch")
    if panel_proposal_semantic_payload(
        result.proposal
    ) != panel_proposal_semantic_payload(proposal):
        raise ValueError("panel proposal replay mismatch")


def read_panel_proposal(
    payload: dict[str, object],
    **inputs: object,
) -> ReviewerPanelProposal:
    """只有在完整规划上下文重放成功后才返回可信求解结果。"""

    proposal = ReviewerPanelProposal.model_validate(payload)
    validate_panel_proposal(proposal, **inputs)
    return proposal


def _trusted_request(
    request: ReviewerPlanRequest,
    risk_profile: TaskRiskProfile,
    registry: ReviewerCapabilityRegistry,
    selection: ReviewerSelectionPolicy,
    quorum: ReviewerQuorumPolicy,
    budget: ReviewerBudgetPolicy,
    authorization: ReviewerPlanningAuthorization,
    role_options: Sequence[ReviewerRoleOption],
) -> ReviewerPlanRequest:
    try:
        trusted = ReviewerPlanRequest.model_validate(request.model_dump(mode="json"))
        risk = TaskRiskProfile.model_validate(risk_profile.model_dump(mode="json"))
    except ValidationError as exc:
        code = _request_validation_result_code(exc)
        raise PanelPlanningError(code, "panel.request-invalid") from exc
    try:
        trusted_authorization = trusted_planning_authorization(
            authorization,
            registry=registry,
            role_options=role_options,
            selection_policy=selection,
            quorum_policy=quorum,
            budget_policy=budget,
        )
    except (ValidationError, ValueError) as exc:
        raise PanelPlanningError(
            "invalid_input", "panel.planning-authorization-invalid"
        ) from exc
    coverage = coverage_requirements(risk, selection, trusted.stage_key)
    required = tuple(item.capability_id for item in coverage)
    expected = (
        trusted.work_item_id == risk.work_item_id,
        trusted.stage_key == risk.stage_key,
        trusted.risk_level == risk.risk_level,
        trusted.task_risk_profile_digest == risk.profile_digest,
        trusted.coverage_requirements == coverage,
        trusted.required_capability_ids == required,
        trusted.blocking_capability_ids == blocking_capability_ids(required, selection),
        trusted.role_catalog_digest == role_option_catalog_digest(tuple(role_options)),
        trusted.planning_authorization_digest
        == trusted_authorization.authorization_digest,
    )
    if not all(expected):
        raise PanelPlanningError("invalid_input", "panel.request-lineage-mismatch")
    return trusted


def _request_validation_result_code(exc: ValidationError) -> PlannerResultCode:
    version_fields = {
        "schema_version",
        "canonicalization_version",
        "solver_version",
    }
    if any(
        any(str(part) in version_fields for part in error.get("loc", ()))
        for error in exc.errors()
    ):
        return "incompatible_schema"
    return "invalid_input"


def _trusted_governance(
    registry: ReviewerCapabilityRegistry,
    selection: ReviewerSelectionPolicy,
    quorum: ReviewerQuorumPolicy,
    budget: ReviewerBudgetPolicy,
) -> tuple[
    ReviewerCapabilityRegistry,
    ReviewerSelectionPolicy,
    ReviewerQuorumPolicy,
    ReviewerBudgetPolicy,
]:
    try:
        trusted_registry = ReviewerCapabilityRegistry.model_validate(
            registry.model_dump(mode="json")
        )
    except ValidationError as exc:
        raise PanelPlanningError(
            "registry_unavailable", "panel.registry-invalid"
        ) from exc
    try:
        trusted_selection = ReviewerSelectionPolicy.model_validate(
            selection.model_dump(mode="json")
        )
        trusted_quorum = ReviewerQuorumPolicy.model_validate(
            quorum.model_dump(mode="json")
        )
        trusted_budget = ReviewerBudgetPolicy.model_validate(
            budget.model_dump(mode="json")
        )
    except ValidationError as exc:
        raise PanelPlanningError("policy_conflict", "panel.policy-invalid") from exc
    return trusted_registry, trusted_selection, trusted_quorum, trusted_budget


__all__ = [
    "ReviewerPanelProposal",
    "build_budget_policy",
    "build_planning_authorization",
    "build_plan_request",
    "build_quorum_policy",
    "build_role_option",
    "panel_proposal_digest",
    "plan_reviewer_panel",
    "read_panel_proposal",
    "validate_panel_proposal",
]
