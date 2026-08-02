"""随 AI-SDLC 1.0.0 分发的最小 Reviewer Registry 基线。"""

from __future__ import annotations

from dataclasses import dataclass

from ai_sdlc.core.stage_review.registry import (
    build_capability_registry,
    build_role_module,
    build_selection_policy,
    validate_registry_bundle,
)
from ai_sdlc.core.stage_review.registry_models import (
    CapabilityDefinition,
    ReviewerCapabilityRegistry,
    ReviewerRoleModule,
    ReviewerSelectionPolicy,
)

_STAGES = [
    "requirement",
    "design-contract",
    "implementation",
    "frontend-evidence",
    "local-pr-review",
]
_RISKS = ["low", "medium", "high", "critical"]
_REVIEW_DATE = "2026-07-20"


@dataclass(frozen=True, slots=True)
class ReviewerRegistryBundle:
    registry: ReviewerCapabilityRegistry
    policy: ReviewerSelectionPolicy
    role_modules: tuple[ReviewerRoleModule, ...]


def build_default_bundle() -> ReviewerRegistryBundle:
    capabilities = _default_capabilities()
    modules = _default_modules()
    registry = build_capability_registry(
        registry_id="registry.ai-sdlc-default",
        registry_version="1.0.0",
        capabilities=capabilities,
    )
    policy = _default_policy(modules)
    bundle = ReviewerRegistryBundle(
        registry=registry,
        policy=policy,
        role_modules=modules,
    )
    validate_registry_bundle(
        registry=bundle.registry,
        policy=bundle.policy,
        module_catalog=bundle.role_modules,
    )
    return bundle


def _default_capabilities() -> tuple[CapabilityDefinition, ...]:
    return (
        _capability(
            "capability.correctness",
            evidence=["evidence.test", "evidence.contract"],
        ),
        _capability(
            "capability.delivery-operability",
            evidence=["evidence.install", "evidence.runtime"],
        ),
        _capability(
            "capability.evolution-architecture",
            evidence=["evidence.architecture", "evidence.compatibility"],
        ),
        _capability(
            "capability.security",
            authority_ceiling="block",
            risks=["medium", "high", "critical"],
            evidence=["evidence.security", "evidence.isolation"],
        ),
        _capability(
            "capability.data-integrity",
            authority_ceiling="block",
            risks=["medium", "high", "critical"],
            evidence=["evidence.data-integrity", "evidence.recovery"],
        ),
        _capability(
            "capability.user-journey",
            stages=["frontend-evidence", "local-pr-review"],
            evidence=["evidence.e2e", "evidence.accessibility"],
        ),
    )


def _default_modules() -> tuple[ReviewerRoleModule, ...]:
    return (
        _module(
            "role.delivery-operability",
            capabilities=[
                "capability.correctness",
                "capability.delivery-operability",
            ],
            dimensions=["dimension.delivery", "dimension.operability"],
            evidence=["evidence.runtime"],
        ),
        _module(
            "role.evolution-architecture",
            capabilities=[
                "capability.correctness",
                "capability.evolution-architecture",
            ],
            dimensions=["dimension.evolution", "dimension.architecture"],
            evidence=["evidence.compatibility"],
        ),
        *_blocking_modules(),
        _module(
            "role.user-journey",
            capabilities=["capability.user-journey"],
            dimensions=["dimension.user-journey"],
            evidence=["evidence.e2e"],
        ),
    )


def _blocking_modules() -> tuple[ReviewerRoleModule, ...]:
    return (
        _module(
            "role.security",
            capabilities=["capability.security"],
            dimensions=["dimension.security"],
            evidence=["evidence.security"],
            authority_ceiling="block",
            blocking=["capability.security"],
        ),
        _module(
            "role.data-integrity",
            capabilities=["capability.data-integrity"],
            dimensions=["dimension.data-integrity"],
            evidence=["evidence.data-integrity"],
            authority_ceiling="block",
            blocking=["capability.data-integrity"],
        ),
        _module(
            "role.trust-boundary-integrity",
            capabilities=[
                "capability.security",
                "capability.data-integrity",
            ],
            dimensions=["dimension.trust-boundary"],
            evidence=["evidence.security", "evidence.data-integrity"],
            authority_ceiling="block",
            blocking=["capability.security", "capability.data-integrity"],
        ),
    )


def _default_policy(
    modules: tuple[ReviewerRoleModule, ...],
) -> ReviewerSelectionPolicy:
    return build_selection_policy(
        policy_id="policy.ai-sdlc-default",
        version="1.0.0",
        registry_compatibility_range=">=1.0.0,<2.0.0",
        merge_semantics_version="role-merge.v1",
        allowed_blocking_authority_ids=[
            "capability.data-integrity",
            "capability.security",
        ],
        enabled_module_ids=[item.module_id for item in modules],
        constraint_conflicts=[
            ("provider.local-only", "provider.remote-required"),
            ("isolation.enforced", "isolation.unproven"),
        ],
        owner="ai-sdlc",
        review_date=_REVIEW_DATE,
    )


def _capability(
    capability_id: str,
    *,
    authority_ceiling: str = "advise",
    stages: list[str] | None = None,
    risks: list[str] | None = None,
    evidence: list[str],
) -> CapabilityDefinition:
    return CapabilityDefinition.model_validate(
        {
            "capability_id": capability_id,
            "version": "1.0.0",
            "applicable_stage": stages or _STAGES,
            "applicable_risk": risks or _RISKS,
            "authority_ceiling": authority_ceiling,
            "required_evidence_types": evidence,
            "maturity": "active",
            "compatibility_range": ">=1.0.0,<2.0.0",
            "owner": "ai-sdlc",
            "review_date": _REVIEW_DATE,
        }
    )


def _module(
    module_id: str,
    *,
    capabilities: list[str],
    dimensions: list[str],
    evidence: list[str],
    authority_ceiling: str = "advise",
    blocking: list[str] | None = None,
) -> ReviewerRoleModule:
    return build_role_module(
        module_id=module_id,
        version="1.0.0",
        module_kind="base",
        capability_ids=capabilities,
        primary_dimensions=dimensions,
        in_scope=["scope.stage-candidate"],
        out_of_scope=["scope.candidate-mutation"],
        blocking_authority=blocking or [],
        authority_ceiling=authority_ceiling,
        required_evidence=evidence,
        forbidden_actions=["action.mutate-candidate"],
        provider_constraints=[f"provider-scope.{module_id}"],
        isolation_requirements=["isolation.read-only"],
        cost_ceiling=10,
        merge_semantics_version="role-merge.v1",
        compatibility_range=">=1.0.0,<2.0.0",
        owner="ai-sdlc",
        review_date=_REVIEW_DATE,
    )
