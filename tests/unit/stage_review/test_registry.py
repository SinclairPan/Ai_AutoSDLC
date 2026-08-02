from __future__ import annotations

from datetime import date
from itertools import permutations
from typing import cast

import pytest
from pydantic import ValidationError

from ai_sdlc.core.stage_review.registry import (
    CapabilityDefinition,
    ReviewerCapabilityRegistry,
    ReviewerRoleContract,
    ReviewerRoleModule,
    ReviewerSelectionPolicy,
    build_capability_registry,
    build_role_module,
    build_selection_policy,
    default_registry_bundle,
    merge_role_modules,
    read_role_contract,
    role_contract_digest,
    validate_registry_bundle,
)


def test_default_registry_bundle_is_valid_and_machine_addressable() -> None:
    bundle = default_registry_bundle()

    assert isinstance(bundle.registry, ReviewerCapabilityRegistry)
    assert isinstance(bundle.policy, ReviewerSelectionPolicy)
    assert bundle.registry.registry_digest.startswith("sha256:")
    assert bundle.policy.policy_digest.startswith("sha256:")
    assert len(bundle.role_modules) >= 2
    assert all(" " not in item.capability_id for item in bundle.registry.capabilities)
    roles = [
        merge_role_modules(
            role_profile_id=module.module_id,
            version=module.version,
            modules=[module],
            registry=bundle.registry,
            policy=bundle.policy,
            module_catalog=bundle.role_modules,
        )
        for module in bundle.role_modules
    ]
    assert len(roles) == len(bundle.role_modules)


def test_registry_rejects_free_text_identity_and_missing_governance() -> None:
    with pytest.raises(ValidationError):
        _cap("Security Reviewer")
    with pytest.raises(ValidationError):
        _cap("capability.security", owner="")
    with pytest.raises(ValidationError):
        _cap("capability.security", review_date="20-07-2026")


def test_registry_rejects_dependency_cycle_and_conflict_closure() -> None:
    with pytest.raises(ValueError, match="cycle"):
        build_capability_registry(
            registry_id="registry.test",
            registry_version="1.0.0",
            capabilities=[
                _cap("capability.a", implies=["capability.b"]),
                _cap("capability.b", implies=["capability.a"]),
            ],
        )
    with pytest.raises(ValueError, match="conflict"):
        build_capability_registry(
            registry_id="registry.test",
            registry_version="1.0.0",
            capabilities=[
                _cap("capability.a", implies=["capability.b"]),
                _cap("capability.b", conflicts=["capability.a"]),
            ],
        )


def test_registry_rejects_invalid_supersession_and_compatibility_chain() -> None:
    with pytest.raises(ValueError, match="superseded"):
        build_capability_registry(
            registry_id="registry.test",
            registry_version="1.0.0",
            capabilities=[
                _cap(
                    "capability.old",
                    maturity="deprecated",
                    superseded_by="capability.missing",
                )
            ],
        )
    with pytest.raises(ValueError, match="compatibility"):
        build_capability_registry(
            registry_id="registry.test",
            registry_version="1.0.0",
            capabilities=[
                _cap("capability.future", compatibility_range=">=2.0.0,<3.0.0")
            ],
        )


def test_registry_rejects_asymmetric_conflicts_and_duplicate_identity() -> None:
    with pytest.raises(ValueError, match="symmetric"):
        build_capability_registry(
            registry_id="registry.test",
            registry_version="1.0.0",
            capabilities=[
                _cap("capability.a", conflicts=["capability.b"]),
                _cap("capability.b"),
            ],
        )
    with pytest.raises(ValueError, match="duplicate"):
        build_capability_registry(
            registry_id="registry.test",
            registry_version="1.0.0",
            capabilities=[_cap("capability.a"), _cap("capability.a")],
        )


