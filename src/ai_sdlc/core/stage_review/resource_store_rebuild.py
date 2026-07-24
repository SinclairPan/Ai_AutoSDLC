"""ResourceGovernor 事件真值的确定性重建。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.resource_digests import (
    resource_config_digest,
    resource_state_digest,
)
from ai_sdlc.core.stage_review.resource_grant_models import BudgetGrantOperation
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceGovernorState,
    ResourceLedgerEvent,
    ResourceReconciliation,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.resource_models import (
    ResourceAmounts,
    ResourceGovernorConfig,
)
from ai_sdlc.core.stage_review.resource_state_validation import (
    _aggregate_reserved as aggregate_reserved,
)
from ai_sdlc.core.stage_review.resource_state_validation import (
    _verify_pool_capacity as verify_pool_capacity,
)
from ai_sdlc.core.stage_review.resource_transitions import verify_resource_transition


def _build_resource_config(
    project_id: str,
    foreground: ResourceAmounts,
    offline: ResourceAmounts,
) -> ResourceGovernorConfig:
    draft = ResourceGovernorConfig.model_construct(
        project_id=project_id,
        foreground_capacity=foreground,
        offline_optimization_capacity=offline,
        config_digest="",
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["config_digest"] = resource_config_digest(draft)
    return ResourceGovernorConfig.model_validate(payload)


def rebuild_resource_state(
    config: ResourceGovernorConfig,
    events: tuple[ResourceLedgerEvent, ...],
    grant_operations: dict[str, BudgetGrantOperation],
) -> ResourceGovernorState:
    reservations: dict[str, ResourceReservation] = {}
    operations: dict[str, int] = {}
    reconciliations: dict[str, ResourceReconciliation] = {}
    events_by_digest: dict[str, ResourceLedgerEvent] = {}
    head_digest = ""
    max_fencing = 0
    for expected_sequence, event in enumerate(events, start=1):
        _verify_event_link(event, expected_sequence, head_digest, operations)
        previous = reservations.get(event.reservation.reservation_id)
        verify_resource_transition(
            config.project_id,
            event,
            previous,
            grant_operations.get(event.operation_id),
            events_by_digest,
        )
        reservations[event.reservation.reservation_id] = event.reservation
        verify_pool_capacity(config, reservations)
        operations[event.operation_id] = event.sequence
        if event.reconciliation is not None:
            reconciliations[event.reconciliation.reconciliation_id] = (
                event.reconciliation
            )
        head_digest = event.event_digest
        events_by_digest[event.event_digest] = event
        max_fencing = max(max_fencing, event.reservation.fencing_token)
    reserved = aggregate_reserved(tuple(reservations.values()))
    draft = ResourceGovernorState.model_construct(
        project_id=config.project_id,
        config_digest=config.config_digest,
        revision=len(events),
        head_sequence=len(events),
        head_digest=head_digest,
        next_fencing_token=max_fencing + 1,
        reserved=reserved,
        reservations=reservations,
        operation_events=operations,
        reconciliations=reconciliations,
        state_digest="",
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["state_digest"] = resource_state_digest(draft)
    return ResourceGovernorState.model_validate(payload)


def _verify_event_link(
    event: ResourceLedgerEvent,
    sequence: int,
    previous_digest: str,
    operations: dict[str, int],
) -> None:
    if event.sequence != sequence or event.previous_event_digest != previous_digest:
        raise SharedStateIntegrityError("resource event chain is not contiguous")
    if event.operation_id in operations:
        raise SharedStateIntegrityError("resource operation is committed twice")
