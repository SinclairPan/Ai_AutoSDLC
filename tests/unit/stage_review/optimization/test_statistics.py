import pytest

from ai_sdlc.core.stage_review.optimization import statistics as statistics_module
from ai_sdlc.core.stage_review.optimization.controller_models import (
    OptimizationConstitution,
)
from ai_sdlc.core.stage_review.optimization.defaults import baseline_constitution
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationEvaluationReport,
    OptimizationStatisticsPolicy,
)
from ai_sdlc.core.stage_review.optimization.statistics import (
    _apply_holm_bonferroni as apply_holm_bonferroni,
)
from ai_sdlc.core.stage_review.optimization.statistics import (
    _binary_improvement_statistics as binary_improvement_statistics,
)
from ai_sdlc.core.stage_review.optimization.statistics import (
    baseline_statistics_policy,
    required_sample_size,
)


def test_binary_improvement_statistics_use_the_full_trial_count() -> None:
    sparse_p_value, sparse_power, _ = binary_improvement_statistics(5, 100)
    complete_p_value, complete_power, _ = binary_improvement_statistics(5, 5)
    _, same_design_power, _ = binary_improvement_statistics(100, 100)

    assert sparse_p_value > 0.95
    assert sparse_power == same_design_power
    assert complete_p_value == 0.03125
    assert 0 < complete_power < 0.8


def test_power_uses_preregistered_effect_and_actual_alpha() -> None:
    policy = baseline_statistics_policy()
    _, permissive_power, _ = binary_improvement_statistics(
        17,
        20,
        alpha=0.05,
        policy=policy,
    )
    _, strict_power, _ = binary_improvement_statistics(
        17,
        20,
        alpha=0.05 / 12,
        policy=policy,
    )
    _, same_design_power, _ = binary_improvement_statistics(
        10,
        20,
        alpha=0.05 / 12,
        policy=policy,
    )

    assert strict_power < permissive_power
    assert strict_power == same_design_power
    assert strict_power < policy.minimum_statistical_power


def test_required_sample_size_is_the_first_powered_design() -> None:
    policy = baseline_statistics_policy()
    required = required_sample_size(policy, alpha=policy.shadow_alpha)
    _, previous_power, _ = binary_improvement_statistics(
        0,
        required - 1,
        alpha=policy.shadow_alpha,
        policy=policy,
    )
    _, required_power, _ = binary_improvement_statistics(
        0,
        required,
        alpha=policy.shadow_alpha,
        policy=policy,
    )

    assert required > 10
    assert previous_power < policy.minimum_statistical_power
    assert required_power >= policy.minimum_statistical_power


def test_exact_binomial_power_boundaries_are_not_treated_as_monotonic() -> None:
    policy = baseline_statistics_policy()
    powers = {
        count: binary_improvement_statistics(
            0,
            count,
            alpha=policy.shadow_alpha,
            policy=policy,
        )[1]
        for count in (37, 38, 40, 41, 42)
    }

    assert required_sample_size(policy, alpha=policy.shadow_alpha) == 37
    assert powers[37] >= policy.minimum_statistical_power
    assert powers[38] < policy.minimum_statistical_power
    assert powers[40] >= policy.minimum_statistical_power
    assert powers[41] < policy.minimum_statistical_power
    assert powers[42] >= policy.minimum_statistical_power
    assert required_sample_size(
        policy,
        alpha=policy.shadow_alpha,
        minimum_count=38,
    ) == 40
    assert required_sample_size(
        policy,
        alpha=policy.shadow_alpha,
        minimum_count=41,
    ) == 42


def test_required_sample_size_is_cached_by_versioned_design(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = OptimizationStatisticsPolicy(
        policy_id="statistics.cache-test",
        policy_version="1.0.0",
        minimum_detectable_effect=0.03,
    )
    monkeypatch.setattr(
        statistics_module,
        "bundled_statistics_policies",
        lambda: (policy,),
    )
    statistics_module._required_sample_size.cache_clear()

    first = required_sample_size(policy, alpha=policy.shadow_alpha)
    before = statistics_module._required_sample_size.cache_info()
    second = required_sample_size(policy, alpha=policy.shadow_alpha)
    after = statistics_module._required_sample_size.cache_info()

    assert first == second
    assert after.hits == before.hits + 1


def test_constitution_rejects_unresolvable_statistics_policy() -> None:
    values = baseline_constitution().model_dump(mode="json")
    values.update(
        constitution_digest="",
        statistics_policy_digest="sha256:future-policy",
    )

    with pytest.raises(
        ValueError,
        match="statistics policy is unavailable",
    ):
        OptimizationConstitution.model_validate(values)


def test_constitution_cannot_override_statistics_policy_familywise_alpha() -> None:
    values = baseline_constitution().model_dump(mode="json")
    values.update(constitution_digest="", familywise_alpha=0.01)

    with pytest.raises(
        ValueError,
        match="familywise alpha diverged",
    ):
        OptimizationConstitution.model_validate(values)


def test_holm_correction_is_scoped_to_explicit_hypothesis_family() -> None:
    adjusted = apply_holm_bonferroni(
        (
            _report("family.a", "candidate.a"),
            _report("family.b", "candidate.b"),
        )
    )

    assert {item.holm_rank for item in adjusted} == {1}
    assert {item.holm_threshold for item in adjusted} == {0.05}
    assert {item.recommendation for item in adjusted} == {"finalist_eligible"}


def test_report_rejects_self_reported_statistical_power() -> None:
    values = _report("family.a", "candidate.a").model_dump(mode="json")
    values.update(report_digest="", statistical_power=0.5)

    with pytest.raises(
        ValueError,
        match="statistical evidence diverged from policy",
    ):
        OptimizationEvaluationReport.model_validate(values)


def _report(family: str, candidate: str) -> OptimizationEvaluationReport:
    policy = baseline_statistics_policy()
    session_ids = tuple(f"session.{index:03d}" for index in range(500))
    p_value, power, lower = binary_improvement_statistics(
        350,
        len(session_ids),
        alpha=policy.shadow_alpha,
        policy=policy,
    )
    return OptimizationEvaluationReport(
        report_id=f"report.{candidate}",
        candidate_digest=f"sha256:{candidate}",
        domain_contract_digest="sha256:contract",
        domain_adapter_id="candidate-domain.test",
        domain_adapter_version="1.0.0",
        domain_adapter_digest="sha256:adapter",
        domain_registry_digest="sha256:registry",
        evaluator_kind="population-metrics",
        evaluator_version="1.0.0",
        evaluator_contract_digest="sha256:evaluator-contract",
        dataset_digest="sha256:dataset",
        partition="validation",
        evaluation_binding_id="evaluation-binding.local",
        quality_deltas={"critical_detection": 1},
        cost_deltas={"cost": 0},
        censoring_metrics={"unknown": 0},
        guard_results={"protocol": True},
        comparison_session_ids=session_ids,
        hypothesis_family_digest=f"sha256:{family}",
        improved_count=350,
        sample_count=len(session_ids),
        statistical_sample_digest="sha256:statistical-sample",
        statistics_policy_digest=policy.policy_digest,
        statistical_alpha=policy.shadow_alpha,
        raw_p_value=p_value,
        statistical_power=power,
        effect_confidence_lower=lower,
        recommendation="no_change",
    )
