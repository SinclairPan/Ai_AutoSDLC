from ai_sdlc.core.stage_review.optimization.models import OptimizationEvaluationReport
from ai_sdlc.core.stage_review.optimization.statistics import (
    _apply_holm_bonferroni as apply_holm_bonferroni,
)


def test_holm_correction_is_scoped_to_explicit_hypothesis_family() -> None:
    adjusted = apply_holm_bonferroni(
        (
            _report("family.a", "candidate.a", 0.03),
            _report("family.b", "candidate.b", 0.03),
        )
    )

    assert {item.holm_rank for item in adjusted} == {1}
    assert {item.holm_threshold for item in adjusted} == {0.05}
    assert {item.recommendation for item in adjusted} == {"finalist_eligible"}


def _report(family: str, candidate: str, p_value: float) -> OptimizationEvaluationReport:
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
        dataset_digest="sha256:dataset",
        partition="validation",
        evaluation_binding_id="evaluation-binding.local",
        quality_deltas={"critical_detection": 1},
        cost_deltas={"cost": 0},
        censoring_metrics={"unknown": 0},
        guard_results={"protocol": True},
        comparison_session_ids=("session.1",),
        hypothesis_family_digest=f"sha256:{family}",
        raw_p_value=p_value,
        statistical_power=0.9,
        effect_confidence_lower=0.1,
        recommendation="no_change",
    )
