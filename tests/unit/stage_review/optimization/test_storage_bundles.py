from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
    create_json_exclusive,
    serialized_json_bytes,
)
from ai_sdlc.core.stage_review.optimization.accounting import (
    OfflineOptimizationAccounting,
)
from ai_sdlc.core.stage_review.optimization.storage_models import (
    OptimizationStoragePolicy,
)
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.resource_storage_bundles import (
    LegacyStorageBundleEventV1,
    ResourceStorageBundleLedger,
    StorageBundleUnavailableError,
)
from ai_sdlc.core.stage_review.resources import ResourceGovernor


def test_resource_governor_reserves_storage_bundles_atomically(tmp_path: Path) -> None:
    governor = _governor(tmp_path)
    policy = _policy()

    with governor.storage_bundle(
        bundle_class="critical_recovery",
        bundle_bytes=250,
        net_reclaim_bytes=0,
        policy=policy,
        operation_id="bundle.critical",
    ) as critical:
        assert critical.active
        with pytest.raises(
            StorageBundleUnavailableError
        ), governor.storage_bundle(
            bundle_class="session_binding",
            bundle_bytes=51,
            net_reclaim_bytes=0,
            policy=policy,
            operation_id="bundle.session-too-large",
        ):
            pass
        with governor.storage_bundle(
            bundle_class="session_binding",
            bundle_bytes=50,
            net_reclaim_bytes=0,
            policy=policy,
            operation_id="bundle.session-fit",
        ) as session:
            assert session.active
    assert not critical.active


def test_bundle_classes_cannot_borrow_forbidden_reserves(tmp_path: Path) -> None:
    governor = _governor(tmp_path)
    policy = _policy()

    with pytest.raises(StorageBundleUnavailableError), governor.storage_bundle(
        bundle_class="session_binding",
        bundle_bytes=101,
        net_reclaim_bytes=0,
        policy=policy,
        operation_id="bundle.session-borrow-critical",
    ):
        pass
    with governor.storage_bundle(
        bundle_class="critical_recovery",
        bundle_bytes=300,
        net_reclaim_bytes=0,
        policy=policy,
        operation_id="bundle.critical-borrow-session",
    ) as critical:
        assert critical.active
    with pytest.raises(StorageBundleUnavailableError), governor.storage_bundle(
        bundle_class="reclamation",
        bundle_bytes=200,
        net_reclaim_bytes=200,
        policy=policy,
        operation_id="bundle.reclamation-no-net-release",
    ):
        pass


