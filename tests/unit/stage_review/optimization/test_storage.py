from __future__ import annotations

from pathlib import Path

import pytest

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
)
from ai_sdlc.core.stage_review.optimization.commit_fencing import (
    OptimizationCommitLeaseStore,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    OptimizationObservationStore,
)
from ai_sdlc.core.stage_review.optimization.storage import (
    OptimizationStorage,
    SegmentIndexLookupIncompleteError,
    StoragePressureError,
)
from ai_sdlc.core.stage_review.optimization.storage_compaction import (
    PreparedCompaction,
    _tree_bytes,
)
from ai_sdlc.core.stage_review.optimization.storage_models import (
    OptimizationStoragePolicy,
)
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.resource_storage_bundles import (
    StorageBundleHandle,
)
from ai_sdlc.core.stage_review.resources import ResourceGovernor


def test_policy_defaults_freeze_required_storage_bounds() -> None:
    policy = OptimizationStoragePolicy()

    assert policy.maximum_total_bytes == 1024**3
    assert policy.minimum_free_bytes == 2 * 1024**3
    assert policy.minimum_free_ratio == 0.1
    assert policy.critical_recovery_reserve_bytes == 48 * 1024**2
    assert policy.session_binding_reserve_bytes == 16 * 1024**2
    assert policy.maintenance_reclamation_reserve_bytes == 128 * 1024**2
    assert policy.maximum_index_scan_items == 10_000
    assert policy.maximum_index_scan_seconds == 0.5
    assert policy.maximum_segment_records == 10_000
    assert policy.maximum_segment_bytes == 64 * 1024**2


def test_custom_storage_policy_binding_is_independent_of_store_order(
    tmp_path: Path,
) -> None:
    first = OptimizationObservationStore(tmp_path, project_id="project.shared")
    custom = OptimizationStoragePolicy(
        maximum_total_bytes=20_000,
        minimum_free_bytes=0,
        minimum_free_ratio=0,
        critical_recovery_reserve_bytes=3_000,
        session_binding_reserve_bytes=2_000,
        maintenance_reclamation_reserve_bytes=4_000,
        safety_bundle_max_bytes=2_000,
    )
    leases = OptimizationCommitLeaseStore(
        tmp_path,
        project_id="project.shared",
        lock_timeout_seconds=1,
    )
    storage = OptimizationStorage(
        tmp_path,
        project_id="project.shared",
        policy=custom,
        commit_leases=leases,
        disk_probe=lambda: (100_000, 100_000),
    )
    later = OptimizationObservationStore(tmp_path, project_id="project.shared")

    assert storage.policy == custom
    assert first.accounting._current_policy() == custom
    assert later.accounting._current_policy() == custom


def test_tree_bytes_tolerates_atomic_temporary_file_disappearing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transient = tmp_path / ".transient.tmp"
    committed = tmp_path / "committed.json"
    transient.write_bytes(b"committed")
    path_stat = Path.stat
    replaced = False

    def replace_during_stat(path: Path, *args: object, **kwargs: object) -> object:
        nonlocal replaced
        if path == transient and not replaced:
            replaced = True
            transient.replace(committed)
            raise FileNotFoundError(path)
        return path_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", replace_during_stat)

    assert _tree_bytes(tmp_path) == len(b"committed")


def test_tree_bytes_fails_closed_when_scan_never_stabilizes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unstable = tmp_path / "unstable.json"
    unstable.write_bytes(b"unstable")
    path_stat = Path.stat

    def missing_during_stat(path: Path, *args: object, **kwargs: object) -> object:
        if path == unstable:
            raise FileNotFoundError(path)
        return path_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", missing_during_stat)

    with pytest.raises(ResourceLockUnavailableError, match="accounting is unstable"):
        _tree_bytes(tmp_path)


