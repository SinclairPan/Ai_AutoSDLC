from __future__ import annotations

from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_attribution_policy as baseline_attribution_policy,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_evaluator_contract as baseline_evaluator_contract,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_optimization_snapshot as baseline_optimization_snapshot,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_session_budget_policies as baseline_session_budget_policies,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_storage_policy as baseline_storage_policy,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_usage_estimate_policy as baseline_usage_estimate_policy,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    baseline_auto_promotion_policy,
    baseline_constitution,
    baseline_epoch_budget_policy,
    baseline_offline_capacity,
)


def test_versioned_baseline_lineage_is_complete_and_deterministic() -> None:
    constitution = baseline_constitution()
    snapshot = baseline_optimization_snapshot("project.shared")

    assert constitution.constitution_version == "1.0.0"
    assert (
        constitution.epoch_budget_policy_digest
        == baseline_epoch_budget_policy().policy_digest
    )
    assert (
        constitution.attribution_policy_digest
        == baseline_attribution_policy().policy_digest
    )
    assert (
        constitution.auto_promotion_policy_digest
        == baseline_auto_promotion_policy().policy_digest
    )
    assert constitution.storage_policy_digest == canonical_digest(
        baseline_storage_policy(), CanonicalizationPolicy()
    )
    assert constitution.evaluator_registry_digest == canonical_digest(
        (baseline_evaluator_contract(),), CanonicalizationPolicy()
    )
    assert snapshot == baseline_optimization_snapshot("project.shared")
    assert (
        snapshot.policy_payload["optimization_constitution_digest"]
        == constitution.constitution_digest
    )
    assert (
        snapshot.policy_payload["usage_estimation_policy"]
        == baseline_usage_estimate_policy().model_dump(mode="json")
    )


def test_epoch_budget_and_offline_capacity_share_the_same_hard_limits() -> None:
    policy = baseline_epoch_budget_policy()
    capacity = baseline_offline_capacity()

    assert policy.hard_provider_calls == capacity.provider_calls == 32
    assert policy.hard_tokens == capacity.tokens == 1_500_000
    assert policy.hard_cost == capacity.cost == 30
    assert policy.hard_wall_clock == capacity.active_wall_clock == 10_800
    assert policy.hard_parallelism == capacity.parallelism == 1


def test_session_budget_baseline_matches_prd_risk_table() -> None:
    policies = baseline_session_budget_policies()

    assert tuple(policies) == ("high", "low", "medium")
    assert (
        policies["low"].maximum_slots,
        policies["low"].hard_provider_calls,
        policies["low"].hard_review_passes,
        policies["low"].hard_tokens,
        policies["low"].hard_cost,
        policies["low"].hard_wall_clock,
    ) == (2, 8, 4, 300_000, 10, 1_800)
    assert policies["medium"].hard_tokens == 1_000_000
    assert policies["high"].hard_tokens == 3_000_000
