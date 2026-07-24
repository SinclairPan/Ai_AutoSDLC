"""产品运行时使用的不可变 OptimizationDatasetSnapshot 端口。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.candidate_dataset import (
    CandidateDatasetView,
)
from ai_sdlc.core.stage_review.optimization.candidate_dataset import (
    _build_candidate_dataset_view as build_candidate_dataset_view,
)
from ai_sdlc.core.stage_review.optimization.controller_models import OptimizationEpoch
from ai_sdlc.core.stage_review.optimization.datasets import (
    DatasetPolicy,
    OptimizationDatasetSnapshot,
    freeze_optimization_dataset,
)
from ai_sdlc.core.stage_review.optimization.holdout_store import (
    HoldoutCommitmentStore,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBindingStore,
    OptimizationObservationStore,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    _materialize_open_censored_observations as materialize_open_censored_observations,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    PipelineSnapshotResult,
)
from ai_sdlc.core.stage_review.optimization.pipeline_effects import commit_effect
from ai_sdlc.core.stage_review.optimization.snapshots import SnapshotControlService
from ai_sdlc.core.stage_review.provider_usage_models import ProviderUsageEstimatePolicy
from ai_sdlc.core.stage_review.resource_builders import stable_id


class LocalDatasetSnapshotPort:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        snapshots: SnapshotControlService,
        bindings: CommittedSessionBindingStore,
        observations: OptimizationObservationStore,
        holdout_commitments: HoldoutCommitmentStore,
        policy: DatasetPolicy,
        clock: Callable[[], str],
        attribution_source: Callable[[], tuple[FindingAttribution, ...]] | None = None,
        usage_policy_source: (
            Callable[[str], ProviderUsageEstimatePolicy] | None
        ) = None,
    ) -> None:
        shared = resolve_canonical_shared_state(root, project_id)
        self.root = shared / "offline-optimization" / "datasets"
        self.project_id = project_id
        self.snapshots = snapshots
        self.bindings = bindings
        self.observations = observations
        self.holdout_commitments = holdout_commitments
        self.policy = policy
        self.clock = clock
        self.attribution_source = attribution_source or (lambda: ())
        self.usage_policy_source = usage_policy_source or (
            lambda digest: _usage_policy(self.snapshots, digest)
        )

    def freeze(
        self, epoch: OptimizationEpoch, authorize_effect: Callable[[], None]
    ) -> PipelineSnapshotResult:
        existing = self._read(epoch.epoch_id)
        if existing is None:
            authorize_effect()
            self.snapshots.recover_session_population(
                binding_store=self.bindings,
                observation_store=self.observations,
            )
            snapshot = self._build(epoch)
            existing = commit_effect(
                authorize_effect, lambda: self._persist(epoch.epoch_id, snapshot)
            )
        return PipelineSnapshotResult(
            dataset_digest=existing.dataset_digest,
            evaluable_session_count=len(existing.evaluable_session_ids),
        )

    def load(self, epoch_id: str) -> OptimizationDatasetSnapshot:
        snapshot = self._read(epoch_id)
        if snapshot is None:
            raise SharedStateIntegrityError("optimization dataset is unavailable")
        return snapshot

    def load_digest(self, dataset_digest: str) -> OptimizationDatasetSnapshot:
        if not self.root.is_dir():
            raise SharedStateIntegrityError("optimization dataset is unavailable")
        for path in sorted(self.root.glob("*.json")):
            snapshot = OptimizationDatasetSnapshot.model_validate(
                read_json_object(path)
            )
            if snapshot.dataset_digest == dataset_digest:
                return snapshot
        raise SharedStateIntegrityError("optimization dataset digest is unavailable")

    def candidate_view(self, epoch_id: str) -> CandidateDatasetView:
        return build_candidate_dataset_view(
            self.load(epoch_id), self.attribution_source()
        )

    def _build(self, epoch: OptimizationEpoch) -> OptimizationDatasetSnapshot:
        if not epoch.started_at:
            raise SharedStateIntegrityError("optimization epoch start is unavailable")
        bindings = self.bindings.read_all()
        occurred_at = self.clock()
        materialize_open_censored_observations(
            bindings,
            self.observations,
            sequence_high_watermark=epoch.session_sequence_high_watermark,
            occurred_at=occurred_at,
        )
        return freeze_optimization_dataset(
            project_id=self.project_id,
            bindings=bindings,
            observations=self.observations.read_all(),
            epoch_started_at=epoch.started_at,
            session_sequence_high_watermark=epoch.session_sequence_high_watermark,
            trigger_fingerprint=epoch.trigger_fingerprint,
            constitution_digest=epoch.constitution_digest,
            baseline_snapshot_digest=epoch.baseline_snapshot_digest,
            holdout_generation_id=stable_id("holdout-generation", epoch.epoch_id),
            policy=self.policy,
            usage_policy_source=self.usage_policy_source,
            permanently_held_out_session_ids=_held_out_sessions(
                self.holdout_commitments
            ),
            attributions=self.attribution_source(),
        )

    def _persist(
        self,
        epoch_id: str,
        snapshot: OptimizationDatasetSnapshot,
    ) -> OptimizationDatasetSnapshot:
        path = self.root / f"{epoch_id}.json"
        if create_json_exclusive(path, snapshot.model_dump(mode="json")):
            return snapshot
        existing = self._read(epoch_id)
        if existing != snapshot:
            raise SharedStateIntegrityError("optimization dataset identity diverged")
        return existing

    def _read(self, epoch_id: str) -> OptimizationDatasetSnapshot | None:
        path = self.root / f"{epoch_id}.json"
        if not path.is_file():
            return None
        return OptimizationDatasetSnapshot.model_validate(read_json_object(path))


def _held_out_sessions(store: HoldoutCommitmentStore) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                session_id
                for commitment in store.commitments()
                for session_id in commitment.holdout_session_ids
            }
        )
    )


def _usage_policy(
    snapshots: SnapshotControlService,
    digest: str,
) -> ProviderUsageEstimatePolicy:
    snapshot = snapshots.store.snapshot(digest)
    if snapshot is None:
        raise SharedStateIntegrityError(
            "optimization usage policy snapshot is unavailable"
        )
    return ProviderUsageEstimatePolicy.model_validate(
        snapshot.policy_payload.get("usage_estimation_policy")
    )