def test_compaction_seals_segment_index_checkpoint_and_manifest(
    tmp_path: Path,
) -> None:
    storage, leases, governor = _storage(tmp_path)
    with leases.acquire(
        owner_id="writer.one", scope="append", expected_head="head.0"
    ) as lease:
        first = storage.append(
            "query-commitments",
            _large_payload(1),
            keys={"idempotency_key": "key.one", "generation": "generation.one"},
            lease=lease,
        )
        second = storage.append(
            "query-commitments",
            _large_payload(2),
            keys={"idempotency_key": "key.two", "generation": "generation.two"},
            lease=lease,
        )
    prepared = _prepared(storage, "query-commitments")
    assert prepared.required_bundle_bytes - prepared.bundle.temporary_bytes > 2048
    assert prepared.net_reclaim_bytes == prepared.bundle.loose_bytes
    with _compaction_bundle(
        governor, storage, prepared, "bundle.compact-one"
    ) as bundle, _prepared_lease(storage, prepared, bundle) as lease:
        manifest = storage._commit_compaction(
            prepared, lease=lease, resource_bundle=bundle
        )

    assert manifest.revision == 1
    assert manifest.checkpoint_digest
    committed_bytes = sum(
        path.stat().st_size
        for root in (
            storage.segment_root,
            storage.index_root,
            storage.checkpoint_root,
        )
        for path in root.rglob("*")
        if path.is_file()
    ) + storage.manifest_path.stat().st_size
    assert committed_bytes <= prepared.required_bundle_bytes
    assert list(storage.segment_root.rglob("*.jsonl.gz"))
    assert list(storage.index_root.rglob("*.index.json"))
    assert not list(storage.loose_root.rglob("*.json"))
    assert storage.read_stream("query-commitments") == (first, second)
    assert storage.lookup(
        "query-commitments", key_kind="idempotency_key", key="key.two"
    ) == second
    assert storage.lookup(
        "query-commitments", key_kind="generation", key="generation.one"
    ) == first
    assert storage.lookup(
        "query-commitments", key_kind="idempotency_key", key="missing"
    ) is None


def test_compaction_accounts_for_growing_manifest_and_checkpoint(
    tmp_path: Path,
) -> None:
    storage, leases, governor = _storage(tmp_path)
    with leases.acquire(
        owner_id="writer.first", scope="append", expected_head="head.0"
    ) as lease:
        for index in range(2):
            storage.append(
                "query-commitments",
                _large_payload(index),
                keys={"idempotency_key": f"first.{index}"},
                lease=lease,
            )
    first = _prepared(storage, "query-commitments")
    with _compaction_bundle(
        governor, storage, first, "bundle.compact-first"
    ) as bundle, _prepared_lease(storage, first, bundle) as lease:
        storage._commit_compaction(first, lease=lease, resource_bundle=bundle)

    previous_manifest_bytes = storage.manifest_path.stat().st_size
    with leases.acquire(
        owner_id="writer.second",
        scope="append",
        expected_head=storage.manifest().manifest_digest,
    ) as lease:
        for index in range(2, 4):
            storage.append(
                "query-commitments",
                _large_payload(index),
                keys={"idempotency_key": f"second.{index}"},
                lease=lease,
            )
    second = _prepared(storage, "query-commitments")

    assert (
        second.required_bundle_bytes - second.bundle.temporary_bytes
        > first.required_bundle_bytes - first.bundle.temporary_bytes
    )
    assert (
        second.net_reclaim_bytes
        == second.bundle.loose_bytes + previous_manifest_bytes
    )
    with _compaction_bundle(
        governor, storage, second, "bundle.compact-second"
    ) as bundle, _prepared_lease(storage, second, bundle) as lease:
        storage._commit_compaction(second, lease=lease, resource_bundle=bundle)

    checkpoint = storage.checkpoint_root / f"{storage.manifest().revision:020d}.json"
    committed_bytes = (
        len(second.bundle.segment)
        + (
            storage.root / second.bundle.index_relative_path
        ).stat().st_size
        + checkpoint.stat().st_size
        + storage.manifest_path.stat().st_size
    )
    assert committed_bytes == second.required_bundle_bytes


