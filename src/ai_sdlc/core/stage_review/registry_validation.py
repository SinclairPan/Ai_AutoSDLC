"""Reviewer Registry 结构、治理引用与 Role 血缘校验。"""

from __future__ import annotations

from collections.abc import Sequence

from ai_sdlc.core.stage_review.capability_graph import (
    build_canonical_graph,
    capabilities_by_id,
    direct_dependencies,
    expand_capability_ids,
    resolve_capability_ids,
    resolved_dependency_closure,
    resolved_reference_ids,
    verify_acyclic,
)
from ai_sdlc.core.stage_review.registry_digests import (
    registry_digest,
    role_module_digest,
    selection_policy_digest,
)
from ai_sdlc.core.stage_review.registry_models import (
    CapabilityDefinition,
    ReviewerCapabilityRegistry,
    ReviewerRoleModule,
    ReviewerSelectionPolicy,
)
from ai_sdlc.core.stage_review.registry_versions import version_in_range
from ai_sdlc.core.stage_review.role_contract_models import ReviewerRoleContract


def verify_registry_artifact(registry: ReviewerCapabilityRegistry) -> None:
    """验证图、治理、兼容链和内容摘要。"""

    if not registry.capabilities:
        raise ValueError("capability registry cannot be empty")
    by_id = capabilities_by_id(registry.capabilities)
    _verify_capability_references(by_id)
    dependencies = {
        item.capability_id: direct_dependencies(item) for item in by_id.values()
    }
    verify_acyclic(dependencies, label="capability dependency cycle")
    _verify_supersession(by_id)
    build_canonical_graph(by_id, allow_shadow=False)
    build_canonical_graph(by_id, allow_shadow=True)
    _verify_capability_governance(registry, by_id)
    if registry.registry_digest != registry_digest(registry):
        raise ValueError("capability registry digest does not match content")


def verify_policy_artifact(policy: ReviewerSelectionPolicy) -> None:
    """验证 Policy 的静态摘要。"""

    if policy.policy_digest != selection_policy_digest(policy):
        raise ValueError("reviewer selection policy digest does not match content")


def validate_registry_bundle(
    *,
    registry: ReviewerCapabilityRegistry,
    policy: ReviewerSelectionPolicy,
    module_catalog: Sequence[ReviewerRoleModule],
) -> None:
    """整体校验 Registry、Policy 与 Module Catalog 的引用闭包。"""

    verify_registry_artifact(registry)
    verify_policy_artifact(policy)
    if not version_in_range(
        registry.registry_version,
        policy.registry_compatibility_range,
    ):
        raise ValueError("selection policy is incompatible with capability registry")
    by_capability = capabilities_by_id(registry.capabilities)
    _verify_policy_authorities(policy, by_capability)
    _verify_policy_requirements(policy, by_capability)
    by_module = _verified_module_catalog(module_catalog, registry, policy)
    missing = sorted(set(policy.enabled_module_ids) - set(by_module))
    if missing:
        raise ValueError(f"unknown enabled module: {missing}")


def verify_role_contract_lineage(
    contract: ReviewerRoleContract,
    *,
    registry: ReviewerCapabilityRegistry,
    policy: ReviewerSelectionPolicy,
    module_catalog: Sequence[ReviewerRoleModule],
) -> None:
    """验证 Role 绑定摘要，并用绑定模块重演全部语义字段。"""

    from ai_sdlc.core.stage_review.role_merge import derive_role_contract_fields

    validate_registry_bundle(
        registry=registry,
        policy=policy,
        module_catalog=module_catalog,
    )
    if contract.registry_digest != registry.registry_digest:
        raise ValueError("reviewer role registry digest does not match context")
    if contract.policy_digest != policy.policy_digest:
        raise ValueError("reviewer role policy digest does not match context")
    selected = _bound_modules(contract, module_catalog)
    expected = derive_role_contract_fields(
        role_profile_id=contract.role_profile_id,
        version=contract.version,
        capability_mode=contract.capability_mode,
        modules=selected,
        registry=registry,
        policy=policy,
    )
    mismatched = [
        name for name, value in expected.items() if getattr(contract, name) != value
    ]
    if mismatched:
        raise ValueError(f"reviewer role lineage mismatch: {sorted(mismatched)}")


