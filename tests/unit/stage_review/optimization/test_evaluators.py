from __future__ import annotations

from dataclasses import dataclass

import pytest

from ai_sdlc.core.stage_review.optimization.candidate_domain_defaults import (
    default_candidate_domain_registry,
)
from ai_sdlc.core.stage_review.optimization.evaluators import (
    EvaluationContext,
    EvaluatorContract,
    OptimizationEvaluatorRegistry,
)
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationEvaluationReport,
    OptimizationPatchOperation,
)


def test_all_candidate_domains_share_one_schema_and_enforce_patch_authority() -> None:
    domains_and_paths = (
        ("role_profile", "role_profiles.compositions"),
        ("selection", "selection_policy.capability_requirement_rules"),
        ("binding", "binding_policy.require_independent_blocking_slots"),
        ("budget", "budget_policy.high.hard_review_passes"),
        ("capability_mapping", "capability_mapping.registry_digest"),
    )

    candidates = tuple(
        _candidate(domain, path, suffix=str(index))
        for index, (domain, path) in enumerate(domains_and_paths, start=1)
    )
    domains = default_candidate_domain_registry()

    assert {type(item) for item in candidates} == {OptimizationCandidate}
    for candidate in candidates:
        domains.require_candidate(candidate)
    with pytest.raises(ValueError, match="not authorized"):
        domains.require_candidate(
            _candidate(
                "budget",
                "optimization_constitution.familywise_alpha",
                suffix="forbidden",
            )
        )


def test_new_evaluator_contract_and_adapter_register_without_core_branch() -> None:
    registry = OptimizationEvaluatorRegistry()
    adapter = _Adapter()
    registry.register(_contract("custom-risk-evaluator"), adapter)

    report = registry.evaluate(
        evaluator_kind="custom-risk-evaluator",
        candidate=_candidate(
            "selection",
            "selection_policy.capability_requirement_rules",
            suffix="custom",
        ),
        context=_context(),
    )

    assert report.recommendation == "finalist_eligible"
    assert report.evaluator_kind == "custom-risk-evaluator"
    assert adapter.calls == 1


def test_evaluator_rejects_partition_not_authorized_by_contract() -> None:
    registry = OptimizationEvaluatorRegistry()
    adapter = _Adapter()
    registry.register(_contract("validation-only"), adapter)

    with pytest.raises(ValueError, match="partition is not authorized"):
        registry.evaluate(
            evaluator_kind="validation-only",
            candidate=_candidate(
                "selection",
                "selection_policy.capability_requirement_rules",
                suffix="partition",
            ),
            context=_context(partition="holdout"),
        )

    assert adapter.calls == 0


def test_semantic_evaluator_must_be_independent_from_candidate_generator() -> None:
    registry = OptimizationEvaluatorRegistry()
    adapter = _Adapter()
    registry.register(_contract("independent-semantic"), adapter)
    candidate = _candidate(
        "role_profile",
        "role_profiles.compositions",
        suffix="independence",
    )

    with pytest.raises(ValueError, match="independent evaluation binding"):
        registry.evaluate(
            evaluator_kind="independent-semantic",
            candidate=candidate,
            context=_context(evaluation_binding_id=candidate.generator_identity),
        )

    assert adapter.calls == 0


def test_evaluator_provider_identity_and_capabilities_are_enforced() -> None:
    registry = OptimizationEvaluatorRegistry()
    adapter = _Adapter()
    registry.register(_contract("provider-bound"), adapter)
    candidate = _candidate(
        "binding", "binding_policy.require_independent_blocking_slots", suffix="provider"
    )

    with pytest.raises(ValueError, match="generator provider"):
        registry.evaluate(
            evaluator_kind="provider-bound",
            candidate=candidate,
            context=_context(evaluation_provider_id=candidate.generator_provider_id),
        )
    with pytest.raises(ValueError, match="provider constraints"):
        registry.evaluate(
            evaluator_kind="provider-bound",
            candidate=candidate,
            context=_context(provider_capabilities=("network-write",)),
        )

    assert adapter.calls == 0


def test_schema_incompatible_evaluator_is_rejected_before_adapter_call() -> None:
    registry = OptimizationEvaluatorRegistry()
    adapter = _Adapter()
    registry.register(
        _contract(
            "future-schema",
            candidate_schema_version="optimization-candidate.v2",
        ),
        adapter,
    )

    with pytest.raises(ValueError, match="candidate schema is incompatible"):
        registry.evaluate(
            evaluator_kind="future-schema",
            candidate=_candidate(
                "budget",
                "budget_policy.high.hard_tokens",
                suffix="schema",
            ),
            context=_context(),
        )

    assert adapter.calls == 0


