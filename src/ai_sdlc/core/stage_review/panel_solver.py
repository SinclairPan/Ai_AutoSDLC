"""Reviewer Panel 的无 I/O、确定性组合求解。"""

from __future__ import annotations

from collections.abc import Sequence

from ai_sdlc.core.stage_review.panel_digests import role_option_catalog_digest
from ai_sdlc.core.stage_review.panel_models import (
    PanelPlanningError,
    PlannerResultCode,
    ReviewerBudgetPolicy,
    ReviewerPlanRequest,
    ReviewerQuorumPolicy,
    ReviewerRoleOption,
)
from ai_sdlc.core.stage_review.panel_plan_builder import build_panel_proposal
from ai_sdlc.core.stage_review.panel_plan_models import (
    ReviewerPanelResolution,
)
from ai_sdlc.core.stage_review.panel_selection import select_panel_roles
from ai_sdlc.core.stage_review.registry import read_role_contract
from ai_sdlc.core.stage_review.registry_models import (
    CapabilityDefinition,
    ReviewerCapabilityRegistry,
    ReviewerRoleModule,
    ReviewerSelectionPolicy,
)
from ai_sdlc.core.stage_review.registry_validation import validate_registry_bundle


def solve_reviewer_panel(
    *,
    request: ReviewerPlanRequest,
    registry: ReviewerCapabilityRegistry,
    selection_policy: ReviewerSelectionPolicy,
    quorum_policy: ReviewerQuorumPolicy,
    budget_policy: ReviewerBudgetPolicy,
    role_options: Sequence[ReviewerRoleOption],
    module_catalog: Sequence[ReviewerRoleModule],
) -> ReviewerPanelResolution:
    """根据冻结输入生成唯一 Panel；失败时不降级为近似方案。"""

    _verify_bound_inputs(
        request,
        registry,
        selection_policy,
        quorum_policy,
        budget_policy,
        module_catalog,
    )
    capabilities = _applicable_capabilities(request, registry)
    options = _trusted_options(
        role_options,
        registry,
        selection_policy,
        module_catalog,
    )
    if request.role_catalog_digest != role_option_catalog_digest(options):
        raise PanelPlanningError("invalid_input", "panel.role-catalog-mismatch")
    selected = select_panel_roles(
        request=request,
        selection=selection_policy,
        quorum=quorum_policy,
        budget=budget_policy,
        options=options,
        capabilities=capabilities,
    )
    return ReviewerPanelResolution(
        result_code="resolved",
        proposal=build_panel_proposal(
            request=request,
            quorum=quorum_policy,
            budget=budget_policy,
            options=options,
            selected=selected,
        ),
    )


def _verify_bound_inputs(
    request: ReviewerPlanRequest,
    registry: ReviewerCapabilityRegistry,
    selection: ReviewerSelectionPolicy,
    quorum: ReviewerQuorumPolicy,
    budget: ReviewerBudgetPolicy,
    modules: Sequence[ReviewerRoleModule],
) -> None:
    try:
        validate_registry_bundle(
            registry=registry, policy=selection, module_catalog=modules
        )
    except ValueError as exc:
        code: PlannerResultCode = (
            "policy_conflict" if "policy" in str(exc) else "registry_unavailable"
        )
        raise PanelPlanningError(code, "panel.registry-bundle-invalid") from exc
    expected = {
        "registry": (request.registry_digest, registry.registry_digest),
        "selection": (request.selection_policy_digest, selection.policy_digest),
        "quorum": (request.quorum_policy_digest, quorum.policy_digest),
        "budget": (request.budget_policy_digest, budget.policy_digest),
    }
    mismatch = sorted(name for name, pair in expected.items() if pair[0] != pair[1])
    versions = (
        request.registry_version == registry.registry_version
        and request.selection_policy_version == selection.version
        and request.quorum_policy_version == quorum.version
    )
    if mismatch or not versions:
        raise PanelPlanningError("invalid_input", "panel.input-binding-mismatch")
    if set(request.blocking_capability_ids) - set(quorum.veto_authorities):
        raise PanelPlanningError("policy_conflict", "panel.blocking-veto-gap")


def _applicable_capabilities(
    request: ReviewerPlanRequest,
    registry: ReviewerCapabilityRegistry,
) -> dict[str, CapabilityDefinition]:
    applicable = {
        item.capability_id: item
        for item in registry.capabilities
        if item.maturity != "deprecated"
        and request.stage_key in item.applicable_stage
        and request.risk_level in item.applicable_risk
    }
    active = {
        item_id for item_id, item in applicable.items() if item.maturity == "active"
    }
    missing = sorted(set(request.required_capability_ids) - active)
    if missing:
        raise PanelPlanningError(
            "unsatisfied_required_capability",
            "panel.required-capability-unavailable",
            missing=missing,
        )
    return applicable


def _trusted_options(
    options: Sequence[ReviewerRoleOption],
    registry: ReviewerCapabilityRegistry,
    selection: ReviewerSelectionPolicy,
    modules: Sequence[ReviewerRoleModule],
) -> tuple[ReviewerRoleOption, ...]:
    trusted: list[ReviewerRoleOption] = []
    identities: set[str] = set()
    for option in options:
        try:
            payload = option.model_dump(mode="json")
            payload["role_contract"] = option.role_contract
            validated = ReviewerRoleOption.model_validate(payload)
            contract = read_role_contract(
                validated.role_contract.model_dump(mode="json"),
                registry=registry,
                policy=selection,
                module_catalog=modules,
            )
        except ValueError as exc:
            raise PanelPlanningError(
                "role_contract_conflict", "panel.role-option-invalid"
            ) from exc
        identity = contract.role_profile_id
        if identity in identities:
            raise PanelPlanningError(
                "role_contract_conflict", "panel.duplicate-role-option"
            )
        identities.add(identity)
        trusted.append(validated.model_copy(update={"role_contract": contract}))
    return tuple(
        sorted(
            trusted,
            key=lambda item: (
                item.estimated_cost,
                item.estimated_tokens,
                item.estimated_provider_calls,
                item.role_contract.role_profile_id,
                item.role_contract.role_contract_digest,
            ),
        )
    )
