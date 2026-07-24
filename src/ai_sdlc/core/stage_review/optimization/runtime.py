"""单一项目本地 Optimization Runtime 与普通命令维护入口。"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ai_sdlc.core.stage_review.activation_policy_store import (
    current_activation_policy,
)
from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    resolve_canonical_shared_state,
    resolve_repository_project_id,
)
from ai_sdlc.core.stage_review.optimization.controller import (
    OfflineOptimizationController,
)
from ai_sdlc.core.stage_review.optimization.controller_models import (
    MaintenanceBudget,
    OptimizationMaintenanceResult,
    OptimizationTriggerEvent,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_foreground_capacity as baseline_foreground_capacity,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_optimization_snapshot as baseline_optimization_snapshot,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_storage_policy as baseline_storage_policy,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    baseline_constitution,
    baseline_epoch_budget_policy,
    baseline_offline_capacity,
)
from ai_sdlc.core.stage_review.optimization.finding_lineage import (
    FindingEventLineageReader,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBindingStore,
    OptimizationObservationStore,
)
from ai_sdlc.core.stage_review.optimization.pipeline import OptimizationPipelineExecutor
from ai_sdlc.core.stage_review.optimization.product_pipeline import (
    _build_product_optimization_pipeline as build_product_optimization_pipeline,
)
from ai_sdlc.core.stage_review.optimization.session_coordinator import (
    SessionOptimizationCoordinator,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import (
    OptimizationSnapshot,
)
from ai_sdlc.core.stage_review.optimization.snapshot_monitor import (
    reconcile_active_snapshot,
)
from ai_sdlc.core.stage_review.optimization.snapshots import SnapshotControlService
from ai_sdlc.core.stage_review.provider_journal import ProviderInvocationJournal
from ai_sdlc.core.stage_review.resource_builders import utc_iso
from ai_sdlc.core.stage_review.resource_runtime import utc_now
from ai_sdlc.core.stage_review.resources import ResourceGovernor
from ai_sdlc.core.stage_review.session_contracts import SessionTrustResolver


@dataclass(frozen=True, slots=True)
class OptimizationRuntime:
    project_id: str
    controller: OfflineOptimizationController
    snapshots: SnapshotControlService
    bindings: CommittedSessionBindingStore
    observations: OptimizationObservationStore
    finding_events: FindingEventLineageReader
    clock: Callable[[], str]

    def refresh_optimization_state(self) -> OptimizationTriggerEvent:
        reconcile_active_snapshot(
            self.snapshots,
            self.observations,
            clock=self.clock,
        )
        return self.controller.refresh_trigger()

    def session_coordinator(
        self,
        resolver: SessionTrustResolver,
        *,
        candidate_size_classifier: Callable[[str], str],
    ) -> SessionOptimizationCoordinator:
        return SessionOptimizationCoordinator(
            snapshots=self.snapshots,
            resolver=resolver,
            binding_store=self.bindings,
            observation_store=self.observations,
            candidate_size_classifier=candidate_size_classifier,
            clock=self.clock,
            trigger_refresher=self.refresh_optimization_state,
            finding_event_source=self.finding_events.event_digests,
        )


def build_optimization_runtime(
    root: Path,
    *,
    clock: Callable[[], str] | None = None,
) -> OptimizationRuntime:
    resolved = root.resolve()
    project_id = resolve_repository_project_id(resolved)
    runtime_clock = clock or (lambda: utc_iso(utc_now(None)))
    governor, journal, snapshots = _runtime_infrastructure(resolved, project_id)
    bindings = CommittedSessionBindingStore(resolved, project_id=project_id)
    observations = OptimizationObservationStore(resolved, project_id=project_id)
    finding_events = FindingEventLineageReader(resolved, project_id=project_id)
    pipeline = build_product_optimization_pipeline(
        resolved,
        project_id=project_id,
        snapshots=snapshots,
        bindings=bindings,
        observations=observations,
        journal=journal,
        resources=governor,
        clock=runtime_clock,
    )
    controller = _build_controller(
        resolved,
        project_id,
        snapshots.store.baseline_digest,
        governor,
        journal,
        pipeline,
        snapshots,
        runtime_clock,
    )
    return OptimizationRuntime(
        project_id=project_id,
        controller=controller,
        snapshots=snapshots,
        bindings=bindings,
        observations=observations,
        finding_events=finding_events,
        clock=runtime_clock,
    )


def _runtime_infrastructure(
    root: Path, project_id: str
) -> tuple[ResourceGovernor, ProviderInvocationJournal, SnapshotControlService]:
    governor = ResourceGovernor(
        root,
        project_id=project_id,
        foreground_capacity=baseline_foreground_capacity(),
        offline_optimization_capacity=baseline_offline_capacity(),
    )
    journal = ProviderInvocationJournal(
        root, project_id=project_id, resource_governor=governor
    )
    snapshots = SnapshotControlService(
        root,
        project_id=project_id,
        baseline_snapshot=baseline_optimization_snapshot(project_id),
        resource_governor=governor,
        storage_policy=baseline_storage_policy(),
    )
    return governor, journal, snapshots


def _resolve_active_optimization_snapshot(
    root: Path,
    *,
    project_id: str,
) -> OptimizationSnapshot:
    runtime = build_optimization_runtime(root)
    if runtime.project_id != project_id:
        raise SharedStateIntegrityError("optimization runtime project identity diverged")
    token = runtime.snapshots.resolve_snapshot()
    snapshot = runtime.snapshots.store.snapshot(token.active_snapshot_digest)
    if snapshot is None or snapshot.snapshot_digest in token.revoked_snapshot_digests:
        raise SharedStateIntegrityError("active optimization snapshot is unavailable")
    return snapshot


def _build_controller(
    root: Path,
    project_id: str,
    baseline_snapshot_digest: str,
    governor: ResourceGovernor,
    journal: ProviderInvocationJournal,
    pipeline: OptimizationPipelineExecutor,
    snapshots: SnapshotControlService,
    clock: Callable[[], str],
) -> OfflineOptimizationController:
    return OfflineOptimizationController(
        root,
        project_id=project_id,
        constitution=baseline_constitution(),
        baseline_snapshot_digest=baseline_snapshot_digest,
        epoch_budget_policy=baseline_epoch_budget_policy(),
        resource_governor=governor,
        provider_journal=journal,
        step_executor=pipeline,
        foreground_requested=lambda: _foreground_requested(root, project_id),
        active_snapshot_digest=lambda: (
            snapshots.resolve_snapshot().active_snapshot_digest
        ),
        clock=clock,
    )


def _run_bounded_optimization_maintenance(
    root: Path,
) -> OptimizationMaintenanceResult:
    enabled = current_activation_policy(root).offline_optimization_enabled
    if not enabled and not _optimization_storage_has_loose_records(root):
        return OptimizationMaintenanceResult(result_code="not_ready")
    runtime = build_optimization_runtime(root)
    _compact_optimization_storage(runtime)
    if not enabled:
        return OptimizationMaintenanceResult(result_code="not_ready")
    trigger = runtime.refresh_optimization_state()
    if not trigger.triggered:
        return OptimizationMaintenanceResult(result_code="not_ready")
    return runtime.controller.advance_optimization(
        runtime.project_id,
        MaintenanceBudget(),
        owner_id=f"optimization-worker.{os.getpid()}",
    )


def _optimization_storage_has_loose_records(root: Path) -> bool:
    project_id = resolve_repository_project_id(root)
    shared = resolve_canonical_shared_state(root, project_id)
    loose = shared / "offline-optimization" / "storage" / "loose"
    return loose.is_dir() and next(loose.rglob("*.json"), None) is not None


def _compact_optimization_storage(runtime: OptimizationRuntime) -> None:
    for stream_kind in ("snapshot-control", "query-commitments"):
        if _compact_storage_stream(runtime, stream_kind):
            return


def _compact_storage_stream(runtime: OptimizationRuntime, stream_kind: str) -> bool:
    storage = runtime.snapshots.store.storage
    loose = tuple((storage.loose_root / stream_kind).glob("*.json"))
    minimum_records = min(64, max(2, storage.policy.maximum_segment_records))
    minimum_bytes = min(4 * 1024**2, storage.policy.maximum_segment_bytes // 4)
    if len(loose) < minimum_records and sum(path.stat().st_size for path in loose) < minimum_bytes:
        return False
    prepared = storage._prepare_compaction(stream_kind)
    if prepared is None:
        return False
    operation_id = (
        f"optimization-compaction.{stream_kind}.{prepared.before.revision + 1}"
    )
    with runtime.snapshots.resources.storage_bundle(
        bundle_class="reclamation",
        bundle_bytes=prepared.required_bundle_bytes,
        net_reclaim_bytes=prepared.net_reclaim_bytes,
        policy=storage.policy,
        operation_id=operation_id,
    ) as bundle, storage.commit_leases.acquire(
        owner_id=operation_id,
        scope="compaction",
        expected_head=prepared.before.manifest_digest,
    ) as lease:
        storage._commit_compaction(
            prepared,
            lease=lease,
            resource_bundle=bundle,
        )
    return True


def _foreground_requested(root: Path, project_id: str) -> bool:
    from ai_sdlc.core.stage_review.optimization.foreground import (
        _foreground_execution_requested as foreground_execution_requested,
    )

    return foreground_execution_requested(root, project_id=project_id)
