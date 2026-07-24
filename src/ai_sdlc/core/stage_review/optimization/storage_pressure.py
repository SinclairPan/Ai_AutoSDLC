"""OptimizationStorage 的 Reserve 类别与事务包边界。"""

from __future__ import annotations

import shutil
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.storage_models import (
    OptimizationStoragePolicy,
)
from ai_sdlc.core.stage_review.resource_storage_bundles import StorageBundleHandle

WriteClass = str


def _default_disk_probe(root: Path) -> tuple[int, int]:
    usage = shutil.disk_usage(root.parent if root.parent.exists() else Path.cwd())
    return usage.free, usage.total


def _storage_write_limit(
    policy: OptimizationStoragePolicy,
    write_class: WriteClass,
) -> int:
    steady = policy.maximum_total_bytes - (
        policy.critical_recovery_reserve_bytes
        + policy.session_binding_reserve_bytes
        + policy.maintenance_reclamation_reserve_bytes
    )
    return {
        "normal": steady,
        "critical_recovery": policy.critical_recovery_reserve_bytes
        + policy.session_binding_reserve_bytes,
        "session_binding": policy.session_binding_reserve_bytes,
        "reclamation": policy.maintenance_reclamation_reserve_bytes,
    }[write_class]


def _storage_usage_limit(
    policy: OptimizationStoragePolicy,
    write_class: WriteClass,
) -> int:
    steady = _storage_write_limit(policy, "normal")
    return {
        "normal": steady,
        "session_binding": steady + policy.session_binding_reserve_bytes,
        "critical_recovery": steady
        + policy.session_binding_reserve_bytes
        + policy.critical_recovery_reserve_bytes,
        "reclamation": policy.maximum_total_bytes,
    }[write_class]


def _require_storage_bundle(
    write_class: WriteClass,
    resource_bundle: StorageBundleHandle | None,
) -> None:
    if write_class == "critical_recovery":
        if resource_bundle is None:
            raise SharedStateIntegrityError("safety write requires resource bundle")
        resource_bundle.assert_active("critical_recovery")
    elif write_class == "session_binding":
        if resource_bundle is None:
            raise SharedStateIntegrityError("safety write requires resource bundle")
        resource_bundle.assert_active("session_binding")
    elif resource_bundle is not None:
        raise SharedStateIntegrityError("ordinary write cannot consume safety reserve")