def test_incomplete_index_scan_fails_closed_instead_of_reporting_absent(
    tmp_path: Path,
) -> None:
    storage, leases, governor = _storage(tmp_path, maximum_index_scan_items=1)
    with leases.acquire(
        owner_id="writer.one", scope="append", expected_head="head.0"
    ) as lease:
        for index in range(2):
            storage.append(
                "query-commitments",
                _large_payload(index),
                keys={"idempotency_key": f"key.{index}"},
                lease=lease,
            )
    prepared = _prepared(storage, "query-commitments")
    with _compaction_bundle(
        governor, storage, prepared, "bundle.compact-index"
    ) as bundle, _prepared_lease(storage, prepared, bundle) as lease:
        storage._commit_compaction(
            prepared, lease=lease, resource_bundle=bundle
        )

    with pytest.raises(SegmentIndexLookupIncompleteError):
        storage.lookup(
            "query-commitments", key_kind="idempotency_key", key="missing"
        )


def test_crash_before_manifest_is_ignored_and_after_manifest_deduplicates_loose(
    tmp_path: Path,
) -> None:
    storage, leases, governor = _storage(tmp_path)
    with leases.acquire(
        owner_id="writer.one", scope="append", expected_head="head.0"
    ) as lease:
        records = tuple(
            storage.append(
                "snapshot-control",
                _large_payload(index),
                keys={"operation_id": f"operation.{index}"},
                lease=lease,
            )
            for index in range(2)
        )
    prepared = _prepared(storage, "snapshot-control")
    with _compaction_bundle(
        governor, storage, prepared, "bundle.crash-index"
    ) as bundle, _prepared_lease(
        storage, prepared, bundle
    ) as lease, pytest.raises(RuntimeError, match="after index"):
        storage._commit_compaction(
            prepared,
            lease=lease,
            resource_bundle=bundle,
            crash_point="after_index",
        )
    assert storage.manifest().revision == 0
    assert storage.read_stream("snapshot-control") == records

    prepared = _prepared(storage, "snapshot-control")
    with _compaction_bundle(
        governor, storage, prepared, "bundle.crash-manifest"
    ) as bundle, _prepared_lease(
        storage, prepared, bundle
    ) as lease, pytest.raises(RuntimeError, match="after manifest"):
        storage._commit_compaction(
            prepared,
            lease=lease,
            resource_bundle=bundle,
            crash_point="after_manifest",
        )
    assert storage.manifest().revision == 1
    assert storage.read_stream("snapshot-control") == records
    assert list(storage.loose_root.rglob("*.json"))

    storage.cleanup_committed_loose("snapshot-control")
    assert storage.read_stream("snapshot-control") == records
    assert not list(storage.loose_root.rglob("*.json"))


def test_crash_after_checkpoint_retries_with_current_fencing_claim(
    tmp_path: Path,
) -> None:
    storage, leases, governor = _storage(tmp_path)
    with leases.acquire(
        owner_id="writer.one", scope="append", expected_head="head.0"
    ) as lease:
        for index in range(2):
            storage.append(
                "snapshot-control",
                _large_payload(index),
                keys={"operation_id": f"operation.{index}"},
                lease=lease,
            )
    prepared = _prepared(storage, "snapshot-control")
    with _compaction_bundle(
        governor, storage, prepared, prepared.operation_id
    ) as bundle, _prepared_lease(
        storage, prepared, bundle
    ) as lease, pytest.raises(RuntimeError, match="after checkpoint"):
        storage._commit_compaction(
            prepared,
            lease=lease,
            resource_bundle=bundle,
            crash_point="after_checkpoint",
        )
    orphan = next(storage.checkpoint_root.glob("*.json")).read_bytes()
    retry = _prepared(storage, "snapshot-control")
    assert retry.operation_id == prepared.operation_id
    assert (
        retry.net_reclaim_bytes
        == retry.bundle.loose_bytes + len(orphan)
    )

    with _compaction_bundle(
        governor, storage, retry, retry.operation_id
    ) as bundle, _prepared_lease(storage, retry, bundle) as lease:
        manifest = storage._commit_compaction(
            retry,
            lease=lease,
            resource_bundle=bundle,
        )

    assert manifest.revision == 1
    assert next(storage.checkpoint_root.glob("*.json")).read_bytes() != orphan
    assert storage.manifest() == manifest


