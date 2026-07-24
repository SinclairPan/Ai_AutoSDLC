"""阶段关闭不可变 Claim、Event 与 Receipt 的确定性构建。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.close_models import (
    CloseConsumptionClaim,
    CloseConsumptionEvent,
    CloseConsumptionState,
    CloseEventKind,
    StageCloseAuthorization,
    StageCloseConsumptionReceipt,
    StageCloseContext,
)
from ai_sdlc.core.stage_review.repo_write_lease import RepoWriteLeaseRequest
from ai_sdlc.core.stage_review.resource_builders import stable_id


def _build_close_claim(
    context: StageCloseContext,
    *,
    prepared_at: str,
    session_start_revision: int | None = None,
) -> CloseConsumptionClaim:
    certificate = context.certificate
    intent = context.certificate_request.intent
    return CloseConsumptionClaim(
        claim_id=stable_id("close-consumption-claim", certificate.certificate_id),
        scope=certificate.scope,
        certificate_id=certificate.certificate_id,
        certificate_digest=certificate.certificate_digest,
        certificate_revision=certificate.certificate_revision,
        session_start_revision=(
            certificate.session_revision
            if session_start_revision is None
            else session_start_revision
        ),
        command_id=intent.command_id,
        idempotency_key=intent.idempotency_key,
        close_intent_digest=intent.close_intent_digest,
        candidate_manifest_digest=certificate.candidate_manifest_digest,
        protected_path_set=certificate.protected_path_set,
        artifact_path=context.close_artifact.artifact_path,
        content_contract_digest=context.close_artifact.content_contract_digest,
        worktree_identity=context.worktree_identity,
        final_resource_reservation_digest=(
            context.final_resource_reservation_digest
        ),
        resource_reconciliation_digest=context.resource_reconciliation_digest,
        fencing_epoch=context.fencing_epoch,
        prepared_at=prepared_at,
    )


def _build_close_event(
    claim: CloseConsumptionClaim,
    *,
    sequence: int,
    event_kind: CloseEventKind,
    previous_event_digest: str,
    occurred_at: str,
    close_artifact_digest: str | None = None,
    receipt_digest: str = "",
    governance_decision_digest: str = "",
) -> CloseConsumptionEvent:
    return CloseConsumptionEvent(
        sequence=sequence,
        event_id=stable_id(
            "close-consumption-event",
            claim.claim_digest,
            event_kind,
        ),
        event_kind=event_kind,
        claim_id=claim.claim_id,
        claim_digest=claim.claim_digest,
        previous_event_digest=previous_event_digest,
        close_intent_digest=claim.close_intent_digest,
        artifact_path=claim.artifact_path,
        content_contract_digest=claim.content_contract_digest,
        close_artifact_digest=close_artifact_digest,
        resource_reconciliation_digest=claim.resource_reconciliation_digest,
        receipt_digest=receipt_digest,
        governance_decision_digest=governance_decision_digest,
        occurred_at=occurred_at,
    )


def _build_close_receipt(
    claim: CloseConsumptionClaim,
    *,
    close_artifact_digest: str,
    reconciled_event_digest: str,
    committed_at: str,
) -> StageCloseConsumptionReceipt:
    return StageCloseConsumptionReceipt(
        receipt_id=stable_id(
            "stage-close-consumption-receipt",
            claim.claim_digest,
        ),
        claim_id=claim.claim_id,
        claim_digest=claim.claim_digest,
        certificate_id=claim.certificate_id,
        certificate_digest=claim.certificate_digest,
        command_id=claim.command_id,
        close_intent_digest=claim.close_intent_digest,
        close_artifact_digest=close_artifact_digest,
        reconciled_event_digest=reconciled_event_digest,
        final_resource_reservation_digest=(
            claim.final_resource_reservation_digest
        ),
        resource_reconciliation_digest=claim.resource_reconciliation_digest,
        fencing_epoch=claim.fencing_epoch,
        committed_at=committed_at,
    )


def build_close_lease_request(
    context: StageCloseContext,
    claim: CloseConsumptionClaim,
    state: CloseConsumptionState,
) -> RepoWriteLeaseRequest:
    return RepoWriteLeaseRequest(
        worktree_identity=claim.worktree_identity,
        stage_review_session_id=claim.scope.session_id,
        protected_path_set=tuple(
            sorted({*claim.protected_path_set, claim.artifact_path})
        ),
        lease_owner=context.lease_owner,
        idempotency_key=stable_id(
            "stage-close-write-lease",
            claim.claim_digest,
            state.head_event_digest or claim.claim_digest,
        ),
        lease_seconds=context.lease_seconds,
    )


def _build_needs_user_authorization(
    claim: CloseConsumptionClaim,
    state: CloseConsumptionState,
) -> StageCloseAuthorization:
    return StageCloseAuthorization(
        status="needs_user",
        claim=claim,
        receipt=None,
        state=state,
    )
