"""一次性 Holdout Commitment、统计预算与 Journal 查询恢复。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.holdout_contracts import (
    HoldoutEvaluationResult,
    HoldoutProviderSpec,
    HoldoutQueryCommitment,
    HoldoutQueryRequest,
)
from ai_sdlc.core.stage_review.optimization.holdout_store import (
    HoldoutCommitmentStore,
)
from ai_sdlc.core.stage_review.provider_journal import (
    ProviderInvocationDriver,
    ProviderInvocationJournal,
)
from ai_sdlc.core.stage_review.provider_journal_builders import (
    build_provider_invocation_request,
)
from ai_sdlc.core.stage_review.provider_journal_driver import ProviderOutputValidator
from ai_sdlc.core.stage_review.provider_journal_models import ProviderInvocationRequest
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.resources import ResourceGovernor


class HoldoutEvaluationService:
    def __init__(
        self,
        *,
        store: HoldoutCommitmentStore,
        journal: ProviderInvocationJournal,
        resource_governor: ResourceGovernor,
    ) -> None:
        self.store = store
        self.journal = journal
        self.resources = resource_governor

    def evaluate(
        self,
        request: HoldoutQueryRequest,
        *,
        provider: HoldoutProviderSpec,
        driver: ProviderInvocationDriver,
        validator: ProviderOutputValidator,
        reservation_id: str,
        lease_owner: str,
        crash_after_commit: bool = False,
    ) -> HoldoutEvaluationResult:
        commitment = self.store.commit(request)
        if crash_after_commit:
            raise RuntimeError("injected holdout commitment crash")
        reservation = self.resources.get_reservation(reservation_id)
        if (
            reservation.pool != "offline_optimization"
            or reservation.state != "final"
            or reservation.lease_owner != lease_owner
        ):
            raise SharedStateIntegrityError("holdout reservation is unavailable")
        invocation = self._resolve_invocation(
            commitment, request, provider, reservation
        )
        prepared = self.journal.prepare(invocation, lease_owner=lease_owner)
        if prepared.invocation is None:
            raise SharedStateIntegrityError("holdout provider preparation failed")
        result = self.journal.resume(
            invocation.invocation_id,
            driver=driver,
            validator=validator,
            lease_owner=lease_owner,
        )
        return HoldoutEvaluationResult(
            commitment=commitment,
            invocation_result=result,
        )

    def _resolve_invocation(
        self,
        commitment: HoldoutQueryCommitment,
        request: HoldoutQueryRequest,
        provider: HoldoutProviderSpec,
        reservation: ResourceReservation,
    ) -> ProviderInvocationRequest:
        invocation_id = stable_id(
            "provider-invocation",
            commitment.project_id,
            reservation.stage_review_session_id,
            provider.provider_id,
            request.provider_query_idempotency_key,
        )
        existing = self.journal.get(invocation_id)
        if existing is not None:
            return existing.request
        return build_provider_invocation_request(
            project_id=commitment.project_id,
            work_item_id=reservation.work_item_id,
            stage_review_session_id=reservation.stage_review_session_id,
            owner_scope_id=f"offline-optimization.{request.epoch_id}",
            candidate_digest=request.finalist_candidate_digest,
            assignment_digest=commitment.commitment_digest,
            epoch_id=request.epoch_id,
            provider_id=provider.provider_id,
            request_digest=provider.request_digest,
            reservation_id=reservation.reservation_id,
            expected_reservation_digest=reservation.reservation_digest,
            expected_fencing_token=reservation.fencing_token,
            anticipated_usage=provider.anticipated_usage,
            capabilities=provider.capabilities,
            command_id=stable_id("holdout-query-command", commitment.commitment_id),
            idempotency_key=request.provider_query_idempotency_key,
        )
