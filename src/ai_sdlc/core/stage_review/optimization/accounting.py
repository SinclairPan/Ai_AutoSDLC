"""离线优化工件的统一容量准入与跨进程写锁。"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import cast

from ai_sdlc.core.stage_review.artifact_compat import JsonValue
from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    ShortFileLock,
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
    serialized_json_bytes,
)
from ai_sdlc.core.stage_review.optimization.storage_compaction import _tree_bytes
from ai_sdlc.core.stage_review.optimization.storage_models import (
    OptimizationStoragePolicy,
    StoragePressureError,
)
from ai_sdlc.core.stage_review.optimization.storage_pressure import (
    _default_disk_probe,
    _require_storage_bundle,
    _storage_usage_limit,
    _storage_write_limit,
)
from ai_sdlc.core.stage_review.registry_versions import require_machine_id
from ai_sdlc.core.stage_review.resource_storage_bundles import (
    StorageBundleClass,
    StorageBundleHandle,
)


class OfflineOptimizationAccounting:
    """把所有 offline-optimization 写入收敛到同一锁与同一 Policy。"""

    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        policy: OptimizationStoragePolicy | None = None,
        disk_probe: Callable[[], tuple[int, int]] | None = None,
        lock_timeout_seconds: float = 2,
    ) -> None:
        self.project_id = require_machine_id(project_id, "project_id")
        shared = resolve_canonical_shared_state(root, self.project_id)
        bind_repository_project(shared, self.project_id)
        self.root = shared / "offline-optimization"
        self.lock_path = shared / "locks" / "optimization-storage-accounting.lock"
        self.policy_path = (
            shared / "optimization-control" / "offline-storage-policy.json"
        )
        self.lock_timeout_seconds = lock_timeout_seconds
        self._explicit_policy = policy is not None
        self.policy = self._bind_policy(policy)
        self._disk_probe = disk_probe or (lambda: _default_disk_probe(self.root))

    def _bind_policy(
        self,
        policy: OptimizationStoragePolicy | None,
    ) -> OptimizationStoragePolicy:
        trusted = (
            OptimizationStoragePolicy()
            if policy is None
            else OptimizationStoragePolicy.model_validate(policy.model_dump())
        )
        with self.locked():
            if not self.policy_path.is_file():
                if policy is None:
                    return trusted
                create_json_exclusive(
                    self.policy_path,
                    trusted.model_dump(mode="json"),
                )
            existing = OptimizationStoragePolicy.model_validate(
                read_json_object(self.policy_path)
            )
            if policy is not None and existing != trusted:
                raise SharedStateIntegrityError(
                    "offline optimization storage policy diverged"
                )
            if _tree_bytes(self.root) > existing.maximum_total_bytes:
                raise StoragePressureError(
                    "offline optimization usage exceeds bound storage policy"
                )
            return existing

    def _current_policy(self) -> OptimizationStoragePolicy:
        if not self.policy_path.is_file():
            return self.policy
        current = OptimizationStoragePolicy.model_validate(
            read_json_object(self.policy_path)
        )
        if self._explicit_policy and current != self.policy:
            raise SharedStateIntegrityError(
                "offline optimization storage policy changed"
            )
        return current

    @contextmanager
    def locked(self) -> Iterator[None]:
        with ShortFileLock(
            self.lock_path,
            timeout_seconds=self.lock_timeout_seconds,
        ):
            yield

    def reserve_bundle(
        self,
        *,
        write_class: str,
        bundle_bytes: int,
        net_reclaim_bytes: int,
        resource_bundle: StorageBundleHandle | None = None,
        assume_bundle_active: bool = False,
    ) -> None:
        if bundle_bytes < 0 or net_reclaim_bytes < 0:
            raise ValueError("storage bundle sizes cannot be negative")
        policy = self._current_policy()
        limits = _storage_write_limit(policy, write_class)
        usage_limit = _storage_usage_limit(policy, write_class)
        usage = _tree_bytes(self.root)
        if bundle_bytes > limits or usage + bundle_bytes > usage_limit:
            raise StoragePressureError("storage bundle exceeds its dedicated reserve")
        if write_class == "reclamation" and net_reclaim_bytes <= bundle_bytes:
            raise StoragePressureError("reclamation bundle must release net space")
        free, total = self._disk_probe()
        minimum = max(
            policy.minimum_free_bytes,
            int(total * policy.minimum_free_ratio),
        )
        reserved_class: StorageBundleClass | None = None
        if write_class == "critical_recovery":
            reserved_class = "critical_recovery"
        elif write_class == "session_binding":
            reserved_class = "session_binding"
        reserved_safety = reserved_class is not None and (
            resource_bundle is not None
            and resource_bundle.reservation.bundle_bytes >= bundle_bytes
        )
        if reserved_safety and not assume_bundle_active:
            assert resource_bundle is not None
            assert reserved_class is not None
            resource_bundle.assert_active(reserved_class)
        if free < bundle_bytes or (
            not reserved_safety and free - bundle_bytes < minimum
        ):
            raise StoragePressureError("filesystem free-space floor reached")

    def persist_json_exclusive(
        self,
        path: Path,
        payload: Mapping[str, object],
        *,
        write_class: str = "normal",
        resource_bundle: StorageBundleHandle | None = None,
    ) -> bool:
        trusted_payload = cast(dict[str, JsonValue], dict(payload))
        self._require_accounted_path(path)
        _require_storage_bundle(write_class, resource_bundle)
        bundle_class = cast(StorageBundleClass, write_class)
        authorization = (
            nullcontext()
            if resource_bundle is None
            else resource_bundle.hold_active(bundle_class)
        )
        with self.locked(), authorization:
            encoded = serialized_json_bytes(trusted_payload)
            if path.is_file():
                if resource_bundle is not None and path.read_bytes() == encoded:
                    resource_bundle.confirm_artifact(path, encoded)
                return False
            artifact_authorization = (
                nullcontext()
                if resource_bundle is None
                else resource_bundle.authorize_artifact(path, encoded)
            )
            with artifact_authorization:
                self.reserve_bundle(
                    write_class=write_class,
                    bundle_bytes=len(encoded),
                    net_reclaim_bytes=0,
                    resource_bundle=resource_bundle,
                    assume_bundle_active=resource_bundle is not None,
                )
                if create_json_exclusive(path, trusted_payload):
                    return True
                if path.read_bytes() != encoded:
                    raise SharedStateIntegrityError(
                        "offline optimization artifact diverged"
                    )
                return False

    def _require_accounted_path(self, path: Path) -> None:
        try:
            path.resolve().relative_to(self.root.resolve())
        except ValueError as exc:
            raise SharedStateIntegrityError(
                "offline optimization artifact escaped accounting root"
            ) from exc
