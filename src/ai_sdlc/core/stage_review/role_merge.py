"""Reviewer Role Module 的确定性可交换合并。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from ai_sdlc.core.stage_review.capability_graph import (
    capabilities_by_id,
    expand_capability_ids,
    resolve_capability_ids,
)
from ai_sdlc.core.stage_review.registry_models import (
    AuthorityLevel,
    CapabilityConsumptionMode,
    CapabilityDefinition,
    ReviewerCapabilityRegistry,
    ReviewerRoleModule,
    ReviewerSelectionPolicy,
)
from ai_sdlc.core.stage_review.registry_validation import (
    verify_policy_artifact,
    verify_registry_artifact,
)
from ai_sdlc.core.stage_review.registry_versions import (
    require_machine_id,
    require_version,
    version_in_range,
)
from ai_sdlc.core.stage_review.role_contract_models import (
    ReviewerRoleContract,
    RoleModuleBinding,
)

_AUTHORITY_ORDER: dict[str, int] = {"observe": 0, "advise": 1, "block": 2}


@dataclass(frozen=True, slots=True)
class _MergedRole:
    capability_ids: list[str]
    primary_dimensions: list[str]
    in_scope: list[str]
    out_of_scope: list[str]
    blocking_authority: list[str]
    authority_ceiling: AuthorityLevel
    required_evidence: list[str]
    forbidden_actions: list[str]
    provider_constraints: list[str]
    isolation_requirements: list[str]
    cost_ceiling: float


def merge_role_modules(
    *,
    role_profile_id: str,
    version: str,
    modules: list[ReviewerRoleModule],
    registry: ReviewerCapabilityRegistry,
    policy: ReviewerSelectionPolicy,
    module_catalog: list[ReviewerRoleModule],
    capability_mode: CapabilityConsumptionMode = "active",
) -> ReviewerRoleContract:
    """按 FR-007 合并基础、Stage 与 Risk Module。"""

    from ai_sdlc.core.stage_review.registry import read_role_contract

    fields = derive_role_contract_fields(
        role_profile_id=role_profile_id,
        version=version,
        capability_mode=capability_mode,
        modules=modules,
        registry=registry,
        policy=policy,
    )
    draft = ReviewerRoleContract.model_construct(
        _fields_set=None,
        **fields,
        role_contract_digest="",
    )
    from ai_sdlc.core.stage_review.registry_digests import role_contract_digest

    payload = draft.model_dump(mode="json")
    payload["role_contract_digest"] = role_contract_digest(draft)
    return read_role_contract(
        payload,
        registry=registry,
        policy=policy,
        module_catalog=module_catalog,
    )


def derive_role_contract_fields(
    *,
    role_profile_id: str,
    version: str,
    capability_mode: CapabilityConsumptionMode,
    modules: list[ReviewerRoleModule],
    registry: ReviewerCapabilityRegistry,
    policy: ReviewerSelectionPolicy,
) -> dict[str, object]:
    """重演模块合并并返回不含运行时元数据和摘要的合同字段。"""

    _validate_merge_context(role_profile_id, version, registry, policy)
    ordered = _validated_modules(modules, registry, policy)
    capabilities = set(
        expand_capability_ids(
            registry,
            {item for module in ordered for item in module.capability_ids},
            allow_shadow=capability_mode == "shadow",
        )
    )
    by_id = capabilities_by_id(registry.capabilities)
    merged = _merge_module_sets(
        ordered,
        capabilities,
        registry,
        by_id,
        capability_mode,
    )
    _verify_merged_boundaries(
        merged,
        capabilities,
        by_id,
        policy,
        capability_mode,
    )
    return _role_contract_fields(
        role_profile_id,
        version,
        capability_mode,
        ordered,
        merged,
        registry,
        policy,
    )


def _validate_merge_context(
    role_profile_id: str,
    version: str,
    registry: ReviewerCapabilityRegistry,
    policy: ReviewerSelectionPolicy,
) -> None:
    require_machine_id(role_profile_id, "role_profile_id")
    require_version(version)
    verify_registry_artifact(registry)
    verify_policy_artifact(policy)
    if not version_in_range(
        registry.registry_version,
        policy.registry_compatibility_range,
    ):
        raise ValueError("selection policy is incompatible with capability registry")


def _validated_modules(
    modules: list[ReviewerRoleModule],
    registry: ReviewerCapabilityRegistry,
    policy: ReviewerSelectionPolicy,
) -> list[ReviewerRoleModule]:
    if not modules:
        raise ValueError("reviewer role requires at least one module")
    by_id: dict[str, ReviewerRoleModule] = {}
    for module in modules:
        previous = by_id.get(module.module_id)
        if previous is not None and previous != module:
            raise ValueError(f"conflicting module identity: {module.module_id}")
        by_id[module.module_id] = module
    base_count = sum(item.module_kind == "base" for item in by_id.values())
    if base_count != 1:
        raise ValueError("reviewer role requires exactly one base profile")
    if policy.enabled_module_ids:
        disabled = sorted(set(by_id) - set(policy.enabled_module_ids))
        if disabled:
            raise ValueError(f"reviewer role module is not enabled: {disabled}")
    from ai_sdlc.core.stage_review.registry_digests import role_module_digest

    for module in by_id.values():
        if module.module_digest != role_module_digest(module):
            raise ValueError(f"reviewer role module digest mismatch: {module.module_id}")
        if module.merge_semantics_version != policy.merge_semantics_version:
            raise ValueError("role module merge semantics are incompatible with policy")
        if not version_in_range(registry.registry_version, module.compatibility_range):
            raise ValueError("role module compatibility excludes capability registry")
    return sorted(by_id.values(), key=lambda item: (item.module_kind, item.module_id))


def _merge_module_sets(
    modules: list[ReviewerRoleModule],
    capability_ids: set[str],
    registry: ReviewerCapabilityRegistry,
    by_id: dict[str, CapabilityDefinition],
    capability_mode: CapabilityConsumptionMode,
) -> _MergedRole:
    def union(field: str) -> list[str]:
        return sorted(
            {
                value
                for item in modules
                for value in cast(tuple[str, ...], getattr(item, field))
            }
        )

    evidence = set(union("required_evidence"))
    for capability_id in capability_ids:
        evidence.update(by_id[capability_id].required_evidence_types)
    ceilings = [item.authority_ceiling for item in modules]
    ceilings.extend(by_id[item].authority_ceiling for item in capability_ids)
    authority_ceiling = min(ceilings, key=_AUTHORITY_ORDER.__getitem__)
    if capability_mode == "shadow" and authority_ceiling == "block":
        authority_ceiling = "advise"
    return _MergedRole(
        capability_ids=sorted(capability_ids),
        primary_dimensions=union("primary_dimensions"),
        in_scope=union("in_scope"),
        out_of_scope=union("out_of_scope"),
        blocking_authority=list(
            resolve_capability_ids(registry, union("blocking_authority"))
        ),
        authority_ceiling=authority_ceiling,
        required_evidence=sorted(evidence),
        forbidden_actions=union("forbidden_actions"),
        provider_constraints=union("provider_constraints"),
        isolation_requirements=union("isolation_requirements"),
        cost_ceiling=min(item.cost_ceiling for item in modules),
    )


def _verify_merged_boundaries(
    merged: _MergedRole,
    capability_ids: set[str],
    by_id: dict[str, CapabilityDefinition],
    policy: ReviewerSelectionPolicy,
    capability_mode: CapabilityConsumptionMode,
) -> None:
    conflict = set(merged.in_scope) & set(merged.out_of_scope)
    if conflict:
        raise ValueError(f"reviewer role scope conflict: {sorted(conflict)}")
    constraints = set(merged.provider_constraints) | set(merged.isolation_requirements)
    for left, right in policy.constraint_conflicts:
        if {left, right} <= constraints:
            raise ValueError(f"reviewer role constraint conflict: {left}, {right}")
    blocking = set(merged.blocking_authority)
    if capability_mode == "shadow" and blocking:
        raise ValueError("shadow reviewer role cannot hold blocking authority")
    if blocking - capability_ids:
        raise ValueError("reviewer role blocking authority is not owned by capability")
    active_registry_grants = {
        item
        for item in capability_ids
        if by_id[item].maturity == "active"
        and by_id[item].authority_ceiling == "block"
    }
    policy_grants = set(policy.allowed_blocking_authority_ids)
    if blocking - policy_grants:
        raise ValueError("reviewer role blocking authority is not in policy allowlist")
    if blocking - active_registry_grants or (
        blocking and merged.authority_ceiling != "block"
    ):
        raise ValueError("reviewer role blocking authority exceeds active capability")


def _role_contract_fields(
    role_profile_id: str,
    version: str,
    capability_mode: CapabilityConsumptionMode,
    modules: list[ReviewerRoleModule],
    merged: _MergedRole,
    registry: ReviewerCapabilityRegistry,
    policy: ReviewerSelectionPolicy,
) -> dict[str, object]:
    bindings = tuple(
        RoleModuleBinding(
            module_id=item.module_id,
            version=item.version,
            module_kind=item.module_kind,
            module_digest=item.module_digest,
        )
        for item in modules
    )
    return {
        "role_profile_id": role_profile_id,
        "version": version,
        "capability_mode": capability_mode,
        "registry_digest": registry.registry_digest,
        "policy_digest": policy.policy_digest,
        "source_profile_ids": tuple(
            item.module_id for item in modules if item.module_kind == "base"
        ),
        "source_module_ids": tuple(
            sorted(item.module_id for item in modules if item.module_kind != "base")
        ),
        "source_module_bindings": bindings,
        "capability_ids": tuple(merged.capability_ids),
        "primary_dimensions": tuple(merged.primary_dimensions),
        "in_scope": tuple(merged.in_scope),
        "out_of_scope": tuple(merged.out_of_scope),
        "blocking_authority": tuple(merged.blocking_authority),
        "authority_ceiling": merged.authority_ceiling,
        "required_evidence": tuple(merged.required_evidence),
        "forbidden_actions": tuple(merged.forbidden_actions),
        "provider_constraints": tuple(merged.provider_constraints),
        "isolation_requirements": tuple(merged.isolation_requirements),
        "cost_ceiling": merged.cost_ceiling,
        "merge_semantics_version": policy.merge_semantics_version,
    }
