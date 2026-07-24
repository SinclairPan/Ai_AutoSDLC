"""唯一项目级 ResourceGovernor、两阶段 Reservation 与真实计量。"""

from __future__ import annotations

from datetime import datetime
from math import isfinite
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.authority_binding_artifacts import (
    _build_budget_grant_authority_binding as build_budget_grant_authority_binding,
)
from ai_sdlc.core.stage_review.authority_binding_artifacts import (
    _ensure_budget_grant_authority_binding as ensure_budget_grant_authority_binding,
)
from ai_sdlc.core.stage_review.panel_finalization import (
    PanelProposalReplayContext,
    _build_reviewer_panel_plan,
)
from ai_sdlc.core.stage_review.panel_models import ReviewerBudgetPolicy
from ai_sdlc.core.stage_review.panel_plan_models import (
    ReviewerPanelPlan,
    ReviewerPanelProposal,
)
from ai_sdlc.core.stage_review.resource_accounting import (
    reconcile_reservation,
    record_resource_usage,
)
from ai_sdlc.core.stage_review.resource_admission_ops import (
    _finalize_offline_reservation as finalize_offline_reservation,
)
from ai_sdlc.core.stage_review.resource_admission_ops import (
    finalize_reservation,
    reserve_admission,
)
from ai_sdlc.core.stage_review.resource_builders import build_budget_envelope
from ai_sdlc.core.stage_review.resource_certificate_inputs import (
    _ResourceCertificateInputsMixin as ResourceCertificateInputsMixin,
)
from ai_sdlc.core.stage_review.resource_governor_queries import (
    _ResourceGovernorQueryMixin as ResourceGovernorQueryMixin,
)
from ai_sdlc.core.stage_review.resource_grant_gateway_ops import (
    _ResourceGrantGatewayMixin as ResourceGrantGatewayMixin,
)
from ai_sdlc.core.stage_review.resource_lease_ops import (
    release_reservation,
    renew_reservation_lease,
)
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceReservationResult,
)
from ai_sdlc.core.stage_review.resource_models import BudgetEnvelope, ResourceAmounts
from ai_sdlc.core.stage_review.resource_provider_ops import (
    authorize_provider_call,
    settle_provider_call,
)
from ai_sdlc.core.stage_review.resource_provider_reconciliation import (
    reconcile_expired_provider_call,
)
from ai_sdlc.core.stage_review.resource_runtime import (
    prepare_state,
    reservation_failure,
    utc_now,
)
from ai_sdlc.core.stage_review.resource_storage_bundles import (
    _ResourceStorageBundleMixin as ResourceStorageBundleMixin,
)
from ai_sdlc.core.stage_review.resource_store import ResourceEventStore
from ai_sdlc.core.stage_review.session_budget_grant_authority_contracts import (
    BudgetGrantApprovalResolver,
)
from ai_sdlc.core.stage_review.session_budget_grant_authority_store import (
    _canonical_budget_grant_authority,
    ensure_shared_state_binding_id,
)


