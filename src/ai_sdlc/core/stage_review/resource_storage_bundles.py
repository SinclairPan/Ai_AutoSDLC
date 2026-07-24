"""ResourceGovernor 内的安全写入与回收事务包账本。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal, Protocol, Self

from pydantic import Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    create_json_exclusive,
    read_json_object,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.resource_store import ResourceEventStore

StorageBundleClass = Literal["critical_recovery", "session_binding", "reclamation"]
StorageBundleEventKind = Literal["reserved", "released"]


class StorageReservePolicy(Protocol):
    critical_recovery_reserve_bytes: int
    session_binding_reserve_bytes: int
    maintenance_reclamation_reserve_bytes: int
    safety_bundle_max_bytes: int

    def model_dump(self, *, mode: str) -> dict[str, object]: ...


class StorageBundleUnavailableError(RuntimeError):
    """完整事务包无法从其授权 Reserve 原子预留。"""


class StorageBundleEvent(ArtifactCompatibility):
    schema_version: Literal["storage-bundle-event.v1"] = "storage-bundle-event.v1"
    artifact_kind: Literal["storage-bundle-event"] = "storage-bundle-event"
    project_id: str
    sequence: int = Field(ge=1)
    event_kind: StorageBundleEventKind
    bundle_id: str
    bundle_class: StorageBundleClass
    bundle_bytes: int = Field(gt=0)
    net_reclaim_bytes: int = Field(ge=0)
    policy_digest: str
    operation_id: str
    previous_event_digest: str = ""
    event_digest: str = ""

    @field_validator("project_id", "bundle_id", "policy_digest", "operation_id")
    @classmethod
    def _identity_is_present(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("storage bundle identity is invalid")
        return value

    @model_validator(mode="after")
    def _verify_event(self) -> Self:
        if self.sequence == 1 and self.previous_event_digest:
            raise ValueError("first storage bundle event cannot have predecessor")
        if self.sequence > 1 and not self.previous_event_digest:
            raise ValueError("storage bundle predecessor is required")
        if self.bundle_class == "reclamation":
            if self.net_reclaim_bytes <= self.bundle_bytes:
                raise ValueError("reclamation bundle must release net storage")
        elif self.net_reclaim_bytes:
            raise ValueError("safety bundle cannot declare reclamation")
        return fill_artifact_digest(self, "event_digest")


class StorageBundleHandle:
    def __init__(self, ledger: ResourceStorageBundleLedger, event: StorageBundleEvent):
        self._ledger = ledger
        self.reservation = event
        self.active = True

    def assert_active(self, expected_class: StorageBundleClass) -> None:
        if not self.active or self.reservation.bundle_class != expected_class:
            raise SharedStateIntegrityError("storage bundle is not active for write")

    def release(self) -> None:
        if self.active:
            self._ledger.release(self.reservation)
            self.active = False


class ResourceStorageBundleLedger:
    def __init__(self, resource_store: ResourceEventStore) -> None:
        self.resource_store = resource_store
        self.root = resource_store.root / "storage-bundle-ledger"

    def reserve(
        self,
        *,
        bundle_class: StorageBundleClass,
        bundle_bytes: int,
        net_reclaim_bytes: int,
        policy: StorageReservePolicy,
        operation_id: str,
    ) -> StorageBundleHandle:
        if bundle_bytes <= 0:
            raise ValueError("storage bundle must contain bytes")
        with self.resource_store.locked():
            events = self.events()
            active = _active_reservations(events)
            _require_capacity(
                active,
                bundle_class=bundle_class,
                bundle_bytes=bundle_bytes,
                net_reclaim_bytes=net_reclaim_bytes,
                policy=policy,
            )
            event = self._append(
                events,
                event_kind="reserved",
                bundle_id=f"storage-bundle.{operation_id}.{len(events) + 1}",
                bundle_class=bundle_class,
                bundle_bytes=bundle_bytes,
                net_reclaim_bytes=net_reclaim_bytes,
                policy_digest=_policy_digest(policy),
                operation_id=f"{operation_id}.reserve.{len(events) + 1}",
            )
        return StorageBundleHandle(self, event)

    def release(self, reservation: StorageBundleEvent) -> None:
        with self.resource_store.locked():
            events = self.events()
            active = _active_reservations(events)
            if active.get(reservation.bundle_id) != reservation:
                raise SharedStateIntegrityError("storage bundle release is stale")
            self._append(
                events,
                event_kind="released",
                bundle_id=reservation.bundle_id,
                bundle_class=reservation.bundle_class,
                bundle_bytes=reservation.bundle_bytes,
                net_reclaim_bytes=reservation.net_reclaim_bytes,
                policy_digest=reservation.policy_digest,
                operation_id=f"{reservation.operation_id}.release",
            )

    def events(self) -> tuple[StorageBundleEvent, ...]:
        if not self.root.is_dir():
            return ()
        events = tuple(
            StorageBundleEvent.model_validate(read_json_object(path))
            for path in sorted(self.root.glob("*.json"))
        )
        _verify_chain(events)
        return events

    def _append(
        self,
        events: tuple[StorageBundleEvent, ...],
        *,
        event_kind: StorageBundleEventKind,
        bundle_id: str,
        bundle_class: StorageBundleClass,
        bundle_bytes: int,
        net_reclaim_bytes: int,
        policy_digest: str,
        operation_id: str,
    ) -> StorageBundleEvent:
        event = StorageBundleEvent(
            project_id=self.resource_store.project_id,
            sequence=len(events) + 1,
            previous_event_digest="" if not events else events[-1].event_digest,
            event_kind=event_kind,
            bundle_id=bundle_id,
            bundle_class=bundle_class,
            bundle_bytes=bundle_bytes,
            net_reclaim_bytes=net_reclaim_bytes,
            policy_digest=policy_digest,
            operation_id=operation_id,
        )
        path = self.root / f"{event.sequence:020d}.json"
        if not create_json_exclusive(path, event.model_dump(mode="json")):
            raise SharedStateIntegrityError("storage bundle sequence collided")
        return event


class _ResourceStorageBundleMixin:
    _store: ResourceEventStore

    @contextmanager
    def storage_bundle(
        self,
        *,
        bundle_class: StorageBundleClass,
        bundle_bytes: int,
        net_reclaim_bytes: int,
        policy: StorageReservePolicy,
        operation_id: str,
    ) -> Iterator[StorageBundleHandle]:
        handle = ResourceStorageBundleLedger(self._store).reserve(
            bundle_class=bundle_class,
            bundle_bytes=bundle_bytes,
            net_reclaim_bytes=net_reclaim_bytes,
            policy=policy,
            operation_id=operation_id,
        )
        try:
            yield handle
        finally:
            handle.release()


def _active_reservations(
    events: tuple[StorageBundleEvent, ...],
) -> dict[str, StorageBundleEvent]:
    active: dict[str, StorageBundleEvent] = {}
    for event in events:
        if event.event_kind == "reserved":
            if event.bundle_id in active:
                raise SharedStateIntegrityError("storage bundle was reserved twice")
            active[event.bundle_id] = event
        elif active.pop(event.bundle_id, None) is None:
            raise SharedStateIntegrityError("storage bundle release has no reservation")
    return active


def _require_capacity(
    active: dict[str, StorageBundleEvent],
    *,
    bundle_class: StorageBundleClass,
    bundle_bytes: int,
    net_reclaim_bytes: int,
    policy: StorageReservePolicy,
) -> None:
    if bundle_class != "reclamation" and bundle_bytes > policy.safety_bundle_max_bytes:
        raise StorageBundleUnavailableError("safety bundle exceeds transaction maximum")
    used = {
        kind: sum(item.bundle_bytes for item in active.values() if item.bundle_class == kind)
        for kind in ("critical_recovery", "session_binding", "reclamation")
    }
    if bundle_class == "critical_recovery":
        available = (
            policy.critical_recovery_reserve_bytes
            + policy.session_binding_reserve_bytes
            - used["critical_recovery"]
            - used["session_binding"]
        )
    elif bundle_class == "session_binding":
        critical_borrow = max(
            0,
            used["critical_recovery"] - policy.critical_recovery_reserve_bytes,
        )
        available = (
            policy.session_binding_reserve_bytes
            - critical_borrow
            - used["session_binding"]
        )
    else:
        if net_reclaim_bytes <= bundle_bytes:
            raise StorageBundleUnavailableError("reclamation has no net release")
        available = (
            policy.maintenance_reclamation_reserve_bytes - used["reclamation"]
        )
    if bundle_bytes > available:
        raise StorageBundleUnavailableError("storage reserve bundle is unavailable")


def _verify_chain(events: tuple[StorageBundleEvent, ...]) -> None:
    for sequence, event in enumerate(events, start=1):
        previous = "" if sequence == 1 else events[sequence - 2].event_digest
        if event.sequence != sequence or event.previous_event_digest != previous:
            raise SharedStateIntegrityError("storage bundle event chain diverged")
    _active_reservations(events)


def _policy_digest(policy: StorageReservePolicy) -> str:
    return canonical_digest(policy.model_dump(mode="json"), CanonicalizationPolicy())