def test_after_index_retry_counts_only_pending_artifacts_at_capacity_limit(
    tmp_path: Path,
) -> None:
    storage, leases, governor = _storage(tmp_path)
    with leases.acquire(
        owner_id="writer.index-retry",
        scope="append",
        expected_head="head.0",
    ) as lease:
        for index in range(2):
            storage.append(
                "snapshot-control",
                _large_payload(index),
                keys={"operation_id": f"operation.index-retry.{index}"},
                lease=lease,
            )
    first = _prepared(storage, "snapshot-control")
    with _compaction_bundle(
        governor, storage, first, first.operation_id
    ) as bundle, _prepared_lease(
        storage, first, bundle
    ) as lease, pytest.raises(RuntimeError, match="after index"):
        storage._commit_compaction(
            first,
            lease=lease,
            resource_bundle=bundle,
            crash_point="after_index",
        )

    retry = _prepared(storage, "snapshot-control")
    assert retry.operation_id == first.operation_id
    assert retry.required_bundle_bytes < first.required_bundle_bytes
    current = _tree_bytes(storage.accounting_root)
    filler = storage.accounting_root / "capacity-padding.bin"
    filler.write_bytes(
        b"x"
        * (
            storage.policy.maximum_total_bytes
            - current
            - retry.required_bundle_bytes
        )
    )
    before_retry = _tree_bytes(storage.accounting_root)

    with _compaction_bundle(
        governor, storage, retry, retry.operation_id
    ) as bundle, _prepared_lease(storage, retry, bundle) as lease:
        manifest = storage._commit_compaction(
            retry,
            lease=lease,
            resource_bundle=bundle,
        )

    assert manifest.revision == 1
    assert not tuple(storage.loose_root.rglob("*.json"))
    assert _tree_bytes(storage.accounting_root) < before_retry


def test_incomplete_reclamation_bundle_writes_no_compaction_artifact(
    tmp_path: Path,
) -> None:
    storage, leases, governor = _storage(tmp_path)
    with leases.acquire(
        owner_id="writer.one", scope="append", expected_head="head.0"
    ) as lease:
        for index in range(2):
            storage.append(
                "query-commitments",
                _large_payload(index),
                keys={"idempotency_key": f"key.{index}"},
                lease=lease,
            )
    prepared = _prepared(storage, "query-commitments")
    with governor.storage_bundle(
        bundle_class="reclamation",
        bundle_bytes=prepared.required_bundle_bytes - 1,
        net_reclaim_bytes=prepared.net_reclaim_bytes,
        policy=storage.policy,
        operation_id="bundle.under-reserved",
    ) as bundle, pytest.raises(
        StoragePressureError, match="incomplete"
    ), _prepared_lease(storage, prepared, bundle) as lease:
        storage._commit_compaction(prepared, lease=lease, resource_bundle=bundle)

    assert storage.manifest().revision == 0
    assert not list(storage.segment_root.rglob("*"))
    assert not list(storage.index_root.rglob("*"))
    assert not list(storage.checkpoint_root.rglob("*"))


def test_persistently_released_bundle_blocks_compaction_before_data_write(
    tmp_path: Path,
) -> None:
    storage, leases, governor = _storage(tmp_path)
    with leases.acquire(
        owner_id="writer.bundle-revocation",
        scope="append",
        expected_head="head.0",
    ) as lease:
        for index in range(2):
            storage.append(
                "query-commitments",
                _large_payload(index),
                keys={"idempotency_key": f"revocation.{index}"},
                lease=lease,
            )
    prepared = _prepared(storage, "query-commitments")

    with _compaction_bundle(
        governor, storage, prepared, "bundle.revoked-before-commit"
    ) as bundle, _prepared_lease(storage, prepared, bundle) as lease:
        bundle.release()
        with pytest.raises(SharedStateIntegrityError, match="not active"):
            storage._commit_compaction(
                prepared,
                lease=lease,
                resource_bundle=bundle,
            )

    assert storage.manifest().revision == 0
    assert not list(storage.segment_root.rglob("*"))
    assert not list(storage.index_root.rglob("*"))
    assert not list(storage.checkpoint_root.rglob("*"))


