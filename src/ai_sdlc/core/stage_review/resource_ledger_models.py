"""ResourceGovernor Ledger、Reservation 与结果投影合同。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.contracts import StageReviewArtifactModel
from ai_sdlc.core.stage_review.resource_models import (
    BudgetPressure,
    ReservationResultCode,
    ReservationState,
    ResourceAmounts,
    ResourceEventKind,
    ResourcePool,
    ResourceSoftLimits,
)


class ProviderCallPermit(BaseModel):
    """Provider 调用前原子占用的最坏情况额度。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    permit_id: str
    invocation_id: str
    anticipated_usage: ResourceAmounts

    @model_validator(mode="after")
    def _verify_permit(self) -> Self:
        amounts = self.anticipated_usage
        if not self.permit_id.strip() or not self.invocation_id.strip():
            raise ValueError("provider permit identity cannot be empty")
        if (
            amounts.provider_calls != 1
            or amounts.tokens <= 0
            or amounts.cost <= 0
            or amounts.active_wall_clock <= 0
            or amounts.parallelism != 1
        ):
            raise ValueError("provider permit requires complete worst-case usage")
        return self


class ResourceReservation(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    artifact_kind: Literal["resource-reservation"] = "resource-reservation"
    reservation_id: str
    project_id: str
    work_item_id: str
    stage_review_session_id: str
    pool: ResourcePool
    state: ReservationState
    admission_operation_id: str
    idempotency_key: str
    budget_envelope_digest: str
    budget_policy_digest: str
    proposal_digest: str = ""
    proposal_lineage_digest: str = ""
    provider_scope_ids: tuple[str, ...] = Field(default_factory=tuple)
    reserved: ResourceAmounts
    usage: ResourceAmounts = Field(default_factory=ResourceAmounts)
    observed_overrun: ResourceAmounts = Field(default_factory=ResourceAmounts)
    authorized_pending: ResourceAmounts = Field(default_factory=ResourceAmounts)
    provider_permits: tuple[ProviderCallPermit, ...] = Field(default_factory=tuple)
    provider_invocation_ids: tuple[str, ...] = Field(default_factory=tuple)
    policy_hard_limits: ResourceAmounts
    hard_limits: ResourceAmounts
    soft_limits: ResourceSoftLimits
    budget_revision: int = Field(default=0, ge=0)
    last_budget_grant_operation_id: str = ""
    budget_grant_ids: tuple[str, ...] = Field(default_factory=tuple)
    reconciled_budget_grant_ids: tuple[str, ...] = Field(default_factory=tuple)
    revision: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    lease_owner: str
    lease_expires_at: str
    last_operation_id: str
    operation_effect_digest: str
    reservation_digest: str

    @field_validator("lease_expires_at")
    @classmethod
    def _lease_is_utc(cls, value: str) -> str:
        _parse_utc(value)
        return value

    @field_validator(
        "admission_operation_id",
        "idempotency_key",
        "lease_owner",
        "last_operation_id",
        "operation_effect_digest",
        "project_id",
        "work_item_id",
        "stage_review_session_id",
        "budget_envelope_digest",
        "budget_policy_digest",
    )
    @classmethod
    def _identity_is_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("resource reservation identity cannot be empty")
        return value.strip()

    @model_validator(mode="after")
    def _verify_reservation(self) -> Self:
        from ai_sdlc.core.stage_review.resource_digests import reservation_digest

        committed = self.usage + self.authorized_pending
        if not committed.fits_within(self.reserved + self.observed_overrun):
            raise ValueError("reservation actual plus pending exceeds allocation")
        if not committed.fits_within(self.hard_limits + self.observed_overrun):
            raise ValueError("reservation actual plus pending exceeds hard limits")
        if not self.observed_overrun.fits_within(self.usage):
            raise ValueError("reservation overrun exceeds observed usage")
        if not self.reserved.fits_within(self.hard_limits):
            raise ValueError("reservation allocation exceeds hard limits")
        expected_soft = ResourceSoftLimits.model_validate(
            {
                name: getattr(self.hard_limits, name) * 0.8
                for name in ResourceAmounts.ALL_FIELDS
            }
        )
        if self.soft_limits != expected_soft:
            raise ValueError("reservation soft limits are inconsistent")
        _verify_provider_accounting(self)
        _verify_budget_grant_lineage(self)
        if self.reservation_digest != reservation_digest(self):
            raise ValueError("resource reservation digest does not match content")
        return self


class ResourceReconciliation(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["resource-reconciliation"] = "resource-reconciliation"
    reconciliation_id: str
    reservation_id: str
    reservation_digest: str
    usage: ResourceAmounts
    authorized_pending: ResourceAmounts = Field(default_factory=ResourceAmounts)
    released: ResourceAmounts
    fencing_token: int
    operation_id: str
    reconciliation_digest: str

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        from ai_sdlc.core.stage_review.resource_digests import reconciliation_digest

        if self.reconciliation_digest != reconciliation_digest(self):
            raise ValueError("resource reconciliation digest does not match content")
        return self


class ResourceLedgerEvent(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["resource-ledger-event"] = "resource-ledger-event"
    sequence: int = Field(ge=1)
    event_kind: ResourceEventKind
    event_id: str
    operation_id: str
    previous_event_digest: str
    previous_reservation_digest: str
    operation_effect_digest: str
    target_reservation_digest: str
    reservation: ResourceReservation
    provider_permit: ProviderCallPermit | None = None
    actual_usage: ResourceAmounts | None = None
    reconciled_event_digest: str = ""
    reconciliation: ResourceReconciliation | None = None
    event_digest: str

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        from ai_sdlc.core.stage_review.resource_digests import resource_event_digest

        if (
            not self.operation_id.strip()
            or self.operation_id != self.operation_id.strip()
        ):
            raise ValueError("resource event operation identity is invalid")
        expected = (
            self.reservation.last_operation_id == self.operation_id,
            self.reservation.operation_effect_digest == self.operation_effect_digest,
            self.reservation.reservation_digest == self.target_reservation_digest,
        )
        if not all(expected):
            raise ValueError("resource event operation target is inconsistent")
        if self.event_digest != resource_event_digest(self):
            raise ValueError("resource ledger event digest does not match content")
        return self


class ResourceGovernorState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    config_digest: str
    revision: int = Field(ge=0)
    head_sequence: int = Field(ge=0)
    head_digest: str
    next_fencing_token: int = Field(ge=1)
    reserved: ResourceAmounts
    reservations: dict[str, ResourceReservation]
    operation_events: dict[str, int]
    reconciliations: dict[str, ResourceReconciliation]
    state_digest: str

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        from ai_sdlc.core.stage_review.resource_digests import resource_state_digest

        if self.state_digest != resource_state_digest(self):
            raise ValueError("resource governor state digest does not match content")
        return self


class ResourceReservationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result_code: ReservationResultCode
    reservation: ResourceReservation | None = None
    operation_reservation: ResourceReservation | None = None
    provider_permit: ProviderCallPermit | None = None
    reconciliation: ResourceReconciliation | None = None
    pressure: BudgetPressure = "within"


class ResourceGovernorSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int
    head_digest: str
    reserved: ResourceAmounts
    reservation_count: int


def _verify_provider_accounting(reservation: ResourceReservation) -> None:
    if reservation.provider_scope_ids != tuple(
        sorted(set(reservation.provider_scope_ids))
    ):
        raise ValueError("reservation provider scopes must be unique and sorted")
    permit_ids = tuple(item.permit_id for item in reservation.provider_permits)
    if permit_ids != tuple(sorted(set(permit_ids))):
        raise ValueError("provider permits must be unique and sorted")
    pending = ResourceAmounts()
    for permit in reservation.provider_permits:
        pending = pending + permit.anticipated_usage
    if pending != reservation.authorized_pending:
        raise ValueError("provider pending usage does not match permits")
    if reservation.provider_invocation_ids != tuple(
        sorted(set(reservation.provider_invocation_ids))
    ) or not {item.invocation_id for item in reservation.provider_permits} <= set(
        reservation.provider_invocation_ids
    ):
        raise ValueError("provider invocation lineage is invalid")


def _verify_budget_grant_lineage(reservation: ResourceReservation) -> None:
    if reservation.budget_grant_ids != tuple(sorted(set(reservation.budget_grant_ids))):
        raise ValueError("budget grant identities must be unique and sorted")
    reconciled = reservation.reconciled_budget_grant_ids
    if reconciled != tuple(sorted(set(reconciled))) or not set(reconciled) <= set(
        reservation.budget_grant_ids
    ):
        raise ValueError("reconciled budget grant lineage is invalid")


def _parse_utc(value: str) -> None:
    from datetime import datetime

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("resource timestamp must include UTC offset")