class ResourceGovernor(
    ResourceCertificateInputsMixin,
    ResourceGrantGatewayMixin,
    ResourceGovernorQueryMixin,
    ResourceStorageBundleMixin,
):
    """以不可变事件和短锁 CAS 统一所有 Worktree 的资源预留。"""

    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        foreground_capacity: ResourceAmounts,
        offline_optimization_capacity: ResourceAmounts | None = None,
        lock_timeout_seconds: float = 2,
        budget_grant_approval_resolver: BudgetGrantApprovalResolver | None = None,
    ) -> None:
        if not isfinite(lock_timeout_seconds) or lock_timeout_seconds <= 0:
            raise ValueError("resource lock timeout must be positive and finite")
        shared_root = resolve_canonical_shared_state(root, project_id)
        self.project_id = project_id
        self.shared_state_binding_id = ensure_shared_state_binding_id(
            shared_root,
            project_id,
        )
        budget_grant_authority = _canonical_budget_grant_authority(
            root,
            project_id,
            budget_grant_approval_resolver,
            lock_timeout_seconds,
        )
        self._store = ResourceEventStore(
            shared_root,
            project_id=project_id,
            foreground_capacity=foreground_capacity,
            offline_optimization_capacity=(
                offline_optimization_capacity or ResourceAmounts()
            ),
            lock_timeout_seconds=lock_timeout_seconds,
        )
        self._budget_grant_authority = budget_grant_authority
        if budget_grant_authority is not None:
            binding = build_budget_grant_authority_binding(
                project_id=project_id,
                shared_state_binding_id=budget_grant_authority.shared_state_binding_id,
                authority_id=budget_grant_authority.authority_id,
            )
            with self._store.locked():
                ensure_budget_grant_authority_binding(
                    self._store.root / "budget-grant-authority.json",
                    binding,
                )

    def require_shared_state_binding(self) -> str:
        current = ensure_shared_state_binding_id(
            self._store.shared_root,
            self.project_id,
        )
        if current != self.shared_state_binding_id:
            raise SharedStateIntegrityError("resource shared state binding changed")
        return current

    def reserve_admission(
        self,
        envelope: BudgetEnvelope,
        *,
        budget_policy: ReviewerBudgetPolicy,
        lease_owner: str,
        operation_id: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> ResourceReservationResult:
        return reserve_admission(
            self._store,
            envelope,
            budget_policy=budget_policy,
            lease_owner=lease_owner,
            operation_id=operation_id,
            lease_seconds=lease_seconds,
            now=now,
        )

    def finalize_reservation(
        self,
        reservation_id: str,
        *,
        proposal: ReviewerPanelProposal,
        lease_owner: str,
        expected_fencing_token: int,
        operation_id: str,
        now: datetime | None = None,
    ) -> ResourceReservationResult:
        return finalize_reservation(
            self._store,
            reservation_id,
            proposal=proposal,
            lease_owner=lease_owner,
            expected_fencing_token=expected_fencing_token,
            operation_id=operation_id,
            now=now,
        )

    def finalize_offline_reservation(
        self,
        reservation_id: str,
        *,
        lease_owner: str,
        expected_fencing_token: int,
        operation_id: str,
        now: datetime | None = None,
    ) -> ResourceReservationResult:
        return finalize_offline_reservation(
            self._store,
            reservation_id,
            lease_owner=lease_owner,
            expected_fencing_token=expected_fencing_token,
            operation_id=operation_id,
            now=now,
        )

    def record_usage(
        self,
        reservation_id: str,
        *,
        delta: ResourceAmounts,
        lease_owner: str,
        expected_fencing_token: int,
        operation_id: str,
        now: datetime | None = None,
    ) -> ResourceReservationResult:
        return record_resource_usage(
            self._store,
            reservation_id,
            delta=delta,
            lease_owner=lease_owner,
            expected_fencing_token=expected_fencing_token,
            operation_id=operation_id,
            now=now,
        )

    def freeze_panel_plan(
        self,
        reservation_id: str,
        *,
        proposal: ReviewerPanelProposal,
        replay_context: PanelProposalReplayContext,
        lease_owner: str,
        expected_fencing_token: int,
        now: datetime | None = None,
    ) -> ReviewerPanelPlan:
        from ai_sdlc.core.stage_review.panel import validate_panel_proposal

        trusted_context = PanelProposalReplayContext.model_validate(replay_context)
        trusted_proposal = ReviewerPanelProposal.model_validate(
            proposal.model_dump(mode="json")
        )
        validate_panel_proposal(
            trusted_proposal,
            **trusted_context.replay_inputs(),
        )
        current_time = utc_now(now)
        with self._store.locked():
            state = prepare_state(self._store, current_time)
            current = state.reservations.get(reservation_id)
            failure = reservation_failure(
                current,
                expected_fencing_token,
                current_time,
                "final",
                lease_owner=lease_owner,
            )
            if failure is not None or current is None:
                code = "invalid_reservation" if failure is None else failure.result_code
                raise ValueError(f"reviewer panel freeze rejected: {code}")
            return _build_reviewer_panel_plan(trusted_proposal, current)

    def renew_reservation(
        self,
        reservation_id: str,
        *,
        lease_owner: str,
        lease_seconds: float,
        expected_fencing_token: int,
        operation_id: str,
        now: datetime | None = None,
    ) -> ResourceReservationResult:
        return renew_reservation_lease(
            self._store,
            reservation_id,
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
            expected_fencing_token=expected_fencing_token,
            operation_id=operation_id,
            now=now,
        )

    def release_reservation(
        self,
        reservation_id: str,
        *,
        lease_owner: str,
        expected_fencing_token: int,
        operation_id: str,
        now: datetime | None = None,
    ) -> ResourceReservationResult:
        return release_reservation(
            self._store,
            reservation_id,
            lease_owner=lease_owner,
            expected_fencing_token=expected_fencing_token,
            operation_id=operation_id,
            now=now,
        )

    def provider_call_authorized(
        self,
        reservation_id: str,
        *,
        invocation_id: str,
        anticipated_usage: ResourceAmounts,
        lease_owner: str,
        expected_fencing_token: int,
        operation_id: str,
        now: datetime | None = None,
    ) -> ResourceReservationResult:
        return authorize_provider_call(
            self._store,
            reservation_id,
            invocation_id=invocation_id,
            anticipated_usage=anticipated_usage,
            lease_owner=lease_owner,
            expected_fencing_token=expected_fencing_token,
            operation_id=operation_id,
            now=now,
        )

    def settle_provider_call(
        self,
        reservation_id: str,
        *,
        invocation_id: str,
        actual_usage: ResourceAmounts,
        lease_owner: str,
        expected_fencing_token: int,
        operation_id: str,
        now: datetime | None = None,
    ) -> ResourceReservationResult:
        return settle_provider_call(
            self._store,
            reservation_id,
            invocation_id=invocation_id,
            actual_usage=actual_usage,
            lease_owner=lease_owner,
            expected_fencing_token=expected_fencing_token,
            operation_id=operation_id,
            now=now,
        )

    def reconcile_expired_provider_call(
        self,
        reservation_id: str,
        *,
        invocation_id: str,
        actual_usage: ResourceAmounts,
        lease_owner: str,
        expected_fencing_token: int,
        operation_id: str,
        now: datetime | None = None,
    ) -> ResourceReservationResult:
        return reconcile_expired_provider_call(
            self._store,
            reservation_id,
            invocation_id=invocation_id,
            actual_usage=actual_usage,
            lease_owner=lease_owner,
            expected_fencing_token=expected_fencing_token,
            operation_id=operation_id,
            now=now,
        )

    def reconcile(
        self,
        reservation_id: str,
        *,
        lease_owner: str,
        expected_fencing_token: int,
        operation_id: str,
        now: datetime | None = None,
    ) -> ResourceReservationResult:
        return reconcile_reservation(
            self._store,
            reservation_id,
            lease_owner=lease_owner,
            expected_fencing_token=expected_fencing_token,
            operation_id=operation_id,
            now=now,
        )

__all__ = ["ResourceGovernor", "build_budget_envelope"]