def test_role_merge_is_commutative_and_uses_strict_boundaries() -> None:
    registry = _registry(
        _cap(
            "capability.correctness",
            authority_ceiling="block",
            required_evidence_types=["evidence.test"],
        ),
        _cap(
            "capability.delivery",
            authority_ceiling="advise",
            required_evidence_types=["evidence.install"],
        ),
    )
    policy = _policy()
    base = _module(
        "role.base",
        module_kind="base",
        capability_ids=["capability.correctness"],
        authority_ceiling="block",
        cost_ceiling=5,
        in_scope=["scope.source"],
    )
    stage = _module(
        "module.delivery",
        module_kind="stage",
        capability_ids=["capability.delivery"],
        authority_ceiling="advise",
        cost_ceiling=3,
        required_evidence=["evidence.package"],
    )

    first = merge_role_modules(
        role_profile_id="role.delivery",
        version="1.0.0",
        modules=[base, stage],
        registry=registry,
        policy=policy,
        module_catalog=[base, stage],
    )
    second = merge_role_modules(
        role_profile_id="role.delivery",
        version="1.0.0",
        modules=[stage, base],
        registry=registry,
        policy=policy,
        module_catalog=[base, stage],
    )

    assert isinstance(first, ReviewerRoleContract)
    assert first.role_contract_digest == second.role_contract_digest
    assert first.capability_ids == (
        "capability.correctness",
        "capability.delivery",
    )
    assert first.required_evidence == (
        "evidence.install",
        "evidence.package",
        "evidence.test",
    )
    assert first.cost_ceiling == 3
    assert first.authority_ceiling == "advise"
    assert first.role_contract_digest.startswith("sha256:")


def test_role_merge_rejects_scope_and_constraint_conflicts() -> None:
    registry = _registry(_cap("capability.correctness"))
    policy = _policy(
        constraint_conflicts=[("provider.local", "provider.remote")]
    )
    base = _module(
        "role.base",
        module_kind="base",
        capability_ids=["capability.correctness"],
        in_scope=["scope.source"],
        provider_constraints=["provider.local"],
    )
    conflicting = _module(
        "module.conflict",
        module_kind="risk",
        out_of_scope=["scope.source"],
        provider_constraints=["provider.remote"],
    )

    with pytest.raises(ValueError, match="scope conflict"):
        merge_role_modules(
            role_profile_id="role.conflict",
            version="1.0.0",
            modules=[base, conflicting],
            registry=registry,
            policy=policy,
            module_catalog=[base, conflicting],
        )

    provider_conflict = build_role_module(
        **{
            **conflicting.model_dump(mode="python", exclude={"module_digest"}),
            "out_of_scope": [],
        }
    )
    with pytest.raises(ValueError, match="constraint conflict"):
        merge_role_modules(
            role_profile_id="role.conflict",
            version="1.0.0",
            modules=[base, provider_conflict],
            registry=registry,
            policy=policy,
            module_catalog=[base, provider_conflict],
        )


def test_role_merge_rejects_blocking_authority_escalation() -> None:
    registry = _registry(
        _cap("capability.correctness", authority_ceiling="advise")
    )
    module = _module(
        "role.base",
        module_kind="base",
        capability_ids=["capability.correctness"],
        blocking_authority=["capability.correctness"],
        authority_ceiling="block",
    )

    with pytest.raises(ValueError, match="blocking authority"):
        merge_role_modules(
            role_profile_id="role.escalated",
            version="1.0.0",
            modules=[module],
            registry=registry,
            policy=_policy(),
            module_catalog=[module],
        )


def test_role_merge_rejects_unknown_or_policy_disabled_module() -> None:
    registry = _registry(_cap("capability.correctness"))
    unknown = _module(
        "role.base",
        module_kind="base",
        capability_ids=["capability.unknown"],
    )
    with pytest.raises(ValueError, match="unknown capability"):
        merge_role_modules(
            role_profile_id="role.unknown",
            version="1.0.0",
            modules=[unknown],
            registry=registry,
            policy=_policy(),
            module_catalog=[unknown],
        )

    valid = _module(
        "role.base",
        module_kind="base",
        capability_ids=["capability.correctness"],
    )
    with pytest.raises(ValueError, match="not enabled"):
        merge_role_modules(
            role_profile_id="role.disabled",
            version="1.0.0",
            modules=[valid],
            registry=registry,
            policy=_policy(enabled_module_ids=["module.other"]),
            module_catalog=[valid],
        )


def test_role_merge_rejects_same_module_id_with_different_content() -> None:
    registry = _registry(_cap("capability.correctness"))
    first = _module(
        "role.base",
        module_kind="base",
        capability_ids=["capability.correctness"],
        cost_ceiling=3,
    )
    second = first.model_copy(update={"cost_ceiling": 4})

    with pytest.raises(ValueError, match="conflicting module identity"):
        merge_role_modules(
            role_profile_id="role.duplicate",
            version="1.0.0",
            modules=[first, second],
            registry=registry,
            policy=_policy(),
            module_catalog=[first],
        )


