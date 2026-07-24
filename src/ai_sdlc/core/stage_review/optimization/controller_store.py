"""Trigger、Epoch 与 Epoch Lease 的项目级不可变存储。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    ShortFileLock,
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.optimization.controller_models import (
    OptimizationEpoch,
    OptimizationEpochLeaseClaim,
    OptimizationEpochLeaseRelease,
    OptimizationTriggerEvent,
)
from ai_sdlc.core.stage_review.registry_versions import require_machine_id
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id


class OptimizationEpochLeaseBusyError(RuntimeError):
    """仍有未释放且未过期的写入型 Epoch Lease。"""


class OptimizationControllerStore:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        lock_timeout_seconds: float,
    ) -> None:
        self.project_id = require_machine_id(project_id, "project_id")
        shared_root = resolve_canonical_shared_state(root, self.project_id)
        bind_repository_project(shared_root, self.project_id)
        self.root = shared_root / "offline-optimization" / "controller"
        self.lock_timeout_seconds = lock_timeout_seconds

    @contextmanager
    def locked(self) -> Iterator[None]:
        with ShortFileLock(
            self.root / "controller.lock",
            timeout_seconds=self.lock_timeout_seconds,
        ):
            yield

    def append_trigger(
        self, event: OptimizationTriggerEvent
    ) -> OptimizationTriggerEvent:
        trusted = OptimizationTriggerEvent.model_validate(event.model_dump(mode="json"))
        path = self.root / "triggers" / f"{trusted.trigger_fingerprint}.json"
        if create_json_exclusive(path, trusted.model_dump(mode="json")):
            return trusted
        existing = OptimizationTriggerEvent.model_validate(read_json_object(path))
        if existing != trusted:
            raise SharedStateIntegrityError("trigger fingerprint content diverged")
        return existing

    def triggers(self) -> tuple[OptimizationTriggerEvent, ...]:
        directory = self.root / "triggers"
        if not directory.is_dir():
            return ()
        values = (
            OptimizationTriggerEvent.model_validate(read_json_object(path))
            for path in directory.glob("*.json")
        )
        return tuple(
            sorted(
                values,
                key=lambda item: (
                    item.session_sequence_high_watermark,
                    len(item.trigger_fact_digests),
                    item.trigger_fingerprint,
                ),
            )
        )

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
            OptimizationEpoch.model_validate(read_json_object(path)) for path in paths
        )
        self._verify_epoch_chain(values)
        return values[-1]

    def epochs(self) -> tuple[OptimizationEpoch, ...]:
        directory = self.root / "epochs"
        if not directory.is_dir():
            return ()
        values = tuple(
            current
            for child in directory.iterdir()
            if child.is_dir()
            for current in (self.epoch(child.name),)
            if current is not None
        )
        trigger_order = {
            item.trigger_digest: index for index, item in enumerate(self.triggers())
        }
        return tuple(
            sorted(
                values,
                key=lambda item: (
                    item.session_sequence_high_watermark,
                    trigger_order.get(item.trigger_digest, -1),
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
        if not create_json_exclusive(path, claim.model_dump(mode="json")):
            raise SharedStateIntegrityError("optimization lease fencing collided")
        return claim

    def lease_claims(
        self, epoch_id: str
    ) -> tuple[OptimizationEpochLeaseClaim, ...]:
        return self._claims(epoch_id)

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
        if create_json_exclusive(path, release.model_dump(mode="json")):
            return release
        existing = self._release(claim)
        if existing != release:
            raise SharedStateIntegrityError("optimization lease release diverged")
        return release

    def _append_epoch(self, epoch: OptimizationEpoch) -> OptimizationEpoch:
        trusted = OptimizationEpoch.model_validate(epoch.model_dump(mode="json"))
        path = self.root / "epochs" / trusted.epoch_id / f"{trusted.revision:020d}.json"
        if create_json_exclusive(path, trusted.model_dump(mode="json")):
            return trusted
        existing = OptimizationEpoch.model_validate(read_json_object(path))
        if existing != trusted:
            raise SharedStateIntegrityError("optimization epoch revision diverged")
        return existing

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
