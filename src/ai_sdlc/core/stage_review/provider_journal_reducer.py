"""Provider Journal 事件构建与确定性投影重放。"""

from __future__ import annotations

from typing import TypedDict

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocation,
    ProviderInvocationEvent,
    ProviderInvocationRequest,
    ProviderInvocationState,
    event_digest,
    projection_digest,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id

STATE_ORDER: dict[ProviderInvocationState, int] = {
    "prepared": 1,
    "dispatched": 2,
    "refused": 3,
    "submitted": 3,
    "executed_invalid": 4,
    "validated": 4,
    "committed": 5,
}


class _EventIdentity(TypedDict):
    invocation_id: str
    sequence: int
    state: ProviderInvocationState
    event_id: str


def build_provider_event(
    request: ProviderInvocationRequest,
    current: ProviderInvocation | None,
    target_state: ProviderInvocationState,
    authorized_digest: str,
    submission_digest: str,
    validation_digest: str,
    settlement_operation_id: str,
    settlement_digest: str,
    settlement_event_digest: str,
    isolation_receipt_digests: tuple[str, ...] = (),
    egress_receipt_digests: tuple[str, ...] = (),
    execution_evidence_root_digest: str = "",
) -> ProviderInvocationEvent:
    sequence = STATE_ORDER[target_state]
    values = _event_values(
        current,
        authorized_digest,
        submission_digest,
        isolation_receipt_digests,
        egress_receipt_digests,
        execution_evidence_root_digest,
        validation_digest,
        settlement_operation_id,
        settlement_digest,
        settlement_event_digest,
    )
    draft = ProviderInvocationEvent.model_construct(
        **_event_identity(request, target_state, sequence),
        previous_event_digest="" if current is None else current.last_event_digest,
        request=request,
        authorized_reservation_digest=values["authorized_reservation_digest"],
        submission_digest=values["submission_digest"],
        isolation_receipt_digests=values["isolation_receipt_digests"],
        egress_receipt_digests=values["egress_receipt_digests"],
        execution_evidence_root_digest=values["execution_evidence_root_digest"],
        validation_digest=values["validation_digest"],
        resource_settlement_operation_id=values["resource_settlement_operation_id"],
        settlement_reservation_digest=values["settlement_reservation_digest"],
        resource_settlement_event_digest=values["resource_settlement_event_digest"],
        event_digest="",
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["request"] = request
    payload["event_digest"] = event_digest(draft)
    return ProviderInvocationEvent.model_validate(payload)


def _event_identity(
    request: ProviderInvocationRequest,
    state: ProviderInvocationState,
    sequence: int,
) -> _EventIdentity:
    return {
        "invocation_id": request.invocation_id,
        "sequence": sequence,
        "state": state,
        "event_id": stable_id(
            "provider-invocation-event", request.invocation_id, state
        ),
    }


def rebuild_provider_invocation(
    events: tuple[ProviderInvocationEvent, ...],
) -> ProviderInvocation:
    previous: ProviderInvocationEvent | None = None
    for sequence, event in enumerate(events, start=1):
        if event.sequence != sequence:
            raise SharedStateIntegrityError(
                "provider journal sequence is not contiguous"
            )
        if previous is not None:
            _verify_event_lineage(previous, event)
        previous = event
    if previous is None:
        raise SharedStateIntegrityError("provider journal has no events")
    draft = ProviderInvocation.model_construct(
        created_by=previous.created_by,
        created_at=previous.created_at,
        ai_sdlc_version=previous.ai_sdlc_version,
        request=previous.request,
        state=previous.state,
        revision=previous.sequence,
        authorized_reservation_digest=previous.authorized_reservation_digest,
        submission_digest=previous.submission_digest,
        isolation_receipt_digests=previous.isolation_receipt_digests,
        egress_receipt_digests=previous.egress_receipt_digests,
        execution_evidence_root_digest=previous.execution_evidence_root_digest,
        validation_digest=previous.validation_digest,
        resource_settlement_operation_id=previous.resource_settlement_operation_id,
        settlement_reservation_digest=previous.settlement_reservation_digest,
        resource_settlement_event_digest=previous.resource_settlement_event_digest,
        last_event_digest=previous.event_digest,
        projection_digest="",
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["request"] = previous.request
    payload["projection_digest"] = projection_digest(draft)
    return ProviderInvocation.model_validate(payload)


def verify_repeated_transition(
    current: ProviderInvocation,
    authorized: str,
    submission: str,
    validation: str,
    settlement_operation: str,
    settlement: str,
    settlement_event: str,
    isolation_receipt_digests: tuple[str, ...] = (),
    egress_receipt_digests: tuple[str, ...] = (),
    execution_evidence_root_digest: str = "",
) -> None:
    supplied = (
        (authorized, current.authorized_reservation_digest),
        (submission, current.submission_digest),
        (isolation_receipt_digests, current.isolation_receipt_digests),
        (egress_receipt_digests, current.egress_receipt_digests),
        (execution_evidence_root_digest, current.execution_evidence_root_digest),
        (validation, current.validation_digest),
        (settlement_operation, current.resource_settlement_operation_id),
        (settlement, current.settlement_reservation_digest),
        (settlement_event, current.resource_settlement_event_digest),
    )
    if any(value and value != expected for value, expected in supplied):
        raise SharedStateIntegrityError("provider transition replay diverged")


def _verify_event_lineage(
    previous: ProviderInvocationEvent,
    current: ProviderInvocationEvent,
) -> None:
    expected = (
        current.previous_event_digest == previous.event_digest,
        current.request.request_artifact_digest
        == previous.request.request_artifact_digest,
        current.authorized_reservation_digest == previous.authorized_reservation_digest,
        current.sequence < 4 or current.submission_digest == previous.submission_digest,
        current.sequence < 4
        or current.isolation_receipt_digests == previous.isolation_receipt_digests,
        current.sequence < 4
        or current.egress_receipt_digests == previous.egress_receipt_digests,
        current.sequence < 4
        or current.execution_evidence_root_digest
        == previous.execution_evidence_root_digest,
        current.sequence < 5 or current.validation_digest == previous.validation_digest,
    )
    if not all(expected):
        raise SharedStateIntegrityError("provider journal submission lineage diverged")


def _event_values(
    current: ProviderInvocation | None,
    authorized: str,
    submission: str,
    isolation_receipt_digests: tuple[str, ...],
    egress_receipt_digests: tuple[str, ...],
    execution_evidence_root_digest: str,
    validation: str,
    settlement_operation: str,
    settlement: str,
    settlement_event: str,
) -> dict[str, object]:
    return {
        "authorized_reservation_digest": authorized
        or _current(current, "authorized_reservation_digest"),
        "submission_digest": submission or _current(current, "submission_digest"),
        "isolation_receipt_digests": isolation_receipt_digests
        or _current_tuple(current, "isolation_receipt_digests"),
        "egress_receipt_digests": egress_receipt_digests
        or _current_tuple(current, "egress_receipt_digests"),
        "execution_evidence_root_digest": execution_evidence_root_digest
        or _current(current, "execution_evidence_root_digest"),
        "validation_digest": validation or _current(current, "validation_digest"),
        "resource_settlement_operation_id": settlement_operation
        or _current(current, "resource_settlement_operation_id"),
        "settlement_reservation_digest": settlement
        or _current(current, "settlement_reservation_digest"),
        "resource_settlement_event_digest": settlement_event
        or _current(current, "resource_settlement_event_digest"),
    }


def _current(current: ProviderInvocation | None, field: str) -> str:
    return "" if current is None else str(getattr(current, field))


def _current_tuple(
    current: ProviderInvocation | None,
    field: str,
) -> tuple[str, ...]:
    return () if current is None else tuple(getattr(current, field))