def test_safety_append_without_governor_bundle_is_rejected_before_write(
    tmp_path: Path,
) -> None:
    storage, leases, _ = _storage(tmp_path)

    with leases.acquire(
        owner_id="writer.unsafe", scope="append", expected_head="head.0"
    ) as lease, pytest.raises(SharedStateIntegrityError, match="resource bundle"):
        storage.append(
            "snapshot-control",
            {"value": "unsafe"},
            keys={"operation_id": "operation.unsafe"},
            lease=lease,
            write_class="critical_recovery",
        )

    assert storage.read_stream("snapshot-control") == ()


def test_commit_lease_control_plane_is_outside_offline_data_quota(
    tmp_path: Path,
) -> None:
    storage, leases, _governor = _storage(
        tmp_path,
        maximum_total_bytes=5000,
        critical_recovery_reserve_bytes=200,
        session_binding_reserve_bytes=200,
        maintenance_reclamation_reserve_bytes=500,
    )
    normal_limit = 4100
    current = _tree_bytes(storage.accounting_root)
    storage.accounting_root.mkdir(parents=True, exist_ok=True)
    filler = storage.accounting_root / "pressure.bin"
    filler.write_bytes(b"x" * (normal_limit - current - 1))
    before = _tree_bytes(storage.accounting_root)

    with leases.acquire(
        owner_id="writer.control-plane",
        scope="append",
        expected_head="sha256:head",
    ):
        pass

    assert _tree_bytes(storage.accounting_root) == before
    assert tuple(leases.claim_root.glob("*.json"))
    assert leases.root.is_relative_to(storage.accounting_root) is False


def test_pressure_reserves_cannot_be_borrowed_by_normal_or_session_writes(
    tmp_path: Path,
) -> None:
    storage, _leases, _ = _storage(
        tmp_path,
        maximum_total_bytes=1000,
        critical_recovery_reserve_bytes=200,
        session_binding_reserve_bytes=100,
        maintenance_reclamation_reserve_bytes=200,
        disk_probe=lambda: (10_000, 10_000),
    )
    with pytest.raises(StoragePressureError):
        storage.reserve_bundle(
            write_class="normal", bundle_bytes=501, net_reclaim_bytes=0
        )
    storage.reserve_bundle(
        write_class="session_binding", bundle_bytes=100, net_reclaim_bytes=0
    )
    with pytest.raises(StoragePressureError):
        storage.reserve_bundle(
            write_class="session_binding", bundle_bytes=101, net_reclaim_bytes=0
        )
    storage.reserve_bundle(
        write_class="critical_recovery", bundle_bytes=300, net_reclaim_bytes=0
    )
    with pytest.raises(StoragePressureError):
        storage.reserve_bundle(
            write_class="reclamation", bundle_bytes=200, net_reclaim_bytes=199
        )
    storage.reserve_bundle(
        write_class="reclamation", bundle_bytes=200, net_reclaim_bytes=201
    )


def test_storage_limit_counts_every_offline_optimization_artifact(
    tmp_path: Path,
) -> None:
    storage, _leases, _governor = _storage(
        tmp_path,
        maximum_total_bytes=2000,
        critical_recovery_reserve_bytes=200,
        session_binding_reserve_bytes=100,
        maintenance_reclamation_reserve_bytes=200,
        disk_probe=lambda: (10_000, 10_000),
    )
    external = storage.accounting_root / "datasets" / "large.json"
    external.parent.mkdir(parents=True, exist_ok=True)
    external.write_bytes(b"x" * 1950)

    with pytest.raises(StoragePressureError, match="dedicated reserve"):
        storage.reserve_bundle(
            write_class="normal",
            bundle_bytes=1,
            net_reclaim_bytes=0,
        )


def test_reserved_critical_recovery_can_cross_common_free_space_floor(
    tmp_path: Path,
) -> None:
    storage, _leases, governor = _storage(
        tmp_path,
        minimum_free_bytes=900,
        minimum_free_ratio=0,
        critical_recovery_reserve_bytes=3000,
        session_binding_reserve_bytes=3000,
        safety_bundle_max_bytes=2000,
        disk_probe=lambda: (2000, 2000),
    )
    with governor.storage_bundle(
        bundle_class="critical_recovery",
        bundle_bytes=2000,
        net_reclaim_bytes=0,
        policy=storage.policy,
        operation_id="bundle.critical-floor",
    ) as bundle, storage.acquire_lease(
        owner_id="writer.critical-floor",
        scope="snapshot_control",
        expected_head="sha256:head",
        write_class="critical_recovery",
        resource_bundle=bundle,
    ) as lease:
        record = storage.append(
            "snapshot-control",
            {"value": "recover"},
            keys={"operation_id": "critical-floor"},
            lease=lease,
            write_class="critical_recovery",
            resource_bundle=bundle,
        )

    assert record.sequence == 1


