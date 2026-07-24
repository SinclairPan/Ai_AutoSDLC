"""Resource 与 Repo Write Lease 共用的项目级单调 Fencing 域。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    ShortFileLock,
    read_json_object,
)
from ai_sdlc.core.stage_review.repo_write_lease_models import RepoWriteLeaseEvent
from ai_sdlc.core.stage_review.repo_write_lease_store import (
    rebuild_repo_write_lease_state,
)
from ai_sdlc.core.stage_review.resource_grant_models import (
    BudgetGrant,
    BudgetGrantOperation,
)
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceLedgerEvent
from ai_sdlc.core.stage_review.resource_models import ResourceGovernorConfig
from ai_sdlc.core.stage_review.resource_store_rebuild import rebuild_resource_state
from ai_sdlc.core.stage_review.transaction_artifact_codec import (
    decode_transaction_artifact,
)

FencingIdentity = tuple[str, str]
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class ProjectFencingDomain:
    """从不可变业务事件重建高水位，避免独立计数器双写真值。"""

    def __init__(
        self,
        shared_root: Path,
        *,
        project_id: str,
        lock_timeout_seconds: float,
    ) -> None:
        self.shared_root = shared_root
        self.project_id = project_id
        self.lock_path = shared_root / "project-fencing" / "domain.lock"
        self.lock_timeout_seconds = lock_timeout_seconds

    @contextmanager
    def locked(self) -> Iterator[None]:
        with ShortFileLock(
            self.lock_path,
            timeout_seconds=self.lock_timeout_seconds,
        ):
            yield

    def next_epoch_locked(self) -> int:
        allocations = self._allocations()
        return max(allocations, default=0) + 1

    def require_allocation_locked(
        self,
        epoch: int,
        identity: FencingIdentity,
    ) -> None:
        allocations = self._allocations()
        existing = allocations.get(epoch)
        if existing is not None:
            if existing != identity:
                raise SharedStateIntegrityError("project fencing epoch is duplicated")
            return
        if epoch != max(allocations, default=0) + 1:
            raise SharedStateIntegrityError("project fencing epoch is not next")

    def _allocations(self) -> dict[int, FencingIdentity]:
        allocations: dict[int, FencingIdentity] = {}
        operations = self._budget_operations()
        for event in self._resource_events(operations):
            self._record(
                allocations,
                event.reservation.fencing_token,
                ("resource", event.reservation.reservation_id),
            )
        for operation in operations:
            target = operation.target_event.reservation
            self._record(
                allocations,
                target.fencing_token,
                ("resource", target.reservation_id),
            )
        for repo_event in self._repo_events():
            self._record(
                allocations,
                repo_event.lease.fencing_epoch,
                ("repo-write", repo_event.lease.lease_id),
            )
        return allocations

    @staticmethod
    def _record(
        allocations: dict[int, FencingIdentity],
        epoch: int,
        identity: FencingIdentity,
    ) -> None:
        existing = allocations.setdefault(epoch, identity)
        if existing != identity:
            raise SharedStateIntegrityError("project fencing history diverged")

    def _resource_events(
        self,
        operations: tuple[BudgetGrantOperation, ...],
    ) -> tuple[ResourceLedgerEvent, ...]:
        root = self.shared_root / "reviewer-resource-governor" / "ledger"
        events = self._read_many(root, ResourceLedgerEvent, "resource event")
        _validate_resource_events(events, self.project_id)
        config_path = root.parent / "config.json"
        if config_path.exists():
            try:
                config = ResourceGovernorConfig.model_validate(
                    read_json_object(config_path)
                )
                rebuild_resource_state(
                    config,
                    events,
                    {item.operation_id: item for item in operations},
                )
            except (OSError, ValidationError, ValueError) as exc:
                raise SharedStateIntegrityError(
                    "resource fencing state is invalid"
                ) from exc
        elif events:
            raise SharedStateIntegrityError("resource fencing config is missing")
        return events

    def _budget_operations(self) -> tuple[BudgetGrantOperation, ...]:
        root = (
            self.shared_root
            / "reviewer-resource-governor"
            / "budget-grant-operations"
        )
        operations = self._read_many(root, BudgetGrantOperation, "budget operation")
        for operation in operations:
            grant_path = (
                root.parent
                / "budget-grants"
                / f"{operation.grant.idempotency_key}.json"
            )
            try:
                grant = BudgetGrant.model_validate(read_json_object(grant_path))
            except (OSError, ValidationError, ValueError) as exc:
                raise SharedStateIntegrityError(
                    "budget operation grant is invalid"
                ) from exc
            if grant != operation.grant or grant.project_id != self.project_id:
                raise SharedStateIntegrityError("budget operation grant diverged")
        return operations

    def _repo_events(self) -> tuple[RepoWriteLeaseEvent, ...]:
        root = self.shared_root / "repo-write-leases" / "events"
        if not root.exists():
            return ()
        try:
            events = tuple(
                decode_transaction_artifact(
                    RepoWriteLeaseEvent,
                    read_json_object(path),
                )
                for path in sorted(root.glob("*.json"))
            )
            _validate_repo_events(events, self.project_id)
            rebuild_repo_write_lease_state(events, self.project_id)
            return events
        except (OSError, ValidationError, ValueError) as exc:
            raise SharedStateIntegrityError("repo lease event is invalid") from exc

    @staticmethod
    def _read_many(
        root: Path,
        model: type[_ModelT],
        label: str,
    ) -> tuple[_ModelT, ...]:
        if not root.exists():
            return ()
        try:
            return tuple(
                model.model_validate(read_json_object(path))
                for path in sorted(root.glob("*.json"))
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise SharedStateIntegrityError(f"{label} is invalid") from exc


def _validate_resource_events(
    events: tuple[ResourceLedgerEvent, ...],
    project_id: str,
) -> None:
    previous = ""
    operations: set[str] = set()
    for sequence, event in enumerate(events, start=1):
        valid = (
            event.sequence == sequence,
            event.previous_event_digest == previous,
            event.reservation.project_id == project_id,
            event.operation_id not in operations,
        )
        if not all(valid):
            raise SharedStateIntegrityError("resource fencing history diverged")
        operations.add(event.operation_id)
        previous = event.event_digest


def _validate_repo_events(
    events: tuple[RepoWriteLeaseEvent, ...],
    project_id: str,
) -> None:
    previous = ""
    for sequence, event in enumerate(events, start=1):
        if (
            event.sequence != sequence
            or event.previous_event_digest != previous
            or event.lease.project_id != project_id
        ):
            raise SharedStateIntegrityError("repo fencing history diverged")
        previous = event.event_digest
