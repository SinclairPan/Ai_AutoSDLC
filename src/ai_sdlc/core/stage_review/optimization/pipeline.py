"""无候选域分支的固定 Offline Optimization Pipeline。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.controller_models import (
    MaintenanceBudget,
    OptimizationConstitution,
    OptimizationEpoch,
    OptimizationStepResult,
    resolve_optimization_constitution,
)
from ai_sdlc.core.stage_review.optimization.evaluators import (
    EvaluationContext,
    OptimizationEvaluatorRegistry,
    component_module_runtime_identity,
    component_runtime_digest,
    optimization_runtime_identity_snapshot,
)
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationEvaluationReport,
    OptimizationStatisticsPolicy,
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
    PromotionAuthorizationPort,
    PromotionEvaluationPort,
    ShadowObservationPort,
    SnapshotPublicationPort,
)
from ai_sdlc.core.stage_review.optimization.pipeline_effects import (
    EpochRuntimeAuthorizer,
    PipelineEffects,
)
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
from ai_sdlc.core.stage_review.optimization.statistics import (
    baseline_statistics_policy,
)
from ai_sdlc.core.stage_review.panel_models import ReviewerBudgetPolicy

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class OptimizationRuntimeBundle:
    constitution: OptimizationConstitution
    maintenance_budget_limit: MaintenanceBudget
    evaluator_registry: OptimizationEvaluatorRegistry
    replay_evaluator_kinds: tuple[str, ...]
    dataset_port: DatasetSnapshotPort
    candidate_port: CandidateGenerationPort
    holdout_port: HoldoutEvaluationPort
    shadow_port: ShadowObservationPort
    promotion_port: PromotionEvaluationPort
    publication_port: SnapshotPublicationPort
    domain_registry_digest: str
    statistics_policy: OptimizationStatisticsPolicy

    def __post_init__(self) -> None:
        constitution = self.constitution
        lineage = (
            self.evaluator_registry.registry_digest
            == constitution.evaluator_registry_digest,
            self.promotion_port.policy_digest
            == constitution.auto_promotion_policy_digest,
            self.domain_registry_digest
            == constitution.candidate_domain_registry_digest,
            self.statistics_policy.policy_digest
            == constitution.statistics_policy_digest,
            self.statistics_policy.familywise_alpha == constitution.familywise_alpha,
            bool(self.replay_evaluator_kinds),
            self.replay_evaluator_kinds
            == tuple(sorted(set(self.replay_evaluator_kinds))),
        )
        if not all(lineage):
            raise ValueError("optimization runtime bundle lineage diverged")

    @property
    def manifest_digest(self) -> str:
        with optimization_runtime_identity_snapshot():
            return self._manifest_digest()

    def _manifest_digest(self) -> str:
        from ai_sdlc.core.stage_review.canonical import (
            CanonicalizationPolicy,
            canonical_digest,
        )

        return canonical_digest(
            {
                "constitution_digest": self.constitution.constitution_digest,
                "epoch_budget_policy_digest": (
                    self.constitution.epoch_budget_policy_digest
                ),
                "attribution_policy_digest": (
                    self.constitution.attribution_policy_digest
                ),
                "storage_policy_digest": self.constitution.storage_policy_digest,
                "candidate_domain_registry_digest": self.domain_registry_digest,
                "statistics_policy_digest": self.statistics_policy.policy_digest,
                "evaluator_registry_digest": (self.evaluator_registry.registry_digest),
                "evaluator_implementation_digest": (
                    self.evaluator_registry.implementation_digest
                ),
                "replay_evaluator_kinds": self.replay_evaluator_kinds,
                "maintenance_budget_limit": self.maintenance_budget_limit,
                "dataset_runtime_digest": (component_runtime_digest(self.dataset_port)),
                "candidate_runtime_digest": (
                    component_runtime_digest(self.candidate_port)
                ),
                "holdout_runtime_digest": (component_runtime_digest(self.holdout_port)),
                "shadow_runtime_digest": (component_runtime_digest(self.shadow_port)),
                "promotion_runtime_digest": (
                    component_runtime_digest(self.promotion_port)
                ),
                "publication_runtime_digest": (
                    component_runtime_digest(self.publication_port)
                ),
                "pipeline_engine_runtime_identity": (
                    component_module_runtime_identity(
                        OptimizationPipelineExecutor
                    )
                ),
            },
            CanonicalizationPolicy(),
        )


class OptimizationPipelineExecutor:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        minimum_evaluable_sessions: int,
        candidate_family_limit: int,
        evaluator_registry: OptimizationEvaluatorRegistry,
        evaluator_registry_digest: str,
        configured_constitution: OptimizationConstitution,
        replay_evaluator_kinds: tuple[str, ...],
        dataset_port: DatasetSnapshotPort,
        candidate_port: CandidateGenerationPort,
        holdout_port: HoldoutEvaluationPort,
        shadow_port: ShadowObservationPort,
        promotion_port: PromotionEvaluationPort,
        promotion_policy_digest: str,
        publication_port: SnapshotPublicationPort,
        promotion_authority: PromotionAuthorizationPort,
        runtime_bundles: Mapping[str, OptimizationRuntimeBundle] | None = None,
        runtime_budget_policies: Mapping[str, ReviewerBudgetPolicy] | None = None,
        domain_registry_digest: str = "",
        statistics_policy: OptimizationStatisticsPolicy | None = None,
        familywise_alpha: float = 0.05,
    ) -> None:
        if minimum_evaluable_sessions < 1 or candidate_family_limit < 1:
            raise ValueError("optimization pipeline limits must be positive")
        if not replay_evaluator_kinds or replay_evaluator_kinds != tuple(
            sorted(set(replay_evaluator_kinds))
        ):
            raise ValueError("replay evaluator kinds must be canonical")
        self.constitution = OptimizationConstitution.model_validate(
            configured_constitution.model_dump(mode="json")
        )
        if (
            minimum_evaluable_sessions != self.constitution.minimum_evaluable_sessions
            or candidate_family_limit != self.constitution.candidate_family_limit
        ):
            raise ValueError("optimization pipeline limit lineage diverged")
        if evaluator_registry_digest != evaluator_registry.registry_digest:
            raise ValueError("optimization evaluator registry lineage diverged")
        if evaluator_registry_digest != self.constitution.evaluator_registry_digest:
            raise ValueError("optimization constitution evaluator registry diverged")
        if not promotion_policy_digest.strip():
            raise ValueError("optimization promotion policy digest is required")
        if promotion_policy_digest != self.constitution.auto_promotion_policy_digest:
            raise ValueError("optimization constitution promotion policy diverged")
        self.statistics_policy = statistics_policy or baseline_statistics_policy()
        if not 0 < familywise_alpha < 1:
            raise ValueError("optimization familywise alpha is invalid")
        if familywise_alpha != self.statistics_policy.familywise_alpha:
            raise ValueError(
                "optimization familywise alpha diverged from statistics policy"
            )
        current_bundle = OptimizationRuntimeBundle(
            constitution=self.constitution,
            maintenance_budget_limit=MaintenanceBudget(),
            evaluator_registry=evaluator_registry,
            replay_evaluator_kinds=replay_evaluator_kinds,
            dataset_port=dataset_port,
            candidate_port=candidate_port,
            holdout_port=holdout_port,
            shadow_port=shadow_port,
            promotion_port=promotion_port,
            publication_port=publication_port,
            domain_registry_digest=(
                domain_registry_digest
                or self.constitution.candidate_domain_registry_digest
            ),
            statistics_policy=self.statistics_policy,
        )
        self.runtime_bundles = {
            self.constitution.constitution_digest: current_bundle,
            **dict(runtime_bundles or {}),
        }
        if any(
            digest != bundle.constitution.constitution_digest
            for digest, bundle in self.runtime_bundles.items()
        ):
            raise ValueError("optimization runtime bundle identity diverged")
        manifests = tuple(
            bundle.manifest_digest for bundle in self.runtime_bundles.values()
        )
        if len(manifests) != len(set(manifests)):
            raise ValueError("optimization runtime bundle manifest collided")
        self.runtime_budget_policies = dict(runtime_budget_policies or {})
        if self.runtime_budget_policies and (
            set(self.runtime_budget_policies) != set(self.runtime_bundles)
        ):
            raise ValueError("optimization runtime budget coverage diverged")
        self.store = OptimizationPipelineStore(root, project_id=project_id)
        self.promotion_authority = promotion_authority

    def advance(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        *,
        authorize_effect: Callable[[], None],
    ) -> OptimizationStepResult:
        runtime = self._runtime(epoch)
        self._validate_budget(runtime, budget)
        require_epoch_domain_registry(epoch, runtime.domain_registry_digest)
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
            raise SharedStateIntegrityError(
                "optimization pipeline state is invalid"
            ) from exc
        runtime_authorizer = EpochRuntimeAuthorizer.for_epoch(
            authorize_effect,
            lambda: self._runtime(epoch),
            epoch,
        )
        return handler(
            epoch,
            budget,
            PipelineEffects(self.store, runtime_authorizer),
        )

    def _snapshot(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        effects: PipelineEffects,
    ) -> OptimizationStepResult:
        del budget
        runtime = self._runtime(epoch)
        result = self.store.read(epoch.epoch_id, "snapshotting", PipelineSnapshotResult)
        if result is None:
            frozen = effects.call(
                lambda: runtime.dataset_port.freeze(epoch, effects.authorize)
            )
            result = effects.write(epoch.epoch_id, "snapshotting", frozen)
        if (
            result.evaluable_session_count
            < runtime.constitution.minimum_evaluable_sessions
        ):
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
        runtime = self._runtime(epoch)
        dataset = self._required(epoch, "snapshotting", PipelineSnapshotResult)
        result = self.store.read(
            epoch.epoch_id, "generating", CandidateGenerationResult
        )
        if result is None:
            generated = effects.call(
                lambda: runtime.candidate_port.generate(
                    epoch,
                    dataset,
                    runtime.constitution.candidate_family_limit,
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
        if len(result.candidates) > runtime.constitution.candidate_family_limit:
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
        replay_evaluator_kinds = self._runtime(epoch).replay_evaluator_kinds
        cached = self.store.read(epoch.epoch_id, "replaying", PipelineReplayResult)
        if cached is None:
            candidates = self._candidates(epoch)
            raw_reports = tuple(
                self._evaluate_candidate(epoch, candidate, evaluator_kind, effects)
                for candidate in candidates
                for evaluator_kind in replay_evaluator_kinds
            )
            reports = apply_holm_bonferroni(
                raw_reports,
                familywise_alpha=self._statistics_policy(epoch).familywise_alpha,
            )
            finalist = _select_finalist(candidates, reports)
            cached = effects.write(
                epoch.epoch_id,
                "replaying",
                PipelineReplayResult(
                    reports=tuple(sorted(reports, key=lambda item: item.report_digest)),
                    finalist_candidate_digest="" if finalist is None else finalist,
                ),
            )
        self._verify_replay_result(
            epoch,
            self._candidates(epoch),
            cached,
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
        policy = self._statistics_policy(epoch)
        holdout_port = self._holdout_port(epoch)
        cached = self.store.read(
            epoch.epoch_id, "holdout_evaluating", PipelineHoldoutResult
        )
        if cached is None:
            report = effects.call(
                lambda: holdout_port.evaluate(
                    epoch, self._finalist(epoch), effects.authorize
                )
            )
            if report.statistics_policy_digest != policy.policy_digest:
                raise SharedStateIntegrityError("holdout statistics policy diverged")
            self._verify_holdout_report(
                epoch,
                report,
                policy,
                holdout_port,
            )
            cached = effects.write(
                epoch.epoch_id,
                "holdout_evaluating",
                PipelineHoldoutResult(report=report),
            )
        if cached.report.statistics_policy_digest != policy.policy_digest:
            raise SharedStateIntegrityError("holdout statistics policy diverged")
        self._verify_holdout_report(
            epoch,
            cached.report,
            policy,
            holdout_port,
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
        policy = self._statistics_policy(epoch)
        shadow_port = self._runtime(epoch).shadow_port
        cached = self.store.read(
            epoch.epoch_id, "shadow_observing", PipelineShadowResult
        )
        if cached is None:
            try:
                observed = effects.call(
                    lambda: shadow_port.observe(
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
                if observed.reason == "shadow_outcome_maturity_expired":
                    return _no_change(observed.reason)
                return OptimizationStepResult(
                    next_state="shadow_observing", reason=observed.reason
                )
            if observed.statistics_policy_digest != policy.policy_digest:
                raise SharedStateIntegrityError("shadow statistics policy diverged")
            cached = effects.write(epoch.epoch_id, "shadow_observing", observed)
        if not cached.complete:
            raise SharedStateIntegrityError(
                "committed shadow evidence must be complete"
            )
        if cached.statistics_policy_digest != policy.policy_digest:
            raise SharedStateIntegrityError("shadow statistics policy diverged")
        self._verify_shadow_result(cached, policy)
        return OptimizationStepResult(next_state="evaluating")

    def _evaluate(
        self,
        epoch: OptimizationEpoch,
        budget: MaintenanceBudget,
        effects: PipelineEffects,
    ) -> OptimizationStepResult:
        del budget
        self._verify_epoch_evidence(epoch)
        cached = self.store.read(epoch.epoch_id, "evaluating", PipelinePromotionPackage)
        if cached is None:
            reports = self._reports(epoch)
            shadow = self._required(epoch, "shadow_observing", PipelineShadowResult)
            package = effects.call(
                lambda: self._promotion_port(epoch).evaluate(
                    epoch, self._finalist(epoch), reports, shadow
                )
            )
            cached = effects.write(epoch.epoch_id, "evaluating", package)
        self._verify_promotion_package(epoch, cached)
        authorization = self.promotion_authority.promotion_authorization(
            cached.package_digest
        )
        if authorization is None:
            fencing_epoch, claim_digest = effects.epoch_fencing_identity()
            authorization = effects.commit(
                lambda: self.promotion_authority.issue_promotion_authorization(
                    epoch,
                    cached,
                    fencing_epoch=fencing_epoch,
                    claim_digest=claim_digest,
                )
            )
        self.promotion_authority.verify_promotion_authorization(
            authorization,
            cached,
        )
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
        publication_port = self._runtime(epoch).publication_port
        self._verify_epoch_evidence(epoch)
        package = self._required(epoch, "evaluating", PipelinePromotionPackage)
        self._verify_promotion_package(epoch, package)
        published = self.store.read(
            epoch.epoch_id, "promoting", PipelinePublicationResult
        )
        if published is None:
            result = effects.call(
                lambda: publication_port.promote(package, effects.authorize)
            )
            self._verify_publication_result(package, result)
            published = effects.write(
                epoch.epoch_id,
                "promoting",
                result,
            )
        self._verify_publication_result(package, published)
        publication_port.validate_cached(package, published)
        return OptimizationStepResult(next_state="promoted")

    def _evaluate_candidate(
        self,
        epoch: OptimizationEpoch,
        candidate: OptimizationCandidate,
        evaluator_kind: str,
        effects: PipelineEffects,
    ) -> OptimizationEvaluationReport:
        policy = self._statistics_policy(epoch)
        return effects.call(
            lambda: self._evaluator_registry(epoch).evaluate(
                evaluator_kind=evaluator_kind,
                candidate=candidate,
                context=self._replay_evaluation_context(
                    epoch,
                    evaluator_kind,
                    policy,
                ),
            )
        )

    def _statistics_policy(
        self,
        epoch: OptimizationEpoch,
    ) -> OptimizationStatisticsPolicy:
        return self._runtime(epoch).statistics_policy

    def _verify_epoch_evidence(self, epoch: OptimizationEpoch) -> None:
        policy = self._statistics_policy(epoch)
        replay = self._required(epoch, "replaying", PipelineReplayResult)
        self._verify_replay_result(epoch, self._candidates(epoch), replay)
        holdout = self._required(
            epoch,
            "holdout_evaluating",
            PipelineHoldoutResult,
        )
        self._verify_holdout_report(
            epoch,
            holdout.report,
            policy,
            self._holdout_port(epoch),
        )
        shadow = self._required(
            epoch,
            "shadow_observing",
            PipelineShadowResult,
        )
        self._verify_shadow_result(shadow, policy)

    def _verify_replay_result(
        self,
        epoch: OptimizationEpoch,
        candidates: tuple[OptimizationCandidate, ...],
        replay: PipelineReplayResult,
    ) -> None:
        policy = self._statistics_policy(epoch)
        replay_evaluator_kinds = self._runtime(epoch).replay_evaluator_kinds
        expected_coverage = {
            (candidate.candidate_digest, evaluator_kind)
            for candidate in candidates
            for evaluator_kind in replay_evaluator_kinds
        }
        actual_coverage = {
            (report.candidate_digest, report.evaluator_kind)
            for report in replay.reports
        }
        if (
            not replay.reports
            or len(replay.reports) != len(expected_coverage)
            or actual_coverage != expected_coverage
            or any(
                report.statistics_policy_digest != policy.policy_digest
                for report in replay.reports
            )
        ):
            raise SharedStateIntegrityError("replay statistics policy lineage diverged")
        by_digest = {item.candidate_digest: item for item in candidates}
        try:
            for report in replay.reports:
                self._evaluator_registry(epoch).validate_cached_report(
                    evaluator_kind=report.evaluator_kind,
                    candidate=by_digest[report.candidate_digest],
                    context=self._replay_evaluation_context(
                        epoch,
                        report.evaluator_kind,
                        policy,
                    ),
                    report=report,
                )
        except (KeyError, ValueError) as exc:
            raise SharedStateIntegrityError("replay evidence lineage diverged") from exc
        expected_reports = apply_holm_bonferroni(
            replay.reports,
            familywise_alpha=policy.familywise_alpha,
        )
        if expected_reports != replay.reports:
            raise SharedStateIntegrityError("replay statistical correction diverged")
        expected_finalist = _select_finalist(candidates, replay.reports)
        if replay.finalist_candidate_digest != (
            "" if expected_finalist is None else expected_finalist
        ):
            raise SharedStateIntegrityError("replay finalist lineage diverged")

    def _verify_holdout_report(
        self,
        epoch: OptimizationEpoch,
        report: OptimizationEvaluationReport,
        policy: OptimizationStatisticsPolicy,
        holdout_port: HoldoutEvaluationPort,
    ) -> None:
        sequence = report.holdout_test_sequence
        expected_alpha = (
            0.0
            if sequence < 1
            else policy.familywise_alpha / (sequence * (sequence + 1))
        )
        lineage = (
            report.partition == "holdout",
            report.candidate_digest == self._finalist(epoch).candidate_digest,
            report.dataset_digest == epoch.dataset_digest,
            report.domain_contract_digest
            == self._finalist(epoch).domain_contract_digest,
            report.domain_adapter_id == self._finalist(epoch).domain_adapter_id,
            report.domain_adapter_version
            == self._finalist(epoch).domain_adapter_version,
            report.domain_adapter_digest == self._finalist(epoch).domain_adapter_digest,
            report.domain_registry_digest
            == self._finalist(epoch).domain_registry_digest,
            report.evaluator_kind == "fixed-holdout",
            report.evaluator_version
            == self._evaluator_registry(epoch)
            .contract("fixed-holdout")
            .evaluator_version,
            report.evaluator_contract_digest
            == self._evaluator_registry(epoch)
            .contract("fixed-holdout")
            .contract_digest,
            report.evaluation_binding_id == "evaluation-binding.local-holdout-v1",
            report.statistics_policy_digest == policy.policy_digest,
            sequence >= 1,
            report.holm_rank == sequence,
            report.statistical_alpha == expected_alpha,
            report.holdout_alpha == expected_alpha,
            report.holm_threshold == expected_alpha,
        )
        if not all(lineage):
            raise SharedStateIntegrityError("holdout evidence lineage diverged")
        holdout_port.validate_cached(
            epoch,
            self._finalist(epoch),
            report,
        )

    @staticmethod
    def _verify_shadow_result(
        shadow: PipelineShadowResult,
        policy: OptimizationStatisticsPolicy,
    ) -> None:
        lineage = (
            shadow.complete,
            shadow.statistics_policy_digest == policy.policy_digest,
            shadow.statistical_alpha == policy.shadow_alpha,
        )
        if not all(lineage):
            raise SharedStateIntegrityError(
                "shadow statistical design lineage diverged"
            )

    def _candidates(
        self, epoch: OptimizationEpoch
    ) -> tuple[OptimizationCandidate, ...]:
        return self._required(epoch, "generating", CandidateGenerationResult).candidates

    def _finalist(self, epoch: OptimizationEpoch) -> OptimizationCandidate:
        digest = self._required(
            epoch, "replaying", PipelineReplayResult
        ).finalist_candidate_digest
        try:
            return next(
                item
                for item in self._candidates(epoch)
                if item.candidate_digest == digest
            )
        except StopIteration as exc:
            raise SharedStateIntegrityError("pipeline finalist is unavailable") from exc

    def _reports(
        self, epoch: OptimizationEpoch
    ) -> tuple[OptimizationEvaluationReport, ...]:
        replay = self._required(epoch, "replaying", PipelineReplayResult)
        holdout = self._required(epoch, "holdout_evaluating", PipelineHoldoutResult)
        return tuple(
            sorted(
                (*replay.reports, holdout.report), key=lambda item: item.report_digest
            )
        )

    def _constitution(
        self,
        epoch: OptimizationEpoch,
    ) -> OptimizationConstitution:
        return self._runtime(epoch).constitution

    def _runtime(
        self,
        epoch: OptimizationEpoch,
    ) -> OptimizationRuntimeBundle:
        if not epoch.statistics_policy_digest.strip():
            raise ValueError("statistics policy digest is required")
        try:
            constitution = resolve_optimization_constitution(
                epoch.constitution_digest,
                configured_constitution=self.constitution,
            )
        except ValueError as exc:
            raise SharedStateIntegrityError(str(exc)) from exc
        lineage = (
            epoch.candidate_domain_registry_digest
            == constitution.candidate_domain_registry_digest,
            epoch.statistics_policy_digest == constitution.statistics_policy_digest,
            epoch.evaluator_registry_digest == constitution.evaluator_registry_digest,
            epoch.auto_promotion_policy_digest
            == constitution.auto_promotion_policy_digest,
        )
        if not all(lineage):
            raise SharedStateIntegrityError(
                "optimization epoch constitution lineage diverged"
            )
        try:
            bundle = self.runtime_bundles[epoch.constitution_digest]
        except KeyError as exc:
            raise SharedStateIntegrityError(
                "optimization runtime bundle is unavailable"
            ) from exc
        if bundle.constitution != constitution:
            raise SharedStateIntegrityError(
                "optimization runtime bundle constitution diverged"
            )
        if epoch.runtime_bundle_manifest_digest != bundle.manifest_digest:
            raise SharedStateIntegrityError(
                "optimization epoch runtime bundle manifest diverged"
            )
        return bundle

    def _evaluator_registry(
        self,
        epoch: OptimizationEpoch,
    ) -> OptimizationEvaluatorRegistry:
        return self._runtime(epoch).evaluator_registry

    def _holdout_port(
        self,
        epoch: OptimizationEpoch,
    ) -> HoldoutEvaluationPort:
        return self._runtime(epoch).holdout_port

    def _promotion_port(
        self,
        epoch: OptimizationEpoch,
    ) -> PromotionEvaluationPort:
        return self._runtime(epoch).promotion_port

    @staticmethod
    def _validate_budget(
        runtime: OptimizationRuntimeBundle,
        budget: MaintenanceBudget,
    ) -> None:
        limit = runtime.maintenance_budget_limit
        fields = (
            "maximum_provider_calls",
            "maximum_tokens",
            "maximum_cost",
            "maximum_active_wall_clock",
            "maximum_parallelism",
        )
        if any(getattr(budget, field) > getattr(limit, field) for field in fields):
            raise SharedStateIntegrityError("optimization epoch budget policy diverged")

    def _replay_evaluation_context(
        self,
        epoch: OptimizationEpoch,
        evaluator_kind: str,
        policy: OptimizationStatisticsPolicy,
    ) -> EvaluationContext:
        return EvaluationContext(
            dataset_digest=epoch.dataset_digest,
            partition="validation",
            evaluation_binding_id=f"evaluation-binding.{evaluator_kind}",
            evaluation_provider_id="provider.local-evaluator",
            provider_capabilities=("local-read-only", "read-only"),
            resource_reservation_digest=epoch.reservation_id,
            statistics_policy_digest=policy.policy_digest,
            statistical_alpha=policy.familywise_alpha,
        )

    def _verify_promotion_package(
        self,
        epoch: OptimizationEpoch,
        package: PipelinePromotionPackage,
    ) -> None:
        reports = self._reports(epoch)
        shadow = self._required(
            epoch,
            "shadow_observing",
            PipelineShadowResult,
        )
        verify_promotion_package(
            epoch,
            self._finalist(epoch),
            tuple(item.report_digest for item in reports),
            shadow,
            package,
            expected_policy_digest=epoch.auto_promotion_policy_digest,
        )
        self._promotion_port(epoch).validate_cached(
            epoch,
            self._finalist(epoch),
            reports,
            shadow,
            package,
        )

    @staticmethod
    def _verify_publication_result(
        package: PipelinePromotionPackage,
        publication: PipelinePublicationResult,
    ) -> None:
        lineage = (
            publication.promotion_package_digest == package.package_digest,
            publication.decision_digest == package.decision.decision_digest,
            publication.snapshot_digest == package.snapshot.snapshot_digest,
            publication.shadow_result_digest == package.snapshot.shadow_result_digest,
            publication.evaluation_report_digests
            == package.snapshot.evaluation_report_digests,
            publication.promotion_policy_digest == package.decision.policy_digest,
        )
        if not all(lineage):
            raise SharedStateIntegrityError("publication result lineage diverged")

    def _required(self, epoch: OptimizationEpoch, stage: str, model: type[T]) -> T:
        value = self.store.read(epoch.epoch_id, stage, model)
        if value is None:
            raise SharedStateIntegrityError(
                "optimization pipeline prerequisite is missing"
            )
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
