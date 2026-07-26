"""组装唯一可运行的项目本地离线优化产品流水线。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.attribution_store import (
    FindingAttributionStore,
)
from ai_sdlc.core.stage_review.optimization.candidate_domain_defaults import (
    default_candidate_domain_registry,
)
from ai_sdlc.core.stage_review.optimization.candidate_domain_registry import (
    CandidateDomainRegistry,
)
from ai_sdlc.core.stage_review.optimization.candidate_generation import (
    LocalCandidateGenerationPort,
)
from ai_sdlc.core.stage_review.optimization.controller_models import (
    MaintenanceBudget,
    OptimizationConstitution,
    bundled_optimization_constitutions,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_evaluator_contract as baseline_evaluator_contract,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    baseline_auto_promotion_policy,
    baseline_constitution,
    baseline_epoch_budget_policy,
    baseline_offline_capacity,
)
from ai_sdlc.core.stage_review.optimization.evaluators import (
    OptimizationEvaluatorRegistry,
    component_runtime_identity,
    fixed_holdout_evaluator_contract,
    has_explicit_runtime_identity,
)
from ai_sdlc.core.stage_review.optimization.holdout_store import (
    HoldoutCommitmentStore,
)
from ai_sdlc.core.stage_review.optimization.local_evaluation import (
    LocalCandidateEvaluator,
    LocalEvaluationStatisticsAuthority,
)
from ai_sdlc.core.stage_review.optimization.local_holdout import (
    LocalHoldoutEvaluationPort,
)
from ai_sdlc.core.stage_review.optimization.local_promotion import (
    LocalPromotionEvaluationPort,
)
from ai_sdlc.core.stage_review.optimization.local_shadow import (
    LocalProspectiveShadowPort,
)
from ai_sdlc.core.stage_review.optimization.models import OptimizationStatisticsPolicy
from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBindingStore,
    OptimizationObservationStore,
)
from ai_sdlc.core.stage_review.optimization.pipeline import (
    OptimizationPipelineExecutor,
    OptimizationRuntimeBundle,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    PipelinePromotionPackage,
    PipelinePublicationResult,
    PromotionAuthorizationPort,
)
from ai_sdlc.core.stage_review.optimization.pipeline_effects import commit_effect
from ai_sdlc.core.stage_review.optimization.pipeline_store import (
    OptimizationPipelineStore,
)
from ai_sdlc.core.stage_review.optimization.product_shadow_executor import (
    build_product_shadow_executor,
)
from ai_sdlc.core.stage_review.optimization.promotion import AutoPromotionGate
from ai_sdlc.core.stage_review.optimization.runtime_dataset import (
    LocalDatasetSnapshotPort,
)
from ai_sdlc.core.stage_review.optimization.shadow import (
    OptimizationShadowAssignmentStore,
)
from ai_sdlc.core.stage_review.optimization.shadow_execution import (
    ShadowAssignmentExecutor,
)
from ai_sdlc.core.stage_review.optimization.shadow_observations import (
    OptimizationShadowObservationStore,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import OptimizationSnapshot
from ai_sdlc.core.stage_review.optimization.snapshots import SnapshotControlService
from ai_sdlc.core.stage_review.optimization.statistics import (
    statistics_policy_for_digest,
)
from ai_sdlc.core.stage_review.panel_models import ReviewerBudgetPolicy
from ai_sdlc.core.stage_review.provider_journal import ProviderInvocationJournal
from ai_sdlc.core.stage_review.provider_usage_models import ProviderUsageEstimatePolicy
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resources import ResourceGovernor


@dataclass(frozen=True, slots=True)
class ProductOptimizationRuntimeContext:
    root: Path
    project_id: str
    snapshots: SnapshotControlService
    bindings: CommittedSessionBindingStore
    observations: OptimizationObservationStore
    journal: ProviderInvocationJournal
    resources: ResourceGovernor
    clock: Callable[[], str]
    domains: CandidateDomainRegistry
    attributions: FindingAttributionStore


@dataclass(frozen=True, slots=True)
class ProductOptimizationRuntimeFactory:
    constitution_factory: Callable[[], OptimizationConstitution]
    budget_policy_factory: Callable[[], ReviewerBudgetPolicy]
    bundle_builder: Callable[
        [ProductOptimizationRuntimeContext, OptimizationConstitution],
        OptimizationRuntimeBundle,
    ]

    def build(
        self,
        context: ProductOptimizationRuntimeContext,
    ) -> tuple[OptimizationRuntimeBundle, ReviewerBudgetPolicy]:
        constitution = self.constitution_factory()
        policy = self.budget_policy_factory()
        bundle = self.bundle_builder(context, constitution)
        if (
            bundle.constitution != constitution
            or policy.policy_digest != constitution.epoch_budget_policy_digest
        ):
            raise ValueError("product optimization runtime factory diverged")
        bundle.evaluator_registry.require_explicit_runtime_identity()
        components = (
            bundle.dataset_port,
            bundle.candidate_port,
            bundle.holdout_port,
            bundle.shadow_port,
            bundle.promotion_port,
            bundle.publication_port,
        )
        if any(
            not has_explicit_runtime_identity(component)
            for component in components
        ):
            raise ValueError(
                "product optimization runtime identity is not explicit"
            )
        return bundle, policy


def product_optimization_runtime_factories() -> tuple[
    ProductOptimizationRuntimeFactory, ...
]:
    """随发布版本保留完整 Bundle 构建器；新增 Constitution 必须同步登记。"""
    return (
        ProductOptimizationRuntimeFactory(
            constitution_factory=baseline_constitution,
            budget_policy_factory=baseline_epoch_budget_policy,
            bundle_builder=_build_baseline_runtime_bundle,
        ),
    )


def _build_product_optimization_pipeline(
    root: Path,
    *,
    project_id: str,
    snapshots: SnapshotControlService,
    bindings: CommittedSessionBindingStore,
    observations: OptimizationObservationStore,
    journal: ProviderInvocationJournal,
    resources: ResourceGovernor,
    clock: Callable[[], str],
    domain_registry: CandidateDomainRegistry | None = None,
) -> OptimizationPipelineExecutor:
    attributions = FindingAttributionStore(root, project_id=project_id)
    domains = domain_registry or default_candidate_domain_registry()
    context = ProductOptimizationRuntimeContext(
        root=root,
        project_id=project_id,
        snapshots=snapshots,
        bindings=bindings,
        observations=observations,
        journal=journal,
        resources=resources,
        clock=clock,
        domains=domains,
        attributions=attributions,
    )
    bundles, budgets = _build_product_runtime_registry(context)
    constitution = baseline_constitution()
    try:
        active = bundles[constitution.constitution_digest]
    except KeyError as exc:
        raise ValueError("active product optimization runtime is unavailable") from exc
    return _pipeline_executor(
        context,
        active,
        bundles,
        budgets,
    )


def _build_product_runtime_registry(
    context: ProductOptimizationRuntimeContext,
) -> tuple[
    dict[str, OptimizationRuntimeBundle],
    dict[str, ReviewerBudgetPolicy],
]:
    factories = product_optimization_runtime_factories()
    expected = {
        item.constitution_digest for item in bundled_optimization_constitutions()
    }
    bundles: dict[str, OptimizationRuntimeBundle] = {}
    budgets: dict[str, ReviewerBudgetPolicy] = {}
    for factory in factories:
        bundle, policy = factory.build(context)
        digest = bundle.constitution.constitution_digest
        if digest in bundles:
            raise ValueError("product optimization runtime factory duplicated")
        bundles[digest] = bundle
        budgets[digest] = policy
    if set(bundles) != expected or set(budgets) != expected:
        raise ValueError("product optimization runtime factory coverage diverged")
    return bundles, budgets


def _pipeline_executor(
    context: ProductOptimizationRuntimeContext,
    active: OptimizationRuntimeBundle,
    bundles: Mapping[str, OptimizationRuntimeBundle],
    budgets: Mapping[str, ReviewerBudgetPolicy],
) -> OptimizationPipelineExecutor:
    constitution = active.constitution
    return OptimizationPipelineExecutor(
        context.root,
        project_id=context.project_id,
        minimum_evaluable_sessions=constitution.minimum_evaluable_sessions,
        candidate_family_limit=constitution.candidate_family_limit,
        evaluator_registry=active.evaluator_registry,
        evaluator_registry_digest=constitution.evaluator_registry_digest,
        configured_constitution=constitution,
        replay_evaluator_kinds=active.replay_evaluator_kinds,
        dataset_port=active.dataset_port,
        candidate_port=active.candidate_port,
        holdout_port=active.holdout_port,
        shadow_port=active.shadow_port,
        promotion_port=active.promotion_port,
        promotion_policy_digest=constitution.auto_promotion_policy_digest,
        publication_port=active.publication_port,
        promotion_authority=context.snapshots.promotion_authority,
        runtime_bundles=bundles,
        runtime_budget_policies=budgets,
        domain_registry_digest=active.domain_registry_digest,
        statistics_policy=active.statistics_policy,
        familywise_alpha=active.statistics_policy.familywise_alpha,
    )


def _build_baseline_runtime_bundle(
    context: ProductOptimizationRuntimeContext,
    constitution: OptimizationConstitution,
) -> OptimizationRuntimeBundle:
    statistics_policy = statistics_policy_for_digest(
        constitution.statistics_policy_digest
    )
    holdout = _holdout_store(
        context.root,
        context.project_id,
        statistics_policy.familywise_alpha,
    )
    dataset = _dataset_port(
        context.root,
        project_id=context.project_id,
        snapshots=context.snapshots,
        bindings=context.bindings,
        observations=context.observations,
        holdout=holdout,
        clock=context.clock,
        attribution_source=context.attributions.attributions,
    )
    registry = _evaluator_registry(
        dataset,
        context.attributions,
        context.domains,
        statistics_policy,
    )
    snapshot_source = _snapshot_source(context.snapshots)
    return OptimizationRuntimeBundle(
        constitution=constitution,
        maintenance_budget_limit=MaintenanceBudget(),
        evaluator_registry=registry,
        replay_evaluator_kinds=("population-metrics",),
        dataset_port=dataset,
        candidate_port=_candidate_port(
            context.project_id,
            snapshot_source,
            dataset,
            context.domains,
        ),
        holdout_port=LocalHoldoutEvaluationPort(
            store=holdout,
            dataset_source=dataset.load,
            attribution_source=context.attributions.attributions,
            domain_registry=context.domains,
            statistics_policy=statistics_policy,
        ),
        shadow_port=_product_shadow_port(
            context.root,
            context.project_id,
            context.bindings,
            context.observations,
            context.journal,
            context.resources,
            snapshot_source,
            context.clock,
            context.domains,
            constitution,
            statistics_policy,
        ),
        promotion_port=_promotion_port(
            snapshot_source,
            context.attributions,
            context.clock,
            context.domains,
        ),
        publication_port=SnapshotPublication(
            context.snapshots,
            OptimizationPipelineStore(
                context.root,
                project_id=context.project_id,
            ),
            context.snapshots.promotion_authority,
        ),
        domain_registry_digest=context.domains.snapshot_digest,
        statistics_policy=statistics_policy,
    )


def _holdout_store(
    root: Path, project_id: str, familywise_alpha: float
) -> HoldoutCommitmentStore:
    return HoldoutCommitmentStore(
        root,
        project_id=project_id,
        familywise_alpha=familywise_alpha,
    )


def _snapshot_source(
    snapshots: SnapshotControlService,
) -> Callable[[str], OptimizationSnapshot]:
    return lambda digest: _snapshot(snapshots, digest)


def _product_shadow_port(
    root: Path,
    project_id: str,
    bindings: CommittedSessionBindingStore,
    observations: OptimizationObservationStore,
    journal: ProviderInvocationJournal,
    resources: ResourceGovernor,
    snapshot_source: Callable[[str], OptimizationSnapshot],
    clock: Callable[[], str],
    domains: CandidateDomainRegistry,
    constitution: OptimizationConstitution,
    statistics_policy: OptimizationStatisticsPolicy,
) -> LocalProspectiveShadowPort:
    assignments = OptimizationShadowAssignmentStore(root, project_id=project_id)
    results = OptimizationShadowObservationStore(
        root,
        project_id=project_id,
        journal=journal,
    )
    executor = build_product_shadow_executor(
        root,
        project_id=project_id,
        assignments=assignments,
        observations=observations,
        shadow_observations=results,
        journal=journal,
        resources=resources,
        snapshot_source=snapshot_source,
        clock=clock,
    )
    return _shadow_port(
        bindings,
        observations,
        assignments,
        results,
        executor,
        clock,
        snapshot_source,
        domains,
        constitution,
        statistics_policy,
    )


def _evaluator_registry(
    dataset: LocalDatasetSnapshotPort,
    attributions: FindingAttributionStore,
    domains: CandidateDomainRegistry,
    statistics_policy: OptimizationStatisticsPolicy,
) -> OptimizationEvaluatorRegistry:
    registry = OptimizationEvaluatorRegistry(
        statistics_authority=LocalEvaluationStatisticsAuthority(
            dataset_source=dataset.load_digest,
            attribution_source=attributions.attributions,
            domain_registry=domains,
        )
    )
    registry.register(
        baseline_evaluator_contract(domains.domain_ids),
        LocalCandidateEvaluator(
            dataset_source=dataset.load_digest,
            attribution_source=attributions.attributions,
            domain_registry=domains,
            statistics_policy=statistics_policy,
        ),
    )
    registry.register_contract(fixed_holdout_evaluator_contract(domains.domain_ids))
    return registry


def _candidate_port(
    project_id: str,
    snapshot_source: Callable[[str], OptimizationSnapshot],
    dataset: LocalDatasetSnapshotPort,
    domains: CandidateDomainRegistry,
) -> LocalCandidateGenerationPort:
    return LocalCandidateGenerationPort(
        project_id=project_id,
        snapshot_source=snapshot_source,
        candidate_view_source=dataset.candidate_view,
        domain_registry=domains,
    )


def _shadow_port(
    bindings: CommittedSessionBindingStore,
    observations: OptimizationObservationStore,
    assignments: OptimizationShadowAssignmentStore,
    shadow_observations: OptimizationShadowObservationStore,
    executor: ShadowAssignmentExecutor | None,
    clock: Callable[[], str],
    snapshot_source: Callable[[str], OptimizationSnapshot],
    domains: CandidateDomainRegistry,
    constitution: OptimizationConstitution,
    statistics_policy: OptimizationStatisticsPolicy,
) -> LocalProspectiveShadowPort:
    return LocalProspectiveShadowPort(
        assignments=assignments,
        bindings=bindings,
        observations=observations,
        shadow_observations=shadow_observations,
        clock=clock,
        minimum_sessions=constitution.minimum_shadow_sessions,
        minimum_days=constitution.minimum_shadow_days,
        usage_policy_source=lambda digest: ProviderUsageEstimatePolicy.model_validate(
            snapshot_source(digest).policy_payload.get("usage_estimation_policy")
        ),
        executor=executor,
        domain_registry=domains,
        statistics_policy=statistics_policy,
    )


def _promotion_port(
    snapshot_source: Callable[[str], OptimizationSnapshot],
    attributions: FindingAttributionStore,
    clock: Callable[[], str],
    domains: CandidateDomainRegistry,
) -> LocalPromotionEvaluationPort:
    return LocalPromotionEvaluationPort(
        snapshot_source=snapshot_source,
        attribution_source=attributions.attributions,
        gate=AutoPromotionGate(baseline_auto_promotion_policy()),
        resource_capacity=baseline_offline_capacity(),
        clock=clock,
        domain_registry=domains,
    )


def _dataset_port(
    root: Path,
    *,
    project_id: str,
    snapshots: SnapshotControlService,
    bindings: CommittedSessionBindingStore,
    observations: OptimizationObservationStore,
    holdout: HoldoutCommitmentStore,
    clock: Callable[[], str],
    attribution_source: Callable[[], tuple[FindingAttribution, ...]],
) -> LocalDatasetSnapshotPort:
    constitution = baseline_constitution()
    from ai_sdlc.core.stage_review.optimization.datasets import DatasetPolicy

    return LocalDatasetSnapshotPort(
        root,
        project_id=project_id,
        snapshots=snapshots,
        bindings=bindings,
        observations=observations,
        holdout_commitments=holdout,
        policy=DatasetPolicy(
            holdout_ratio=constitution.holdout_ratio,
            minimum_holdout_size=constitution.minimum_holdout_sessions,
        ),
        clock=clock,
        attribution_source=attribution_source,
    )


def _snapshot(snapshots: SnapshotControlService, digest: str) -> OptimizationSnapshot:
    value = snapshots.store.snapshot(digest)
    if value is None:
        raise SharedStateIntegrityError("optimization snapshot is unavailable")
    return value


class SnapshotPublication:
    def __init__(
        self,
        snapshots: SnapshotControlService,
        pipeline_store: OptimizationPipelineStore,
        promotion_authority: PromotionAuthorizationPort,
    ) -> None:
        self.snapshots = snapshots
        self.pipeline_store = pipeline_store
        self.promotion_authority = promotion_authority

    def runtime_identity(self) -> dict[str, object]:
        return {
            "project_id": self.pipeline_store.project_id,
            "snapshot_control": component_runtime_identity(
                self.snapshots
            ),
            "promotion_authority": component_runtime_identity(
                self.promotion_authority
            ),
        }

    def promote(
        self,
        package: PipelinePromotionPackage,
        authorize_effect: Callable[[], None],
    ) -> PipelinePublicationResult:
        committed = self.pipeline_store.read(
            package.epoch_id,
            "evaluating",
            PipelinePromotionPackage,
        )
        if committed != package:
            raise SharedStateIntegrityError(
                "promotion package is not committed by the pipeline"
            )
        authorization = self.promotion_authority.promotion_authorization(
            package.package_digest
        )
        if authorization is None:
            raise SharedStateIntegrityError(
                "promotion authorization receipt is unavailable"
            )
        self.promotion_authority.verify_promotion_authorization(
            authorization,
            package,
        )
        trusted_package = commit_effect(
            authorize_effect,
            lambda: self.snapshots.store.register_promotion_package(
                package,
                authorization_digest=authorization.authorization_digest,
            ),
        )
        snapshot = commit_effect(
            authorize_effect,
            lambda: self.snapshots.store.register_snapshot(trusted_package.snapshot),
        )
        operation_id = stable_id(
            "snapshot-promotion",
            trusted_package.package_digest,
        )
        event = commit_effect(
            authorize_effect,
            lambda: self.snapshots._promote_committed_package(
                snapshot.snapshot_digest,
                promotion_package_digest=trusted_package.package_digest,
                promotion_authorization_digest=(authorization.authorization_digest),
                operation_id=operation_id,
            ),
        )
        if event is None:
            raise SharedStateIntegrityError("snapshot promotion returned no_change")
        result = PipelinePublicationResult(
            control_event_digest=event.event_digest,
            operation_id=operation_id,
            promotion_package_digest=trusted_package.package_digest,
            decision_digest=trusted_package.decision.decision_digest,
            snapshot_digest=trusted_package.snapshot.snapshot_digest,
            shadow_result_digest=trusted_package.snapshot.shadow_result_digest,
            evaluation_report_digests=(
                trusted_package.snapshot.evaluation_report_digests
            ),
            promotion_policy_digest=trusted_package.decision.policy_digest,
        )
        self.validate_cached(trusted_package, result)
        return result

    def validate_cached(
        self,
        package: PipelinePromotionPackage,
        publication: PipelinePublicationResult,
    ) -> None:
        committed = self.pipeline_store.read(
            package.epoch_id,
            "evaluating",
            PipelinePromotionPackage,
        )
        authorization = self.promotion_authority.promotion_authorization(
            package.package_digest
        )
        if authorization is None:
            raise SharedStateIntegrityError(
                "promotion authorization receipt is unavailable"
            )
        self.promotion_authority.verify_promotion_authorization(
            authorization,
            package,
        )
        event = self.snapshots.store.event(publication.control_event_digest)
        expected_extensions = {
            "promotion_package_digest": package.package_digest,
            "promotion_evidence_digest": package.evidence.evidence_digest,
            "promotion_decision_digest": package.decision.decision_digest,
            "promotion_policy_digest": package.decision.policy_digest,
            "shadow_result_digest": package.snapshot.shadow_result_digest,
            "evaluation_report_digests": list(
                package.snapshot.evaluation_report_digests
            ),
        }
        lineage = (
            committed == package,
            self.snapshots.store.promotion_authorization_digest(package.package_digest)
            == authorization.authorization_digest,
            publication.operation_id
            == stable_id("snapshot-promotion", package.package_digest),
            publication.promotion_package_digest == package.package_digest,
            publication.decision_digest == package.decision.decision_digest,
            publication.snapshot_digest == package.snapshot.snapshot_digest,
            publication.shadow_result_digest == package.snapshot.shadow_result_digest,
            publication.evaluation_report_digests
            == package.snapshot.evaluation_report_digests,
            publication.promotion_policy_digest == package.decision.policy_digest,
            event is not None,
            False if event is None else event.event_kind == "promotion",
            False if event is None else event.operation_id == publication.operation_id,
            False
            if event is None
            else event.target_snapshot_digest == package.snapshot.snapshot_digest,
            False if event is None else dict(event.extensions) == expected_extensions,
        )
        if not all(lineage):
            raise SharedStateIntegrityError(
                "cached snapshot publication lineage diverged"
            )
