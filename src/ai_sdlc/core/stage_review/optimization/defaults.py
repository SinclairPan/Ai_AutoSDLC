"""AI-SDLC 1.0.0 随包离线优化基线与版本化硬边界。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.binding_policy import baseline_binding_policy
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.capability_mapping import CapabilityMappingPolicy
from ai_sdlc.core.stage_review.optimization.attribution import AttributionPolicy
from ai_sdlc.core.stage_review.optimization.candidate_domain_registry import (
    CandidateDomainRegistry,
)
from ai_sdlc.core.stage_review.optimization.controller_models import (
    OptimizationConstitution,
)
from ai_sdlc.core.stage_review.optimization.evaluators import EvaluatorContract
from ai_sdlc.core.stage_review.optimization.promotion import AutoPromotionPolicy
from ai_sdlc.core.stage_review.optimization.snapshot_models import OptimizationSnapshot
from ai_sdlc.core.stage_review.optimization.storage_models import (
    OptimizationStoragePolicy,
)
from ai_sdlc.core.stage_review.panel import build_budget_policy
from ai_sdlc.core.stage_review.panel_models import ReviewerBudgetPolicy
from ai_sdlc.core.stage_review.provider_usage_models import (
    ProviderUsageEstimatePolicy,
    build_usage_estimate_policy,
)
from ai_sdlc.core.stage_review.registry import default_registry_bundle
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.role_profiles import baseline_role_profile_policy

_VERSION = "1.0.0"
_REVIEW_DATE = "2026-07-20"
_BASELINE_CREATED_AT = "2026-07-20T00:00:00Z"
_RUNTIME_METADATA = {"created_at", "created_by", "ai_sdlc_version"}


def baseline_epoch_budget_policy() -> ReviewerBudgetPolicy:
    return build_budget_policy(
        created_at=_BASELINE_CREATED_AT,
        created_by="ai-sdlc",
        ai_sdlc_version=_VERSION,
        policy_id="budget.offline-optimization-baseline",
        version=_VERSION,
        maximum_slots=8,
        hard_provider_calls=32,
        hard_review_passes=32,
        hard_tokens=1_500_000,
        hard_cost=30,
        hard_wall_clock=10_800,
        hard_parallelism=1,
        hard_role_replans=1,
        hard_provider_retries=8,
        hard_binding_attempts=8,
        owner="ai-sdlc",
        review_date=_REVIEW_DATE,
    )


def _baseline_session_budget_policies() -> dict[str, ReviewerBudgetPolicy]:
    return {
        "high": _session_budget_policy(
            "high", 5, 60, 12, 3_000_000, 100, 14_400, 5, 6, 10
        ),
        "low": _session_budget_policy(
            "low", 2, 8, 4, 300_000, 10, 1_800, 2, 2, 3
        ),
        "medium": _session_budget_policy(
            "medium", 4, 24, 8, 1_000_000, 30, 5_400, 4, 4, 6
        ),
    }


def _baseline_usage_estimate_policy() -> ProviderUsageEstimatePolicy:
    return build_usage_estimate_policy(
        policy_id="usage-estimate.codex-local",
        version=_VERSION,
        characters_per_token=4,
        estimated_cost_per_token=0.000001,
    )


def _baseline_attribution_policy() -> AttributionPolicy:
    return AttributionPolicy.baseline()


def baseline_auto_promotion_policy() -> AutoPromotionPolicy:
    return AutoPromotionPolicy(policy_version=_VERSION)


def _baseline_storage_policy() -> OptimizationStoragePolicy:
    return OptimizationStoragePolicy()


def _baseline_evaluator_contract(
    candidate_domains: tuple[str, ...] | None = None,
) -> EvaluatorContract:
    domains = candidate_domains or _candidate_domain_registry().domain_ids
    return EvaluatorContract(
        evaluator_kind="population-metrics",
        evaluator_version=_VERSION,
        candidate_schema_version="optimization-candidate.v1",
        report_schema_version="optimization-evaluation-report.v1",
        allowed_partitions=("holdout", "train", "validation"),
        compatible_candidate_domains=domains,
        independence_level="deterministic",
        deterministic=True,
        provider_constraints=("local-read-only",),
    )


def baseline_constitution() -> OptimizationConstitution:
    domains = _candidate_domain_registry()
    budget = baseline_epoch_budget_policy()
    attribution = _baseline_attribution_policy()
    promotion = baseline_auto_promotion_policy()
    storage = _baseline_storage_policy()
    evaluator_digest = canonical_digest(
        (_baseline_evaluator_contract(domains.domain_ids),), CanonicalizationPolicy()
    )
    return OptimizationConstitution(
        constitution_version=_VERSION,
        epoch_budget_policy_digest=budget.policy_digest,
        attribution_policy_digest=attribution.policy_digest,
        evaluator_registry_digest=evaluator_digest,
        auto_promotion_policy_digest=promotion.policy_digest,
        storage_policy_digest=canonical_digest(storage, CanonicalizationPolicy()),
        candidate_domain_registry_digest=domains.snapshot_digest,
    )


def _candidate_domain_registry() -> CandidateDomainRegistry:
    from ai_sdlc.core.stage_review.optimization.candidate_domain_defaults import (
        default_candidate_domain_registry,
    )

    return default_candidate_domain_registry()


def _baseline_optimization_snapshot(project_id: str) -> OptimizationSnapshot:
    registry = default_registry_bundle()
    budgets = _baseline_session_budget_policies()
    attribution = _baseline_attribution_policy()
    promotion = baseline_auto_promotion_policy()
    constitution = baseline_constitution()
    role_profiles = baseline_role_profile_policy(registry.role_modules)
    return OptimizationSnapshot(
        snapshot_id="optimization-snapshot.baseline-v1",
        project_id=project_id,
        policy_payload={
            "attribution_policy": attribution.model_dump(mode="json"),
            "binding_policy": baseline_binding_policy().model_dump(mode="json"),
            "budget_policy": {
                risk: policy.model_dump(mode="json")
                for risk, policy in budgets.items()
            },
            "usage_estimation_policy": _baseline_usage_estimate_policy().model_dump(
                mode="json"
            ),
            "capability_mapping": CapabilityMappingPolicy(
                registry_digest=registry.registry.registry_digest
            ).model_dump(mode="json"),
            "optimization_constitution_digest": constitution.constitution_digest,
            "promotion_policy_digest": promotion.policy_digest,
            "role_profiles": role_profiles.model_dump(mode="json"),
            "selection_policy": registry.policy.model_dump(
                mode="json",
                exclude=_RUNTIME_METADATA,
            ),
        },
        created_at=_BASELINE_CREATED_AT,
        is_baseline=True,
    )


def baseline_offline_capacity() -> ResourceAmounts:
    return _resource_amounts(baseline_epoch_budget_policy())


def _resource_amounts(policy: ReviewerBudgetPolicy) -> ResourceAmounts:
    return ResourceAmounts(
        slots=policy.maximum_slots,
        provider_calls=policy.hard_provider_calls,
        review_passes=policy.hard_review_passes,
        tokens=policy.hard_tokens,
        cost=policy.hard_cost,
        active_wall_clock=policy.hard_wall_clock,
        parallelism=policy.hard_parallelism,
        role_replans=policy.hard_role_replans,
        provider_retries=policy.hard_provider_retries,
        binding_attempts=policy.hard_binding_attempts,
    )


def _baseline_foreground_capacity() -> ResourceAmounts:
    """允许最多八个常规 Session，共享资源仍保持硬上限。"""

    return _resource_amounts(_baseline_session_budget_policies()["high"]).scaled(8)


def _session_budget_policy(
    risk: str,
    slots: int,
    calls: int,
    passes: int,
    tokens: int,
    cost: float,
    wall_clock: float,
    parallelism: int,
    retries: int,
    bindings: int,
) -> ReviewerBudgetPolicy:
    return build_budget_policy(
        created_at=_BASELINE_CREATED_AT,
        created_by="ai-sdlc",
        ai_sdlc_version=_VERSION,
        policy_id=f"budget.{risk}",
        version=_VERSION,
        maximum_slots=slots,
        hard_provider_calls=calls,
        hard_review_passes=passes,
        hard_tokens=tokens,
        hard_cost=cost,
        hard_wall_clock=wall_clock,
        hard_parallelism=parallelism,
        hard_role_replans=1,
        hard_provider_retries=retries,
        hard_binding_attempts=bindings,
        owner="ai-sdlc",
        review_date=_REVIEW_DATE,
    )
