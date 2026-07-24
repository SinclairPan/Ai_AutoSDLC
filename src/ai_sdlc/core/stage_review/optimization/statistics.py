"""固定 Holm-Bonferroni 与二元改善统计，避免候选自报显著性。"""

from __future__ import annotations

from collections import defaultdict
from math import sqrt

from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationEvaluationReport,
)


def _binary_improvement_statistics(
    improved_count: int, sample_count: int
) -> tuple[float, float, float]:
    if improved_count < 0 or sample_count < improved_count or sample_count < 1:
        raise ValueError("binary comparison sample is invalid")
    p_value = 0.5**improved_count if improved_count else 1.0
    power = 1 - p_value if improved_count else 0.0
    return p_value, power, _wilson_lower(improved_count, sample_count)


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
    ordered = sorted(reports, key=lambda item: (item.raw_p_value, item.report_digest))
    accepted = True
    adjusted: list[OptimizationEvaluationReport] = []
    family_size = len(ordered)
    for rank, report in enumerate(ordered, start=1):
        threshold = familywise_alpha / (family_size - rank + 1)
        passed = _statistical_guards_pass(report, threshold) and accepted
        accepted = accepted and report.raw_p_value <= threshold
        values = report.model_dump(mode="json")
        values.update(
            report_digest="",
            holm_rank=rank,
            holm_threshold=threshold,
            recommendation="finalist_eligible" if passed else "reject",
        )
        adjusted.append(OptimizationEvaluationReport.model_validate(values))
    return tuple(adjusted)


def _statistical_guards_pass(
    report: OptimizationEvaluationReport, threshold: float
) -> bool:
    return (
        bool(report.comparison_session_ids)
        and bool(report.hypothesis_family_digest)
        and all(report.guard_results.values())
        and report.raw_p_value <= threshold
        and report.statistical_power >= 0.8
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
