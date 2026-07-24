"""持有 Repo Write Lease 时推进关闭 Artifact、Receipt 与事件链。"""

from __future__ import annotations

from collections.abc import Callable

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.close_builders import (
    _build_close_receipt as build_close_receipt,
)
from ai_sdlc.core.stage_review.close_models import (
    CloseConsumptionClaim,
    CloseConsumptionState,
    StageCloseConsumptionReceipt,
    StageCloseContext,
)
from ai_sdlc.core.stage_review.close_store import StageCloseStore
from ai_sdlc.core.stage_review.close_validation import (
    _require_reconciled_receipt as require_reconciled_receipt,
)
from ai_sdlc.core.stage_review.close_validation import (
    require_closed_reconciliation,
)


class CloseFactAdvancer:
    def __init__(
        self,
        store: StageCloseStore,
        *,
        clock: Callable[[], str],
    ) -> None:
        self._store = store
        self._clock = clock

    def advance(
        self,
        context: StageCloseContext,
        claim: CloseConsumptionClaim,
        state: CloseConsumptionState,
        authorize_write: Callable[[], object],
        checkpoint: Callable[[str], None],
        *,
        before_close_artifact: Callable[[], object] | None = None,
    ) -> tuple[CloseConsumptionState, StageCloseConsumptionReceipt]:
        state = self._advance_artifact(
            context,
            claim,
            state,
            authorize_write,
            checkpoint,
            before_close_artifact,
        )
        state, receipt = self._advance_receipt(
            claim, state, authorize_write, checkpoint
        )
        return state, self._verify_closed(context, claim, state, receipt)

    def _advance_artifact(
        self,
        context: StageCloseContext,
        claim: CloseConsumptionClaim,
        state: CloseConsumptionState,
        authorize_write: Callable[[], object],
        checkpoint: Callable[[str], None],
        before_close_artifact: Callable[[], object] | None,
    ) -> CloseConsumptionState:
        if state.event_kinds[-1] == "prepared":
            if before_close_artifact is not None:
                before_close_artifact()
            artifact_digest = self._store.write_artifact(
                context.close_artifact,
                authorize_write=authorize_write,
            )
            checkpoint("artifact_written")
            state = self._store.append_event(
                claim,
                state,
                "close_written",
                occurred_at=self._clock(),
                close_artifact_digest=artifact_digest,
                authorize_write=authorize_write,
            )
            checkpoint("close_written")
        artifact_digest = state.close_artifact_digest
        self._store.require_artifact(context.close_artifact, artifact_digest)
        if state.event_kinds[-1] == "close_written":
            state = self._store.append_event(
                claim,
                state,
                "reconciled",
                occurred_at=self._clock(),
                close_artifact_digest=artifact_digest,
                authorize_write=authorize_write,
            )
            checkpoint("reconciled")
        return state

    def _advance_receipt(
        self,
        claim: CloseConsumptionClaim,
        state: CloseConsumptionState,
        authorize_write: Callable[[], object],
        checkpoint: Callable[[str], None],
    ) -> tuple[CloseConsumptionState, StageCloseConsumptionReceipt | None]:
        receipt = self._store.read_receipt(claim.claim_id)
        if state.event_kinds[-1] == "reconciled":
            if receipt is None:
                receipt = self._store.create_receipt(
                    build_close_receipt(
                        claim,
                        close_artifact_digest=state.close_artifact_digest,
                        reconciled_event_digest=state.head_event_digest,
                        committed_at=self._clock(),
                    ),
                    authorize_write=authorize_write,
                )
            else:
                require_reconciled_receipt(claim, state, receipt)
            checkpoint("receipt_created")
            state = self._store.append_event(
                claim,
                state,
                "committed",
                occurred_at=self._clock(),
                close_artifact_digest=state.close_artifact_digest,
                receipt_digest=receipt.receipt_digest,
                authorize_write=authorize_write,
            )
            checkpoint("committed")
        return state, receipt

    def _verify_closed(
        self,
        context: StageCloseContext,
        claim: CloseConsumptionClaim,
        state: CloseConsumptionState,
        receipt: StageCloseConsumptionReceipt | None,
    ) -> StageCloseConsumptionReceipt:
        if receipt is None or not state.closed:
            raise SharedStateIntegrityError("stage close did not reach committed state")
        committed = self._store.last_event(claim.certificate_id)
        self._store.require_artifact(
            context.close_artifact,
            state.close_artifact_digest,
        )
        require_closed_reconciliation(claim, state, receipt, committed)
        return receipt