def test_role_merge_requires_exactly_one_base_profile() -> None:
    registry = _registry(_cap("capability.correctness"))
    first = _module(
        "role.first",
        module_kind="base",
        capability_ids=["capability.correctness"],
    )
    second = _module(
        "role.second",
        module_kind="base",
        capability_ids=["capability.correctness"],
    )
    with pytest.raises(ValueError, match="exactly one base"):
        merge_role_modules(
            role_profile_id="role.ambiguous",
            version="1.0.0",
            modules=[first, second],
            registry=registry,
            policy=_policy(),
            module_catalog=[first, second],
        )


def test_role_merge_policy_cannot_grant_unowned_capability_authority() -> None:
    registry = _registry(
        _cap("capability.correctness", authority_ceiling="block"),
        _cap("capability.security", authority_ceiling="block"),
    )
    module = _module(
        "role.base",
        module_kind="base",
        capability_ids=["capability.correctness"],
        blocking_authority=["capability.security"],
        authority_ceiling="block",
    )
    with pytest.raises(ValueError, match="not owned"):
        merge_role_modules(
            role_profile_id="role.escalated",
            version="1.0.0",
            modules=[module],
            registry=registry,
            policy=_policy(
                allowed_blocking_authority_ids=["capability.security"]
            ),
            module_catalog=[module],
        )


def test_registry_requires_nonempty_stage_and_risk_applicability() -> None:
    with pytest.raises(ValueError, match="applicability"):
        _registry(_cap("capability.empty", applicable_stage=[]))
    with pytest.raises(ValueError, match="applicability"):
        _registry(_cap("capability.empty", applicable_risk=[]))


def test_registry_policy_and_role_digests_are_fail_closed() -> None:
    registry = _registry(_cap("capability.correctness"))
    policy = _policy()
    module = _module(
        "role.base",
        module_kind="base",
        capability_ids=["capability.correctness"],
    )
    role = merge_role_modules(
        role_profile_id="role.digest",
        version="1.0.0",
        modules=[module],
        registry=registry,
        policy=policy,
        module_catalog=[module],
    )

    with pytest.raises(ValidationError, match="registry digest"):
        ReviewerCapabilityRegistry.model_validate(
            {**registry.model_dump(mode="json"), "registry_version": "1.0.1"}
        )
    with pytest.raises(ValidationError, match="policy digest"):
        ReviewerSelectionPolicy.model_validate(
            {**policy.model_dump(mode="json"), "version": "1.0.1"}
        )
    with pytest.raises(ValidationError, match="role contract digest"):
        ReviewerRoleContract.model_validate(
            {**role.model_dump(mode="json"), "cost_ceiling": 9}
        )


def test_blocking_authority_requires_registry_and_policy_grant() -> None:
    registry = _registry(
        _cap("capability.security", authority_ceiling="block")
    )
    module = _module(
        "role.security",
        module_kind="base",
        capability_ids=["capability.security"],
        blocking_authority=["capability.security"],
        authority_ceiling="block",
    )

    with pytest.raises(ValueError, match="policy allowlist"):
        merge_role_modules(
            role_profile_id="role.security",
            version="1.0.0",
            modules=[module],
            registry=registry,
            policy=_policy(),
            module_catalog=[module],
        )


def test_shadow_capability_cannot_grant_blocking_authority() -> None:
    registry = _registry(
        _cap(
            "capability.security",
            maturity="shadow",
            authority_ceiling="block",
        )
    )
    module = _module(
        "role.security",
        module_kind="base",
        capability_ids=["capability.security"],
        blocking_authority=["capability.security"],
        authority_ceiling="block",
    )

    with pytest.raises(ValueError, match="active capability"):
        merge_role_modules(
            role_profile_id="role.security",
            version="1.0.0",
            modules=[module],
            registry=registry,
            policy=_policy(
                allowed_blocking_authority_ids=["capability.security"]
            ),
            module_catalog=[module],
        )


def test_deprecated_capability_resolves_to_non_weaker_active_successor() -> None:
    old = _cap(
        "capability.old",
        maturity="deprecated",
        superseded_by="capability.new",
        required_evidence_types=["evidence.test"],
    )
    new = _cap(
        "capability.new",
        required_evidence_types=["evidence.test", "evidence.runtime"],
    )
    registry = _registry(old, new)
    module = _module(
        "role.base",
        module_kind="base",
        capability_ids=["capability.old"],
    )
    role = merge_role_modules(
        role_profile_id="role.compatible",
        version="1.0.0",
        modules=[module],
        registry=registry,
        policy=_policy(),
        module_catalog=[module],
    )

    assert role.capability_ids == ("capability.new",)


