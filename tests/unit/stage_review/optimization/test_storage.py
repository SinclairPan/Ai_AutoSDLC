from __future__ import annotations

from pathlib import Path

import pytest

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.commit_fencing import (
    OptimizationCommitLeaseStore,
)
from ai_sdlc.core.stage_review.optimization.storage import (
    OptimizationStorage,
    SegmentIndexLookupIncompleteError,
    StoragePressureError,
)
from ai_sdlc.core.stage_review.optimization.storage_compaction import (
    PreparedCompaction,
)
from ai_sdlc.core.stage_review.optimization.storage_models import (
    OptimizationStoragePolicy,
)
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
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


def test_compaction_seals_segment_index_checkpoint_and_manifest(
    tmp_path: Path,
) -> None:
    storage, leases, governor = _storage(tmp_path)
    with leases.acquire(
        owner_id="writer.one", scope="append", expected_head="head.0"
    ) as lease:
        first = storage.append(
            "query-commitments",
            {"value": 1},
            keys={"idempotency_key": "key.one", "generation": "generation.one"},
            lease=lease,
        )
        second = storage.append(
            "query-commitments",
            {"value": 2},
            keys={"idempotency_key": "key.two", "generation": "generation.two"},
            lease=lease,
        )
    prepared = _prepared(storage, "query-commitments")
    with _compaction_bundle(
        governor, storage, prepared, "bundle.compact-one"
    ) as bundle, leases.acquire(
        owner_id="compactor.one", scope="compaction", expected_head=second.record_digest
    ) as lease:
        manifest = storage._commit_compaction(
            prepared, lease=lease, resource_bundle=bundle
        )

    assert manifest.revision == 1
    assert manifest.checkpoint_digest
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
                {"value": index},
                keys={"idempotency_key": f"key.{index}"},
                lease=lease,
            )
    prepared = _prepared(storage, "query-commitments")
    with _compaction_bundle(
        governor, storage, prepared, "bundle.compact-index"
    ) as bundle, leases.acquire(
        owner_id="compactor.one", scope="compaction", expected_head="head.2"
    ) as lease:
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
                {"value": index},
                keys={"operation_id": f"operation.{index}"},
                lease=lease,
            )
            for index in range(2)
        )
    prepared = _prepared(storage, "snapshot-control")
    with _compaction_bundle(
        governor, storage, prepared, "bundle.crash-index"
    ) as bundle, leases.acquire(
        owner_id="compactor.one", scope="compaction", expected_head="head.2"
    ) as lease, pytest.raises(RuntimeError, match="after index"):
        storage._commit_compaction(
            prepared,
            lease=lease,
            resource_bundle=bundle,
            crash_point="after_index",
        )
    assert storage.manifest().revision == 0
    assert storage.read_stream("snapshot-control") == records

    with _compaction_bundle(
        governor, storage, prepared, "bundle.crash-manifest"
    ) as bundle, leases.acquire(
        owner_id="compactor.two", scope="compaction", expected_head="head.2"
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
                {"value": index},
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
    ) as bundle, leases.acquire(
        owner_id="compactor.under-reserved",
        scope="compaction",
        expected_head="head.2",
    ) as lease, pytest.raises(StoragePressureError, match="incomplete"):
        storage._commit_compaction(prepared, lease=lease, resource_bundle=bundle)

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
    storage, leases, governor = _storage(
        tmp_path,
        minimum_free_bytes=900,
        minimum_free_ratio=0,
        safety_bundle_max_bytes=1000,
        disk_probe=lambda: (500, 1000),
    )
    with governor.storage_bundle(
        bundle_class="critical_recovery",
        bundle_bytes=1000,
        net_reclaim_bytes=0,
        policy=storage.policy,
        operation_id="bundle.critical-floor",
    ) as bundle, leases.acquire(
        owner_id="writer.critical-floor",
        scope="snapshot_control",
        expected_head="sha256:head",
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
    storage, leases, governor = _storage(
        tmp_path,
        minimum_free_bytes=900,
        minimum_free_ratio=0,
        safety_bundle_max_bytes=1000,
        disk_probe=lambda: (500, 1000),
    )
    with governor.storage_bundle(
        bundle_class="session_binding",
        bundle_bytes=1000,
        net_reclaim_bytes=0,
        policy=storage.policy,
        operation_id="bundle.session-floor",
    ) as bundle, leases.acquire(
        owner_id="writer.session-floor",
        scope="snapshot_control",
        expected_head="sha256:head",
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


def _prepared(
    storage: OptimizationStorage,
    stream_kind: str,
) -> PreparedCompaction:
    prepared = storage._prepare_compaction(stream_kind)
    assert prepared is not None
    return prepared
