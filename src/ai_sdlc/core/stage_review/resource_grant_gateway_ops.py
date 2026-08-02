"""ResourceGovernor 面向 Session BudgetGrant 的窄网关实现。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from typing import Protocol, cast

from ai_sdlc.core.stage_review.resource_grant_decisions import (
    _decide_budget_grant as decide_budget_grant,
)
from ai_sdlc.core.stage_review.resource_grant_gateway_validation import (
    EventReader,
    ReservationReader,
    require_grant_operation,
    verify_request,
)
from ai_sdlc.core.stage_review.resource_grant_gateway_validation import (
    _approval_state as approval_state,
)
from ai_sdlc.core.stage_review.resource_grant_gateway_validation import (
    _decision_can_release as decision_can_release,
)
from ai_sdlc.core.stage_review.resource_grant_gateway_validation import (
    _existing_reconciliation as existing_reconciliation,
)
from ai_sdlc.core.stage_review.resource_grant_gateway_validation import (
    _require_decision as require_decision,
)
from ai_sdlc.core.stage_review.resource_grant_gateway_validation import (
    _validate_reconcile_decision as validate_reconcile_decision,
)
from ai_sdlc.core.stage_review.resource_grant_models import (
    BudgetGrant,
    BudgetGrantDecisionClaim,
    BudgetGrantDecisionKind,
    BudgetGrantOperation,
    BudgetGrantResourceError,
)
from ai_sdlc.core.stage_review.resource_grant_reconciliation import (
    _reconcile_budget_grant_current as reconcile_budget_grant_current,
)
from ai_sdlc.core.stage_review.resource_grant_request_authority import (
    validate_budget_grant_request,
)
from ai_sdlc.core.stage_review.resource_grants import (
    apply_budget_grant,
)
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceLedgerEvent,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.resource_runtime import prepare_state, utc_now
from ai_sdlc.core.stage_review.resource_store import ResourceEventStore
from ai_sdlc.core.stage_review.session_budget_grant_authority_store import (
    BudgetGrantRequestAuthority,
)
from ai_sdlc.core.stage_review.session_budget_grant_request import (
    BudgetGrantRequestProof,
)


def _apply_session_budget_grant(
    store: ResourceEventStore,
    reservation_for: ReservationReader,
    event_for: EventReader,
    grant: BudgetGrant,
    request_proof: BudgetGrantRequestProof,
    request_authority: BudgetGrantRequestAuthority,
    now: datetime | None,
) -> BudgetGrantOperation:
    verify_request(request_authority, request_proof)
    if not approval_state(request_authority, request_proof).active:
        raise BudgetGrantResourceError("approval_revoked")
    validate_budget_grant_request(grant, request_proof)
    current = reservation_for(grant.final_reservation_id)
    result = apply_budget_grant(
        store,
        grant,
        lease_owner=current.lease_owner,
        expected_reservation_revision=request_proof.approval.final_reservation_revision,
        expected_reservation_digest=request_proof.approval.final_reservation_digest,
        expected_fencing_token=request_proof.approval.final_fencing_token,
        now=now,
    )
    return require_grant_operation(store, event_for, grant, "apply", result.result_code)


def _reconcile_session_budget_grant(
    store: ResourceEventStore,
    reservation_for: ReservationReader,
    event_for: EventReader,
    application_operation: BudgetGrantOperation,
    decision: BudgetGrantDecisionClaim,
    request_proof: BudgetGrantRequestProof,
    request_authority: BudgetGrantRequestAuthority,
    apply_command_id: str,
    now: datetime | None,
) -> BudgetGrantOperation:
    grant = application_operation.grant
    verify_request(request_authority, request_proof)
    trusted = require_grant_operation(store, event_for, grant, "apply", "expanded")
    if trusted != application_operation:
        raise BudgetGrantResourceError("state_corrupt")
    persisted_decision = require_decision(store, decision)
    validate_reconcile_decision(
        persisted_decision,
        application_operation,
        request_proof,
        apply_command_id,
    )
    existing = existing_reconciliation(store, event_for, grant)
    if existing is not None:
        return existing
    if not decision_can_release(
        persisted_decision,
        request_authority,
        request_proof,
        apply_command_id,
    ):
        raise BudgetGrantResourceError("decision_conflict")
    result = reconcile_budget_grant_current(
        store,
        grant,
        now=now,
    )
    return require_grant_operation(
        store,
        event_for,
        grant,
        "reconcile",
        result.result_code,
    )


def _verify_session_budget_grant(
    store: ResourceEventStore,
    reservation_for: ReservationReader,
    event_for: EventReader,
    operation: BudgetGrantOperation,
    request_proof: BudgetGrantRequestProof,
    request_authority: BudgetGrantRequestAuthority,
) -> ResourceReservation:
    verify_request(request_authority, request_proof)
    validate_budget_grant_request(operation.grant, request_proof)
    trusted = require_grant_operation(
        store,
        event_for,
        operation.grant,
        "apply",
        "expanded",
    )
    if trusted != operation:
        raise BudgetGrantResourceError("state_corrupt")
    current = reservation_for(operation.grant.final_reservation_id)
    target = operation.target_event.reservation
    if (
        current.reservation_digest != target.reservation_digest
        or operation.grant.grant_id in current.reconciled_budget_grant_ids
    ):
        raise BudgetGrantResourceError("grant_not_current")
    return current


@contextmanager
def _hold_session_budget_grant_apply(
    store: ResourceEventStore,
    operation: BudgetGrantOperation,
    decision: BudgetGrantDecisionClaim,
    request_proof: BudgetGrantRequestProof,
    request_authority: BudgetGrantRequestAuthority,
    now: datetime | None,
) -> Iterator[None]:
    verify_request(request_authority, request_proof)
    validate_budget_grant_request(operation.grant, request_proof)
    current_time = utc_now(now)
    with store.locked():
        state = prepare_state(store, current_time)

        def event_for(operation_id: str) -> ResourceLedgerEvent | None:
            return store.event_for_operation(state, operation_id)

        trusted = require_grant_operation(
            store,
            event_for,
            operation.grant,
            "apply",
            "expanded",
        )
        persisted = require_decision(store, decision)
        current = state.reservations.get(operation.grant.final_reservation_id)
        if (
            trusted != operation
            or persisted.decision_kind != "session_apply"
            or persisted.grant.grant_digest != operation.grant.grant_digest
            or persisted.request_proof_digest != request_proof.proof_digest
            or current != operation.target_event.reservation
        ):
            raise BudgetGrantResourceError("grant_not_current")
        yield


def _decide_session_budget_grant(
    store: ResourceEventStore,
    event_for: EventReader,
    application_operation: BudgetGrantOperation,
    request_proof: BudgetGrantRequestProof,
    request_authority: BudgetGrantRequestAuthority,
    desired_kind: BudgetGrantDecisionKind,
    now: datetime | None,
) -> BudgetGrantDecisionClaim:
    verify_request(request_authority, request_proof)
    current_approval = approval_state(request_authority, request_proof)
    if desired_kind == "session_apply" and not current_approval.active:
        desired_kind = "reconcile"
    validate_budget_grant_request(application_operation.grant, request_proof)
    trusted = require_grant_operation(
        store,
        event_for,
        application_operation.grant,
        "apply",
        "expanded",
    )
    if trusted != application_operation:
        raise BudgetGrantResourceError("state_corrupt")
    return decide_budget_grant(
        store,
        application_operation,
        request_proof,
        current_approval,
        desired_kind,
        now=now,
    )


class _ResourceGrantGatewayHost(Protocol):
    _store: ResourceEventStore
    _budget_grant_authority: BudgetGrantRequestAuthority | None

    def get_reservation(self, reservation_id: str) -> ResourceReservation: ...

    def get_operation_event(self, operation_id: str) -> ResourceLedgerEvent | None: ...


class _ResourceGrantGatewayMixin:
    """ResourceGovernor 对 Session 暴露的唯一 BudgetGrant 门面。"""

    def apply_session_budget_grant(
        self,
        grant: BudgetGrant,
        request_proof: BudgetGrantRequestProof,
        *,
        now: datetime | None = None,
    ) -> BudgetGrantOperation:
        host = cast(_ResourceGrantGatewayHost, self)
        return _apply_session_budget_grant(
            host._store,
            host.get_reservation,
            host.get_operation_event,
            grant,
            request_proof,
            _require_authority(host),
            now,
        )

    def decide_session_budget_grant(
        self,
        application_operation: BudgetGrantOperation,
        request_proof: BudgetGrantRequestProof,
        desired_kind: BudgetGrantDecisionKind,
        *,
        now: datetime | None = None,
    ) -> BudgetGrantDecisionClaim:
        host = cast(_ResourceGrantGatewayHost, self)
        return _decide_session_budget_grant(
            host._store,
            host.get_operation_event,
            application_operation,
            request_proof,
            _require_authority(host),
            desired_kind,
            now,
        )

    def hold_session_budget_grant_apply(
        self,
        operation: BudgetGrantOperation,
        decision: BudgetGrantDecisionClaim,
        request_proof: BudgetGrantRequestProof,
        *,
        now: datetime | None = None,
    ) -> AbstractContextManager[None]:
        host = cast(_ResourceGrantGatewayHost, self)
        return _hold_session_budget_grant_apply(
            host._store,
            operation,
            decision,
            request_proof,
            _require_authority(host),
            now,
        )

    def reconcile_session_budget_grant(
        self,
        application_operation: BudgetGrantOperation,
        decision: BudgetGrantDecisionClaim,
        request_proof: BudgetGrantRequestProof,
        apply_command_id: str,
        *,
        now: datetime | None = None,
    ) -> BudgetGrantOperation:
        host = cast(_ResourceGrantGatewayHost, self)
        return _reconcile_session_budget_grant(
            host._store,
            host.get_reservation,
            host.get_operation_event,
            application_operation,
            decision,
            request_proof,
            _require_authority(host),
            apply_command_id,
            now,
        )

    def verify_session_budget_grant(
        self,
        operation: BudgetGrantOperation,
        request_proof: BudgetGrantRequestProof,
    ) -> ResourceReservation:
        host = cast(_ResourceGrantGatewayHost, self)
        return _verify_session_budget_grant(
            host._store,
            host.get_reservation,
            host.get_operation_event,
            operation,
            request_proof,
            _require_authority(host),
        )


def _require_authority(
    host: _ResourceGrantGatewayHost,
) -> BudgetGrantRequestAuthority:
    if host._budget_grant_authority is None:
        raise BudgetGrantResourceError("state_corrupt")
    return host._budget_grant_authority
