"""Trigger、Epoch 与 Epoch Lease 的项目级不可变存储。"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_sdlc.core.stage_review.artifact_compat import fill_artifact_digest
from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    ShortFileLock,
    bind_repository_project,
    portable_content_digest_name,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.optimization.accounting import (
    OfflineOptimizationAccounting,
)
from ai_sdlc.core.stage_review.optimization.controller_models import (
    OptimizationConstitution,
    OptimizationEpoch,
    OptimizationEpochEffectReceipt,
    OptimizationEpochLeaseClaim,
    OptimizationEpochLeaseRelease,
    OptimizationTriggerEvent,
    bundled_legacy_runtime_bundle_manifests,
    bundled_optimization_constitutions,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    PipelinePromotionAuthorization,
    PipelinePromotionPackage,
)
from ai_sdlc.core.stage_review.registry_versions import require_machine_id
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id


class OptimizationEpochLeaseBusyError(RuntimeError):
    """仍有未释放且未过期的写入型 Epoch Lease。"""


class _TriggerOrderEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["optimization-trigger-order-entry.v1"] = (
        "optimization-trigger-order-entry.v1"
    )
    sequence: int = Field(ge=1)
    previous_entry_digest: str
    event: OptimizationTriggerEvent
    entry_digest: str = ""

    @model_validator(mode="after")
    def _verify_entry(self) -> Self:
        if not self.event.triggered:
            raise ValueError("ordered optimization trigger must be active")
        return fill_artifact_digest(self, "entry_digest")


class _LegacyTriggerOrderMigration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["optimization-legacy-trigger-order.v1"] = (
        "optimization-legacy-trigger-order.v1"
    )
    evidence_version: Literal["causal-epoch-state.v1"] = "causal-epoch-state.v1"
    legacy_set_anchor: str
    ordered_trigger_digests: tuple[str, ...]
    ordered_trigger_groups: tuple[tuple[str, ...], ...]
    migration_digest: str = ""

    @model_validator(mode="after")
    def _verify_migration(self) -> Self:
        if (
            not self.legacy_set_anchor.strip()
            or not self.ordered_trigger_digests
            or len(set(self.ordered_trigger_digests))
            != len(self.ordered_trigger_digests)
            or not self.ordered_trigger_groups
            or any(
                not group or group != tuple(sorted(set(group)))
                for group in self.ordered_trigger_groups
            )
            or tuple(
                digest
                for group in self.ordered_trigger_groups
                for digest in group
            )
            != self.ordered_trigger_digests
        ):
            raise ValueError("legacy trigger migration is incomplete")
        return fill_artifact_digest(self, "migration_digest")


class OptimizationControllerStore:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        lock_timeout_seconds: float,
        constitution_bundles: Mapping[
            str, OptimizationConstitution
        ] | None = None,
        runtime_bundle_manifests: Mapping[str, str] | None = None,
        legacy_runtime_bundle_manifests: Mapping[str, str] | None = None,
    ) -> None:
        self.worktree_root = root.resolve()
        self.project_id = require_machine_id(project_id, "project_id")
        shared_root = resolve_canonical_shared_state(root, self.project_id)
        bind_repository_project(shared_root, self.project_id)
        self.root = shared_root / "offline-optimization" / "controller"
        self.lock_path = (
            shared_root / "locks" / "optimization-controller-mutation.lock"
        )
        self.accounting = OfflineOptimizationAccounting(
            root,
            project_id=self.project_id,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        self.lock_timeout_seconds = lock_timeout_seconds
        self._local = threading.local()
        self.constitutions = {
            constitution.constitution_digest: constitution
            for constitution in (
                *bundled_optimization_constitutions(),
                *tuple((constitution_bundles or {}).values()),
            )
        }
        if any(
            digest != constitution.constitution_digest
            for digest, constitution in (constitution_bundles or {}).items()
        ):
            raise ValueError("optimization constitution bundle diverged")
        self.runtime_bundle_manifests = dict(runtime_bundle_manifests or {})
        if any(
            digest not in self.constitutions
            for digest in self.runtime_bundle_manifests
        ):
            raise ValueError(
                "optimization runtime bundle manifest coverage diverged"
            )
        if any(not value.strip() for value in self.runtime_bundle_manifests.values()):
            raise ValueError("optimization runtime bundle manifest is invalid")
        self.legacy_runtime_bundle_manifests = {
            **bundled_legacy_runtime_bundle_manifests(),
            **dict(legacy_runtime_bundle_manifests or {}),
        }
        if any(
            not key.strip() or not value.strip()
            for key, value in self.legacy_runtime_bundle_manifests.items()
        ):
            raise ValueError("legacy optimization runtime manifest is invalid")

    def runtime_identity(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "lock_timeout_seconds": self.lock_timeout_seconds,
            "constitution_digests": tuple(sorted(self.constitutions)),
            "runtime_bundle_manifests": dict(
                sorted(self.runtime_bundle_manifests.items())
            ),
            "legacy_runtime_bundle_manifests": dict(
                sorted(self.legacy_runtime_bundle_manifests.items())
            ),
        }

    @contextmanager
    def locked(self) -> Iterator[None]:
        depth = int(getattr(self._local, "lock_depth", 0))
        if depth:
            self._local.lock_depth = depth + 1
            try:
                yield
            finally:
                self._local.lock_depth = depth
            return
        with ShortFileLock(
            self.lock_path,
            timeout_seconds=self.lock_timeout_seconds,
        ):
            self._local.lock_depth = 1
            try:
                yield
            finally:
                self._local.lock_depth = 0

    def append_trigger(
        self, event: OptimizationTriggerEvent
    ) -> OptimizationTriggerEvent:
        with self.locked():
            return self._append_trigger_locked(event)

    def _append_trigger_locked(
        self, event: OptimizationTriggerEvent
    ) -> OptimizationTriggerEvent:
        trusted = OptimizationTriggerEvent.model_validate(event.model_dump(mode="json"))
        existing = next(
            (
                item
                for item in self.triggers()
                if item.trigger_fingerprint == trusted.trigger_fingerprint
            ),
            None,
        )
        if existing is not None:
            if existing != trusted:
                raise SharedStateIntegrityError(
                    "trigger fingerprint content diverged"
                )
            return existing
        entries = self._trigger_order_entries()
        entry = _TriggerOrderEntry(
            sequence=len(entries) + 1,
            previous_entry_digest=(
                entries[-1].entry_digest
                if entries
                else self._legacy_trigger_anchor()
            ),
            event=trusted,
        )
        path = self.root / "trigger-order" / f"{entry.sequence:020d}.json"
        if self.accounting.persist_json_exclusive(
            path,
            entry.model_dump(mode="json"),
        ):
            return trusted
        persisted = _TriggerOrderEntry.model_validate(read_json_object(path))
        if persisted != entry:
            raise SharedStateIntegrityError("trigger sequence content diverged")
        return persisted.event

    def triggers(self) -> tuple[OptimizationTriggerEvent, ...]:
        with self.locked():
            return self._triggers_locked()

    def _triggers_locked(self) -> tuple[OptimizationTriggerEvent, ...]:
        legacy = self._legacy_triggers()
        migration = self._legacy_trigger_migration(legacy)
        if migration is not None:
            by_digest = {item.trigger_digest: item for item in legacy}
            legacy = tuple(
                by_digest[digest]
                for digest in migration.ordered_trigger_digests
            )
        ordered = tuple(item.event for item in self._trigger_order_entries())
        values = (*legacy, *ordered)
        if len({item.trigger_fingerprint for item in values}) != len(values):
            raise SharedStateIntegrityError("optimization trigger identity fork")
        return values

    def _legacy_triggers(self) -> tuple[OptimizationTriggerEvent, ...]:
        directory = self.root / "triggers"
        if not directory.is_dir():
            return ()
        paths = tuple(sorted(directory.glob("*.json")))
        values = tuple(
            self._decode_trigger(read_json_object(path)) for path in paths
        )
        if len({item.trigger_digest for item in values}) != len(values):
            raise SharedStateIntegrityError("legacy optimization trigger fork")
        return values

    def _legacy_trigger_anchor(self) -> str:
        legacy = self._legacy_triggers()
        if not legacy:
            return ""
        migration = self._legacy_trigger_migration(legacy)
        if migration is None:
            raise SharedStateIntegrityError(
                "legacy optimization trigger migration is unavailable"
            )
        return migration.migration_digest

    def _legacy_trigger_migration(
        self,
        legacy: tuple[OptimizationTriggerEvent, ...],
    ) -> _LegacyTriggerOrderMigration | None:
        if not legacy:
            return None
        anchor = canonical_digest(
            {
                "identity_contract": "legacy-trigger-set.v1",
                "trigger_digests": sorted(
                    item.trigger_digest for item in legacy
                ),
            },
            CanonicalizationPolicy(),
        )
        path = self.root / "legacy-trigger-order.json"
        if path.is_file():
            migration = _LegacyTriggerOrderMigration.model_validate(
                read_json_object(path)
            )
            if (
                migration.legacy_set_anchor != anchor
                or set(migration.ordered_trigger_digests)
                != {item.trigger_digest for item in legacy}
            ):
                raise SharedStateIntegrityError(
                    "legacy optimization trigger set changed after migration"
                )
            return migration
        groups = self._infer_legacy_trigger_order(legacy)
        migration = _LegacyTriggerOrderMigration(
            legacy_set_anchor=anchor,
            ordered_trigger_digests=tuple(
                digest for group in groups for digest in group
            ),
            ordered_trigger_groups=groups,
        )
        if self.accounting.persist_json_exclusive(
            path,
            migration.model_dump(mode="json"),
        ):
            return migration
        persisted = _LegacyTriggerOrderMigration.model_validate(
            read_json_object(path)
        )
        if persisted != migration:
            raise SharedStateIntegrityError(
                "legacy optimization trigger migration diverged"
            )
        return persisted

    def _infer_legacy_trigger_order(
        self,
        legacy: tuple[OptimizationTriggerEvent, ...],
    ) -> tuple[tuple[str, ...], ...]:
        epochs = self._current_epochs_unordered()
        epoch_states: dict[str, tuple[str, ...]] = {}
        for event in legacy:
            states = tuple(
                epoch.state
                for epoch in epochs
                if epoch.trigger_digest == event.trigger_digest
            )
            epoch_states[event.trigger_digest] = tuple(sorted(states))
        edges: dict[str, set[str]] = {
            item.trigger_digest: set() for item in legacy
        }
        indegree = {item.trigger_digest: 0 for item in legacy}
        for index, left in enumerate(legacy):
            for right in legacy[index + 1 :]:
                before, after = self._legacy_pair_order(
                    left,
                    right,
                    epoch_states=epoch_states,
                )
                if before is None or after is None:
                    continue
                if after.trigger_digest not in edges[before.trigger_digest]:
                    edges[before.trigger_digest].add(after.trigger_digest)
                    indegree[after.trigger_digest] += 1
        by_digest = {item.trigger_digest: item for item in legacy}
        ordered_groups: list[tuple[str, ...]] = []
        remaining = set(by_digest)
        while remaining:
            ready = tuple(
                sorted(
                    digest for digest in remaining if indegree[digest] == 0
                )
            )
            if not ready or (
                len(ready) > 1
                and not self._legacy_ready_group_is_control_equivalent(
                    tuple(by_digest[digest] for digest in ready),
                    epoch_states=epoch_states,
                )
            ):
                raise SharedStateIntegrityError(
                    "legacy optimization trigger order is ambiguous"
                )
            ordered_groups.append(ready)
            for digest in ready:
                remaining.remove(digest)
                for successor in edges[digest]:
                    indegree[successor] -= 1
        return tuple(ordered_groups)

    @staticmethod
    def _legacy_ready_group_is_control_equivalent(
        events: tuple[OptimizationTriggerEvent, ...],
        *,
        epoch_states: Mapping[str, tuple[str, ...]],
    ) -> bool:
        superseded = "superseded_runtime_upgrade"
        if any(
            set(epoch_states[event.trigger_digest]) != {superseded}
            for event in events
        ):
            return False
        control_keys = {
            (
                event.project_id,
                event.session_sequence_high_watermark,
                event.constitution_digest,
                event.baseline_snapshot_digest,
                event.candidate_domain_registry_digest,
                event.statistics_policy_digest,
                event.evaluator_registry_digest,
                event.auto_promotion_policy_digest,
                event.trigger_facts,
                event.trigger_fact_digests,
                event.new_session_count,
                event.triggered,
            )
            for event in events
        }
        return len(control_keys) == 1

    @staticmethod
    def _legacy_pair_order(
        left: OptimizationTriggerEvent,
        right: OptimizationTriggerEvent,
        *,
        epoch_states: Mapping[str, tuple[str, ...]],
    ) -> tuple[
        OptimizationTriggerEvent | None,
        OptimizationTriggerEvent | None,
    ]:
        if (
            left.session_sequence_high_watermark
            != right.session_sequence_high_watermark
        ):
            return (
                (left, right)
                if left.session_sequence_high_watermark
                < right.session_sequence_high_watermark
                else (right, left)
            )
        left_facts = set(left.trigger_fact_digests)
        right_facts = set(right.trigger_fact_digests)
        if left_facts < right_facts:
            return left, right
        if right_facts < left_facts:
            return right, left
        superseded = "superseded_runtime_upgrade"
        left_states = set(epoch_states[left.trigger_digest])
        right_states = set(epoch_states[right.trigger_digest])
        if superseded in left_states and superseded not in right_states:
            return left, right
        if superseded in right_states and superseded not in left_states:
            return right, left
        return None, None

    def _current_epochs_unordered(self) -> tuple[OptimizationEpoch, ...]:
        directory = self.root / "epochs"
        if not directory.is_dir():
            return ()
        return tuple(
            current
            for child in directory.iterdir()
            if child.is_dir()
            for current in (self.epoch(child.name),)
            if current is not None
        )

    def _trigger_order_entries(self) -> tuple[_TriggerOrderEntry, ...]:
        directory = self.root / "trigger-order"
        paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
        entries = tuple(
            _TriggerOrderEntry.model_validate(read_json_object(path))
            for path in paths
        )
        previous = self._legacy_trigger_anchor()
        for sequence, (path, entry) in enumerate(
            zip(paths, entries, strict=True),
            start=1,
        ):
            if (
                entry.sequence != sequence
                or path.name != f"{sequence:020d}.json"
                or entry.previous_entry_digest != previous
            ):
                raise SharedStateIntegrityError(
                    "optimization trigger sequence diverged"
                )
            previous = entry.entry_digest
        return entries

    def create_epoch(self, epoch: OptimizationEpoch) -> OptimizationEpoch:
        if epoch.revision != 1:
            raise SharedStateIntegrityError("epoch creation requires revision one")
        return self._append_epoch(epoch)

    def append_epoch(self, epoch: OptimizationEpoch) -> OptimizationEpoch:
        current = self.epoch(epoch.epoch_id)
        if current is None:
            raise SharedStateIntegrityError("optimization epoch does not exist")
        if (
            epoch.revision != current.revision + 1
            or epoch.previous_epoch_digest != current.epoch_digest
        ):
            raise SharedStateIntegrityError("optimization epoch CAS is stale")
        return self._append_epoch(epoch)

    def epoch(self, epoch_id: str) -> OptimizationEpoch | None:
        directory = self.root / "epochs" / require_machine_id(epoch_id, "epoch_id")
        paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
        if not paths:
            return None
        values = tuple(
            self._decode_epoch(read_json_object(path)) for path in paths
        )
        self._verify_epoch_chain(values)
        return values[-1]

    def epochs(self) -> tuple[OptimizationEpoch, ...]:
        with self.locked():
            values = self._current_epochs_unordered()
            trigger_order = {
                item.trigger_digest: index
                for index, item in enumerate(self._triggers_locked())
            }
            if any(item.trigger_digest not in trigger_order for item in values):
                raise SharedStateIntegrityError(
                    "optimization epoch trigger lineage is unavailable"
                )
            return tuple(
                sorted(
                    values,
                    key=lambda item: (
                        item.session_sequence_high_watermark,
                        trigger_order[item.trigger_digest],
                    ),
                )
            )

    def acquire_lease(
        self,
        epoch_id: str,
        *,
        owner_id: str,
        now: datetime | None = None,
        lease_seconds: float = 30,
    ) -> OptimizationEpochLeaseClaim:
        acquired = (now or datetime.now(UTC)).astimezone(UTC)
        claims = self._claims(epoch_id)
        previous = claims[-1] if claims else None
        if previous is not None and self._claim_is_active(previous, acquired):
            raise OptimizationEpochLeaseBusyError(
                "optimization epoch lease is still active"
            )
        fencing = 1 if previous is None else previous.fencing_epoch + 1
        claim = OptimizationEpochLeaseClaim(
            epoch_id=epoch_id,
            owner_id=owner_id,
            fencing_epoch=fencing,
            acquired_at=acquired.isoformat(),
            expires_at=(acquired + timedelta(seconds=lease_seconds)).isoformat(),
            previous_claim_digest="" if previous is None else previous.claim_digest,
        )
        path = self.root / "epoch-leases" / epoch_id / f"{fencing:020d}.json"
        if not self.accounting.persist_json_exclusive(
            path,
            claim.model_dump(mode="json"),
        ):
            raise SharedStateIntegrityError("optimization lease fencing collided")
        return claim

    def lease_claims(
        self, epoch_id: str
    ) -> tuple[OptimizationEpochLeaseClaim, ...]:
        return self._claims(epoch_id)

    def _prepare_effect_receipt(
        self,
        epoch: OptimizationEpoch,
        claim: OptimizationEpochLeaseClaim,
        *,
        effect_kind: Literal["shadow_observation"],
        effect_digest: str,
        provider_journal_last_event_digest: str,
    ) -> OptimizationEpochEffectReceipt:
        current = self.epoch(epoch.epoch_id)
        claims = self._claims(epoch.epoch_id)
        if (
            current != epoch
            or not claims
            or claims[-1] != claim
            or self._release(claim) is not None
            or epoch.project_id != self.project_id
        ):
            raise SharedStateIntegrityError(
                "optimization effect lost its epoch fence"
            )
        return OptimizationEpochEffectReceipt(
            receipt_id=stable_id(
                "optimization-epoch-effect-receipt",
                effect_kind,
                effect_digest,
                claim.claim_digest,
            ),
            project_id=self.project_id,
            epoch_id=epoch.epoch_id,
            epoch_revision=epoch.revision,
            epoch_digest=epoch.epoch_digest,
            runtime_bundle_manifest_digest=(
                epoch.runtime_bundle_manifest_digest
            ),
            epoch_fencing_epoch=claim.fencing_epoch,
            epoch_claim_digest=claim.claim_digest,
            effect_kind=effect_kind,
            effect_digest=effect_digest,
            provider_journal_last_event_digest=(
                provider_journal_last_event_digest
            ),
        )

    def _persist_effect_receipt(
        self,
        receipt: OptimizationEpochEffectReceipt,
    ) -> OptimizationEpochEffectReceipt:
        trusted = OptimizationEpochEffectReceipt.model_validate(
            receipt.model_dump(mode="json")
        )
        path = self._effect_receipt_path(trusted.receipt_id)
        if self.accounting.persist_json_exclusive(
            path,
            trusted.model_dump(mode="json"),
        ):
            return trusted
        existing = OptimizationEpochEffectReceipt.model_validate(
            read_json_object(path)
        )
        if existing != trusted:
            raise SharedStateIntegrityError(
                "optimization effect receipt diverged"
            )
        return existing

    def effect_receipt(
        self,
        receipt_id: str,
    ) -> OptimizationEpochEffectReceipt | None:
        path = self._effect_receipt_path(receipt_id)
        if not path.is_file():
            return None
        return OptimizationEpochEffectReceipt.model_validate(
            read_json_object(path)
        )

    def verify_effect_receipt(
        self,
        receipt: OptimizationEpochEffectReceipt,
    ) -> None:
        persisted = self.effect_receipt(receipt.receipt_id)
        epoch_path = (
            self.root
            / "epochs"
            / receipt.epoch_id
            / f"{receipt.epoch_revision:020d}.json"
        )
        if not epoch_path.is_file():
            raise SharedStateIntegrityError(
                "optimization effect epoch is unavailable"
            )
        epoch = self._decode_epoch(read_json_object(epoch_path))
        claims = self._claims(receipt.epoch_id)
        claim = next(
            (
                item
                for item in claims
                if item.fencing_epoch == receipt.epoch_fencing_epoch
            ),
            None,
        )
        if not all(
            (
                persisted == receipt,
                claim is not None,
                claim is not None
                and claim.claim_digest == receipt.epoch_claim_digest,
                receipt.project_id == self.project_id == epoch.project_id,
                receipt.epoch_digest == epoch.epoch_digest,
                receipt.runtime_bundle_manifest_digest
                == epoch.runtime_bundle_manifest_digest,
            )
        ):
            raise SharedStateIntegrityError(
                "optimization effect receipt lineage diverged"
            )

    def _effect_receipt_path(self, receipt_id: str) -> Path:
        stable = require_machine_id(receipt_id, "effect receipt id")
        return self.root / "effect-receipts" / f"{stable}.json"

    def issue_promotion_authorization(
        self,
        epoch: OptimizationEpoch,
        package: PipelinePromotionPackage,
        *,
        fencing_epoch: int,
        claim_digest: str,
    ) -> PipelinePromotionAuthorization:
        from ai_sdlc.core.stage_review.optimization.pipeline_store import (
            OptimizationPipelineStore,
        )

        current = self.epoch(epoch.epoch_id)
        committed = OptimizationPipelineStore(
            self.worktree_root,
            project_id=self.project_id,
        ).read(
            epoch.epoch_id,
            "evaluating",
            PipelinePromotionPackage,
        )
        claims = self._claims(epoch.epoch_id)
        claim = next(
            (
                item
                for item in claims
                if item.fencing_epoch == fencing_epoch
                and item.claim_digest == claim_digest
            ),
            None,
        )
        if (
            current != epoch
            or claim is None
            or claims[-1] != claim
            or self._release(claim) is not None
            or epoch.state != "evaluating"
            or package.epoch_id != epoch.epoch_id
            or package.constitution_digest != epoch.constitution_digest
            or package.snapshot.project_id != epoch.project_id
            or committed != package
        ):
            raise SharedStateIntegrityError(
                "promotion authorization lost its epoch fence"
            )
        receipt = PipelinePromotionAuthorization(
            authorization_id=stable_id(
                "pipeline-promotion-authorization",
                package.package_digest,
                claim.claim_digest,
            ),
            epoch_id=epoch.epoch_id,
            epoch_revision=epoch.revision,
            epoch_digest=epoch.epoch_digest,
            constitution_digest=epoch.constitution_digest,
            runtime_bundle_manifest_digest=(
                epoch.runtime_bundle_manifest_digest
            ),
            epoch_fencing_epoch=claim.fencing_epoch,
            epoch_claim_digest=claim.claim_digest,
            promotion_package_digest=package.package_digest,
            decision_digest=package.decision.decision_digest,
            promotion_evidence_digest=package.evidence.evidence_digest,
            snapshot_digest=package.snapshot.snapshot_digest,
            shadow_result_digest=package.snapshot.shadow_result_digest,
            evaluation_report_digests=(
                package.snapshot.evaluation_report_digests
            ),
        )
        path = self._promotion_authorization_path(package.package_digest)
        if self.accounting.persist_json_exclusive(
            path, receipt.model_dump(mode="json")
        ):
            return receipt
        existing = PipelinePromotionAuthorization.model_validate(
            read_json_object(path)
        )
        if existing != receipt:
            raise SharedStateIntegrityError(
                "promotion authorization receipt diverged"
            )
        return existing

    def promotion_authorization(
        self,
        package_digest: str,
    ) -> PipelinePromotionAuthorization | None:
        path = self._promotion_authorization_path(package_digest)
        if not path.is_file():
            return None
        return PipelinePromotionAuthorization.model_validate(
            read_json_object(path)
        )

    def verify_promotion_authorization(
        self,
        receipt: PipelinePromotionAuthorization,
        package: PipelinePromotionPackage,
    ) -> None:
        from ai_sdlc.core.stage_review.optimization.pipeline_store import (
            OptimizationPipelineStore,
        )

        path = (
            self.root
            / "epochs"
            / require_machine_id(receipt.epoch_id, "epoch_id")
            / f"{receipt.epoch_revision:020d}.json"
        )
        if not path.is_file():
            raise SharedStateIntegrityError(
                "promotion authorization epoch is unavailable"
            )
        epoch = self._decode_epoch(read_json_object(path))
        claims = self._claims(receipt.epoch_id)
        claim = next(
            (
                item
                for item in claims
                if item.fencing_epoch == receipt.epoch_fencing_epoch
            ),
            None,
        )
        persisted = self.promotion_authorization(package.package_digest)
        committed = OptimizationPipelineStore(
            self.worktree_root,
            project_id=self.project_id,
        ).read(
            receipt.epoch_id,
            "evaluating",
            PipelinePromotionPackage,
        )
        lineage = (
            persisted == receipt,
            committed == package,
            claim is not None,
            claim is not None
            and claim.claim_digest == receipt.epoch_claim_digest,
            receipt.epoch_digest == epoch.epoch_digest,
            receipt.constitution_digest == epoch.constitution_digest,
            receipt.runtime_bundle_manifest_digest
            == epoch.runtime_bundle_manifest_digest,
            receipt.promotion_package_digest == package.package_digest,
            receipt.decision_digest == package.decision.decision_digest,
            receipt.promotion_evidence_digest
            == package.evidence.evidence_digest,
            receipt.snapshot_digest == package.snapshot.snapshot_digest,
            receipt.shadow_result_digest
            == package.snapshot.shadow_result_digest,
            receipt.evaluation_report_digests
            == package.snapshot.evaluation_report_digests,
            package.epoch_id == epoch.epoch_id,
            package.constitution_digest == epoch.constitution_digest,
            package.snapshot.project_id == epoch.project_id,
            epoch.state == "evaluating",
        )
        if not all(lineage):
            raise SharedStateIntegrityError(
                "promotion authorization lineage diverged"
            )

    def _promotion_authorization_path(self, package_digest: str) -> Path:
        name = portable_content_digest_name(package_digest)
        return self.root / "promotion-authorizations" / f"{name}.json"

    def require_current_lease(
        self,
        claim: OptimizationEpochLeaseClaim,
        *,
        owner_id: str,
        now: datetime | None = None,
    ) -> None:
        current = self._claims(claim.epoch_id)
        if not current or current[-1] != claim:
            raise SharedStateIntegrityError("optimization epoch lease was fenced")
        observed = (now or datetime.now(UTC)).astimezone(UTC)
        if claim.owner_id != owner_id or not self._claim_is_active(claim, observed):
            raise SharedStateIntegrityError("optimization epoch lease is not current")

    def release_lease(
        self,
        claim: OptimizationEpochLeaseClaim,
        *,
        owner_id: str,
        now: datetime | None = None,
    ) -> OptimizationEpochLeaseRelease:
        current = self._claims(claim.epoch_id)
        if not current or current[-1] != claim or claim.owner_id != owner_id:
            raise SharedStateIntegrityError("optimization epoch lease release is stale")
        existing = self._release(claim)
        if existing is not None:
            return existing
        release = OptimizationEpochLeaseRelease(
            release_id=stable_id("optimization-epoch-lease-release", claim.claim_digest),
            epoch_id=claim.epoch_id,
            owner_id=owner_id,
            fencing_epoch=claim.fencing_epoch,
            claim_digest=claim.claim_digest,
            released_at=(now or datetime.now(UTC)).astimezone(UTC).isoformat(),
        )
        path = self._release_path(claim)
        if self.accounting.persist_json_exclusive(
            path,
            release.model_dump(mode="json"),
        ):
            return release
        existing = self._release(claim)
        if existing != release:
            raise SharedStateIntegrityError("optimization lease release diverged")
        return release

    def _append_epoch(self, epoch: OptimizationEpoch) -> OptimizationEpoch:
        trusted = OptimizationEpoch.model_validate(epoch.model_dump(mode="json"))
        path = self.root / "epochs" / trusted.epoch_id / f"{trusted.revision:020d}.json"
        if self.accounting.persist_json_exclusive(
            path,
            trusted.model_dump(mode="json"),
        ):
            return trusted
        existing = self._decode_epoch(read_json_object(path))
        if existing != trusted:
            raise SharedStateIntegrityError("optimization epoch revision diverged")
        return existing

    def _decode_trigger(
        self,
        payload: dict[str, object],
    ) -> OptimizationTriggerEvent:
        return OptimizationTriggerEvent.model_validate(
            payload,
            context={
                "optimization_constitutions": self.constitutions,
                "optimization_runtime_bundle_manifests": (
                    self.runtime_bundle_manifests
                ),
                "optimization_legacy_runtime_bundle_manifests": (
                    self.legacy_runtime_bundle_manifests
                ),
            },
        )

    def _decode_epoch(
        self,
        payload: dict[str, object],
    ) -> OptimizationEpoch:
        return OptimizationEpoch.model_validate(
            payload,
            context={
                "optimization_constitutions": self.constitutions,
                "optimization_runtime_bundle_manifests": (
                    self.runtime_bundle_manifests
                ),
                "optimization_legacy_runtime_bundle_manifests": (
                    self.legacy_runtime_bundle_manifests
                ),
            },
        )

    def _claims(self, epoch_id: str) -> tuple[OptimizationEpochLeaseClaim, ...]:
        directory = self.root / "epoch-leases" / epoch_id
        paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
        claims = tuple(
            OptimizationEpochLeaseClaim.model_validate(read_json_object(path))
            for path in paths
        )
        for index, claim in enumerate(claims, start=1):
            previous = "" if index == 1 else claims[index - 2].claim_digest
            if claim.fencing_epoch != index or claim.previous_claim_digest != previous:
                raise SharedStateIntegrityError("optimization lease chain diverged")
        return claims

    def _claim_is_active(
        self,
        claim: OptimizationEpochLeaseClaim,
        observed_at: datetime,
    ) -> bool:
        return self._release(claim) is None and parse_utc(claim.expires_at) > observed_at

    def _release(
        self,
        claim: OptimizationEpochLeaseClaim,
    ) -> OptimizationEpochLeaseRelease | None:
        path = self._release_path(claim)
        if not path.is_file():
            return None
        value = OptimizationEpochLeaseRelease.model_validate(read_json_object(path))
        if (
            value.epoch_id != claim.epoch_id
            or value.owner_id != claim.owner_id
            or value.fencing_epoch != claim.fencing_epoch
            or value.claim_digest != claim.claim_digest
        ):
            raise SharedStateIntegrityError("optimization lease release lineage diverged")
        return value

    def _release_path(self, claim: OptimizationEpochLeaseClaim) -> Path:
        return (
            self.root
            / "epoch-lease-releases"
            / claim.epoch_id
            / f"{claim.fencing_epoch:020d}.json"
        )

    @staticmethod
    def _verify_epoch_chain(values: tuple[OptimizationEpoch, ...]) -> None:
        for index, value in enumerate(values, start=1):
            previous = "" if index == 1 else values[index - 2].epoch_digest
            if value.revision != index or value.previous_epoch_digest != previous:
                raise SharedStateIntegrityError("optimization epoch chain diverged")