def _bound_modules(
    contract: ReviewerRoleContract,
    module_catalog: Sequence[ReviewerRoleModule],
) -> list[ReviewerRoleModule]:
    by_module = {item.module_id: item for item in module_catalog}
    selected: list[ReviewerRoleModule] = []
    for binding in contract.source_module_bindings:
        module = by_module.get(binding.module_id)
        if module is None:
            raise ValueError(f"reviewer role references unknown module: {binding.module_id}")
        if (
            binding.version != module.version
            or binding.module_kind != module.module_kind
            or binding.module_digest != module.module_digest
        ):
            raise ValueError(f"reviewer role module binding mismatch: {binding.module_id}")
        selected.append(module)
    return selected


def _verify_capability_references(
    by_id: dict[str, CapabilityDefinition],
) -> None:
    known = set(by_id)
    for item in by_id.values():
        refs = direct_dependencies(item) | set(item.conflicts)
        if item.superseded_by:
            refs.add(item.superseded_by)
        unknown = sorted(refs - known)
        if unknown:
            label = "superseded" if item.superseded_by in unknown else "unknown"
            raise ValueError(
                f"{label} capability reference for {item.capability_id}: {unknown}"
            )
        if item.capability_id in refs:
            raise ValueError(f"capability cannot reference itself: {item.capability_id}")


def _verify_capability_governance(
    registry: ReviewerCapabilityRegistry,
    by_id: dict[str, CapabilityDefinition],
) -> None:
    for item in by_id.values():
        if not item.applicable_stage or not item.applicable_risk:
            raise ValueError(
                f"capability applicability cannot be empty: {item.capability_id}"
            )
        if not version_in_range(registry.contract_version, item.compatibility_range):
            raise ValueError(
                f"capability compatibility excludes registry contract: {item.capability_id}"
            )


def _verify_supersession(by_id: dict[str, CapabilityDefinition]) -> None:
    graph: dict[str, set[str]] = {}
    for item in by_id.values():
        if item.maturity == "deprecated" and not item.superseded_by:
            raise ValueError(
                f"deprecated capability requires superseded_by: {item.capability_id}"
            )
        if item.maturity != "deprecated" and item.superseded_by:
            raise ValueError("only deprecated capability may declare superseded_by")
        graph[item.capability_id] = (
            {item.superseded_by} if item.superseded_by else set()
        )
    verify_acyclic(graph, label="capability superseded cycle")
    for item in by_id.values():
        if item.maturity == "deprecated":
            _verify_supersession_target(item, by_id)


def _verify_supersession_target(
    previous: CapabilityDefinition,
    by_id: dict[str, CapabilityDefinition],
) -> None:
    successor_id = resolved_reference_ids(
        [previous.superseded_by],
        by_id=by_id,
        allow_shadow=True,
    )[0]
    successor = by_id[successor_id]
    if successor.maturity != "active":
        raise ValueError(
            f"deprecated capability requires active successor: {previous.capability_id}"
        )
    _verify_non_weaker_successor(previous, successor, by_id)


def _verify_non_weaker_successor(
    previous: CapabilityDefinition,
    successor: CapabilityDefinition,
    by_id: dict[str, CapabilityDefinition],
) -> None:
    authority_order = {"observe": 0, "advise": 1, "block": 2}
    previous_conflicts = set(
        resolved_reference_ids(previous.conflicts, by_id=by_id, allow_shadow=True)
    )
    successor_conflicts = set(
        resolved_reference_ids(successor.conflicts, by_id=by_id, allow_shadow=True)
    )
    weaker = (
        not set(previous.applicable_stage) <= set(successor.applicable_stage)
        or not set(previous.applicable_risk) <= set(successor.applicable_risk)
        or not set(previous.required_evidence_types)
        <= set(successor.required_evidence_types)
        or not resolved_dependency_closure(previous, by_id=by_id)
        <= resolved_dependency_closure(successor, by_id=by_id)
        or not previous_conflicts <= successor_conflicts
        or authority_order[successor.authority_ceiling]
        < authority_order[previous.authority_ceiling]
    )
    if weaker:
        raise ValueError(
            f"deprecated capability has weaker successor: {previous.capability_id}"
        )


