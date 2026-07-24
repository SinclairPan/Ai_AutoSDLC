"""内置 Candidate Domain 通过同一注册表组装，不进入核心分支。"""

from __future__ import annotations

import re

from ai_sdlc.core.stage_review.optimization.budget_candidate_generation import (
    budget_candidates,
)
from ai_sdlc.core.stage_review.optimization.candidate_domain_registry import (
    CandidateDomainAdapterBundle,
    CandidateDomainContract,
    CandidateDomainRegistry,
    CandidateGenerator,
    CandidatePayloadValidator,
)
from ai_sdlc.core.stage_review.optimization.candidate_domain_semantics import (
    apply_registered_patch,
    attribution_improved_sessions,
    attribution_report_metrics,
    budget_improved_sessions,
    budget_metrics,
    budget_shadow_improved,
    capability_mapping_improved_sessions,
    critical_detection_improved,
    role_profile_improved_sessions,
    selection_improved_sessions,
    selection_shadow_matcher,
    standard_promotion_guard,
    stratum_shadow_matcher,
)
from ai_sdlc.core.stage_review.optimization.candidate_generation import (
    selection_candidates,
)
from ai_sdlc.core.stage_review.optimization.candidate_policy import (
    validate_binding_domain,
    validate_budget_domain,
    validate_capability_mapping_domain,
    validate_role_profile_domain,
    validate_selection_domain,
)
from ai_sdlc.core.stage_review.optimization.governance_candidate_generation import (
    binding_candidates,
    capability_mapping_candidates,
    role_profile_candidates,
)

_BUDGET_FIELD_PATTERN = (
    r"budget_policy\.(critical|high|low|medium)\."
    r"(maximum_slots|hard_provider_calls|hard_review_passes|hard_tokens|"
    r"hard_cost|hard_wall_clock|hard_parallelism|hard_role_replans|"
    r"hard_provider_retries|hard_binding_attempts)"
)
_Registration = tuple[
    CandidateDomainContract,
    CandidateGenerator,
    CandidatePayloadValidator,
    object,
    object,
    object,
    object,
]


def default_candidate_domain_registry() -> CandidateDomainRegistry:
    registry = CandidateDomainRegistry()
    registrations = (*_runtime_registrations(), *_governance_registrations())
    for contract, generator, validator, improvement, metrics, matcher, comparator in registrations:
        registry.register(
            contract,
            CandidateDomainAdapterBundle(
                adapter_id=f"candidate-domain.{contract.domain_id}",
                adapter_version="1.0.0",
                generator=generator,
                payload_validator=validator,
                patch_applier=apply_registered_patch,
                improvement_evaluator=improvement,  # type: ignore[arg-type]
                report_metrics=metrics,  # type: ignore[arg-type]
                shadow_matcher=matcher,  # type: ignore[arg-type]
                shadow_comparator=comparator,  # type: ignore[arg-type]
                promotion_guard=standard_promotion_guard,
            ),
        )
    return registry.freeze()


def _runtime_registrations() -> tuple[_Registration, ...]:
    return (
        (
            _contract(
                "binding",
                "attribution",
                (
                    "binding_policy.minimum_blocking_independence_grade",
                    "binding_policy.require_independent_blocking_slots",
                ),
            ),
            binding_candidates,
            validate_binding_domain,
            attribution_improved_sessions,
            attribution_report_metrics,
            stratum_shadow_matcher,
            critical_detection_improved,
        ),
        (
            _contract(
                "budget",
                "metric",
                (_BUDGET_FIELD_PATTERN,),
                escape=False,
            ),
            budget_candidates,
            validate_budget_domain,
            budget_improved_sessions,
            budget_metrics,
            stratum_shadow_matcher,
            budget_shadow_improved,
        ),
    )


def _governance_registrations() -> tuple[_Registration, ...]:
    return (
        (
            _contract(
                "capability_mapping",
                "attribution",
                ("capability_mapping.registry_digest",),
            ),
            capability_mapping_candidates,
            validate_capability_mapping_domain,
            capability_mapping_improved_sessions,
            attribution_report_metrics,
            stratum_shadow_matcher,
            critical_detection_improved,
        ),
        (
            _contract(
                "role_profile",
                "attribution",
                ("role_profiles.compositions",),
            ),
            role_profile_candidates,
            validate_role_profile_domain,
            role_profile_improved_sessions,
            attribution_report_metrics,
            stratum_shadow_matcher,
            critical_detection_improved,
        ),
        (
            _contract(
                "selection",
                "attribution",
                ("selection_policy.capability_requirement_rules",),
            ),
            selection_candidates,
            validate_selection_domain,
            selection_improved_sessions,
            attribution_report_metrics,
            selection_shadow_matcher,
            critical_detection_improved,
        ),
    )


def _contract(
    domain_id: str,
    lineage_kind: str,
    fields: tuple[str, ...],
    *,
    escape: bool = True,
) -> CandidateDomainContract:
    patterns = tuple(re.escape(item) for item in fields) if escape else fields
    return CandidateDomainContract(
        domain_id=domain_id,
        contract_version="1.0.0",
        lineage_kind=lineage_kind,  # type: ignore[arg-type]
        authorized_field_patterns=patterns,
    )


__all__ = ["default_candidate_domain_registry"]
