"""组装唯一可运行的项目本地离线优化产品流水线。"""

from __future__ import annotations

from collections.abc import Callable
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
    OptimizationConstitution,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_evaluator_contract as baseline_evaluator_contract,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    baseline_auto_promotion_policy,
    baseline_constitution,
    baseline_offline_capacity,
)
from ai_sdlc.core.stage_review.optimization.evaluators import (
    OptimizationEvaluatorRegistry,
)
from ai_sdlc.core.stage_review.optimization.holdout_store import (
    HoldoutCommitmentStore,
)
from ai_sdlc.core.stage_review.optimization.local_evaluation import (
    LocalCandidateEvaluator,
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
from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBindingStore,
    OptimizationObservationStore,
)
from ai_sdlc.core.stage_review.optimization.pipeline import (
    OptimizationPipelineExecutor,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    PipelinePromotionPackage,
)
from ai_sdlc.core.stage_review.optimization.pipeline_effects import commit_effect
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
from ai_sdlc.core.stage_review.provider_journal import ProviderInvocationJournal
from ai_sdlc.core.stage_review.provider_usage_models import ProviderUsageEstimatePolicy
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resources import ResourceGovernor


def _build_product_optimization_pipeline(
    root: Path, *, project_id: str, snapshots: SnapshotControlService,
    bindings: CommittedSessionBindingStore, observations: OptimizationObservationStore,
    journal: ProviderInvocationJournal, resources: ResourceGovernor,
    clock: Callable[[], str],
    domain_registry: CandidateDomainRegistry | None = None,
) -> OptimizationPipelineExecutor:
    constitution = baseline_constitution()
    attributions = FindingAttributionStore(root, project_id=project_id)
    holdout = _holdout_store(root, project_id, constitution.familywise_alpha)
    dataset = _dataset_port(
        root,
        project_id=project_id,
        snapshots=snapshots,
        bindings=bindings,
        observations=observations,
        holdout=holdout,
        clock=clock,
        attribution_source=attributions.attributions,
    )
    domains = domain_registry or default_candidate_domain_registry()
    registry = _evaluator_registry(dataset, attributions, domains)
    snapshot_source = _snapshot_source(snapshots)
    return _pipeline_executor(
        root, project_id, snapshots, bindings, observations, journal, resources,
        clock, constitution, attributions, holdout, dataset, registry, domains,
        snapshot_source,
    )


def _pipeline_executor(
    root: Path, project_id: str, snapshots: SnapshotControlService,
    bindings: CommittedSessionBindingStore, observations: OptimizationObservationStore,
    journal: ProviderInvocationJournal, resources: ResourceGovernor,
    clock: Callable[[], str], constitution: OptimizationConstitution,
    attributions: FindingAttributionStore, holdout: HoldoutCommitmentStore,
    dataset: LocalDatasetSnapshotPort, registry: OptimizationEvaluatorRegistry,
    domains: CandidateDomainRegistry,
    snapshot_source: Callable[[str], OptimizationSnapshot],
) -> OptimizationPipelineExecutor:
    return OptimizationPipelineExecutor(
        root,
        project_id=project_id,
        minimum_evaluable_sessions=constitution.minimum_evaluable_sessions,
        candidate_family_limit=constitution.candidate_family_limit,
        evaluator_registry=registry,
        replay_evaluator_kinds=("population-metrics",),
        dataset_port=dataset,
        candidate_port=_candidate_port(project_id, snapshot_source, dataset, domains),
        holdout_port=LocalHoldoutEvaluationPort(
            store=holdout,
            dataset_source=dataset.load,
            attribution_source=attributions.attributions,
            domain_registry=domains,
        ),
        shadow_port=_product_shadow_port(
            root,
            project_id,
            bindings,
            observations,
            journal,
            resources,
            snapshot_source,
            clock,
            domains,
        ),
        promotion_port=_promotion_port(
            snapshot_source,
            attributions,
            clock,
            domains,
        ),
        publication_port=SnapshotPublication(snapshots),
        domain_registry_digest=domains.snapshot_digest,
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
) -> LocalProspectiveShadowPort:
    assignments = OptimizationShadowAssignmentStore(root, project_id=project_id)
    results = OptimizationShadowObservationStore(root, project_id=project_id)
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
    )


def _evaluator_registry(
    dataset: LocalDatasetSnapshotPort,
    attributions: FindingAttributionStore,
    domains: CandidateDomainRegistry,
) -> OptimizationEvaluatorRegistry:
    registry = OptimizationEvaluatorRegistry()
    registry.register(
        baseline_evaluator_contract(domains.domain_ids),
        LocalCandidateEvaluator(
            dataset_source=dataset.load_digest,
            attribution_source=attributions.attributions,
            domain_registry=domains,
        ),
    )
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
) -> LocalProspectiveShadowPort:
    constitution = baseline_constitution()
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


def _snapshot(
    snapshots: SnapshotControlService, digest: str
) -> OptimizationSnapshot:
    value = snapshots.store.snapshot(digest)
    if value is None:
        raise SharedStateIntegrityError("optimization snapshot is unavailable")
    return value


class SnapshotPublication:
    def __init__(self, snapshots: SnapshotControlService) -> None:
        self.snapshots = snapshots

    def promote(
        self,
        package: PipelinePromotionPackage,
        authorize_effect: Callable[[], None],
    ) -> str:
        snapshot = commit_effect(
            authorize_effect,
            lambda: self.snapshots.store.register_snapshot(package.snapshot),
        )
        event = commit_effect(
            authorize_effect,
            lambda: self.snapshots.promote(
                snapshot.snapshot_digest,
                decision=package.decision,
                operation_id=stable_id(
                    "snapshot-promotion", package.decision.decision_digest
                ),
            ),
        )
        if event is None:
            raise SharedStateIntegrityError("snapshot promotion returned no_change")
        return event.event_digest
