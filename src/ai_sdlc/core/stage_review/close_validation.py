"""阶段关闭 Claim、Receipt 与调用上下文的一致性校验。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.close_builders import (
    _build_close_receipt as build_close_receipt,
)
from ai_sdlc.core.stage_review.close_models import (
    CloseConsumptionClaim,
    CloseConsumptionEvent,
    CloseConsumptionState,
    StageCloseConsumptionReceipt,
    StageCloseContext,
)


class CloseClaimConflictError(SharedStateIntegrityError):
    """Certificate 已由其他关闭命令或内容契约认领。"""


def validate_claim_context(
    claim: CloseConsumptionClaim,
    context: StageCloseContext,
) -> None:
    certificate = context.certificate
    intent = context.certificate_request.intent
    checks = (
        claim.scope == certificate.scope,
        claim.certificate_id == certificate.certificate_id,
        claim.certificate_digest == certificate.certificate_digest,
        claim.certificate_revision == certificate.certificate_revision,
        claim.session_start_revision
        in {certificate.session_revision, certificate.session_revision + 1},
        claim.command_id == intent.command_id,
        claim.idempotency_key == intent.idempotency_key,
        claim.close_intent_digest == intent.close_intent_digest,
        claim.candidate_manifest_digest == certificate.candidate_manifest_digest,
        claim.protected_path_set == certificate.protected_path_set,
        claim.artifact_path == context.close_artifact.artifact_path,
        claim.content_contract_digest
        == context.close_artifact.content_contract_digest,
        claim.worktree_identity == context.worktree_identity,
        claim.final_resource_reservation_digest
        == context.final_resource_reservation_digest,
        claim.resource_reconciliation_digest
        == context.resource_reconciliation_digest,
        claim.fencing_epoch == context.fencing_epoch,
    )
    if not all(checks):
        raise CloseClaimConflictError(
            "certificate claim belongs to another command or close contract"
        )


def _require_reconciled_receipt(
    claim: CloseConsumptionClaim,
    state: CloseConsumptionState,
    receipt: StageCloseConsumptionReceipt,
) -> None:
    expected = build_close_receipt(
        claim,
        close_artifact_digest=state.close_artifact_digest,
        reconciled_event_digest=state.head_event_digest,
        committed_at=receipt.committed_at,
    )
    if receipt != expected:
        raise SharedStateIntegrityError("stage close receipt diverged")


def require_closed_reconciliation(
    claim: CloseConsumptionClaim,
    state: CloseConsumptionState,
    receipt: StageCloseConsumptionReceipt,
    committed: CloseConsumptionEvent | None,
) -> None:
    checks = (
        state.receipt_digest == receipt.receipt_digest,
        receipt.claim_digest == claim.claim_digest,
        receipt.certificate_digest == claim.certificate_digest,
        receipt.command_id == claim.command_id,
        receipt.close_artifact_digest == state.close_artifact_digest,
        receipt.final_resource_reservation_digest
        == claim.final_resource_reservation_digest,
        receipt.resource_reconciliation_digest
        == claim.resource_reconciliation_digest,
        receipt.fencing_epoch == claim.fencing_epoch,
        committed is not None,
        committed is not None and committed.event_kind == "committed",
        committed is not None and committed.event_digest == state.head_event_digest,
        committed is not None
        and receipt.reconciled_event_digest == committed.previous_event_digest,
    )
    if not all(checks):
        raise SharedStateIntegrityError("stage close four-way reconciliation failed")