def test_reserved_session_binding_can_cross_common_free_space_floor(
    tmp_path: Path,
) -> None:
    storage, _leases, governor = _storage(
        tmp_path,
        minimum_free_bytes=900,
        minimum_free_ratio=0,
        critical_recovery_reserve_bytes=3000,
        session_binding_reserve_bytes=3000,
        safety_bundle_max_bytes=2000,
        disk_probe=lambda: (2000, 2000),
    )
    with governor.storage_bundle(
        bundle_class="session_binding",
        bundle_bytes=2000,
        net_reclaim_bytes=0,
        policy=storage.policy,
        operation_id="bundle.session-floor",
    ) as bundle, storage.acquire_lease(
        owner_id="writer.session-floor",
        scope="snapshot_control",
        expected_head="sha256:head",
        write_class="session_binding",
        resource_bundle=bundle,
    ) as lease:
        record = storage.append(
            "snapshot-control",
            {"value": "bind"},
            keys={"operation_id": "session-floor"},
            lease=lease,
            write_class="session_binding",
            resource_bundle=bundle,
        )

    assert record.sequence == 1


def _storage(
    root: Path,
    **overrides: object,
) -> tuple[
    OptimizationStorage,
    OptimizationCommitLeaseStore,
    ResourceGovernor,
]:
    disk_probe = overrides.pop("disk_probe", None)
    values: dict[str, object] = {
        "maximum_total_bytes": 10_000_000,
        "minimum_free_bytes": 0,
        "minimum_free_ratio": 0,
        "critical_recovery_reserve_bytes": 1000,
        "session_binding_reserve_bytes": 1000,
        "maintenance_reclamation_reserve_bytes": 100_000,
        "safety_bundle_max_bytes": 100,
        "maximum_segment_records": 2,
        "maximum_segment_bytes": 100_000,
        "maximum_index_scan_items": 100,
        "maximum_index_scan_seconds": 5,
    }
    values.update(overrides)
    policy = OptimizationStoragePolicy.model_validate(values)
    leases = OptimizationCommitLeaseStore(
        root, project_id="project.shared", lock_timeout_seconds=1
    )
    storage = OptimizationStorage(
        root,
        project_id="project.shared",
        policy=policy,
        commit_leases=leases,
        disk_probe=disk_probe if callable(disk_probe) else None,
    )
    governor = ResourceGovernor(
        root,
        project_id="project.shared",
        foreground_capacity=ResourceAmounts(),
        offline_optimization_capacity=ResourceAmounts(),
        lock_timeout_seconds=1,
    )
    return storage, leases, governor


def _compaction_bundle(
    governor: ResourceGovernor,
    storage: OptimizationStorage,
    prepared: PreparedCompaction,
    operation_id: str,
) -> object:
    return governor.storage_bundle(
        bundle_class="reclamation",
        bundle_bytes=prepared.required_bundle_bytes,
        net_reclaim_bytes=prepared.net_reclaim_bytes,
        policy=storage.policy,
        operation_id=operation_id,
    )


def _prepared_lease(
    storage: OptimizationStorage,
    prepared: PreparedCompaction,
    resource_bundle: StorageBundleHandle,
) -> object:
    plan = prepared.lease_plan
    return storage.acquire_planned_lease(
        plan,
        write_class="reclamation",
        bundle_bytes=prepared.required_bundle_bytes,
        net_reclaim_bytes=prepared.net_reclaim_bytes,
        resource_bundle=resource_bundle,
    )


def _prepared(
    storage: OptimizationStorage,
    stream_kind: str,
) -> PreparedCompaction:
    prepared = storage._prepare_compaction(stream_kind)
    assert prepared is not None
    return prepared


def _large_payload(value: int) -> dict[str, object]:
    return {"value": value, "padding": str(value) * 8192}
