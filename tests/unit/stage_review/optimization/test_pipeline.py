from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.controller_models import (
    MaintenanceBudget,
    OptimizationEpoch,
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
from ai_sdlc.core.stage_review.optimization.pipeline import (
    OptimizationPipelineExecutor,
    _select_finalist,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    CandidateGenerationResult,
    PipelinePromotionPackage,
    PipelineShadowResult,
    PipelineSnapshotResult,
    ShadowComparisonMetrics,
)
from ai_sdlc.core.stage_review.optimization.pipeline_effects import allow_effect
from ai_sdlc.core.stage_review.optimization.promotion import (
    AutoPromotionDecision,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import (
    OptimizationSnapshot,
)


def test_fixed_pipeline_advances_without_candidate_domain_branches(tmp_path: Path) -> None:
    registry = OptimizationEvaluatorRegistry()
    adapter = _EvaluatorAdapter()
    registry.register(_evaluator_contract("custom-evaluator"), adapter)
    publication = _PublicationPort()
    executor = OptimizationPipelineExecutor(
        tmp_path,
        project_id="project.shared",
        minimum_evaluable_sessions=20,
        candidate_family_limit=8,
        evaluator_registry=registry,
        replay_evaluator_kinds=("custom-evaluator",),
        dataset_port=_DatasetPort(evaluable=20),
        candidate_port=_CandidatePort((_candidate("selection"),)),
        holdout_port=_HoldoutPort(),
        shadow_port=_ShadowPort(),
        promotion_port=_PromotionPort(),
        publication_port=publication,
    )
    epoch = _epoch()
    visited: list[str] = []

    for _ in range(7):
        visited.append(epoch.state)
        result = executor.advance(
            epoch, MaintenanceBudget(), authorize_effect=allow_effect
        )
        epoch = epoch.model_copy(
            update={
                "state": result.next_state,
                "dataset_digest": result.dataset_digest or epoch.dataset_digest,
                "finalist_candidate_digest": result.finalist_candidate_digest
                or epoch.finalist_candidate_digest,
            }
        )

    assert visited == [
        "snapshotting",
        "generating",
        "replaying",
        "holdout_evaluating",
        "shadow_observing",
        "evaluating",
        "promoting",
    ]
    assert epoch.state == "promoted"
    assert adapter.calls == 1
    assert publication.calls == 1


def test_pipeline_stops_when_evaluable_baseline_is_not_met(tmp_path: Path) -> None:
    executor = _executor(tmp_path, dataset_port=_DatasetPort(evaluable=19))

    result = executor.advance(
        _epoch(), MaintenanceBudget(), authorize_effect=allow_effect
    )

    assert result.next_state == "no_change"
    assert result.reason == "minimum_evaluable_sessions_not_met"


def test_candidate_without_any_evaluation_report_cannot_be_finalist() -> None:
    candidate = _candidate("selection")

    assert _select_finalist((candidate,), ()) is None


def test_pipeline_rejects_family_or_per_advance_budget_overrun(tmp_path: Path) -> None:
    candidates = tuple(_candidate("budget", suffix=str(index)) for index in range(3))
    executor = _executor(
        tmp_path,
        candidate_port=_CandidatePort(candidates),
        candidate_family_limit=2,
    )
    epoch = _epoch()
    snapshot = executor.advance(
        epoch, MaintenanceBudget(), authorize_effect=allow_effect
    )
    generating = epoch.model_copy(
        update={"state": snapshot.next_state, "dataset_digest": snapshot.dataset_digest}
    )

    result = executor.advance(
        generating, MaintenanceBudget(), authorize_effect=allow_effect
    )

    assert result.next_state == "no_change"
    assert result.reason == "candidate_family_limit_exceeded"


def test_incomplete_shadow_sample_is_not_frozen_as_final_evidence(
    tmp_path: Path,
) -> None:
    shadow = _ProgressingShadowPort()
    executor = _executor(tmp_path, shadow_port=shadow)
    epoch = _epoch()
    for _ in range(4):
        result = executor.advance(
            epoch, MaintenanceBudget(), authorize_effect=allow_effect
        )
        epoch = epoch.model_copy(
            update={
                "state": result.next_state,
                "dataset_digest": result.dataset_digest or epoch.dataset_digest,
                "finalist_candidate_digest": result.finalist_candidate_digest
                or epoch.finalist_candidate_digest,
            }
        )

    waiting = executor.advance(
        epoch, MaintenanceBudget(), authorize_effect=allow_effect
    )
    completed = executor.advance(
        epoch, MaintenanceBudget(), authorize_effect=allow_effect
    )

    assert waiting.next_state == "shadow_observing"
    assert waiting.reason == "minimum_shadow_window_not_met"
    assert completed.next_state == "evaluating"
    assert shadow.calls == 2


def test_snapshot_write_is_fenced_after_external_freeze(tmp_path: Path) -> None:
    executor = _executor(tmp_path)
    authorizer = _LoseLeaseBeforeCommit()

    with pytest.raises(SharedStateIntegrityError, match="fenced"):
        executor.advance(
            _epoch(),
            MaintenanceBudget(),
            authorize_effect=authorizer,
        )

    assert authorizer.authorizations == 2
    assert executor.store.read(
        _epoch().epoch_id, "snapshotting", PipelineSnapshotResult
    ) is None


@dataclass
class _LoseLeaseBeforeCommit:
    authorizations: int = 0

    def __call__(self) -> None:
        self.authorizations += 1
        if self.authorizations == 2:
            raise SharedStateIntegrityError("optimization epoch lease was fenced")

    def commit(self, operation: object) -> object:
        self()
        assert callable(operation)
        return operation()


def test_pipeline_has_no_unfenced_write_default(tmp_path: Path) -> None:
    executor = _executor(tmp_path)

    with pytest.raises(TypeError, match="authorize_effect"):
        executor.advance(_epoch(), MaintenanceBudget())  # type: ignore[call-arg]


def test_publication_is_fenced_before_external_promotion(tmp_path: Path) -> None:
    publication = _PublicationPort()
    executor = _executor(tmp_path)
    executor.publication_port = publication
    epoch = _epoch()
    while epoch.state != "promoting":
        result = executor.advance(
            epoch, MaintenanceBudget(), authorize_effect=allow_effect
        )
        epoch = epoch.model_copy(
            update={
                "state": result.next_state,
                "dataset_digest": result.dataset_digest or epoch.dataset_digest,
                "finalist_candidate_digest": result.finalist_candidate_digest
                or epoch.finalist_candidate_digest,
            }
        )

    def reject_effect() -> None:
        raise SharedStateIntegrityError("optimization epoch lease was fenced")

    with pytest.raises(SharedStateIntegrityError, match="fenced"):
        executor.advance(
            epoch,
            MaintenanceBudget(),
            authorize_effect=reject_effect,
        )

    assert publication.calls == 0


def _executor(
    root: Path,
    *,
    dataset_port: object | None = None,
    candidate_port: object | None = None,
    shadow_port: object | None = None,
    candidate_family_limit: int = 8,
) -> OptimizationPipelineExecutor:
    registry = OptimizationEvaluatorRegistry()
    registry.register(_evaluator_contract("custom-evaluator"), _EvaluatorAdapter())
    return OptimizationPipelineExecutor(
        root,
        project_id="project.shared",
        minimum_evaluable_sessions=20,
        candidate_family_limit=candidate_family_limit,
        evaluator_registry=registry,
        replay_evaluator_kinds=("custom-evaluator",),
        dataset_port=dataset_port or _DatasetPort(evaluable=20),
        candidate_port=candidate_port or _CandidatePort((_candidate("selection"),)),
        holdout_port=_HoldoutPort(),
        shadow_port=shadow_port or _ShadowPort(),
        promotion_port=_PromotionPort(),
        publication_port=_PublicationPort(),
    )


@dataclass
class _DatasetPort:
    evaluable: int

    def freeze(
        self, epoch: OptimizationEpoch, authorize_effect: object
    ) -> PipelineSnapshotResult:
        del authorize_effect
        return PipelineSnapshotResult(
            dataset_digest=f"sha256:dataset.{epoch.epoch_id}",
            evaluable_session_count=self.evaluable,
        )


@dataclass
class _CandidatePort:
    candidates: tuple[OptimizationCandidate, ...]

    def generate(
        self,
        epoch: OptimizationEpoch,
        dataset: PipelineSnapshotResult,
        family_limit: int,
    ) -> CandidateGenerationResult:
        return CandidateGenerationResult(candidates=self.candidates)


@dataclass
class _EvaluatorAdapter:
    calls: int = 0

    def evaluate(
        self,
        candidate: OptimizationCandidate,
        context: EvaluationContext,
        contract: EvaluatorContract,
    ) -> OptimizationEvaluationReport:
        self.calls += 1
        return _report(candidate, context, contract.evaluator_kind)


class _HoldoutPort:
    def evaluate(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        authorize_effect: object,
    ) -> OptimizationEvaluationReport:
        del authorize_effect
        return _report(
            candidate,
            EvaluationContext(
                dataset_digest=epoch.dataset_digest,
                partition="holdout",
                evaluation_binding_id="evaluation-binding.holdout",
                evaluation_provider_id="provider.local-evaluator",
                provider_capabilities=("local-read-only", "read-only"),
                resource_reservation_digest="sha256:reservation",
            ),
            "holdout-gate",
        )


class _ShadowPort:
    def observe(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        authorize_effect: object,
        maximum_provider_calls: int = 0,
    ) -> PipelineShadowResult:
        del authorize_effect, maximum_provider_calls
        return _complete_shadow()


@dataclass
class _ProgressingShadowPort:
    calls: int = 0

    def observe(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        authorize_effect: object,
        maximum_provider_calls: int = 0,
    ) -> PipelineShadowResult:
        del authorize_effect, maximum_provider_calls
        self.calls += 1
        if self.calls == 1:
            return PipelineShadowResult(
                complete=False,
                reason="minimum_shadow_window_not_met",
            )
        return _complete_shadow()


def _complete_shadow() -> PipelineShadowResult:
    return PipelineShadowResult(
        complete=True,
        evidence_digest="sha256:shadow",
        session_ids=("session.shadow",),
        metrics=ShadowComparisonMetrics(
            critical_detection_delta=1,
            late_critical_delta=0,
            reviewer_coverage_leak_delta=0,
            false_positive_delta=0,
            reversal_delta=0,
            stage_reopen_delta=0,
            needs_user_delta=0,
            blocked_delta=0,
            timeout_delta=0,
            abandon_delta=0,
            hard_budget_exhausted_delta=0,
            unknown_or_censored_delta=0,
        ),
        guard_results={"shadow_fixture": True},
        evaluation_binding_id="evaluation-binding.shadow",
    )


class _PromotionPort:
    def evaluate(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        reports: tuple[OptimizationEvaluationReport, ...],
        shadow: PipelineShadowResult,
    ) -> PipelinePromotionPackage:
        report_digests = tuple(sorted(item.report_digest for item in reports))
        snapshot = OptimizationSnapshot(
            snapshot_id="optimization-snapshot.challenger",
            project_id="project.shared",
            parent_snapshot_digest=epoch.baseline_snapshot_digest,
            stable_fallback_digest=epoch.baseline_snapshot_digest,
            candidate_digest=candidate.candidate_digest,
            evaluation_report_digests=report_digests,
            policy_payload={"selection_policy": {"maximum_slots": 2}},
            created_at="2026-07-22T00:00:00Z",
        )
        decision = AutoPromotionDecision(
            decision_id="promotion-decision.pipeline",
            policy_digest="sha256:promotion-policy",
            baseline_snapshot_digest=epoch.baseline_snapshot_digest,
            challenger_snapshot_digest=snapshot.snapshot_digest,
            candidate_digest=candidate.candidate_digest,
            evaluation_report_digests=report_digests,
            approved=True,
            failed_guards=(),
        )
        return PipelinePromotionPackage(decision=decision, snapshot=snapshot)


@dataclass
class _PublicationPort:
    calls: int = 0

    def promote(
        self, package: PipelinePromotionPackage, authorize_effect: object
    ) -> str:
        del authorize_effect
        self.calls += 1
        return f"sha256:published.{package.snapshot.snapshot_id}"


def _epoch() -> OptimizationEpoch:
    return OptimizationEpoch(
        epoch_id="optimization-epoch.pipeline",
        project_id="project.shared",
        trigger_fingerprint="sha256:trigger",
        trigger_digest="sha256:trigger-event",
        constitution_digest="sha256:constitution",
        baseline_snapshot_digest="sha256:baseline",
        candidate_domain_registry_digest="sha256:registry",
        session_sequence_high_watermark=30,
        new_session_count=30,
        state="snapshotting",
        revision=1,
        reservation_id="reservation.pipeline",
        reservation_fencing_token=1,
    )


def _candidate(domain: str, *, suffix: str = "one") -> OptimizationCandidate:
    if domain == "selection":
        field_path = "selection_policy.capability_requirement_rules"
        value: object = [
            {
                "rule_id": f"optimization.coverage.{suffix}",
                "stage_keys": ["implementation"],
                "risk_levels": ["high"],
                "capability_ids": ["capability.security"],
                "coverage_count": 2,
            }
        ]
    else:
        field_path = "budget_policy.low.maximum_slots"
        value = 2
    return OptimizationCandidate(
        candidate_id=f"candidate.{suffix}",
        candidate_domain=domain,
        domain_contract_digest=f"sha256:contract.{domain}",
        domain_adapter_id=f"candidate-domain.{domain}",
        domain_adapter_version="1.0.0",
        domain_adapter_digest=f"sha256:adapter.{domain}",
        domain_registry_digest="sha256:registry",
        base_snapshot_digest="sha256:baseline",
        patch_operations=(
            OptimizationPatchOperation(
                operation="replace",
                field_path=field_path,
                value=value,
            ),
        ),
        expected_effect="improve quality",
        rollback_target="sha256:baseline",
        generator_identity="generator.pipeline",
        generator_provider_id="provider.generator",
        attribution_digests=(
            () if domain == "budget" else ("sha256:attribution",)
        ),
        metric_evidence_digests=(
            ("sha256:metric-evidence",) if domain == "budget" else ()
        ),
        target_stratum_ids=("implementation:high",),
        dataset_partition_refs=("train",),
        estimated_provider_calls=1,
        estimated_tokens=1000,
        estimated_cost=0.5,
        estimated_active_wall_clock=30,
        evidence_refs=("sha256:evidence",),
    )


def _evaluator_contract(kind: str) -> EvaluatorContract:
    return EvaluatorContract(
        evaluator_kind=kind,
        evaluator_version="1.0.0",
        candidate_schema_version="optimization-candidate.v1",
        report_schema_version="optimization-evaluation-report.v1",
        allowed_partitions=("validation",),
        compatible_candidate_domains=("budget", "selection"),
        independence_level="independent_binding",
        deterministic=False,
        provider_constraints=("read-only",),
    )


def _report(
    candidate: OptimizationCandidate,
    context: EvaluationContext,
    evaluator_kind: str,
) -> OptimizationEvaluationReport:
    holdout = context.partition == "holdout"
    return OptimizationEvaluationReport(
        report_id=f"report.{evaluator_kind}.{candidate.candidate_id}",
        candidate_digest=candidate.candidate_digest,
        domain_contract_digest=candidate.domain_contract_digest,
        domain_adapter_id=candidate.domain_adapter_id,
        domain_adapter_version=candidate.domain_adapter_version,
        domain_adapter_digest=candidate.domain_adapter_digest,
        domain_registry_digest=candidate.domain_registry_digest,
        evaluator_kind=evaluator_kind,
        evaluator_version="1.0.0",
        dataset_digest=context.dataset_digest,
        partition=context.partition,
        evaluation_binding_id=context.evaluation_binding_id,
        quality_deltas={"critical_detection": 0.1},
        cost_deltas={"cost": 0},
        censoring_metrics={"unknown_or_censored": 0},
        guard_results={"protocol": True},
        comparison_session_ids=tuple(f"session.{index}" for index in range(5)),
        hypothesis_family_digest=(
            context.hypothesis_family_digest or "sha256:hypothesis-family"
        ),
        raw_p_value=0.01,
        holm_rank=1,
        holm_threshold=0.05,
        statistical_power=0.9,
        effect_confidence_lower=0.1,
        holdout_commitment_digest=("sha256:holdout" if holdout else ""),
        holdout_test_sequence=1 if holdout else 0,
        holdout_alpha=0.025 if holdout else 0,
        recommendation="finalist_eligible",
    )
