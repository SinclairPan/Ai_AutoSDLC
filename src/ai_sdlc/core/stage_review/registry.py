"""构建并公开 Reviewer Registry、Policy 与 Role 的稳定入口。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ai_sdlc.core.stage_review.capability_graph import resolve_capability_ids
from ai_sdlc.core.stage_review.registry_digests import (
    ROLE_MODULE_SET_FIELDS,
    registry_digest,
    role_contract_digest,
    role_module_digest,
    selection_policy_digest,
)
from ai_sdlc.core.stage_review.registry_models import (
    CapabilityConsumptionMode,
    CapabilityDefinition,
    ReviewerCapabilityRegistry,
    ReviewerRoleModule,
    ReviewerSelectionPolicy,
)
from ai_sdlc.core.stage_review.registry_validation import validate_registry_bundle
from ai_sdlc.core.stage_review.registry_versions import normalize_machine_ids
from ai_sdlc.core.stage_review.role_contract_models import ReviewerRoleContract

if TYPE_CHECKING:
    from ai_sdlc.core.stage_review.registry_defaults import ReviewerRegistryBundle


def build_capability_registry(
    *,
    registry_id: str,
    registry_version: str,
    capabilities: Sequence[CapabilityDefinition],
    contract_version: str = "1.0.0",
) -> ReviewerCapabilityRegistry:
    """构建并验证一个内容寻址 Registry。"""

    draft = ReviewerCapabilityRegistry.model_construct(
        registry_id=registry_id,
        registry_version=registry_version,
        contract_version=contract_version,
        capabilities=tuple(
            sorted(capabilities, key=lambda item: (item.capability_id, item.version))
        ),
        registry_digest="",
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["registry_digest"] = registry_digest(draft)
    return ReviewerCapabilityRegistry.model_validate(payload)


def build_selection_policy(
    *,
    policy_id: str,
    version: str,
    registry_compatibility_range: str,
    merge_semantics_version: str,
    owner: str,
    review_date: str,
    allowed_blocking_authority_ids: Sequence[str] = (),
    enabled_module_ids: Sequence[str] = (),
    constraint_conflicts: Sequence[tuple[str, str]] = (),
    minimum_slots: int = 2,
    minimum_distinct_primary_dimensions: int = 2,
    optional_slot_limit: int = 0,
    advisory_slot_limit: int = 0,
    shadow_slot_limit: int = 0,
    double_coverage_risk_levels: Sequence[str] = ("high", "critical"),
    capability_requirement_rules: Sequence[object] = (),
) -> ReviewerSelectionPolicy:
    """构建并验证 Role/Authority 合并策略。"""

    draft = ReviewerSelectionPolicy.model_construct(
        policy_id=policy_id,
        version=version,
        registry_compatibility_range=registry_compatibility_range,
        merge_semantics_version=merge_semantics_version,
        allowed_blocking_authority_ids=tuple(allowed_blocking_authority_ids),
        enabled_module_ids=tuple(enabled_module_ids),
        constraint_conflicts=tuple(constraint_conflicts),
        minimum_slots=minimum_slots,
        minimum_distinct_primary_dimensions=minimum_distinct_primary_dimensions,
        optional_slot_limit=optional_slot_limit,
        advisory_slot_limit=advisory_slot_limit,
        shadow_slot_limit=shadow_slot_limit,
        double_coverage_risk_levels=tuple(double_coverage_risk_levels),
        capability_requirement_rules=tuple(capability_requirement_rules),
        owner=owner,
        review_date=review_date,
        policy_digest="",
    )
    payload = draft.model_dump(mode="json")
    payload["policy_digest"] = selection_policy_digest(draft)
    return ReviewerSelectionPolicy.model_validate(payload)


def build_role_module(**values: object) -> ReviewerRoleModule:
    """构建并验证一个内容寻址、深冻结的 Role Module。"""

    prepared = dict(values)
    for field_name in ROLE_MODULE_SET_FIELDS:
        prepared[field_name] = tuple(
            normalize_machine_ids(prepared.get(field_name, ()))
        )
    draft = ReviewerRoleModule.model_construct(
        _fields_set=None,
        **prepared,
        module_digest="",
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["module_digest"] = role_module_digest(draft)
    return ReviewerRoleModule.model_validate(payload)


def merge_role_modules(
    *,
    role_profile_id: str,
    version: str,
    modules: Sequence[ReviewerRoleModule],
    registry: ReviewerCapabilityRegistry,
    policy: ReviewerSelectionPolicy,
    module_catalog: Sequence[ReviewerRoleModule],
    capability_mode: CapabilityConsumptionMode = "active",
) -> ReviewerRoleContract:
    """保持公共入口稳定，具体合并职责位于 role_merge。"""

    from ai_sdlc.core.stage_review.role_merge import merge_role_modules as merge

    return merge(
        role_profile_id=role_profile_id,
        version=version,
        modules=list(modules),
        registry=registry,
        policy=policy,
        module_catalog=list(module_catalog),
        capability_mode=capability_mode,
    )


def read_role_contract(
    payload: Mapping[str, Any],
    *,
    registry: ReviewerCapabilityRegistry,
    policy: ReviewerSelectionPolicy,
    module_catalog: Sequence[ReviewerRoleModule],
) -> ReviewerRoleContract:
    """仅在完整版本上下文中读取并重演 Role 合同。"""

    context = {
        "registry": registry,
        "policy": policy,
        "module_catalog": tuple(module_catalog),
    }
    return ReviewerRoleContract.model_validate(payload, context=context)


def default_registry_bundle() -> ReviewerRegistryBundle:
    """延迟加载随包默认值，避免核心 Validator 依赖产品夹具。"""

    from ai_sdlc.core.stage_review.registry_defaults import build_default_bundle

    return build_default_bundle()


__all__ = [
    "CapabilityDefinition",
    "ReviewerCapabilityRegistry",
    "ReviewerRoleContract",
    "ReviewerRoleModule",
    "ReviewerSelectionPolicy",
    "build_capability_registry",
    "build_role_module",
    "build_selection_policy",
    "default_registry_bundle",
    "merge_role_modules",
    "read_role_contract",
    "resolve_capability_ids",
    "role_contract_digest",
    "role_module_digest",
    "validate_registry_bundle",
]
