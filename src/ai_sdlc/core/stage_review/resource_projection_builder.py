"""ResourceReservation 不可变投影的单步构建。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ai_sdlc.core.stage_review.resource_builders import soft_limits, utc_iso
from ai_sdlc.core.stage_review.resource_digests import reservation_digest
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ProviderCallPermit,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts


def update_reservation(
    current: ResourceReservation,
    *,
    operation_id: str,
    operation_effect_digest: str,
    state: str | None = None,
    proposal_digest: str | None = None,
    proposal_lineage_digest: str | None = None,
    provider_scope_ids: tuple[str, ...] | None = None,
    reserved: ResourceAmounts | None = None,
    usage: ResourceAmounts | None = None,
    observed_overrun: ResourceAmounts | None = None,
    authorized_pending: ResourceAmounts | None = None,
    provider_permits: tuple[ProviderCallPermit, ...] | None = None,
    provider_invocation_ids: tuple[str, ...] | None = None,
    hard_limits: ResourceAmounts | None = None,
    budget_revision: int | None = None,
    last_budget_grant_operation_id: str | None = None,
    budget_grant_ids: tuple[str, ...] | None = None,
    reconciled_budget_grant_ids: tuple[str, ...] | None = None,
    fencing_token: int | None = None,
    lease_expires_at: datetime | None = None,
) -> ResourceReservation:
    payload = _reservation_update_payload(current, locals())
    draft = ResourceReservation.model_construct(**payload)  # type: ignore[arg-type]
    payload["reservation_digest"] = reservation_digest(draft)
    return ResourceReservation.model_validate(payload)


def _reservation_update_payload(
    current: ResourceReservation,
    changes: dict[str, Any],
) -> dict[str, object]:
    payload = current.model_dump(mode="json")
    payload.update(
        _lineage_updates(
            current,
            changes["state"],
            changes["proposal_digest"],
            changes["proposal_lineage_digest"],
            changes["provider_scope_ids"],
        )
    )
    payload.update(
        _accounting_updates(
            current,
            changes["reserved"],
            changes["usage"],
            changes["observed_overrun"],
            changes["authorized_pending"],
            changes["provider_permits"],
            changes["provider_invocation_ids"],
            changes["hard_limits"],
        )
    )
    payload.update(
        _grant_updates(
            current,
            changes["budget_revision"],
            changes["last_budget_grant_operation_id"],
            changes["budget_grant_ids"],
            changes["reconciled_budget_grant_ids"],
        )
    )
    payload.update(
        _operation_updates(
            current,
            changes["operation_id"],
            changes["operation_effect_digest"],
            changes["fencing_token"],
            changes["lease_expires_at"],
        )
    )
    return payload


def _lineage_updates(
    current: ResourceReservation,
    state: str | None,
    proposal_digest: str | None,
    proposal_lineage_digest: str | None,
    provider_scope_ids: tuple[str, ...] | None,
) -> dict[str, object]:
    return {
        "state": state or current.state,
        "proposal_digest": (
            current.proposal_digest if proposal_digest is None else proposal_digest
        ),
        "proposal_lineage_digest": (
            current.proposal_lineage_digest
            if proposal_lineage_digest is None
            else proposal_lineage_digest
        ),
        "provider_scope_ids": (
            current.provider_scope_ids
            if provider_scope_ids is None
            else tuple(sorted(set(provider_scope_ids)))
        ),
    }


def _accounting_updates(
    current: ResourceReservation,
    reserved: ResourceAmounts | None,
    usage: ResourceAmounts | None,
    observed_overrun: ResourceAmounts | None,
    authorized_pending: ResourceAmounts | None,
    provider_permits: tuple[ProviderCallPermit, ...] | None,
    provider_invocation_ids: tuple[str, ...] | None,
    hard_limits: ResourceAmounts | None,
) -> dict[str, object]:
    return {
        "reserved": current.reserved if reserved is None else reserved,
        "usage": current.usage if usage is None else usage,
        "observed_overrun": (
            current.observed_overrun if observed_overrun is None else observed_overrun
        ),
        "authorized_pending": (
            current.authorized_pending
            if authorized_pending is None
            else authorized_pending
        ),
        "provider_permits": (
            current.provider_permits if provider_permits is None else provider_permits
        ),
        "provider_invocation_ids": (
            current.provider_invocation_ids
            if provider_invocation_ids is None
            else tuple(sorted(set(provider_invocation_ids)))
        ),
        "policy_hard_limits": current.policy_hard_limits,
        "hard_limits": current.hard_limits if hard_limits is None else hard_limits,
        "soft_limits": (
            soft_limits(hard_limits) if hard_limits is not None else current.soft_limits
        ),
    }


def _grant_updates(
    current: ResourceReservation,
    budget_revision: int | None,
    last_operation_id: str | None,
    grant_ids: tuple[str, ...] | None,
    reconciled_ids: tuple[str, ...] | None,
) -> dict[str, object]:
    return {
        "budget_revision": (
            current.budget_revision if budget_revision is None else budget_revision
        ),
        "last_budget_grant_operation_id": (
            current.last_budget_grant_operation_id
            if last_operation_id is None
            else last_operation_id
        ),
        "budget_grant_ids": (
            current.budget_grant_ids
            if grant_ids is None
            else tuple(sorted(set(grant_ids)))
        ),
        "reconciled_budget_grant_ids": (
            current.reconciled_budget_grant_ids
            if reconciled_ids is None
            else tuple(sorted(set(reconciled_ids)))
        ),
    }


def _operation_updates(
    current: ResourceReservation,
    operation_id: str,
    effect_digest: str,
    fencing_token: int | None,
    lease_expires_at: datetime | None,
) -> dict[str, object]:
    return {
        "revision": current.revision + 1,
        "fencing_token": fencing_token or current.fencing_token,
        "lease_expires_at": (
            current.lease_expires_at
            if lease_expires_at is None
            else utc_iso(lease_expires_at)
        ),
        "last_operation_id": operation_id,
        "operation_effect_digest": effect_digest,
        "reservation_digest": "",
    }
