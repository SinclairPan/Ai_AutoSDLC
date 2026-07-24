"""资源工件的确定性构建与预算换算。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_bytes,
)
from ai_sdlc.core.stage_review.panel_models import ReviewerBudgetPolicy
from ai_sdlc.core.stage_review.panel_plan_models import (
    PanelResourceRequirement,
    ReviewerPanelProposal,
)
from ai_sdlc.core.stage_review.resource_digests import (
    budget_envelope_digest,
    reconciliation_digest,
    reservation_digest,
    resource_event_digest,
)
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ProviderCallPermit,
    ResourceLedgerEvent,
    ResourceReconciliation,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.resource_models import (
    BudgetEnvelope,
    ResourceAmounts,
    ResourcePool,
    ResourceSoftLimits,
)


def build_budget_envelope(
    *,
    project_id: str,
    work_item_id: str,
    stage_review_session_id: str,
    risk_level: str,
    budget_policy: ReviewerBudgetPolicy,
    pool: ResourcePool = "foreground",
) -> BudgetEnvelope:
    hard = _policy_limits(budget_policy)
    draft = BudgetEnvelope.model_construct(
        project_id=project_id,
        work_item_id=work_item_id,
        stage_review_session_id=stage_review_session_id,
        risk_level=risk_level,
        pool=pool,
        budget_policy_digest=budget_policy.policy_digest,
        budget_policy_version=budget_policy.version,
        hard_limits=hard,
        soft_limits=soft_limits(hard),
        admission_requirement=hard,
        envelope_digest="",
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["envelope_digest"] = budget_envelope_digest(draft)
    return BudgetEnvelope.model_validate(payload)


def soft_limits(amounts: ResourceAmounts) -> ResourceSoftLimits:
    return ResourceSoftLimits.model_validate(
        {name: getattr(amounts, name) * 0.8 for name in ResourceAmounts.ALL_FIELDS}
    )


def final_requirement(
    requirement: PanelResourceRequirement,
    admission: ResourceReservation,
) -> ResourceAmounts:
    return ResourceAmounts(
        slots=requirement.total_slot_count,
        provider_calls=requirement.total_provider_calls,
        review_passes=requirement.total_review_passes,
        tokens=requirement.total_tokens,
        cost=requirement.total_cost,
        active_wall_clock=requirement.total_wall_clock,
        parallelism=requirement.parallelism,
        role_replans=admission.hard_limits.role_replans,
        provider_retries=admission.hard_limits.provider_retries,
        binding_attempts=admission.hard_limits.binding_attempts,
    )


def proposal_provider_scope_ids(
    proposal: ReviewerPanelProposal,
) -> tuple[str, ...]:
    groups = (
        proposal.required_slots,
        proposal.optional_slots,
        proposal.advisory_slots,
        proposal.shadow_slots,
    )
    return tuple(
        sorted(
            {
                provider
                for slots in groups
                for slot in slots
                for provider in slot.provider_constraints
            }
        )
    )


def build_admission_reservation(
    envelope: BudgetEnvelope,
    *,
    operation_id: str,
    operation_effect_digest: str,
    lease_owner: str,
    fencing_token: int,
    lease_expires_at: datetime,
) -> ResourceReservation:
    idempotency_key = admission_idempotency_key(envelope)
    reservation_id = stable_id("reservation", idempotency_key)
    return _build_reservation(
        reservation_id=reservation_id,
        project_id=envelope.project_id,
        work_item_id=envelope.work_item_id,
        stage_review_session_id=envelope.stage_review_session_id,
        pool=envelope.pool,
        state="admission",
        admission_operation_id=operation_id,
        idempotency_key=idempotency_key,
        budget_envelope_digest=envelope.envelope_digest,
        budget_policy_digest=envelope.budget_policy_digest,
        proposal_lineage_digest="",
        provider_scope_ids=(),
        reserved=envelope.admission_requirement,
        policy_hard_limits=envelope.hard_limits,
        hard_limits=envelope.hard_limits,
        soft_limits=envelope.soft_limits,
        revision=1,
        fencing_token=fencing_token,
        lease_owner=lease_owner,
        lease_expires_at=utc_iso(lease_expires_at),
        last_operation_id=operation_id,
        operation_effect_digest=operation_effect_digest,
    )


def admission_idempotency_key(envelope: BudgetEnvelope) -> str:
    """同一 Session 与预算包只能产生一个语义 Admission。"""

    return stable_id(
        "admission",
        envelope.project_id,
        envelope.work_item_id,
        envelope.stage_review_session_id,
        envelope.pool,
        envelope.envelope_digest,
    )


def build_resource_event(
    *,
    sequence: int,
    event_kind: str,
    operation_id: str,
    previous_event_digest: str,
    previous_reservation_digest: str = "",
    reservation: ResourceReservation,
    provider_permit: ProviderCallPermit | None = None,
    actual_usage: ResourceAmounts | None = None,
    reconciled_event_digest: str = "",
    reconciliation: ResourceReconciliation | None = None,
) -> ResourceLedgerEvent:
    event_id = stable_id("event", operation_id, event_kind)
    draft = ResourceLedgerEvent.model_construct(
        sequence=sequence,
        event_kind=event_kind,
        event_id=event_id,
        operation_id=operation_id,
        previous_event_digest=previous_event_digest,
        previous_reservation_digest=previous_reservation_digest,
        operation_effect_digest=reservation.operation_effect_digest,
        target_reservation_digest=reservation.reservation_digest,
        reservation=reservation,
        provider_permit=provider_permit,
        actual_usage=actual_usage,
        reconciled_event_digest=reconciled_event_digest,
        reconciliation=reconciliation,
        event_digest="",
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["reservation"] = reservation
    payload["provider_permit"] = provider_permit
    payload["actual_usage"] = actual_usage
    payload["reconciliation"] = reconciliation
    payload["event_digest"] = resource_event_digest(draft)
    return ResourceLedgerEvent.model_validate(payload)


def build_reconciliation(
    reservation: ResourceReservation,
    *,
    operation_id: str,
    fencing_token: int,
) -> ResourceReconciliation:
    released = resource_difference(
        reservation.reserved,
        reservation.usage + reservation.authorized_pending,
    )
    draft = ResourceReconciliation.model_construct(
        reconciliation_id=stable_id(
            "reconciliation", reservation.reservation_id, operation_id
        ),
        reservation_id=reservation.reservation_id,
        reservation_digest=reservation.reservation_digest,
        usage=reservation.usage,
        authorized_pending=reservation.authorized_pending,
        released=released,
        fencing_token=fencing_token,
        operation_id=operation_id,
        reconciliation_digest="",
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["reconciliation_digest"] = reconciliation_digest(draft)
    return ResourceReconciliation.model_validate(payload)


def resource_difference(
    upper: ResourceAmounts,
    lower: ResourceAmounts,
) -> ResourceAmounts:
    return ResourceAmounts.model_validate(
        {
            name: max(0, getattr(upper, name) - getattr(lower, name))
            for name in ResourceAmounts.ALL_FIELDS
        }
    )


def subtract_resources(
    upper: ResourceAmounts,
    lower: ResourceAmounts,
) -> ResourceAmounts:
    if not lower.fits_within(upper):
        raise ValueError("resource subtraction would become negative")
    return ResourceAmounts.model_validate(
        {
            name: getattr(upper, name) - getattr(lower, name)
            for name in ResourceAmounts.ALL_FIELDS
        }
    )


def stable_id(prefix: str, *values: str) -> str:
    payload = canonical_bytes(values, CanonicalizationPolicy())
    return f"{prefix}.{hashlib.sha256(payload).hexdigest()[:24]}"


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("resource timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _policy_limits(policy: ReviewerBudgetPolicy) -> ResourceAmounts:
    return ResourceAmounts(
        slots=policy.maximum_slots,
        provider_calls=policy.hard_provider_calls,
        review_passes=policy.hard_review_passes,
        tokens=policy.hard_tokens,
        cost=policy.hard_cost,
        active_wall_clock=policy.hard_wall_clock,
        parallelism=policy.hard_parallelism,
        role_replans=policy.hard_role_replans,
        provider_retries=policy.hard_provider_retries,
        binding_attempts=policy.hard_binding_attempts,
    )


def _build_reservation(**values: object) -> ResourceReservation:
    prepared = dict(values)
    prepared.setdefault("usage", ResourceAmounts())
    prepared["reservation_digest"] = ""
    draft = ResourceReservation.model_construct(**prepared)  # type: ignore[arg-type]
    payload = draft.model_dump(mode="json", warnings=False)
    payload["reservation_digest"] = reservation_digest(draft)
    return ResourceReservation.model_validate(payload)