def test_registry_rejects_shadow_or_weaker_supersession() -> None:
    old = _cap(
        "capability.old",
        maturity="deprecated",
        superseded_by="capability.new",
        authority_ceiling="block",
        required_evidence_types=["evidence.security"],
    )
    with pytest.raises(ValueError, match="active successor"):
        _registry(old, _cap("capability.new", maturity="shadow"))
    with pytest.raises(ValueError, match="weaker successor"):
        _registry(old, _cap("capability.new", authority_ceiling="advise"))


def test_supersession_cannot_drop_resolved_conflicts() -> None:
    old = _cap(
        "capability.old",
        maturity="deprecated",
        superseded_by="capability.new",
        conflicts=["capability.guard"],
    )
    guard = _cap("capability.guard", conflicts=["capability.old"])

    with pytest.raises(ValueError, match="weaker successor"):
        _registry(old, _cap("capability.new"), guard)


def test_supersession_compares_dependencies_after_resolution() -> None:
    registry = _registry(
        _cap(
            "capability.old-dependency",
            maturity="deprecated",
            superseded_by="capability.new-dependency",
        ),
        _cap("capability.new-dependency"),
        _cap(
            "capability.old-parent",
            maturity="deprecated",
            superseded_by="capability.new-parent",
            implies=["capability.old-dependency"],
        ),
        _cap("capability.new-parent", implies=["capability.new-dependency"]),
    )

    module = _module(
        "role.base",
        module_kind="base",
        capability_ids=["capability.old-parent"],
    )
    role = merge_role_modules(
        role_profile_id="role.migrated",
        version="1.0.0",
        modules=[module],
        registry=registry,
        policy=_policy(),
        module_catalog=[module],
    )

    assert role.capability_ids == (
        "capability.new-dependency",
        "capability.new-parent",
    )


def test_registry_rejects_conflict_created_by_resolved_dependency_graph() -> None:
    with pytest.raises(ValueError, match="conflict closure"):
        _registry(
            _cap(
                "capability.parent",
                implies=["capability.old"],
                conflicts=["capability.new"],
            ),
            _cap(
                "capability.old",
                maturity="deprecated",
                superseded_by="capability.new",
            ),
            _cap("capability.new", conflicts=["capability.parent"]),
        )


def test_base_stage_risk_merge_replays_for_every_input_order() -> None:
    registry = _registry(_cap("capability.correctness"))
    policy = _policy()
    modules = [
        _module(
            "role.base",
            module_kind="base",
            capability_ids=["capability.correctness"],
        ),
        _module("module.plugin", module_kind="stage"),
        _module("module.risk", module_kind="risk"),
    ]

    roles = [
        merge_role_modules(
            role_profile_id="role.composed",
            version="1.0.0",
            modules=list(order),
            registry=registry,
            policy=policy,
            module_catalog=modules,
        )
        for order in permutations(modules)
    ]

    assert len({item.role_contract_digest for item in roles}) == 1
    assert roles[0].source_module_ids == ("module.plugin", "module.risk")


def test_shadow_module_is_registered_but_requires_explicit_shadow_role() -> None:
    registry = _registry(_cap("capability.future", maturity="shadow"))
    module = _module(
        "role.future",
        module_kind="base",
        capability_ids=["capability.future"],
    )
    policy = _policy(enabled_module_ids=["role.future"])

    validate_registry_bundle(
        registry=registry,
        policy=policy,
        module_catalog=[module],
    )
    with pytest.raises(ValueError, match="active capability"):
        merge_role_modules(
            role_profile_id="role.future",
            version="1.0.0",
            modules=[module],
            registry=registry,
            policy=policy,
            module_catalog=[module],
        )
    role = merge_role_modules(
        role_profile_id="role.future",
        version="1.0.0",
        modules=[module],
        registry=registry,
        policy=policy,
        module_catalog=[module],
        capability_mode="shadow",
    )

    assert role.capability_mode == "shadow"
    assert role.blocking_authority == ()
    assert role.authority_ceiling != "block"


def test_role_module_is_content_addressed_and_deeply_frozen() -> None:
    module = _module(
        "role.base",
        module_kind="base",
        capability_ids=["capability.correctness"],
    )
    assert module.module_digest.startswith("sha256:")
    with pytest.raises(AttributeError):
        module.capability_ids.append("capability.other")  # type: ignore[attr-defined]
    altered = module.model_copy(update={"cost_ceiling": 9})
    with pytest.raises(ValueError, match="module digest"):
        merge_role_modules(
            role_profile_id="role.changed",
            version="1.0.0",
            modules=[altered],
            registry=_registry(_cap("capability.correctness")),
            policy=_policy(),
            module_catalog=[altered],
        )


