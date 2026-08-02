"""固定 Holm-Bonferroni 与二元改善统计，避免候选自报显著性。"""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from math import exp, fsum, lgamma, log, sqrt

from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationEvaluationReport,
    OptimizationStatisticsPolicy,
)


def baseline_statistics_policy() -> OptimizationStatisticsPolicy:
    return OptimizationStatisticsPolicy(
        policy_id="statistics.optimization-baseline",
        policy_version="1.0.0",
    )


def bundled_statistics_policies() -> tuple[OptimizationStatisticsPolicy, ...]:
    """保留所有仍可恢复在途 epoch 的随包策略版本。"""

    return (baseline_statistics_policy(),)


def statistics_policy_for_digest(digest: str) -> OptimizationStatisticsPolicy:
    matches = tuple(
        policy
        for policy in bundled_statistics_policies()
        if policy.policy_digest == digest
    )
    if len(matches) != 1:
        raise ValueError("optimization statistics policy is unavailable")
    return matches[0]


def required_sample_size(
    policy: OptimizationStatisticsPolicy,
    *,
    alpha: float,
    minimum_count: int = 1,
) -> int:
    """返回不低于业务下限且达到预注册功效的首个固定样本数。"""

    if minimum_count < 1:
        raise ValueError("minimum statistics sample count must be positive")
    return _required_sample_size(policy.policy_digest, alpha, minimum_count)


@lru_cache(maxsize=256)
def _required_sample_size(
    policy_digest: str,
    alpha: float,
    minimum_count: int,
) -> int:
    trusted = statistics_policy_for_digest(policy_digest)
    critical = _critical_success_count(
        minimum_count,
        probability=trusted.null_improvement_rate,
        alpha=alpha,
    )
    for sample_count in range(minimum_count, 1_000_001):
        if sample_count > minimum_count:
            if critical is None:
                critical = _critical_success_count(
                    sample_count,
                    probability=trusted.null_improvement_rate,
                    alpha=alpha,
                )
            else:
                while (
                    critical <= sample_count
                    and _binomial_upper_tail(
                        critical,
                        sample_count,
                        trusted.null_improvement_rate,
                    )
                    > alpha
                ):
                    critical += 1
                if critical > sample_count:
                    critical = None
        power = (
            0.0
            if critical is None
            else _binomial_upper_tail(
                critical,
                sample_count,
                trusted.null_improvement_rate
                + trusted.minimum_detectable_effect,
            )
        )
        if power >= trusted.minimum_statistical_power:
            return sample_count
    raise ValueError("statistics policy sample requirement is impractical")


def design_power(
    sample_count: int,
    *,
    alpha: float,
    policy: OptimizationStatisticsPolicy,
) -> float:
    if sample_count < 1:
        raise ValueError("statistics design sample count must be positive")
    if not 0 < alpha < 1:
        raise ValueError("statistical alpha is invalid")
    critical = _critical_success_count(
        sample_count,
        probability=policy.null_improvement_rate,
        alpha=alpha,
    )
    if critical is None:
        return 0
    return _binomial_upper_tail(
        critical,
        sample_count,
        policy.null_improvement_rate + policy.minimum_detectable_effect,
    )


def resolve_statistics_policy(
    digest: str,
    *,
    configured_policy: OptimizationStatisticsPolicy,
) -> OptimizationStatisticsPolicy:
    """按 epoch/report digest 精确解析，执行路径不接受隐式当前策略。"""

    if not digest:
        raise ValueError("optimization statistics policy digest is required")
    if digest == configured_policy.policy_digest:
        return configured_policy
    return statistics_policy_for_digest(digest)


def _binary_improvement_statistics(
    improved_count: int,
    sample_count: int,
    *,
    alpha: float | None = None,
    policy: OptimizationStatisticsPolicy | None = None,
) -> tuple[float, float, float]:
    if improved_count < 0 or sample_count < improved_count or sample_count < 1:
        raise ValueError("binary comparison sample is invalid")
    active_policy = policy or baseline_statistics_policy()
    active_alpha = active_policy.shadow_alpha if alpha is None else alpha
    if not 0 < active_alpha < 1:
        raise ValueError("statistical alpha is invalid")
    null_rate = active_policy.null_improvement_rate
    alternative_rate = null_rate + active_policy.minimum_detectable_effect
    p_value = _binomial_upper_tail(improved_count, sample_count, null_rate)
    critical = _critical_success_count(
        sample_count,
        probability=null_rate,
        alpha=active_alpha,
    )
    power = (
        _binomial_upper_tail(critical, sample_count, alternative_rate)
        if critical is not None
        else 0.0
    )
    lower = _wilson_lower(improved_count, sample_count) - null_rate
    return p_value, power, lower


