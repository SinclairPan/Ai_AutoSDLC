"""无候选域分支的固定 Offline Optimization Pipeline。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.controller_models import (
    MaintenanceBudget,
    OptimizationEpoch,
    OptimizationStepResult,
)
from ai_sdlc.core.stage_review.optimization.evaluators import (
    EvaluationContext,
    OptimizationEvaluatorRegistry,
)
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationEvaluationReport,
)
from ai_sdlc.core.stage_review.optimization.pipeline_candidate_validation import (
    candidate_budget_fits,
    require_candidate_domain_registry,
    require_epoch_domain_registry,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    CandidateGenerationPort,
    CandidateGenerationResult,
    DatasetSnapshotPort,
    HoldoutEvaluationPort,
    PipelineHoldoutResult,
    PipelinePromotionPackage,
    PipelinePublicationResult,
    PipelineReplayResult,
    PipelineShadowResult,
    PipelineSnapshotResult,
    PromotionEvaluationPort,
    ShadowObservationPort,
    SnapshotPublicationPort,
)
from ai_sdlc.core.stage_review.optimization.pipeline_effects import PipelineEffects
from ai_sdlc.core.stage_review.optimization.pipeline_store import (
    OptimizationPipelineStore,
)
from ai_sdlc.core.stage_review.optimization.pipeline_validation import (
    _verify_promotion_package as verify_promotion_package,
)
from ai_sdlc.core.stage_review.optimization.shadow_execution import (
    ShadowExecutionNoChangeError,
    ShadowExecutionUnrecoverableError,
)
from ai_sdlc.core.stage_review.optimization.statistics import (
    _apply_holm_bonferroni as apply_holm_bonferroni,
)

T = TypeVar("T", bound=BaseModel)


class OptimizationPipelineExecutor:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        minimum_evaluable_sessions: int,
        candidate_family_limit: int,
        evaluator_registry: OptimizationEvaluatorRegistry,
        replay_evaluator_kinds: tuple[str, ...],
        dataset_port: DatasetSnapshotPort,
        candidate_port: CandidateGenerationPort,
        holdout_port: HoldoutEvaluationPort,
        shadow_port: ShadowObservationPort,
        promotion_port: PromotionEvaluationPort,
        publication_port: SnapshotPublicationPort,
        domain_registry_digest: str = "",
    ) -> None:
        if minimum_evaluable_sessions < 1 or candidate_family_limit < 1:
            raise ValueError("optimization pipeline limits must be positive")
        if not replay_evaluator_kinds or replay_evaluator_kinds != tuple(
            sorted(set(replay_evaluator_kinds))
        ):
            raise ValueError("replay evaluator kinds must be canonical")
        self.minimum_evaluable_sessions = minimum_evaluable_sessions
        self.candidate_family_limit = candidate_family_limit
        self.registry = evaluator_registry
        self.replay_evaluator_kinds = replay_evaluator_kinds
        self.dataset_port = dataset_port
        self.candidate_port = candidate_port
        self.holdout_port = holdout_port
        self.shadow_port = shadow_port
        self.promotion_port = promotion_port
        self.publication_port = publication_port
        self.domain_registry_digest = domain_registry_digest
        self.store = OptimizationPipelineStore(root, project_id=project_id)

    def advance(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        *,
        authorize_effect: Callable[[], None],
    ) -> OptimizationStepResult:
        require_epoch_domain_registry(epoch, self.domain_registry_digest)
        handlers: dict[
            str,
            Callable[
                [OptimizationEpoch, MaintenanceBudget, PipelineEffects],
                OptimizationStepResult,
            ],
        ] = {
            "snapshotting": self._snapshot,
            "generating": self._generate,
            "replaying": self._replay,
            "holdout_evaluating": self._holdout,
            "shadow_observing": self._shadow,
            "evaluating": self._evaluate,
            "promoting": self._promote,
        }
        try:
            handler = handlers[epoch.state]
        except KeyError as exc:
            raise SharedStateIntegrityError("optimization pipeline state is invalid") from exc
        return handler(epoch, budget, PipelineEffects(self.store, authorize_effect))

    def _snapshot(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        effects: PipelineEffects,
    ) -> OptimizationStepResult:
        del budget
        result = self.store.read(epoch.epoch_id, "snapshotting", PipelineSnapshotResult)
        if result is None:
            frozen = effects.call(
                lambda: self.dataset_port.freeze(epoch, effects.authorize)
            )
            result = effects.write(epoch.epoch_id, "snapshotting", frozen)
        if result.evaluable_session_count < self.minimum_evaluable_sessions:
            return _no_change("minimum_evaluable_sessions_not_met")
        return OptimizationStepResult(
            next_state="generating", dataset_digest=result.dataset_digest
        )

    def _generate(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        effects: PipelineEffects,
    ) -> OptimizationStepResult:
        dataset = self._required(epoch, "snapshotting", PipelineSnapshotResult)
        result = self.store.read(
            epoch.epoch_id, "generating", CandidateGenerationResult
        )
        if result is None:
            generated = effects.call(
                lambda: self.candidate_port.generate(
                    epoch, dataset, self.candidate_family_limit
                )
            )
            candidates = tuple(
                sorted(generated.candidates, key=lambda item: item.candidate_digest)
            )
            result = effects.write(
                epoch.epoch_id,
                "generating",
                CandidateGenerationResult(candidates=candidates),
            )
        if not result.candidates:
            return _no_change("no_candidate")
        require_candidate_domain_registry(epoch, result.candidates)
        if len(result.candidates) > self.candidate_family_limit:
            return _no_change("candidate_family_limit_exceeded")
        if not candidate_budget_fits(result.candidates, budget):
            return _no_change("maintenance_budget_exceeded")
        return OptimizationStepResult(next_state="replaying")

    def _replay(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        effects: PipelineEffects,
    ) -> OptimizationStepResult:
        del budget
        cached = self.store.read(epoch.epoch_id, "replaying", PipelineReplayResult)
        if cached is None:
            candidates = self._candidates(epoch)
            raw_reports = tuple(
                self._evaluate_candidate(
                    epoch, candidate, evaluator_kind, effects
                )
                for candidate in candidates
                for evaluator_kind in self.replay_evaluator_kinds
            )
            reports = apply_holm_bonferroni(raw_reports)
            finalist = _select_finalist(candidates, reports)
            cached = effects.write(
                epoch.epoch_id,
                "replaying",
                PipelineReplayResult(
                    reports=tuple(sorted(reports, key=lambda item: item.report_digest)),
                    finalist_candidate_digest="" if finalist is None else finalist,
                ),
            )
        if not cached.finalist_candidate_digest:
            return _no_change("no_replay_finalist")
        return OptimizationStepResult(
            next_state="holdout_evaluating",
            finalist_candidate_digest=cached.finalist_candidate_digest,
        )

    def _holdout(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        effects: PipelineEffects,
    ) -> OptimizationStepResult:
        del budget
        cached = self.store.read(
            epoch.epoch_id, "holdout_evaluating", PipelineHoldoutResult
        )
        if cached is None:
            report = effects.call(
                lambda: self.holdout_port.evaluate(
                    epoch, self._finalist(epoch), effects.authorize
                )
            )
            cached = effects.write(
                epoch.epoch_id,
                "holdout_evaluating",
                PipelineHoldoutResult(report=report),
            )
        if cached.report.recommendation != "finalist_eligible":
            return _no_change("holdout_rejected")
        return OptimizationStepResult(next_state="shadow_observing")

    def _shadow(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        effects: PipelineEffects,
    ) -> OptimizationStepResult:
        cached = self.store.read(epoch.epoch_id, "shadow_observing", PipelineShadowResult)
        if cached is None:
            try:
                observed = effects.call(
                    lambda: self.shadow_port.observe(
                        epoch,
                        self._finalist(epoch),
                        effects.authorize,
                        budget.maximum_provider_calls,
                    )
                )
            except ShadowExecutionNoChangeError as exc:
                return _no_change(str(exc))
            except ShadowExecutionUnrecoverableError as exc:
                return OptimizationStepResult(next_state="failed", reason=str(exc))
            if not observed.complete:
                return OptimizationStepResult(
                    next_state="shadow_observing", reason=observed.reason
                )
            cached = effects.write(epoch.epoch_id, "shadow_observing", observed)
        if not cached.complete:
            raise SharedStateIntegrityError(
                "committed shadow evidence must be complete"
            )
        return OptimizationStepResult(next_state="evaluating")

    def _evaluate(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        effects: PipelineEffects,
    ) -> OptimizationStepResult:
        del budget
        cached = self.store.read(
            epoch.epoch_id, "evaluating", PipelinePromotionPackage
        )
        if cached is None:
            reports = self._reports(epoch)
            shadow = self._required(epoch, "shadow_observing", PipelineShadowResult)
            package = effects.call(
                lambda: self.promotion_port.evaluate(
                    epoch, self._finalist(epoch), reports, shadow
                )
            )
            verify_promotion_package(
                epoch,
                self._finalist(epoch),
                tuple(item.report_digest for item in reports),
                package,
            )
            cached = effects.write(epoch.epoch_id, "evaluating", package)
        if not cached.decision.approved:
            return _no_change("promotion_guards_rejected")
        return OptimizationStepResult(next_state="promoting")

    def _promote(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        effects: PipelineEffects,
    ) -> OptimizationStepResult:
        del budget
        published = self.store.read(
            epoch.epoch_id, "promoting", PipelinePublicationResult
        )
        if published is None:
            package = self._required(epoch, "evaluating", PipelinePromotionPackage)
            digest = effects.call(
                lambda: self.publication_port.promote(package, effects.authorize)
            ).strip()
            if not digest:
                raise SharedStateIntegrityError("promotion produced no control event")
            effects.write(
                epoch.epoch_id,
                "promoting",
                PipelinePublicationResult(control_event_digest=digest),
            )
        return OptimizationStepResult(next_state="promoted")

    def _evaluate_candidate(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        evaluator_kind: str,
        effects: PipelineEffects,
    ) -> OptimizationEvaluationReport:
        return effects.call(
            lambda: self.registry.evaluate(
                evaluator_kind=evaluator_kind,
                candidate=candidate,
                context=EvaluationContext(
                    dataset_digest=epoch.dataset_digest,
                    partition="validation",
                    evaluation_binding_id=f"evaluation-binding.{evaluator_kind}",
                    evaluation_provider_id="provider.local-evaluator",
                    provider_capabilities=("local-read-only", "read-only"),
                    resource_reservation_digest=epoch.reservation_id,
                ),
            )
        )

    def _candidates(self, epoch: OptimizationEpoch) -> tuple[OptimizationCandidate, ...]:
        return self._required(epoch, "generating", CandidateGenerationResult).candidates

    def _finalist(self, epoch: OptimizationEpoch) -> OptimizationCandidate:
        digest = self._required(
            epoch, "replaying", PipelineReplayResult
        ).finalist_candidate_digest
        try:
            return next(item for item in self._candidates(epoch) if item.candidate_digest == digest)
        except StopIteration as exc:
            raise SharedStateIntegrityError("pipeline finalist is unavailable") from exc

    def _reports(
        self, epoch: OptimizationEpoch
    ) -> tuple[OptimizationEvaluationReport, ...]:
        replay = self._required(epoch, "replaying", PipelineReplayResult)
        holdout = self._required(epoch, "holdout_evaluating", PipelineHoldoutResult)
        return tuple(
            sorted((*replay.reports, holdout.report), key=lambda item: item.report_digest)
        )

    def _required(self, epoch: OptimizationEpoch, stage: str, model: type[T]) -> T:
        value = self.store.read(epoch.epoch_id, stage, model)
        if value is None:
            raise SharedStateIntegrityError("optimization pipeline prerequisite is missing")
        return value


def _select_finalist(
    candidates: tuple[OptimizationCandidate, ...],
    reports: tuple[OptimizationEvaluationReport, ...],
) -> str | None:
    eligible: list[str] = []
    for item in candidates:
        related = tuple(
            report
            for report in reports
            if report.candidate_digest == item.candidate_digest
        )
        if related and all(
            report.recommendation == "finalist_eligible" for report in related
        ):
            eligible.append(item.candidate_digest)
    return min(eligible) if eligible else None


def _no_change(reason: str) -> OptimizationStepResult:
    return OptimizationStepResult(next_state="no_change", reason=reason)
