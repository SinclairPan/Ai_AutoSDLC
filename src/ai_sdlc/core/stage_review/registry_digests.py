"""Registry、Policy、Role Module 与 Role Contract 的摘要策略。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.registry_models import (
    ReviewerCapabilityRegistry,
    ReviewerRoleModule,
    ReviewerSelectionPolicy,
)
from ai_sdlc.core.stage_review.role_contract_models import ReviewerRoleContract

_REGISTRY_POLICY = CanonicalizationPolicy(
    excluded_fields=frozenset(
        {"created_at", "created_by", "ai_sdlc_version", "registry_digest"}
    ),
    set_like_fields=frozenset(
        {
            "capabilities",
            "implies",
            "conflicts",
            "applicable_stage",
            "applicable_risk",
            "required_evidence_types",
        }
    ),
)
_SELECTION_POLICY = CanonicalizationPolicy(
    excluded_fields=frozenset(
        {"created_at", "created_by", "ai_sdlc_version", "policy_digest"}
    ),
    set_like_fields=frozenset(
        {
            "allowed_blocking_authority_ids",
            "enabled_module_ids",
            "constraint_conflicts",
            "double_coverage_risk_levels",
            "capability_requirement_rules",
        }
    ),
)
ROLE_MODULE_SET_FIELDS = frozenset(
    {
        "capability_ids",
        "primary_dimensions",
        "in_scope",
        "out_of_scope",
        "blocking_authority",
        "required_evidence",
        "forbidden_actions",
        "provider_constraints",
        "isolation_requirements",
    }
)
_ROLE_MODULE_POLICY = CanonicalizationPolicy(
    excluded_fields=frozenset({"module_digest"}),
    set_like_fields=ROLE_MODULE_SET_FIELDS,
)
_ROLE_POLICY = CanonicalizationPolicy(
    excluded_fields=frozenset(
        {"created_at", "created_by", "ai_sdlc_version", "role_contract_digest"}
    ),
    set_like_fields=frozenset(
        {
            "source_profile_ids",
            "source_module_ids",
            "source_module_bindings",
            "capability_ids",
            "primary_dimensions",
            "in_scope",
            "out_of_scope",
            "blocking_authority",
            "required_evidence",
            "forbidden_actions",
            "provider_constraints",
            "isolation_requirements",
        }
    ),
)


def registry_digest(registry: ReviewerCapabilityRegistry) -> str:
    return canonical_digest(registry, _REGISTRY_POLICY)


def selection_policy_digest(policy: ReviewerSelectionPolicy) -> str:
    return canonical_digest(policy, _SELECTION_POLICY)


def role_module_digest(module: ReviewerRoleModule) -> str:
    return canonical_digest(module, _ROLE_MODULE_POLICY)


def role_contract_digest(contract: ReviewerRoleContract) -> str:
    return canonical_digest(contract, _ROLE_POLICY)
