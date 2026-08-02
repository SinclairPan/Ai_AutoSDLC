"""ResourceGovernor 内的安全写入与回收事务包账本。"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
    ShortFileLock,
    atomic_write_json,
    create_json_exclusive,
    portable_content_digest_name,
    read_json_object,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.resource_store import ResourceEventStore

StorageBundleClass = Literal["critical_recovery", "session_binding", "reclamation"]
LegacyStorageBundleEventKind = Literal["reserved", "released"]
StorageBundleEventKind = Literal["reserved", "released", "abandoned"]
_RELEASE_RETRY_BASELINES = (0.010, 0.025)


class StorageReservePolicy(Protocol):
    critical_recovery_reserve_bytes: int
    session_binding_reserve_bytes: int
    maintenance_reclamation_reserve_bytes: int
    safety_bundle_max_bytes: int

    def model_dump(self, *, mode: str) -> dict[str, object]: ...


class StorageBundleUnavailableError(RuntimeError):
    """完整事务包无法从其授权 Reserve 原子预留。"""


class LegacyStorageBundleEventV1(ArtifactCompatibility):
    schema_version: Literal["storage-bundle-event.v1"] = "storage-bundle-event.v1"
    artifact_kind: Literal["storage-bundle-event"] = "storage-bundle-event"
    project_id: str
    sequence: int = Field(ge=1)
    event_kind: LegacyStorageBundleEventKind
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


class StorageBundleEvent(LegacyStorageBundleEventV1):
    schema_version: Literal["storage-bundle-event.v2"] = "storage-bundle-event.v2"  # type: ignore[assignment]
    event_kind: StorageBundleEventKind  # type: ignore[assignment]
    owner_token_digest: str
    fencing_epoch: int = Field(ge=1)

    @field_validator("owner_token_digest")
    @classmethod
    def _owner_identity_is_present(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("storage bundle owner identity is invalid")
        return value


StorageBundleLedgerEvent = LegacyStorageBundleEventV1 | StorageBundleEvent


class StorageBundleCharge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    charge_id: str
    artifact_relative_path: str
    committed_payload_digest: str = ""
    committed_bytes: int = Field(default=0, ge=0)
    pending_payload_digest: str = ""
    pending_bytes: int = Field(default=0, ge=0)

    @field_validator(
        "charge_id",
        "artifact_relative_path",
        "committed_payload_digest",
        "pending_payload_digest",
    )
    @classmethod
    def _identity_is_well_formed(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("storage bundle charge identity is invalid")
        return value

    @model_validator(mode="after")
    def _verify_charge(self) -> Self:
        if not self.charge_id or not self.artifact_relative_path:
            raise ValueError("storage bundle charge identity is invalid")
        relative = Path(self.artifact_relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("storage bundle artifact path is invalid")
        if bool(self.committed_payload_digest) != bool(self.committed_bytes):
            raise ValueError("storage bundle committed charge is incomplete")
        if bool(self.pending_payload_digest) != bool(self.pending_bytes):
            raise ValueError("storage bundle pending charge is incomplete")
        if not self.committed_bytes and not self.pending_bytes:
            raise ValueError("storage bundle charge is empty")
        return self

    @property
    def authorized_bytes(self) -> int:
        return self.committed_bytes + self.pending_bytes


class StorageBundleAttemptAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    charge_id: str
    authorized_bytes: int = Field(gt=0)

    @field_validator("charge_id")
    @classmethod
    def _identity_is_present(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("storage bundle attempt identity is invalid")
        return value


class StorageBundleConsumptionState(ArtifactCompatibility):
    schema_version: Literal["storage-bundle-consumption-state.v2"] = (
        "storage-bundle-consumption-state.v2"
    )
    artifact_kind: Literal["storage-bundle-consumption-state"] = (
        "storage-bundle-consumption-state"
    )
    project_id: str
    operation_id: str
    bundle_class: StorageBundleClass
    bundle_bytes: int = Field(gt=0)
    policy_digest: str
    reservation_event_digest: str
    owner_token_digest: str
    fencing_epoch: int = Field(ge=1)
    consumed_bytes: int = Field(ge=0)
    charges: tuple[StorageBundleCharge, ...] = ()
    attempt_authorizations: tuple[StorageBundleAttemptAuthorization, ...] = ()
    revision: int = Field(ge=1)
    state_digest: str = ""

    @field_validator(
        "project_id",
        "operation_id",
        "policy_digest",
        "reservation_event_digest",
        "owner_token_digest",
    )
    @classmethod
    def _identity_is_present(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("storage bundle consumption identity is invalid")
        return value

    @model_validator(mode="after")
    def _verify_state(self) -> Self:
        if self.consumed_bytes > self.bundle_bytes:
            raise ValueError("storage bundle consumption exceeds reservation")
        if tuple(sorted(self.charges, key=lambda item: item.charge_id)) != self.charges:
            raise ValueError("storage bundle charges are not canonical")
        if len({item.charge_id for item in self.charges}) != len(self.charges):
            raise ValueError("storage bundle charge identity is duplicated")
        if sum(item.authorized_bytes for item in self.charges) != self.consumed_bytes:
            raise ValueError("storage bundle charge total diverged")
        if (
            tuple(
                sorted(
                    self.attempt_authorizations,
                    key=lambda item: item.charge_id,
                )
            )
            != self.attempt_authorizations
        ):
            raise ValueError("storage bundle attempt authorizations are not canonical")
        if len(
            {item.charge_id for item in self.attempt_authorizations}
        ) != len(self.attempt_authorizations):
            raise ValueError("storage bundle attempt identity is duplicated")
        if not {
            item.charge_id for item in self.attempt_authorizations
        }.issubset({item.charge_id for item in self.charges}):
            raise ValueError("storage bundle attempt has no charge")
        if (
            sum(item.authorized_bytes for item in self.attempt_authorizations)
            > self.bundle_bytes
        ):
            raise ValueError("storage bundle attempt exceeds transaction limit")
        return fill_artifact_digest(self, "state_digest")


class StorageBundleReleaseIntent(ArtifactCompatibility):
    schema_version: Literal["storage-bundle-release-intent.v1"] = (
        "storage-bundle-release-intent.v1"
    )
    artifact_kind: Literal["storage-bundle-release-intent"] = (
        "storage-bundle-release-intent"
    )
    project_id: str
    bundle_id: str
    reservation_event_digest: str
    release_operation_id: str
    intent_digest: str = ""

    @field_validator(
        "project_id",
        "bundle_id",
        "reservation_event_digest",
        "release_operation_id",
    )
    @classmethod
    def _identity_is_present(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("storage bundle release intent identity is invalid")
        return value

    @model_validator(mode="after")
    def _verify_intent(self) -> Self:
        portable_content_digest_name(self.reservation_event_digest)
        return fill_artifact_digest(self, "intent_digest")


class StorageBundleHandle:
    def __init__(
        self,
        ledger: ResourceStorageBundleLedger,
        event: StorageBundleEvent,
        *,
        owner_token: str,
        owner_lock: ShortFileLock,
    ):
        self._ledger = ledger
        self.reservation = event
        self._owner_token = owner_token
        self._owner_lock = owner_lock
        self.active = True
        self._state_lock = threading.RLock()
        self._hold_depth = threading.local()

    def assert_active(self, expected_class: StorageBundleClass) -> None:
        with self._state_lock:
            self._require_locally_active(expected_class)
            self._ledger.assert_active(
                self.reservation,
                expected_class,
                self._owner_token,
            )

    @contextmanager
    def hold_active(
        self,
        expected_class: StorageBundleClass,
    ) -> Iterator[None]:
        with self._state_lock:
            self._require_locally_active(expected_class)
            with self._ledger.hold_active(
                self.reservation,
                expected_class,
                self._owner_token,
            ):
                depth = getattr(self._hold_depth, "value", 0)
                self._hold_depth.value = depth + 1
                try:
                    yield
                finally:
                    self._hold_depth.value = depth

    def release(self) -> None:
        with self._state_lock:
            if not self.active:
                return
            for attempt in range(1, len(_RELEASE_RETRY_BASELINES) + 2):
                try:
                    self._ledger.release(self.reservation, self._owner_token)
                except ResourceLockUnavailableError:
                    if attempt > len(_RELEASE_RETRY_BASELINES):
                        self._retire_owner()
                        raise
                    time.sleep(_release_backoff(self.reservation.operation_id, attempt))
                else:
                    self._retire_owner()
                    return

    @contextmanager
    def authorize_artifact(
        self,
        path: Path,
        content: bytes,
        *,
        allow_replacement: bool = False,
    ) -> Iterator[None]:
        if not content:
            raise ValueError("storage bundle cannot authorize an empty artifact")
        with self._state_lock:
            self._require_locally_active(self.reservation.bundle_class)
            if getattr(self._hold_depth, "value", 0):
                self._ledger._begin_artifact_locked(
                    self.reservation,
                    self._owner_token,
                    path,
                    content,
                    allow_replacement=allow_replacement,
                )
            else:
                self._ledger.begin_artifact(
                    self.reservation,
                    self._owner_token,
                    path,
                    content,
                    allow_replacement=allow_replacement,
                )
            try:
                yield
            except BaseException:
                raise
            else:
                if getattr(self._hold_depth, "value", 0):
                    self._ledger._commit_artifact_locked(
                        self.reservation,
                        self._owner_token,
                        path,
                        content,
                    )
                else:
                    self._ledger.commit_artifact(
                        self.reservation,
                        self._owner_token,
                        path,
                        content,
                    )

    def confirm_artifact(self, path: Path, content: bytes) -> None:
        if not content:
            raise ValueError("storage bundle cannot confirm an empty artifact")
        with self._state_lock:
            self._require_locally_active(self.reservation.bundle_class)
            if getattr(self._hold_depth, "value", 0):
                self._ledger._confirm_artifact_locked(
                    self.reservation,
                    self._owner_token,
                    path,
                    content,
                )
            else:
                self._ledger.confirm_artifact(
                    self.reservation,
                    self._owner_token,
                    path,
                    content,
                )

    def _require_locally_active(self, expected_class: StorageBundleClass) -> None:
        if not self.active or self.reservation.bundle_class != expected_class:
            raise SharedStateIntegrityError("storage bundle is not active for write")

    def _retire_owner(self) -> None:
        self.active = False
        self._owner_lock.__exit__(None, None, None)


class ResourceStorageBundleLedger:
    def __init__(self, resource_store: ResourceEventStore) -> None:
        self.resource_store = resource_store
        self.root = resource_store.root / "storage-bundle-ledger"
        self.release_intents_root = self.root / "release-intents"
        self.consumption_root = self.root / "consumption"
        self.release_gate_path = (
            resource_store.shared_root / "locks" / "storage-bundle-release.lock"
        )
        self.owner_locks_root = (
            resource_store.shared_root / "locks" / "storage-bundle-owners"
        )
        self.consumption_locks_root = (
            resource_store.shared_root / "locks" / "storage-bundle-consumption"
        )

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
        policy_digest = _policy_digest(policy)
        owner_lock = ShortFileLock(
            self._owner_lock_path(operation_id),
            timeout_seconds=self.resource_store.lock_timeout_seconds,
        )
        owner_lock.__enter__()
        owner_token = secrets.token_hex(32)
        owner_token_digest = _owner_token_digest(owner_token)
        try:
            with self.resource_store.locked():
                events = self._recover_release_intents(self.events())
                active = _active_reservations(events)
                existing = _active_reservation_for_operation(active, operation_id)
                known_state = self._consumption_state(operation_id)
                previous_fencing_epoch = (
                    0 if existing is None else _event_fencing_epoch(existing)
                )
                if existing is not None:
                    _verify_reservation_identity(
                        existing,
                        project_id=self.resource_store.project_id,
                        bundle_class=bundle_class,
                        bundle_bytes=bundle_bytes,
                        net_reclaim_bytes=net_reclaim_bytes,
                        policy_digest=policy_digest,
                        operation_id=operation_id,
                        original_bundle_bytes=(
                            None if known_state is None else known_state.bundle_bytes
                        ),
                    )
                events = self._abandon_orphaned_reservations(
                    events,
                    current_operation_id=operation_id,
                )
                active = _active_reservations(events)
                _require_capacity(
                    active,
                    bundle_class=bundle_class,
                    bundle_bytes=bundle_bytes,
                    net_reclaim_bytes=net_reclaim_bytes,
                    policy=policy,
                )
                with ShortFileLock(
                    self._consumption_lock_path(operation_id),
                    timeout_seconds=self.resource_store.lock_timeout_seconds,
                ):
                    previous = self._consumption_state(operation_id)
                    fencing_epoch = (
                        max(
                            previous_fencing_epoch,
                            0 if previous is None else previous.fencing_epoch,
                        )
                        + 1
                    )
                    if previous is not None:
                        _verify_consumption_request(
                            previous,
                            project_id=self.resource_store.project_id,
                            operation_id=operation_id,
                            bundle_class=bundle_class,
                            bundle_bytes=bundle_bytes,
                            policy_digest=policy_digest,
                        )
                    reconciled_charges = (
                        ()
                        if previous is None
                        else self._reconcile_persisted_charges(previous.charges)
                    )
                    event = self._append(
                        events,
                        event_kind="reserved",
                        bundle_id=f"storage-bundle.{operation_id}.{len(events) + 1}",
                        bundle_class=bundle_class,
                        bundle_bytes=bundle_bytes,
                        net_reclaim_bytes=net_reclaim_bytes,
                        policy_digest=policy_digest,
                        operation_id=f"{operation_id}.reserve.{len(events) + 1}",
                        owner_token_digest=owner_token_digest,
                        fencing_epoch=fencing_epoch,
                    )
                    self._write_consumption_state(
                        event,
                        operation_id=operation_id,
                        owner_token_digest=owner_token_digest,
                        bundle_bytes=(
                            event.bundle_bytes
                            if previous is None
                            else previous.bundle_bytes
                        ),
                        consumed_bytes=(
                            sum(
                                item.authorized_bytes
                                for item in reconciled_charges
                            )
                        ),
                        charges=reconciled_charges,
                        attempt_authorizations=(),
                        revision=1 if previous is None else previous.revision + 1,
                    )
        except BaseException:
            owner_lock.__exit__(None, None, None)
            raise
        return StorageBundleHandle(
            self,
            event,
            owner_token=owner_token,
            owner_lock=owner_lock,
        )

    def _abandon_orphaned_reservations(
        self,
        events: tuple[StorageBundleLedgerEvent, ...],
        *,
        current_operation_id: str,
    ) -> tuple[StorageBundleLedgerEvent, ...]:
        active = tuple(
            sorted(_active_reservations(events).values(), key=lambda item: item.sequence)
        )
        for reservation in active:
            operation_id = _require_reservation_operation_id(reservation)
            probe: ShortFileLock | None = None
            if operation_id != current_operation_id:
                probe = ShortFileLock(
                    self._owner_lock_path(operation_id),
                    timeout_seconds=0,
                )
                try:
                    probe.__enter__()
                except ResourceLockUnavailableError:
                    continue
            try:
                abandoned = self._append(
                    events,
                    event_kind="abandoned",
                    bundle_id=reservation.bundle_id,
                    bundle_class=reservation.bundle_class,
                    bundle_bytes=reservation.bundle_bytes,
                    net_reclaim_bytes=reservation.net_reclaim_bytes,
                    policy_digest=reservation.policy_digest,
                    operation_id=(
                        f"{reservation.operation_id}.abandon.{len(events) + 1}"
                    ),
                    owner_token_digest=_event_owner_token_digest(reservation),
                    fencing_epoch=_event_fencing_epoch(reservation),
                )
                events = (*events, abandoned)
            finally:
                if probe is not None:
                    probe.__exit__(None, None, None)
        return events

    def release(self, reservation: StorageBundleEvent, owner_token: str) -> None:
        with ShortFileLock(
            self.release_gate_path,
            timeout_seconds=self.resource_store.lock_timeout_seconds,
        ):
            self._verify_reservation_belongs(reservation)
            self._verify_owner(reservation, owner_token)
            self._persist_release_intent(reservation)
            with self.resource_store.locked():
                events = self._recover_release_intents(self.events())
                operation_id = f"{reservation.operation_id}.release"
                existing = next(
                    (event for event in events if event.operation_id == operation_id),
                    None,
                )
                if existing is None:
                    raise SharedStateIntegrityError(
                        "storage bundle release intent was lost"
                    )
                _verify_release_identity(existing, reservation)

    def assert_active(
        self,
        reservation: StorageBundleEvent,
        expected_class: StorageBundleClass,
        owner_token: str,
    ) -> None:
        with self.resource_store.locked():
            self._assert_active_locked(reservation, expected_class, owner_token)

    @contextmanager
    def hold_active(
        self,
        reservation: StorageBundleEvent,
        expected_class: StorageBundleClass,
        owner_token: str,
    ) -> Iterator[None]:
        with (
            ShortFileLock(
                self.release_gate_path,
                timeout_seconds=self.resource_store.lock_timeout_seconds,
            ),
            self.resource_store.locked(),
        ):
            self._assert_active_locked(reservation, expected_class, owner_token)
            yield

    def _assert_active_locked(
        self,
        reservation: StorageBundleEvent,
        expected_class: StorageBundleClass,
        owner_token: str,
    ) -> None:
        self._verify_reservation_belongs(reservation)
        self._verify_owner(reservation, owner_token)
        events = self._recover_release_intents(self.events())
        if (
            reservation.bundle_class != expected_class
            or _active_reservations(events).get(reservation.bundle_id) != reservation
        ):
            raise SharedStateIntegrityError("storage bundle is not active for write")

    def begin_artifact(
        self,
        reservation: StorageBundleEvent,
        owner_token: str,
        path: Path,
        content: bytes,
        *,
        allow_replacement: bool,
    ) -> None:
        with (
            ShortFileLock(
                self.release_gate_path,
                timeout_seconds=self.resource_store.lock_timeout_seconds,
            ),
            self.resource_store.locked(),
        ):
            self._assert_active_locked(
                reservation,
                reservation.bundle_class,
                owner_token,
            )
            self._begin_artifact_locked(
                reservation,
                owner_token,
                path,
                content,
                allow_replacement=allow_replacement,
            )

    def commit_artifact(
        self,
        reservation: StorageBundleEvent,
        owner_token: str,
        path: Path,
        content: bytes,
    ) -> None:
        with (
            ShortFileLock(
                self.release_gate_path,
                timeout_seconds=self.resource_store.lock_timeout_seconds,
            ),
            self.resource_store.locked(),
        ):
            self._assert_active_locked(
                reservation,
                reservation.bundle_class,
                owner_token,
            )
            self._commit_artifact_locked(
                reservation,
                owner_token,
                path,
                content,
            )

    def confirm_artifact(
        self,
        reservation: StorageBundleEvent,
        owner_token: str,
        path: Path,
        content: bytes,
    ) -> None:
        with (
            ShortFileLock(
                self.release_gate_path,
                timeout_seconds=self.resource_store.lock_timeout_seconds,
            ),
            self.resource_store.locked(),
        ):
            self._assert_active_locked(
                reservation,
                reservation.bundle_class,
                owner_token,
            )
            self._confirm_artifact_locked(
                reservation,
                owner_token,
                path,
                content,
            )

    def _begin_artifact_locked(
        self,
        reservation: StorageBundleEvent,
        owner_token: str,
        path: Path,
        content: bytes,
        *,
        allow_replacement: bool,
    ) -> None:
        operation_id = _require_reservation_operation_id(reservation)
        with ShortFileLock(
            self._consumption_lock_path(operation_id),
            timeout_seconds=self.resource_store.lock_timeout_seconds,
        ):
            state = self._require_consumption_owner(
                reservation,
                owner_token,
                operation_id,
            )
            charge_id, payload_digest, relative_path = self._artifact_identity(
                path,
                content,
            )
            charges = {item.charge_id: item for item in state.charges}
            existing = charges.get(charge_id)
            if existing is not None:
                if existing.artifact_relative_path != relative_path:
                    raise SharedStateIntegrityError(
                        "storage bundle artifact identity collided"
                    )
                reconciled = _reconcile_charge_with_disk(existing, path)
                if reconciled is None:
                    charges.pop(charge_id)
                else:
                    charges[charge_id] = reconciled
                existing = reconciled
            if existing is not None:
                if (
                    existing.pending_payload_digest == payload_digest
                    and existing.pending_bytes == len(content)
                ):
                    pass
                elif existing.committed_bytes and not allow_replacement:
                    raise SharedStateIntegrityError(
                        "storage bundle artifact authorization diverged"
                    )
                else:
                    charges[charge_id] = existing.model_copy(
                        update={
                            "pending_payload_digest": payload_digest,
                            "pending_bytes": len(content),
                        }
                    )
            else:
                charges[charge_id] = StorageBundleCharge(
                    charge_id=charge_id,
                    artifact_relative_path=relative_path,
                    pending_payload_digest=payload_digest,
                    pending_bytes=len(content),
                )
            canonical_charges = tuple(
                sorted(charges.values(), key=lambda item: item.charge_id)
            )
            consumed_bytes = sum(item.authorized_bytes for item in canonical_charges)
            if consumed_bytes > state.bundle_bytes:
                raise StorageBundleUnavailableError(
                    "storage bundle transaction bytes are exhausted"
                )
            attempts = {
                item.charge_id: item for item in state.attempt_authorizations
            }
            attempt_changed = (
                charge_id not in attempts
                or attempts[charge_id].authorized_bytes != len(content)
            )
            attempts[charge_id] = StorageBundleAttemptAuthorization(
                charge_id=charge_id,
                authorized_bytes=len(content),
            )
            if (
                sum(item.authorized_bytes for item in attempts.values())
                > reservation.bundle_bytes
            ):
                raise StorageBundleUnavailableError(
                    "storage bundle retry bytes are exhausted"
                )
            canonical_attempts = tuple(
                sorted(attempts.values(), key=lambda item: item.charge_id)
            )
            if (
                canonical_charges == state.charges
                and not attempt_changed
            ):
                return
            self._write_consumption_state(
                reservation,
                operation_id=operation_id,
                owner_token_digest=state.owner_token_digest,
                bundle_bytes=state.bundle_bytes,
                consumed_bytes=consumed_bytes,
                charges=canonical_charges,
                attempt_authorizations=canonical_attempts,
                revision=state.revision + 1,
            )

    def _commit_artifact_locked(
        self,
        reservation: StorageBundleEvent,
        owner_token: str,
        path: Path,
        content: bytes,
    ) -> None:
        self._settle_artifact_locked(
            reservation,
            owner_token,
            path,
            content,
            allow_missing=False,
        )

    def _confirm_artifact_locked(
        self,
        reservation: StorageBundleEvent,
        owner_token: str,
        path: Path,
        content: bytes,
    ) -> None:
        self._settle_artifact_locked(
            reservation,
            owner_token,
            path,
            content,
            allow_missing=True,
        )

    def _settle_artifact_locked(
        self,
        reservation: StorageBundleEvent,
        owner_token: str,
        path: Path,
        content: bytes,
        *,
        allow_missing: bool,
    ) -> None:
        operation_id = _require_reservation_operation_id(reservation)
        with ShortFileLock(
            self._consumption_lock_path(operation_id),
            timeout_seconds=self.resource_store.lock_timeout_seconds,
        ):
            state = self._require_consumption_owner(
                reservation,
                owner_token,
                operation_id,
            )
            charge_id, payload_digest, relative_path = self._artifact_identity(
                path,
                content,
            )
            charges = {item.charge_id: item for item in state.charges}
            existing = charges.get(charge_id)
            if existing is None and allow_missing:
                return
            if existing is None:
                raise SharedStateIntegrityError(
                    "storage bundle artifact receipt has no authorization"
                )
            if existing.artifact_relative_path != relative_path:
                raise SharedStateIntegrityError(
                    "storage bundle artifact identity collided"
                )
            reconciled = _reconcile_charge_with_disk(existing, path)
            if reconciled is None or (
                reconciled.committed_payload_digest != payload_digest
                or reconciled.committed_bytes != len(content)
                or reconciled.pending_bytes
            ):
                raise SharedStateIntegrityError(
                    "storage bundle artifact receipt has no authorization"
                )
            charges[charge_id] = reconciled
            canonical_charges = tuple(
                sorted(charges.values(), key=lambda item: item.charge_id)
            )
            if canonical_charges == state.charges:
                return
            consumed_bytes = sum(item.authorized_bytes for item in canonical_charges)
            self._write_consumption_state(
                reservation,
                operation_id=operation_id,
                owner_token_digest=state.owner_token_digest,
                bundle_bytes=state.bundle_bytes,
                consumed_bytes=consumed_bytes,
                charges=canonical_charges,
                attempt_authorizations=state.attempt_authorizations,
                revision=state.revision + 1,
            )

    def events(self) -> tuple[StorageBundleLedgerEvent, ...]:
        if not self.root.is_dir():
            return ()
        events = tuple(
            _read_storage_bundle_event(path)
            for path in sorted(self.root.glob("*.json"))
        )
        _verify_chain(events)
        return events

    def _verify_owner(
        self,
        reservation: StorageBundleEvent,
        owner_token: str,
    ) -> None:
        operation_id = _require_reservation_operation_id(reservation)
        state = self._consumption_state(operation_id)
        if (
            _owner_token_digest(owner_token) != reservation.owner_token_digest
            or state is None
            or state.reservation_event_digest != reservation.event_digest
            or state.owner_token_digest != reservation.owner_token_digest
            or state.fencing_epoch != reservation.fencing_epoch
        ):
            raise SharedStateIntegrityError("storage bundle owner fence is stale")

    def _require_consumption_owner(
        self,
        reservation: StorageBundleEvent,
        owner_token: str,
        operation_id: str,
    ) -> StorageBundleConsumptionState:
        state = self._consumption_state(operation_id)
        if state is None:
            raise SharedStateIntegrityError("storage bundle consumption state is missing")
        if (
            _owner_token_digest(owner_token),
            state.project_id,
            state.operation_id,
            state.bundle_class,
            state.policy_digest,
            state.reservation_event_digest,
            state.owner_token_digest,
            state.fencing_epoch,
        ) != (
            reservation.owner_token_digest,
            reservation.project_id,
            operation_id,
            reservation.bundle_class,
            reservation.policy_digest,
            reservation.event_digest,
            reservation.owner_token_digest,
            reservation.fencing_epoch,
        ):
            raise SharedStateIntegrityError("storage bundle consumption fence is stale")
        if reservation.bundle_bytes > state.bundle_bytes:
            raise SharedStateIntegrityError(
                "storage bundle replay diverged beyond original limit"
            )
        return state

    def _consumption_state(
        self,
        operation_id: str,
    ) -> StorageBundleConsumptionState | None:
        path = self._consumption_state_path(operation_id)
        if not path.is_file():
            return None
        return StorageBundleConsumptionState.model_validate(read_json_object(path))

    def _write_consumption_state(
        self,
        reservation: StorageBundleEvent,
        *,
        operation_id: str,
        owner_token_digest: str,
        bundle_bytes: int,
        consumed_bytes: int,
        charges: tuple[StorageBundleCharge, ...],
        attempt_authorizations: tuple[StorageBundleAttemptAuthorization, ...],
        revision: int,
    ) -> StorageBundleConsumptionState:
        state = StorageBundleConsumptionState(
            project_id=self.resource_store.project_id,
            operation_id=operation_id,
            bundle_class=reservation.bundle_class,
            bundle_bytes=bundle_bytes,
            policy_digest=reservation.policy_digest,
            reservation_event_digest=reservation.event_digest,
            owner_token_digest=owner_token_digest,
            fencing_epoch=reservation.fencing_epoch,
            consumed_bytes=consumed_bytes,
            charges=charges,
            attempt_authorizations=attempt_authorizations,
            revision=revision,
        )
        atomic_write_json(
            self._consumption_state_path(operation_id),
            state.model_dump(mode="json"),
        )
        return state

    def _persist_release_intent(
        self,
        reservation: StorageBundleEvent,
    ) -> StorageBundleReleaseIntent:
        intent = StorageBundleReleaseIntent(
            project_id=self.resource_store.project_id,
            bundle_id=reservation.bundle_id,
            reservation_event_digest=reservation.event_digest,
            release_operation_id=f"{reservation.operation_id}.release",
        )
        path = self._release_intent_path(reservation.event_digest)
        if create_json_exclusive(path, intent.model_dump(mode="json")):
            return intent
        existing = StorageBundleReleaseIntent.model_validate(read_json_object(path))
        if existing != intent:
            raise SharedStateIntegrityError("storage bundle release intent diverged")
        return existing

    def _verify_reservation_belongs(
        self,
        reservation: StorageBundleEvent,
    ) -> None:
        existing = next(
            (
                event
                for event in self.events()
                if event.event_digest == reservation.event_digest
            ),
            None,
        )
        if (
            reservation.project_id != self.resource_store.project_id
            or reservation.event_kind != "reserved"
            or existing != reservation
        ):
            raise SharedStateIntegrityError(
                "storage bundle reservation does not belong to ledger"
            )

    def _recover_release_intents(
        self,
        events: tuple[StorageBundleLedgerEvent, ...],
    ) -> tuple[StorageBundleLedgerEvent, ...]:
        events_by_digest = {event.event_digest: event for event in events}
        events_by_operation = {event.operation_id: event for event in events}
        for path in sorted(self.release_intents_root.glob("*.json")):
            intent = StorageBundleReleaseIntent.model_validate(read_json_object(path))
            reservation = events_by_digest.get(intent.reservation_event_digest)
            if reservation is None:
                raise SharedStateIntegrityError(
                    "storage bundle release intent has no reservation"
                )
            _verify_release_intent(intent, reservation, self.resource_store.project_id)
            existing = events_by_operation.get(intent.release_operation_id)
            if existing is not None:
                _verify_release_identity(existing, reservation)
                continue
            active = _active_reservations(events)
            if active.get(reservation.bundle_id) != reservation:
                raise SharedStateIntegrityError("storage bundle release is stale")
            release = self._append(
                events,
                event_kind="released",
                bundle_id=reservation.bundle_id,
                bundle_class=reservation.bundle_class,
                bundle_bytes=reservation.bundle_bytes,
                net_reclaim_bytes=reservation.net_reclaim_bytes,
                policy_digest=reservation.policy_digest,
                operation_id=intent.release_operation_id,
                owner_token_digest=_event_owner_token_digest(reservation),
                fencing_epoch=_event_fencing_epoch(reservation),
            )
            events = (*events, release)
            events_by_digest[release.event_digest] = release
            events_by_operation[release.operation_id] = release
        return events

    def _release_intent_path(self, reservation_digest: str) -> Path:
        return self.release_intents_root / (
            f"{portable_content_digest_name(reservation_digest)}.json"
        )

    def _owner_lock_path(self, operation_id: str) -> Path:
        return self.owner_locks_root / f"{_operation_digest(operation_id)}.lock"

    def _consumption_lock_path(self, operation_id: str) -> Path:
        return self.consumption_locks_root / f"{_operation_digest(operation_id)}.lock"

    def _consumption_state_path(self, operation_id: str) -> Path:
        return self.consumption_root / f"{_operation_digest(operation_id)}.json"

    def _reconcile_persisted_charges(
        self,
        charges: tuple[StorageBundleCharge, ...],
    ) -> tuple[StorageBundleCharge, ...]:
        reconciled: list[StorageBundleCharge] = []
        shared_root = self.resource_store.shared_root.resolve(strict=False)
        for charge in charges:
            path = (shared_root / charge.artifact_relative_path).resolve(strict=False)
            try:
                path.relative_to(shared_root)
            except ValueError as exc:
                raise SharedStateIntegrityError(
                    "storage bundle artifact escaped shared state"
                ) from exc
            current = _reconcile_charge_with_disk(charge, path)
            if current is not None:
                reconciled.append(current)
        return tuple(sorted(reconciled, key=lambda item: item.charge_id))

    def _artifact_identity(
        self,
        path: Path,
        content: bytes,
    ) -> tuple[str, str, str]:
        try:
            relative = path.resolve(strict=False).relative_to(
                self.resource_store.shared_root.resolve(strict=False)
            )
        except ValueError as exc:
            raise SharedStateIntegrityError(
                "storage bundle artifact escaped shared state"
            ) from exc
        relative_path = relative.as_posix()
        charge_id = f"sha256:{hashlib.sha256(relative_path.encode()).hexdigest()}"
        payload_digest = _content_digest(content)
        return charge_id, payload_digest, relative_path

    def _append(
        self,
        events: tuple[StorageBundleLedgerEvent, ...],
        *,
        event_kind: StorageBundleEventKind,
        bundle_id: str,
        bundle_class: StorageBundleClass,
        bundle_bytes: int,
        net_reclaim_bytes: int,
        policy_digest: str,
        operation_id: str,
        owner_token_digest: str,
        fencing_epoch: int,
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
            owner_token_digest=owner_token_digest,
            fencing_epoch=fencing_epoch,
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
        except BaseException as body_error:
            try:
                handle.release()
            except Exception as release_error:
                body_error.add_note(
                    "storage bundle release deferred after body failure: "
                    f"{type(release_error).__name__}: {release_error}"
                )
            raise
        else:
            handle.release()


def _active_reservations(
    events: tuple[StorageBundleLedgerEvent, ...],
) -> dict[str, StorageBundleLedgerEvent]:
    active: dict[str, StorageBundleLedgerEvent] = {}
    for event in events:
        if event.event_kind == "reserved":
            if event.bundle_id in active:
                raise SharedStateIntegrityError("storage bundle was reserved twice")
            active[event.bundle_id] = event
        elif active.pop(event.bundle_id, None) is None:
            raise SharedStateIntegrityError("storage bundle release has no reservation")
    return active


def _require_capacity(
    active: dict[str, StorageBundleLedgerEvent],
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


def _verify_chain(events: tuple[StorageBundleLedgerEvent, ...]) -> None:
    for sequence, event in enumerate(events, start=1):
        previous = "" if sequence == 1 else events[sequence - 2].event_digest
        if event.sequence != sequence or event.previous_event_digest != previous:
            raise SharedStateIntegrityError("storage bundle event chain diverged")
    _active_reservations(events)


def _policy_digest(policy: StorageReservePolicy) -> str:
    return canonical_digest(policy.model_dump(mode="json"), CanonicalizationPolicy())


def _verify_release_identity(
    event: StorageBundleLedgerEvent,
    reservation: StorageBundleLedgerEvent,
) -> None:
    common_identity = (
        event.project_id,
        event.event_kind,
        event.bundle_id,
        event.bundle_class,
        event.bundle_bytes,
        event.net_reclaim_bytes,
        event.policy_digest,
    )
    expected_identity = (
        reservation.project_id,
        "released",
        reservation.bundle_id,
        reservation.bundle_class,
        reservation.bundle_bytes,
        reservation.net_reclaim_bytes,
        reservation.policy_digest,
    )
    if common_identity != expected_identity:
        raise SharedStateIntegrityError("storage bundle release operation diverged")
    if isinstance(reservation, StorageBundleEvent) and (
        not isinstance(event, StorageBundleEvent)
        or event.owner_token_digest != reservation.owner_token_digest
        or event.fencing_epoch != reservation.fencing_epoch
    ):
        raise SharedStateIntegrityError("storage bundle release operation diverged")


def _verify_release_intent(
    intent: StorageBundleReleaseIntent,
    reservation: StorageBundleLedgerEvent,
    project_id: str,
) -> None:
    if (
        intent.project_id,
        intent.bundle_id,
        intent.reservation_event_digest,
        intent.release_operation_id,
        reservation.event_kind,
    ) != (
        project_id,
        reservation.bundle_id,
        reservation.event_digest,
        f"{reservation.operation_id}.release",
        "reserved",
    ):
        raise SharedStateIntegrityError("storage bundle release intent diverged")


def _active_reservation_for_operation(
    active: dict[str, StorageBundleLedgerEvent],
    operation_id: str,
) -> StorageBundleLedgerEvent | None:
    matches = tuple(
        event
        for event in active.values()
        if _reservation_operation_id(event) == operation_id
    )
    if len(matches) > 1:
        raise SharedStateIntegrityError("storage bundle reservation operation forked")
    return None if not matches else matches[0]


def _reservation_operation_id(event: StorageBundleLedgerEvent) -> str | None:
    operation_id, marker, sequence = event.operation_id.rpartition(".reserve.")
    if marker and sequence.isdecimal() and int(sequence) == event.sequence:
        return operation_id
    return None


def _require_reservation_operation_id(event: StorageBundleLedgerEvent) -> str:
    operation_id = _reservation_operation_id(event)
    if operation_id is None:
        raise SharedStateIntegrityError("storage bundle reservation identity is invalid")
    return operation_id


def _verify_reservation_identity(
    event: StorageBundleLedgerEvent,
    *,
    project_id: str,
    bundle_class: StorageBundleClass,
    bundle_bytes: int,
    net_reclaim_bytes: int,
    policy_digest: str,
    operation_id: str,
    original_bundle_bytes: int | None,
) -> None:
    if (
        event.project_id,
        event.event_kind,
        event.bundle_id,
        event.bundle_class,
        event.policy_digest,
    ) != (
        project_id,
        "reserved",
        f"storage-bundle.{operation_id}.{event.sequence}",
        bundle_class,
        policy_digest,
    ):
        raise SharedStateIntegrityError("storage bundle reservation operation diverged")
    if original_bundle_bytes is None:
        if (
            event.bundle_bytes,
            event.net_reclaim_bytes,
        ) != (
            bundle_bytes,
            net_reclaim_bytes,
        ):
            raise SharedStateIntegrityError(
                "storage bundle reservation operation diverged"
            )
    elif bundle_bytes > original_bundle_bytes:
        raise SharedStateIntegrityError(
            "storage bundle replay diverged beyond original limit"
        )


def _verify_consumption_request(
    state: StorageBundleConsumptionState,
    *,
    project_id: str,
    operation_id: str,
    bundle_class: StorageBundleClass,
    bundle_bytes: int,
    policy_digest: str,
) -> None:
    if (
        state.project_id,
        state.operation_id,
        state.bundle_class,
        state.policy_digest,
    ) != (
        project_id,
        operation_id,
        bundle_class,
        policy_digest,
    ):
        raise SharedStateIntegrityError("storage bundle replay request diverged")
    if bundle_bytes > state.bundle_bytes:
        raise SharedStateIntegrityError(
            "storage bundle replay diverged beyond original limit"
        )


def _read_storage_bundle_event(path: Path) -> StorageBundleLedgerEvent:
    payload = read_json_object(path)
    schema_version = payload.get("schema_version")
    if schema_version == "storage-bundle-event.v1":
        return LegacyStorageBundleEventV1.model_validate(payload)
    if schema_version == "storage-bundle-event.v2":
        return StorageBundleEvent.model_validate(payload)
    raise SharedStateIntegrityError("storage bundle event schema is unsupported")


def _reconcile_charge_with_disk(
    charge: StorageBundleCharge,
    path: Path,
) -> StorageBundleCharge | None:
    if not path.is_file():
        if charge.committed_bytes:
            raise SharedStateIntegrityError(
                "storage bundle committed artifact is missing"
            )
        return None
    disk_content = path.read_bytes()
    disk_digest = _content_digest(disk_content)
    if (
        charge.pending_bytes == len(disk_content)
        and charge.pending_payload_digest == disk_digest
    ):
        return StorageBundleCharge(
            charge_id=charge.charge_id,
            artifact_relative_path=charge.artifact_relative_path,
            committed_payload_digest=charge.pending_payload_digest,
            committed_bytes=charge.pending_bytes,
        )
    if (
        charge.committed_bytes == len(disk_content)
        and charge.committed_payload_digest == disk_digest
    ):
        return StorageBundleCharge(
            charge_id=charge.charge_id,
            artifact_relative_path=charge.artifact_relative_path,
            committed_payload_digest=charge.committed_payload_digest,
            committed_bytes=charge.committed_bytes,
        )
    raise SharedStateIntegrityError("storage bundle artifact content diverged")


def _content_digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _event_owner_token_digest(event: StorageBundleLedgerEvent) -> str:
    if isinstance(event, StorageBundleEvent):
        return event.owner_token_digest
    return _owner_token_digest(f"read-only-legacy:{event.event_digest}")


def _event_fencing_epoch(event: StorageBundleLedgerEvent) -> int:
    return event.fencing_epoch if isinstance(event, StorageBundleEvent) else 1


def _operation_digest(operation_id: str) -> str:
    return hashlib.sha256(operation_id.encode("utf-8")).hexdigest()


def _owner_token_digest(owner_token: str) -> str:
    return f"sha256:{hashlib.sha256(owner_token.encode('utf-8')).hexdigest()}"


def _release_backoff(operation_id: str, attempt: int) -> float:
    baseline = _RELEASE_RETRY_BASELINES[attempt - 1]
    digest = hashlib.sha256(f"{operation_id}:{attempt}".encode()).digest()
    jitter = 0.9 + (int.from_bytes(digest[:2], "big") % 201) / 1000
    return baseline * jitter