def _binomial_upper_tail(successes: int, total: int, probability: float) -> float:
    if total < 0 or not 0 < probability < 1:
        raise ValueError("binomial distribution parameters are invalid")
    if successes <= 0:
        return 1.0
    if successes > total:
        return 0.0
    log_term = (
        lgamma(total + 1)
        - lgamma(successes + 1)
        - lgamma(total - successes + 1)
        + successes * log(probability)
        + (total - successes) * log(1 - probability)
    )
    term = exp(log_term)
    terms = [term]
    odds = probability / (1 - probability)
    for count in range(successes, total):
        term *= ((total - count) / (count + 1)) * odds
        terms.append(term)
    return min(1.0, fsum(terms))


def _critical_success_count(
    total: int, *, probability: float, alpha: float
) -> int | None:
    if _binomial_upper_tail(total, total, probability) > alpha:
        return None
    lower, upper = 0, total
    while lower < upper:
        midpoint = (lower + upper) // 2
        if _binomial_upper_tail(midpoint, total, probability) <= alpha:
            upper = midpoint
        else:
            lower = midpoint + 1
    return lower


def _apply_holm_bonferroni(
    reports: tuple[OptimizationEvaluationReport, ...],
    *,
    familywise_alpha: float = 0.05,
) -> tuple[OptimizationEvaluationReport, ...]:
    if not 0 < familywise_alpha < 1:
        raise ValueError("familywise alpha is invalid")
    grouped: dict[str, list[OptimizationEvaluationReport]] = defaultdict(list)
    for report in reports:
        if not report.hypothesis_family_digest:
            raise ValueError("Holm family identity is missing")
        grouped[report.hypothesis_family_digest].append(report)
    adjusted = tuple(
        item
        for family in sorted(grouped)
        for item in _adjust_family(tuple(grouped[family]), familywise_alpha)
    )
    return tuple(sorted(adjusted, key=lambda item: item.report_digest))


def _adjust_family(
    reports: tuple[OptimizationEvaluationReport, ...], familywise_alpha: float
) -> tuple[OptimizationEvaluationReport, ...]:
    ordered = sorted(reports, key=lambda item: (item.raw_p_value, item.report_id))
    accepted = True
    adjusted: list[OptimizationEvaluationReport] = []
    family_size = len(ordered)
    for rank, report in enumerate(ordered, start=1):
        threshold = familywise_alpha / (family_size - rank + 1)
        policy = statistics_policy_for_digest(report.statistics_policy_digest)
        _, power, _ = _binary_improvement_statistics(
            report.improved_count,
            report.sample_count,
            alpha=threshold,
            policy=policy,
        )
        passed = _statistical_guards_pass(report, threshold, power, policy) and accepted
        accepted = accepted and report.raw_p_value <= threshold
        values = report.model_dump(mode="json")
        values.update(
            report_digest="",
            holm_rank=rank,
            holm_threshold=threshold,
            statistical_alpha=threshold,
            statistical_power=power,
            recommendation="finalist_eligible" if passed else "reject",
        )
        adjusted.append(OptimizationEvaluationReport.model_validate(values))
    return tuple(adjusted)


def _statistical_guards_pass(
    report: OptimizationEvaluationReport,
    threshold: float,
    power: float,
    policy: OptimizationStatisticsPolicy,
) -> bool:
    return (
        bool(report.comparison_session_ids)
        and bool(report.hypothesis_family_digest)
        and all(report.guard_results.values())
        and report.raw_p_value <= threshold
        and power >= policy.minimum_statistical_power
        and report.effect_confidence_lower > 0
    )


def _wilson_lower(successes: int, total: int) -> float:
    if successes == 0:
        return 0.0
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + (z * z / total)
    centre = proportion + (z * z / (2 * total))
    margin = z * sqrt(
        (proportion * (1 - proportion) / total)
        + (z * z / (4 * total * total))
    )
    return max(0.0, (centre - margin) / denominator)