def test_exhausted_session_reserve_does_not_block_critical_recovery(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    policy = _policy()

    with governor.storage_bundle(
        bundle_class="session_binding",
        bundle_bytes=100,
        net_reclaim_bytes=0,
        policy=policy,
        operation_id="bundle.session-exhausted",
    ):
        with pytest.raises(StorageBundleUnavailableError), governor.storage_bundle(
            bundle_class="session_binding",
            bundle_bytes=1,
            net_reclaim_bytes=0,
            policy=policy,
            operation_id="bundle.session-overflow",
        ):
            pass
        with governor.storage_bundle(
            bundle_class="critical_recovery",
            bundle_bytes=200,
            net_reclaim_bytes=0,
            policy=policy,
            operation_id="bundle.revocation-still-allowed",
        ) as critical:
            assert critical.active


def test_storage_bundle_release_is_idempotent(tmp_path: Path) -> None:
    governor = _governor(tmp_path)
    ledger = ResourceStorageBundleLedger(governor._store)
    handle = ledger.reserve(
        bundle_class="critical_recovery",
        bundle_bytes=10,
        net_reclaim_bytes=0,
        policy=_policy(),
        operation_id="bundle.idempotent-release",
    )

    handle.release()
    handle.release()

    assert [event.event_kind for event in ledger.events()] == ["reserved", "released"]


def test_storage_bundle_handle_retries_transient_release_lock_contention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    governor = _governor(tmp_path)
    ledger = ResourceStorageBundleLedger(governor._store)
    handle = ledger.reserve(
        bundle_class="critical_recovery",
        bundle_bytes=10,
        net_reclaim_bytes=0,
        policy=_policy(),
        operation_id="bundle.release-retry",
    )
    release = ledger.release
    attempts = 0

    def transient_release(*args: object, **kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ResourceLockUnavailableError("resource lock is unavailable")
        release(*args, **kwargs)

    monkeypatch.setattr(ledger, "release", transient_release)

    handle.release()

    assert attempts == 3
    assert not handle.active


def test_replayed_reservation_reuses_bundle_after_release_retry_exhaustion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    governor = _governor(tmp_path)
    ledger = ResourceStorageBundleLedger(governor._store)
    handle = ledger.reserve(
        bundle_class="critical_recovery",
        bundle_bytes=10,
        net_reclaim_bytes=0,
        policy=_policy(),
        operation_id="bundle.exhausted-release-retry",
    )
    locked = ledger.resource_store.locked

    @contextmanager
    def unavailable_lock() -> Iterator[None]:
        raise ResourceLockUnavailableError("resource lock is unavailable")
        yield

    monkeypatch.setattr(ledger.resource_store, "locked", unavailable_lock)
    with pytest.raises(ResourceLockUnavailableError):
        handle.release()
    monkeypatch.setattr(ledger.resource_store, "locked", locked)

    replayed_ledger = ResourceStorageBundleLedger(governor._store)
    replayed = replayed_ledger.reserve(
        bundle_class="critical_recovery",
        bundle_bytes=10,
        net_reclaim_bytes=0,
        policy=_policy(),
        operation_id="bundle.exhausted-release-retry",
    )

    assert replayed.reservation != handle.reservation
    assert [event.event_kind for event in replayed_ledger.events()] == [
        "reserved",
        "released",
        "reserved",
    ]
    replayed.release()
    assert [event.event_kind for event in replayed_ledger.events()] == [
        "reserved",
        "released",
        "reserved",
        "released",
    ]


def test_replayed_reservation_rejects_parameter_drift(tmp_path: Path) -> None:
    governor = _governor(tmp_path)
    ledger = ResourceStorageBundleLedger(governor._store)
    ledger.reserve(
        bundle_class="critical_recovery",
        bundle_bytes=10,
        net_reclaim_bytes=0,
        policy=_policy(),
        operation_id="bundle.replay-drift",
    )

    with pytest.raises(SharedStateIntegrityError, match="diverged"):
        ledger.reserve(
            bundle_class="critical_recovery",
            bundle_bytes=11,
            net_reclaim_bytes=0,
            policy=_policy(),
            operation_id="bundle.replay-drift",
        )


def test_concurrent_reservation_for_same_operation_is_not_shared(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    ledger = ResourceStorageBundleLedger(governor._store)
    first = ledger.reserve(
        bundle_class="critical_recovery",
        bundle_bytes=10,
        net_reclaim_bytes=0,
        policy=_policy(),
        operation_id="bundle.concurrent-owner",
    )

    with pytest.raises(ResourceLockUnavailableError, match="lock"):
        ledger.reserve(
            bundle_class="critical_recovery",
            bundle_bytes=10,
            net_reclaim_bytes=0,
            policy=_policy(),
            operation_id="bundle.concurrent-owner",
        )

    first.release()


def test_body_integrity_error_is_not_masked_by_release_lock_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    governor = _governor(tmp_path)

    def unavailable_release(
        _ledger: ResourceStorageBundleLedger,
        _reservation: object,
        _owner_token: object,
    ) -> None:
        raise ResourceLockUnavailableError("release lock unavailable")

    monkeypatch.setattr(ResourceStorageBundleLedger, "release", unavailable_release)

    with pytest.raises(
        SharedStateIntegrityError, match="real integrity conflict"
    ), governor.storage_bundle(
        bundle_class="critical_recovery",
        bundle_bytes=10,
        net_reclaim_bytes=0,
        policy=_policy(),
        operation_id="bundle.body-error-priority",
    ):
        raise SharedStateIntegrityError("real integrity conflict")


def test_foreign_reservation_cannot_poison_local_release_intents(
    tmp_path: Path,
) -> None:
    local = _governor(tmp_path / "local", project_id="project.local")
    foreign = _governor(tmp_path / "foreign", project_id="project.foreign")
    local_ledger = ResourceStorageBundleLedger(local._store)
    foreign_handle = ResourceStorageBundleLedger(foreign._store).reserve(
        bundle_class="critical_recovery",
        bundle_bytes=10,
        net_reclaim_bytes=0,
        policy=_policy(),
        operation_id="bundle.foreign",
    )

    with pytest.raises(SharedStateIntegrityError, match="does not belong"):
        local_ledger.release(foreign_handle.reservation, "forged-owner")

    assert not list(local_ledger.release_intents_root.glob("*.json"))
    local_handle = local_ledger.reserve(
        bundle_class="critical_recovery",
        bundle_bytes=10,
        net_reclaim_bytes=0,
        policy=_policy(),
        operation_id="bundle.local-after-foreign",
    )
    local_handle.release()


def test_concurrent_release_of_same_reservation_is_idempotent(tmp_path: Path) -> None:
    governor = _governor(tmp_path)
    ledger = ResourceStorageBundleLedger(governor._store)
    handle = ledger.reserve(
        bundle_class="critical_recovery",
        bundle_bytes=10,
        net_reclaim_bytes=0,
        policy=_policy(),
        operation_id="bundle.concurrent-release",
    )

    with ThreadPoolExecutor(max_workers=8) as pool:
        tuple(pool.map(lambda _index: handle.release(), range(8)))

    assert [event.event_kind for event in ledger.events()] == ["reserved", "released"]
    assert len(list(ledger.release_intents_root.glob("*.json"))) == 1


def test_live_owner_cannot_be_released_by_external_ledger(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    ledger = ResourceStorageBundleLedger(governor._store)
    handle = ledger.reserve(
        bundle_class="critical_recovery",
        bundle_bytes=10,
        net_reclaim_bytes=0,
        policy=_policy(),
        operation_id="bundle.external-release",
    )
    with pytest.raises(SharedStateIntegrityError, match="owner fence"):
        ResourceStorageBundleLedger(governor._store).release(
            handle.reservation,
            "forged-owner",
        )

    handle.assert_active("critical_recovery")
    handle.release()


def test_storage_bundle_consumption_is_transaction_aggregate(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    handle = ResourceStorageBundleLedger(governor._store).reserve(
        bundle_class="critical_recovery",
        bundle_bytes=300,
        net_reclaim_bytes=0,
        policy=_policy(),
        operation_id="bundle.aggregate-consumption",
    )

    first = governor._store.shared_root / "test-artifacts" / "first.bin"
    with handle.authorize_artifact(first, b"x" * 175):
        first.parent.mkdir(parents=True, exist_ok=True)
        first.write_bytes(b"x" * 175)
    overflow = governor._store.shared_root / "test-artifacts" / "overflow.bin"
    with (
        pytest.raises(StorageBundleUnavailableError, match="exhausted"),
        handle.authorize_artifact(overflow, b"x" * 126),
    ):
        overflow.write_bytes(b"x" * 126)
    final = governor._store.shared_root / "test-artifacts" / "final.bin"
    with handle.authorize_artifact(final, b"x" * 125):
        final.write_bytes(b"x" * 125)
    handle.release()


def test_crashed_owner_is_fenced_and_same_operation_can_retry(
    tmp_path: Path,
) -> None:
    _crash_after_bundle_reserve(tmp_path, operation_id="bundle.crashed-owner")
    governor = _governor(tmp_path)
    ledger = ResourceStorageBundleLedger(governor._store)

    recovered = ledger.reserve(
        bundle_class="critical_recovery",
        bundle_bytes=300,
        net_reclaim_bytes=0,
        policy=_large_policy(),
        operation_id="bundle.crashed-owner",
    )

    assert [event.event_kind for event in ledger.events()] == [
        "reserved",
        "abandoned",
        "reserved",
    ]
    assert recovered.reservation.fencing_epoch == 2
    recovered.release()


def test_crash_retry_preserves_persistent_aggregate_consumption(
    tmp_path: Path,
) -> None:
    _crash_after_bundle_reserve(
        tmp_path,
        operation_id="bundle.crashed-consumption",
        consume_bytes=600,
        bundle_bytes=1_000,
    )
    persisted = tuple(tmp_path.rglob("crash-first-write.json"))
    assert len(persisted) == 1
    assert len(persisted[0].read_bytes()) == 600
    governor = _governor(tmp_path)
    recovered = ResourceStorageBundleLedger(governor._store).reserve(
        bundle_class="critical_recovery",
        bundle_bytes=1_000,
        net_reclaim_bytes=0,
        policy=_large_policy(),
        operation_id="bundle.crashed-consumption",
    )

    overflow = governor._store.shared_root / "test-artifacts" / "overflow.bin"
    with (
        pytest.raises(StorageBundleUnavailableError, match="exhausted"),
        recovered.authorize_artifact(overflow, b"x" * 401),
    ):
        overflow.write_bytes(b"x" * 401)
    remaining = governor._store.shared_root / "test-artifacts" / "remaining.bin"
    with recovered.authorize_artifact(remaining, b"x" * 400):
        remaining.parent.mkdir(parents=True, exist_ok=True)
        remaining.write_bytes(b"x" * 400)
    exhausted = governor._store.shared_root / "test-artifacts" / "exhausted.bin"
    with (
        pytest.raises(StorageBundleUnavailableError, match="exhausted"),
        recovered.authorize_artifact(exhausted, b"x"),
    ):
        exhausted.write_bytes(b"x")
    recovered.release()


def test_pending_artifact_authorization_is_reused_after_precreate_crash(
    tmp_path: Path,
) -> None:
    _crash_after_artifact_authorization(
        tmp_path,
        operation_id="bundle.precreate-crash",
        artifact_bytes=600,
        bundle_bytes=600,
    )
    governor = _governor(tmp_path)
    accounting = OfflineOptimizationAccounting(
        tmp_path,
        project_id="project.shared",
        policy=_large_policy(),
        lock_timeout_seconds=1,
    )
    target = accounting.root / "crash-fixture" / "precreate.json"
    assert not target.exists()
    handle = ResourceStorageBundleLedger(governor._store).reserve(
        bundle_class="critical_recovery",
        bundle_bytes=600,
        net_reclaim_bytes=0,
        policy=_large_policy(),
        operation_id="bundle.precreate-crash",
    )
    empty_payload_bytes = len(serialized_json_bytes({"value": ""}))
    payload = {"value": "x" * (600 - empty_payload_bytes)}

    assert accounting.persist_json_exclusive(
        target,
        payload,
        write_class="critical_recovery",
        resource_bundle=handle,
    )
    assert len(target.read_bytes()) == 600
    handle.release()


def test_pending_replacement_does_not_release_committed_footprint(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    handle = ResourceStorageBundleLedger(governor._store).reserve(
        bundle_class="critical_recovery",
        bundle_bytes=1_000,
        net_reclaim_bytes=0,
        policy=_large_policy(),
        operation_id="bundle.replacement-peak",
    )
    artifact_a = governor._store.shared_root / "test-artifacts" / "a.bin"
    artifact_a.parent.mkdir(parents=True, exist_ok=True)
    with handle.authorize_artifact(artifact_a, b"a" * 900):
        artifact_a.write_bytes(b"a" * 900)

    with (
        pytest.raises(RuntimeError, match="before replacement write"),
        handle.authorize_artifact(
            artifact_a,
            b"a" * 100,
            allow_replacement=True,
        ),
    ):
        raise RuntimeError("before replacement write")
    assert artifact_a.stat().st_size == 900

    artifact_b = governor._store.shared_root / "test-artifacts" / "b.bin"
    with (
        pytest.raises(StorageBundleUnavailableError, match="exhausted"),
        handle.authorize_artifact(artifact_b, b"b" * 900),
    ):
        artifact_b.write_bytes(b"b" * 900)
    assert not artifact_b.exists()

    with handle.authorize_artifact(
        artifact_a,
        b"a" * 100,
        allow_replacement=True,
    ):
        artifact_a.write_bytes(b"a" * 100)
    with handle.authorize_artifact(artifact_b, b"b" * 900):
        artifact_b.write_bytes(b"b" * 900)
    assert artifact_a.stat().st_size + artifact_b.stat().st_size == 1_000
    handle.release()


def test_landed_pending_artifact_is_confirmed_after_receipt_crash(
    tmp_path: Path,
) -> None:
    _crash_after_artifact_authorization(
        tmp_path,
        operation_id="bundle.postcreate-crash",
        artifact_bytes=600,
        bundle_bytes=1_000,
        crash_phase="postcreate",
    )
    governor = _governor(tmp_path)
    accounting = OfflineOptimizationAccounting(
        tmp_path,
        project_id="project.shared",
        policy=_large_policy(),
        lock_timeout_seconds=1,
    )
    first = accounting.root / "crash-fixture" / "precreate.json"
    assert first.is_file()
    assert len(first.read_bytes()) == 600
    handle = ResourceStorageBundleLedger(governor._store).reserve(
        bundle_class="critical_recovery",
        bundle_bytes=1_000,
        net_reclaim_bytes=0,
        policy=_large_policy(),
        operation_id="bundle.postcreate-crash",
    )
    empty_payload_bytes = len(serialized_json_bytes({"value": ""}))
    first_payload = {"value": "x" * (600 - empty_payload_bytes)}
    assert (
        accounting.persist_json_exclusive(
            first,
            first_payload,
            write_class="critical_recovery",
            resource_bundle=handle,
        )
        is False
    )
    second_payload = {"value": "y" * (400 - empty_payload_bytes)}
    assert accounting.persist_json_exclusive(
        accounting.root / "crash-fixture" / "remaining.json",
        second_payload,
        write_class="critical_recovery",
        resource_bundle=handle,
    )
    with pytest.raises(StorageBundleUnavailableError, match="exhausted"):
        accounting.persist_json_exclusive(
            accounting.root / "crash-fixture" / "overflow.json",
            {"value": "z"},
            write_class="critical_recovery",
            resource_bundle=handle,
        )
    handle.release()


def test_dead_owners_from_other_operations_do_not_exhaust_capacity(
    tmp_path: Path,
) -> None:
    _crash_after_bundle_reserve(
        tmp_path,
        operation_id="bundle.orphan-one",
        bundle_bytes=1_000,
    )
    _crash_after_bundle_reserve(
        tmp_path,
        operation_id="bundle.orphan-two",
        bundle_bytes=1_000,
    )
    governor = _governor(tmp_path)
    ledger = ResourceStorageBundleLedger(governor._store)

    healthy = ledger.reserve(
        bundle_class="critical_recovery",
        bundle_bytes=1_000,
        net_reclaim_bytes=0,
        policy=_large_policy(),
        operation_id="bundle.healthy-three",
    )

    assert [event.event_kind for event in ledger.events()] == [
        "reserved",
        "abandoned",
        "reserved",
        "abandoned",
        "reserved",
    ]
    healthy.release()


def test_live_owner_from_other_operation_is_not_reclaimed(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    ledger = ResourceStorageBundleLedger(governor._store)
    live = ledger.reserve(
        bundle_class="critical_recovery",
        bundle_bytes=1_200,
        net_reclaim_bytes=0,
        policy=_large_policy(),
        operation_id="bundle.live-owner",
    )

    with pytest.raises(StorageBundleUnavailableError, match="unavailable"):
        ledger.reserve(
            bundle_class="critical_recovery",
            bundle_bytes=1_000,
            net_reclaim_bytes=0,
            policy=_large_policy(),
            operation_id="bundle.must-not-steal-live-owner",
        )

    assert [event.event_kind for event in ledger.events()] == ["reserved"]
    live.release()


def test_closed_v1_event_chain_remains_readable_before_v2_append(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    ledger = ResourceStorageBundleLedger(governor._store)
    reserved = _legacy_reservation(operation_id="bundle.legacy-closed")
    released = LegacyStorageBundleEventV1(
        project_id="project.shared",
        sequence=2,
        event_kind="released",
        bundle_id=reserved.bundle_id,
        bundle_class=reserved.bundle_class,
        bundle_bytes=reserved.bundle_bytes,
        net_reclaim_bytes=reserved.net_reclaim_bytes,
        policy_digest=reserved.policy_digest,
        operation_id=f"{reserved.operation_id}.release",
        previous_event_digest=reserved.event_digest,
    )
    assert create_json_exclusive(
        ledger.root / "00000000000000000001.json",
        reserved.model_dump(mode="json"),
    )
    assert create_json_exclusive(
        ledger.root / "00000000000000000002.json",
        released.model_dump(mode="json"),
    )

    handle = ledger.reserve(
        bundle_class="critical_recovery",
        bundle_bytes=10,
        net_reclaim_bytes=0,
        policy=_policy(),
        operation_id="bundle.after-legacy-closed",
    )

    assert [event.schema_version for event in ledger.events()] == [
        "storage-bundle-event.v1",
        "storage-bundle-event.v1",
        "storage-bundle-event.v2",
    ]
    handle.release()


def test_active_v1_reservation_is_abandoned_before_v2_fencing(
    tmp_path: Path,
) -> None:
    governor = _governor(tmp_path)
    ledger = ResourceStorageBundleLedger(governor._store)
    legacy = _legacy_reservation(operation_id="bundle.legacy-active")
    assert create_json_exclusive(
        ledger.root / "00000000000000000001.json",
        legacy.model_dump(mode="json"),
    )

    handle = ledger.reserve(
        bundle_class="critical_recovery",
        bundle_bytes=10,
        net_reclaim_bytes=0,
        policy=_policy(),
        operation_id="bundle.after-legacy-active",
    )

    events = ledger.events()
    assert [event.event_kind for event in events] == [
        "reserved",
        "abandoned",
        "reserved",
    ]
    assert [event.schema_version for event in events] == [
        "storage-bundle-event.v1",
        "storage-bundle-event.v2",
        "storage-bundle-event.v2",
    ]
    handle.release()


def _crash_after_bundle_reserve(
    root: Path,
    *,
    operation_id: str,
    consume_bytes: int = 0,
    bundle_bytes: int = 300,
) -> None:
    script = """
import os
import sys
from pathlib import Path

from ai_sdlc.core.stage_review.optimization.storage_models import OptimizationStoragePolicy
from ai_sdlc.core.stage_review.optimization.accounting import OfflineOptimizationAccounting
from ai_sdlc.core.stage_review.artifacts import serialized_json_bytes
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.resource_storage_bundles import ResourceStorageBundleLedger
from ai_sdlc.core.stage_review.resources import ResourceGovernor

root = Path(sys.argv[1])
operation_id = sys.argv[2]
bundle_bytes = int(sys.argv[3])
consume_bytes = int(sys.argv[4])
governor = ResourceGovernor(
    root,
    project_id="project.shared",
    foreground_capacity=ResourceAmounts(),
    offline_optimization_capacity=ResourceAmounts(),
    lock_timeout_seconds=1,
)
policy = OptimizationStoragePolicy(
    maximum_total_bytes=10_000,
    minimum_free_bytes=0,
    minimum_free_ratio=0,
    critical_recovery_reserve_bytes=2_000,
    session_binding_reserve_bytes=100,
    maintenance_reclamation_reserve_bytes=200,
    safety_bundle_max_bytes=2_000,
)
handle = ResourceStorageBundleLedger(governor._store).reserve(
    bundle_class="critical_recovery",
    bundle_bytes=bundle_bytes,
    net_reclaim_bytes=0,
    policy=policy,
    operation_id=operation_id,
)
if consume_bytes:
    accounting = OfflineOptimizationAccounting(
        root,
        project_id="project.shared",
        policy=policy,
        lock_timeout_seconds=1,
    )
    empty_payload_bytes = len(serialized_json_bytes({"value": ""}))
    payload = {"value": "x" * (consume_bytes - empty_payload_bytes)}
    assert len(serialized_json_bytes(payload)) == consume_bytes
    created = accounting.persist_json_exclusive(
        accounting.root / "crash-fixture" / "crash-first-write.json",
        payload,
        write_class="critical_recovery",
        resource_bundle=handle,
    )
    assert created
os._exit(0)
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(root),
            operation_id,
            str(bundle_bytes),
            str(consume_bytes),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def _crash_after_artifact_authorization(
    root: Path,
    *,
    operation_id: str,
    artifact_bytes: int,
    bundle_bytes: int,
    crash_phase: str = "precreate",
) -> None:
    script = """
import os
import sys
from pathlib import Path

import ai_sdlc.core.stage_review.optimization.accounting as accounting_module
from ai_sdlc.core.stage_review.artifacts import serialized_json_bytes
from ai_sdlc.core.stage_review.optimization.accounting import OfflineOptimizationAccounting
from ai_sdlc.core.stage_review.optimization.storage_models import OptimizationStoragePolicy
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.resource_storage_bundles import ResourceStorageBundleLedger
from ai_sdlc.core.stage_review.resources import ResourceGovernor

root = Path(sys.argv[1])
operation_id = sys.argv[2]
artifact_bytes = int(sys.argv[3])
bundle_bytes = int(sys.argv[4])
crash_phase = sys.argv[5]
policy = OptimizationStoragePolicy(
    maximum_total_bytes=10_000,
    minimum_free_bytes=0,
    minimum_free_ratio=0,
    critical_recovery_reserve_bytes=2_000,
    session_binding_reserve_bytes=100,
    maintenance_reclamation_reserve_bytes=200,
    safety_bundle_max_bytes=2_000,
)
governor = ResourceGovernor(
    root,
    project_id="project.shared",
    foreground_capacity=ResourceAmounts(),
    offline_optimization_capacity=ResourceAmounts(),
    lock_timeout_seconds=1,
)
handle = ResourceStorageBundleLedger(governor._store).reserve(
    bundle_class="critical_recovery",
    bundle_bytes=bundle_bytes,
    net_reclaim_bytes=0,
    policy=policy,
    operation_id=operation_id,
)
accounting = OfflineOptimizationAccounting(
    root,
    project_id="project.shared",
    policy=policy,
    lock_timeout_seconds=1,
)
empty_payload_bytes = len(serialized_json_bytes({"value": ""}))
payload = {"value": "x" * (artifact_bytes - empty_payload_bytes)}

def crash_before_create(*_args, **_kwargs):
    os._exit(0)

if crash_phase == "precreate":
    accounting_module.create_json_exclusive = crash_before_create
elif crash_phase == "postcreate":
    ResourceStorageBundleLedger._commit_artifact_locked = crash_before_create
else:
    raise AssertionError("unknown crash phase")
accounting.persist_json_exclusive(
    accounting.root / "crash-fixture" / "precreate.json",
    payload,
    write_class="critical_recovery",
    resource_bundle=handle,
)
raise AssertionError("create crash injection did not run")
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(root),
            operation_id,
            str(artifact_bytes),
            str(bundle_bytes),
            crash_phase,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def _legacy_reservation(*, operation_id: str) -> LegacyStorageBundleEventV1:
    return LegacyStorageBundleEventV1(
        project_id="project.shared",
        sequence=1,
        event_kind="reserved",
        bundle_id=f"storage-bundle.{operation_id}.1",
        bundle_class="critical_recovery",
        bundle_bytes=10,
        net_reclaim_bytes=0,
        policy_digest="sha256:legacy-policy",
        operation_id=f"{operation_id}.reserve.1",
    )


def _governor(
    root: Path,
    *,
    project_id: str = "project.shared",
) -> ResourceGovernor:
    return ResourceGovernor(
        root,
        project_id=project_id,
        foreground_capacity=ResourceAmounts(),
        offline_optimization_capacity=ResourceAmounts(),
        lock_timeout_seconds=1,
    )


def _policy() -> OptimizationStoragePolicy:
    return OptimizationStoragePolicy(
        maximum_total_bytes=10_000,
        minimum_free_bytes=0,
        minimum_free_ratio=0,
        critical_recovery_reserve_bytes=200,
        session_binding_reserve_bytes=100,
        maintenance_reclamation_reserve_bytes=200,
        safety_bundle_max_bytes=300,
    )


def _large_policy() -> OptimizationStoragePolicy:
    return OptimizationStoragePolicy(
        maximum_total_bytes=10_000,
        minimum_free_bytes=0,
        minimum_free_ratio=0,
        critical_recovery_reserve_bytes=2_000,
        session_binding_reserve_bytes=100,
        maintenance_reclamation_reserve_bytes=200,
        safety_bundle_max_bytes=2_000,
    )
