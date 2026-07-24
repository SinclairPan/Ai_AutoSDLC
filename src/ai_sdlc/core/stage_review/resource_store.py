"""ResourceGovernor 的追加事件存储与可重建投影。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    ShortFileLock,
    atomic_write_json,
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
)
from ai_sdlc.core.stage_review.project_fencing import ProjectFencingDomain
from ai_sdlc.core.stage_review.resource_digests import resource_state_digest
from ai_sdlc.core.stage_review.resource_grant_decision_store import (
    _ResourceGrantDecisionStoreMixin as ResourceGrantDecisionStoreMixin,
)
from ai_sdlc.core.stage_review.resource_grant_models import (
    BudgetGrant,
    BudgetGrantOperation,
)
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceGovernorSnapshot,
    ResourceGovernorState,
    ResourceLedgerEvent,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.resource_models import (
    ResourceAmounts,
    ResourceGovernorConfig,
)
from ai_sdlc.core.stage_review.resource_queries import (
    provider_reconciliation_for,
    reservation_at_digest,
)
from ai_sdlc.core.stage_review.resource_store_rebuild import (
    _build_resource_config as build_resource_config,
)
from ai_sdlc.core.stage_review.resource_store_rebuild import (
    rebuild_resource_state,
)


class ResourceEventStore(ResourceGrantDecisionStoreMixin):
    """以不可变 Event 为真值，state.json 仅作投影。"""

    def __init__(
        self,
        shared_root: Path,
        *,
        project_id: str,
        foreground_capacity: ResourceAmounts,
        offline_optimization_capacity: ResourceAmounts,
        lock_timeout_seconds: float,
    ) -> None:
        self.shared_root = shared_root
        self.project_id = project_id
        self.root = shared_root / "reviewer-resource-governor"
        self.events_dir = self.root / "ledger"
        self.grants_dir = self.root / "budget-grants"
        self.grant_operations_dir = self.root / "budget-grant-operations"
        self.config_path = self.root / "config.json"
        self.projection_path = self.root / "state.json"
        self.lock_path = self.root / "governor.lock"
        self.lock_timeout_seconds = lock_timeout_seconds
        self.expected_config = build_resource_config(
            project_id,
            foreground_capacity,
            offline_optimization_capacity,
        )
        self._fencing = ProjectFencingDomain(
            shared_root,
            project_id=project_id,
            lock_timeout_seconds=lock_timeout_seconds,
        )

    @contextmanager
    def locked(self) -> Iterator[None]:
        with ShortFileLock(
            self.lock_path,
            timeout_seconds=self.lock_timeout_seconds,
        ), self._fencing.locked():
            bind_repository_project(self.shared_root, self.project_id)
            self._ensure_config()
            self._recover_budget_grant_operations()
            yield

    def load_state(self) -> ResourceGovernorState:
        events = self._read_events()
        operations = self.read_budget_grant_operations()
        state = rebuild_resource_state(self.expected_config, events, operations)
        self._verify_projection(state)
        return self._with_shared_fencing(state)

    def event_for_operation(
        self,
        state: ResourceGovernorState,
        operation_id: str,
    ) -> ResourceLedgerEvent | None:
        sequence = state.operation_events.get(operation_id)
        if sequence is None:
            return None
        path = self._event_path(sequence)
        return ResourceLedgerEvent.model_validate(read_json_object(path))

    def provider_reconciliation_for(
        self,
        source_event_digest: str,
    ) -> ResourceLedgerEvent | None:
        return provider_reconciliation_for(self._read_events(), source_event_digest)

    def get_reservation(self, reservation_id: str) -> ResourceReservation:
        with self.locked():
            reservation = self.load_state().reservations.get(reservation_id)
            if reservation is None:
                raise KeyError(reservation_id)
            return reservation

    def reservation_at_digest(
        self,
        reservation_id: str,
        reservation_digest: str,
    ) -> ResourceReservation | None:
        with self.locked():
            return self._reservation_at_digest_locked(
                reservation_id,
                reservation_digest,
            )

    def _reservation_at_digest_locked(
        self,
        reservation_id: str,
        reservation_digest: str,
    ) -> ResourceReservation | None:
        """仅供已经持有 governor.lock 的复合事务读取历史版本。"""

        self.load_state()
        return reservation_at_digest(
            self._read_events(), reservation_id, reservation_digest
        )

    def snapshot(self) -> ResourceGovernorSnapshot:
        with self.locked():
            state = self.load_state()
            return ResourceGovernorSnapshot(
                revision=state.revision,
                head_digest=state.head_digest,
                reserved=state.reserved,
                reservation_count=len(state.reservations),
            )

    def append_event(self, event: ResourceLedgerEvent) -> ResourceGovernorState:
        events = self._read_events()
        grant_operations = self.read_budget_grant_operations()
        current = rebuild_resource_state(
            self.expected_config,
            events,
            grant_operations,
        )
        self._verify_projection(current)
        current = self._with_shared_fencing(current)
        if event.sequence != current.head_sequence + 1:
            raise SharedStateIntegrityError("resource event sequence CAS failed")
        if event.previous_event_digest != current.head_digest:
            raise SharedStateIntegrityError("resource event head digest CAS failed")
        self._fencing.require_allocation_locked(
            event.reservation.fencing_token,
            ("resource", event.reservation.reservation_id),
        )
        prospective = rebuild_resource_state(
            self.expected_config,
            (*events, event),
            grant_operations,
        )
        path = self._event_path(event.sequence)
        if not create_json_exclusive(path, event.model_dump(mode="json")):
            existing = ResourceLedgerEvent.model_validate(read_json_object(path))
            if existing.event_digest != event.event_digest:
                raise SharedStateIntegrityError("resource event sequence fork detected")
        self.materialize_projection(prospective)
        return self._with_shared_fencing(prospective)

    def persist_budget_grant(self, grant: BudgetGrant) -> None:
        path = self.grants_dir / f"{grant.idempotency_key}.json"
        if create_json_exclusive(path, grant.model_dump(mode="json")):
            return
        existing = BudgetGrant.model_validate(read_json_object(path))
        if existing.grant_digest != grant.grant_digest:
            raise SharedStateIntegrityError("budget grant identity fork detected")

    def persist_budget_grant_operation(
        self,
        operation: BudgetGrantOperation,
    ) -> None:
        path = self.grant_operations_dir / f"{operation.operation_id}.json"
        if create_json_exclusive(path, operation.model_dump(mode="json")):
            return
        existing = BudgetGrantOperation.model_validate(read_json_object(path))
        if existing.operation_digest != operation.operation_digest:
            raise SharedStateIntegrityError("budget grant operation fork detected")

    def _recover_budget_grant_operations(self) -> None:
        operations = self.read_budget_grant_operations()
        if not operations:
            return
        state = rebuild_resource_state(
            self.expected_config,
            self._read_events(),
            operations,
        )
        for operation_id in sorted(operations):
            operation = operations[operation_id]
            committed = self.event_for_operation(state, operation_id)
            if committed is not None:
                if committed.event_digest != operation.target_event_digest:
                    raise SharedStateIntegrityError(
                        "BudgetGrantOperation target event diverged"
                    )
                continue
            self._verify_pending_budget_grant_operation(state, operation)
            state = self.append_event(operation.target_event)

    def _verify_pending_budget_grant_operation(
        self,
        state: ResourceGovernorState,
        operation: BudgetGrantOperation,
    ) -> None:
        target = operation.target_event
        current = state.reservations.get(operation.grant.final_reservation_id)
        expected = (
            target.sequence == state.head_sequence + 1,
            target.previous_event_digest == state.head_digest,
            current is not None,
            current is not None
            and target.previous_reservation_digest == current.reservation_digest,
            operation.expected_reservation_revision
            == (0 if current is None else current.revision),
            operation.expected_reservation_digest
            == ("" if current is None else current.reservation_digest),
        )
        if not all(expected):
            raise SharedStateIntegrityError(
                "pending BudgetGrantOperation cannot be resumed exactly"
            )

    def materialize_projection(self, state: ResourceGovernorState) -> None:
        atomic_write_json(self.projection_path, state.model_dump(mode="json"))

    def _ensure_config(self) -> None:
        payload = self.expected_config.model_dump(mode="json")
        if create_json_exclusive(self.config_path, payload):
            return
        try:
            current = ResourceGovernorConfig.model_validate(
                read_json_object(self.config_path)
            )
        except (ValidationError, ValueError) as exc:
            raise SharedStateIntegrityError("resource config is invalid") from exc
        if current.config_digest != self.expected_config.config_digest:
            raise SharedStateIntegrityError("resource governor config changed")

    def _read_events(self) -> tuple[ResourceLedgerEvent, ...]:
        if not self.events_dir.exists():
            return ()
        events: list[ResourceLedgerEvent] = []
        for path in sorted(self.events_dir.glob("*.json")):
            try:
                events.append(
                    ResourceLedgerEvent.model_validate(read_json_object(path))
                )
            except (ValidationError, ValueError) as exc:
                raise SharedStateIntegrityError(
                    f"resource event is invalid: {path}"
                ) from exc
        return tuple(events)

    def read_budget_grant_operations(
        self,
    ) -> dict[str, BudgetGrantOperation]:
        if not self.grant_operations_dir.exists():
            return {}
        operations: dict[str, BudgetGrantOperation] = {}
        for path in sorted(self.grant_operations_dir.glob("*.json")):
            try:
                operation = BudgetGrantOperation.model_validate(read_json_object(path))
                self._verify_persisted_grant(operation)
            except (ValidationError, ValueError) as exc:
                raise SharedStateIntegrityError(
                    f"BudgetGrantOperation is invalid: {path}"
                ) from exc
            operations[operation.operation_id] = operation
        return operations

    def _verify_persisted_grant(self, operation: BudgetGrantOperation) -> None:
        grant_path = self.grants_dir / f"{operation.grant.idempotency_key}.json"
        if not grant_path.exists():
            raise SharedStateIntegrityError("BudgetGrantOperation has no BudgetGrant")
        grant = BudgetGrant.model_validate(read_json_object(grant_path))
        if grant.grant_digest != operation.grant.grant_digest:
            raise SharedStateIntegrityError("BudgetGrantOperation Grant diverged")

    def _event_path(self, sequence: int) -> Path:
        return self.events_dir / f"{sequence:020d}.json"

    def _verify_projection(self, rebuilt: ResourceGovernorState) -> None:
        if not self.projection_path.exists():
            return
        try:
            projected = ResourceGovernorState.model_validate(
                read_json_object(self.projection_path)
            )
        except (ValidationError, ValueError):
            return
        if projected.head_sequence > rebuilt.head_sequence:
            raise SharedStateIntegrityError(
                "resource projection is ahead of event truth"
            )
        if (
            projected.head_sequence == rebuilt.head_sequence
            and projected.state_digest != rebuilt.state_digest
        ):
            raise SharedStateIntegrityError(
                "resource projection diverges from event truth"
            )

    def _with_shared_fencing(
        self,
        state: ResourceGovernorState,
    ) -> ResourceGovernorState:
        next_epoch = self._fencing.next_epoch_locked()
        if state.next_fencing_token == next_epoch:
            return state
        draft = state.model_copy(
            update={"next_fencing_token": next_epoch, "state_digest": ""}
        )
        return draft.model_copy(update={"state_digest": resource_state_digest(draft)})