def test_role_contract_requires_bound_reader_context() -> None:
    registry = _registry(_cap("capability.correctness"))
    policy = _policy()
    module = _module(
        "role.base",
        module_kind="base",
        capability_ids=["capability.correctness"],
    )
    role = merge_role_modules(
        role_profile_id="role.bound",
        version="1.0.0",
        modules=[module],
        registry=registry,
        policy=policy,
        module_catalog=[module],
    )
    payload = role.model_dump(mode="json")

    with pytest.raises(ValidationError, match="registry context"):
        ReviewerRoleContract.model_validate(payload)
    assert read_role_contract(
        payload,
        registry=registry,
        policy=policy,
        module_catalog=[module],
    ) == role
    draft = role.model_copy(
        update={
            "in_scope": ("scope.source",),
            "out_of_scope": ("scope.source",),
            "role_contract_digest": "",
        }
    )
    contradictory = draft.model_dump(mode="json")
    contradictory["role_contract_digest"] = role_contract_digest(draft)
    with pytest.raises(ValidationError, match="scope conflict"):
        read_role_contract(
            contradictory,
            registry=registry,
            policy=policy,
            module_catalog=[module],
        )


def test_bundle_validator_rejects_policy_dangling_references() -> None:
    registry = _registry(
        _cap("capability.correctness"),
        _cap("capability.security", authority_ceiling="block"),
    )
    module = _module(
        "role.base",
        module_kind="base",
        capability_ids=["capability.correctness"],
    )
    with pytest.raises(ValueError, match="unknown blocking authority"):
        validate_registry_bundle(
            registry=registry,
            policy=_policy(
                allowed_blocking_authority_ids=["capability.missing"]
            ),
            module_catalog=[module],
        )
    with pytest.raises(ValueError, match="unknown enabled module"):
        validate_registry_bundle(
            registry=registry,
            policy=_policy(enabled_module_ids=["module.missing"]),
            module_catalog=[module],
        )


def _cap(
    capability_id: str,
    **updates: object,
) -> CapabilityDefinition:
    values: dict[str, object] = {
        "capability_id": capability_id,
        "version": "1.0.0",
        "applicable_stage": ["implementation"],
        "applicable_risk": ["low", "medium", "high", "critical"],
        "authority_ceiling": "advise",
        "required_evidence_types": [],
        "maturity": "active",
        "compatibility_range": ">=1.0.0,<2.0.0",
        "owner": "ai-sdlc",
        "review_date": date.today().isoformat(),
    }
    values.update(updates)
    return CapabilityDefinition.model_validate(values)


def _registry(*capabilities: CapabilityDefinition) -> ReviewerCapabilityRegistry:
    return build_capability_registry(
        registry_id="registry.test",
        registry_version="1.0.0",
        capabilities=list(capabilities),
    )


def _policy(**updates: object) -> ReviewerSelectionPolicy:
    values: dict[str, object] = {
        "policy_id": "policy.test",
        "version": "1.0.0",
        "registry_compatibility_range": ">=1.0.0,<2.0.0",
        "merge_semantics_version": "role-merge/v1",
        "owner": "ai-sdlc",
        "review_date": date.today().isoformat(),
    }
    values.update(updates)
    return build_selection_policy(
        policy_id=cast(str, values["policy_id"]),
        version=cast(str, values["version"]),
        registry_compatibility_range=cast(
            str, values["registry_compatibility_range"]
        ),
        merge_semantics_version=cast(str, values["merge_semantics_version"]),
        allowed_blocking_authority_ids=cast(
            list[str], values.get("allowed_blocking_authority_ids", [])
        ),
        enabled_module_ids=cast(list[str], values.get("enabled_module_ids", [])),
        constraint_conflicts=cast(
            list[tuple[str, str]], values.get("constraint_conflicts", [])
        ),
        owner=cast(str, values["owner"]),
        review_date=cast(str, values["review_date"]),
    )


def _module(
    module_id: str,
    *,
    module_kind: str,
    **updates: object,
) -> ReviewerRoleModule:
    values: dict[str, object] = {
        "module_id": module_id,
        "version": "1.0.0",
        "module_kind": module_kind,
        "authority_ceiling": "advise",
        "cost_ceiling": 10,
        "merge_semantics_version": "role-merge/v1",
        "compatibility_range": ">=1.0.0,<2.0.0",
        "owner": "ai-sdlc",
        "review_date": date.today().isoformat(),
    }
    values.update(updates)
    return build_role_module(**values)
