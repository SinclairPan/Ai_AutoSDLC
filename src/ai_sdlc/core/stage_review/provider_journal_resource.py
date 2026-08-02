"""Provider Journal 与唯一 Resource Ledger 的血缘匹配。"""

from __future__ import annotations

from datetime import datetime

from ai_sdlc.core.stage_review.provider_journal_builders import (
    permit_id,
    settlement_operation_id,
)
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocation,
    ProviderInvocationRequest,
    ProviderSubmission,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceLedgerEvent,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.resource_runtime import utc_now


def resource_lineage_matches(
    request: ProviderInvocationRequest,
    current: ResourceReservation,
    ancestor: ResourceReservation | None,
    project_id: str,
) -> bool:
    return (
        request.project_id == project_id
        and resource_identity_matches(request, current)
        and ancestor is not None
        and ancestor.state == "final"
        and ancestor.fencing_token == request.expected_fencing_token
        and resource_identity_matches(request, ancestor)
    )


def resource_identity_matches(
    request: ProviderInvocationRequest,
    reservation: ResourceReservation,
) -> bool:
    expected_pool = "offline_optimization" if request.epoch_id else "foreground"
    return (
        reservation.project_id == request.project_id
        and reservation.work_item_id == request.work_item_id
        and reservation.stage_review_session_id == request.stage_review_session_id
        and reservation.pool == expected_pool
        and _owner_scope_matches(request, reservation)
    )


def _owner_scope_matches(
    request: ProviderInvocationRequest,
    reservation: ResourceReservation,
) -> bool:
    if reservation.pool == "offline_optimization":
        return (
            _offline_epoch_matches(request, reservation)
            and request.owner_scope_id == f"offline-optimization.{request.epoch_id}"
            and not reservation.provider_scope_ids
        )
    return request.owner_scope_id in reservation.provider_scope_ids


def _offline_epoch_matches(
    request: ProviderInvocationRequest,
    reservation: ResourceReservation,
) -> bool:
    session_id = reservation.stage_review_session_id
    if request.epoch_id == session_id:
        return True
    prefix = f"{request.epoch_id}.window."
    suffix = session_id.removeprefix(prefix)
    return session_id.startswith(prefix) and len(suffix) == 20 and suffix.isdigit()


def resource_ready(
    request: ProviderInvocationRequest,
    reservation: ResourceReservation,
    now: datetime | None,
) -> bool:
    expected_permit_id = permit_id(request)
    permit = next(
        (
            item
            for item in reservation.provider_permits
            if item.permit_id == expected_permit_id
        ),
        None,
    )
    return (
        resource_identity_matches(request, reservation)
        and reservation.state == "final"
        and reservation.fencing_token == request.expected_fencing_token
        and permit is not None
        and permit.anticipated_usage == request.anticipated_usage
        and parse_utc(reservation.lease_expires_at) > utc_now(now)
    )


def settlement_event_matches(
    invocation: ProviderInvocation,
    submission: ProviderSubmission,
    event: ResourceLedgerEvent,
    target: ResourceReservation,
) -> bool:
    permit = event.provider_permit
    return (
        event.event_kind in {"provider_call_settled", "provider_call_reconciled"}
        and event.operation_id == settlement_operation_id(invocation.request)
        and event.target_reservation_digest == target.reservation_digest
        and target.reservation_id == invocation.request.reservation_id
        and permit is not None
        and permit.invocation_id == invocation.invocation_id
        and permit.anticipated_usage == invocation.request.anticipated_usage
        and event.actual_usage == submission.accounted_usage.amounts
    )