@dataclass
class _Adapter:
    calls: int = 0

    def evaluate(
        self,
        candidate: OptimizationCandidate,
        context: EvaluationContext,
        contract: EvaluatorContract,
    ) -> OptimizationEvaluationReport:
        self.calls += 1
        return OptimizationEvaluationReport(
            report_id=f"evaluation.{candidate.candidate_id}",
            candidate_digest=candidate.candidate_digest,
            domain_contract_digest=candidate.domain_contract_digest,
            domain_adapter_id=candidate.domain_adapter_id,
            domain_adapter_version=candidate.domain_adapter_version,
            domain_adapter_digest=candidate.domain_adapter_digest,
            domain_registry_digest=candidate.domain_registry_digest,
            evaluator_kind=contract.evaluator_kind,
            evaluator_version=contract.evaluator_version,
            dataset_digest=context.dataset_digest,
            partition=context.partition,
            evaluation_binding_id=context.evaluation_binding_id,
            quality_deltas={"confirmed_p0_p1_detection": 0.1},
            cost_deltas={"estimated_cost": 0.0},
            censoring_metrics={"unknown_or_censored_rate": 0.0},
            guard_results={"protocol_integrity": True},
            comparison_session_ids=tuple(
                f"session.{index}" for index in range(5)
            ),
            hypothesis_family_digest=context.hypothesis_family_digest,
            raw_p_value=0.01,
            holm_rank=1,
            holm_threshold=0.05,
            statistical_power=0.9,
            effect_confidence_lower=0.1,
            recommendation="finalist_eligible",
        )


def _contract(
    kind: str,
    *,
    candidate_schema_version: str = "optimization-candidate.v1",
) -> EvaluatorContract:
    return EvaluatorContract(
        evaluator_kind=kind,
        evaluator_version="1.0.0",
        candidate_schema_version=candidate_schema_version,
        report_schema_version="optimization-evaluation-report.v1",
        allowed_partitions=("train", "validation"),
        compatible_candidate_domains=(
            "binding",
            "budget",
            "capability_mapping",
            "role_profile",
            "selection",
        ),
        independence_level="independent_binding",
        deterministic=False,
        provider_constraints=("read-only",),
    )


def _context(
    *,
    partition: str = "validation",
    evaluation_binding_id: str = "evaluation-binding.independent",
    evaluation_provider_id: str = "provider.evaluator",
    provider_capabilities: tuple[str, ...] = ("read-only",),
) -> EvaluationContext:
    return EvaluationContext(
        dataset_digest="sha256:dataset.1",
        partition=partition,
        evaluation_binding_id=evaluation_binding_id,
        evaluation_provider_id=evaluation_provider_id,
        provider_capabilities=provider_capabilities,
        resource_reservation_digest="sha256:offline-reservation.1",
    )


def _candidate(domain: str, field_path: str, *, suffix: str) -> OptimizationCandidate:
    values = {
        "binding": True,
        "budget": 2,
        "capability_mapping": "sha256:registry.next",
        "role_profile": [["sha256:role.security"]],
        "selection": [],
    }
    return OptimizationCandidate(
        candidate_id=f"optimization-candidate.{suffix}",
        candidate_domain=domain,
        **default_candidate_domain_registry().candidate_binding(domain),
        base_snapshot_digest="sha256:baseline.1",
        patch_operations=(
            OptimizationPatchOperation(
                operation="replace",
                field_path=field_path,
                value=values[domain],
            ),
        ),
        expected_effect="improve reviewer quality without lowering hard constraints",
        rollback_target="sha256:baseline.1",
        generator_identity="generator.binding.1",
        generator_provider_id="provider.generator",
        attribution_digests=(
            () if domain == "budget" else ("sha256:attribution.1",)
        ),
        metric_evidence_digests=(
            ("sha256:metric-evidence.1",) if domain == "budget" else ()
        ),
        target_stratum_ids=("implementation:high",),
        dataset_partition_refs=("train",),
        estimated_provider_calls=1,
        estimated_tokens=1000,
        estimated_cost=0.5,
        estimated_active_wall_clock=30,
        evidence_refs=("sha256:evidence.1",),
    )
