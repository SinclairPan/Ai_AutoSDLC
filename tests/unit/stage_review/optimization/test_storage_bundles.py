from __future__ import annotations

from pathlib import Path

import pytest

from ai_sdlc.core.stage_review.optimization.storage_models import (
    OptimizationStoragePolicy,
)
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.resource_storage_bundles import (
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


def _governor(root: Path) -> ResourceGovernor:
    return ResourceGovernor(
        root,
        project_id="project.shared",
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