def _verify_policy_authorities(
    policy: ReviewerSelectionPolicy,
    by_capability: dict[str, CapabilityDefinition],
) -> None:
    for capability_id in policy.allowed_blocking_authority_ids:
        item = by_capability.get(capability_id)
        if item is None:
            raise ValueError(f"unknown blocking authority: {capability_id}")
        if item.maturity != "active" or item.authority_ceiling != "block":
            raise ValueError(f"blocking authority is not active and blocking: {capability_id}")


def _verify_policy_requirements(
    policy: ReviewerSelectionPolicy,
    by_capability: dict[str, CapabilityDefinition],
) -> None:
    by_rule: dict[str, object] = {}
    for rule in policy.capability_requirement_rules:
        if rule.rule_id in by_rule:
            raise ValueError(f"duplicate capability requirement rule: {rule.rule_id}")
        by_rule[rule.rule_id] = rule
        if not rule.stage_keys or not rule.risk_levels or not rule.capability_ids:
            raise ValueError(f"empty capability requirement rule: {rule.rule_id}")
        unknown = sorted(set(rule.capability_ids) - set(by_capability))
        if unknown:
            raise ValueError(f"unknown capability requirement: {unknown}")
        invalid = sorted(
            capability_id
            for capability_id in rule.capability_ids
            if by_capability[capability_id].maturity != "active"
        )
        if invalid:
            raise ValueError(f"inactive capability requirement: {invalid}")


def _verified_module_catalog(
    module_catalog: Sequence[ReviewerRoleModule],
    registry: ReviewerCapabilityRegistry,
    policy: ReviewerSelectionPolicy,
) -> dict[str, ReviewerRoleModule]:
    by_module: dict[str, ReviewerRoleModule] = {}
    by_capability = capabilities_by_id(registry.capabilities)
    for module in module_catalog:
        _verify_module_identity(module, by_module, registry, policy)
        capabilities = set(
            expand_capability_ids(registry, module.capability_ids, allow_shadow=True)
        )
        _verify_module_authority(module, capabilities, registry, policy, by_capability)
        if set(module.in_scope) & set(module.out_of_scope):
            raise ValueError(f"reviewer role scope conflict: {module.module_id}")
        by_module[module.module_id] = module
    return by_module


def _verify_module_identity(
    module: ReviewerRoleModule,
    by_module: dict[str, ReviewerRoleModule],
    registry: ReviewerCapabilityRegistry,
    policy: ReviewerSelectionPolicy,
) -> None:
    if module.module_digest != role_module_digest(module):
        raise ValueError(f"reviewer role module digest mismatch: {module.module_id}")
    if module.module_id in by_module:
        raise ValueError(f"duplicate module identity: {module.module_id}")
    if module.merge_semantics_version != policy.merge_semantics_version:
        raise ValueError("role module merge semantics are incompatible with policy")
    if not version_in_range(registry.registry_version, module.compatibility_range):
        raise ValueError("role module compatibility excludes capability registry")


def _verify_module_authority(
    module: ReviewerRoleModule,
    capabilities: set[str],
    registry: ReviewerCapabilityRegistry,
    policy: ReviewerSelectionPolicy,
    by_capability: dict[str, CapabilityDefinition],
) -> None:
    blocking = set(module.blocking_authority)
    if blocking - set(module.capability_ids):
        raise ValueError("reviewer role blocking authority is not owned by capability")
    resolved = set(resolve_capability_ids(registry, blocking, allow_shadow=True))
    if resolved - capabilities:
        raise ValueError("reviewer role blocking authority is not owned by capability")
    if resolved - set(policy.allowed_blocking_authority_ids):
        raise ValueError("reviewer role blocking authority is not in policy allowlist")
    if any(
        by_capability[item].maturity != "active"
        or by_capability[item].authority_ceiling != "block"
        for item in resolved
    ):
        raise ValueError("reviewer role blocking authority exceeds registry")
