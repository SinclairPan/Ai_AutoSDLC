"""Session Close 转换对持久化关闭事实的唯一校验端口。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.authority_binding_artifacts import (
    ensure_shared_state_binding,
)
from ai_sdlc.core.stage_review.close_models import CloseConsumptionClaim
from ai_sdlc.core.stage_review.close_store import StageCloseStore
from ai_sdlc.core.stage_review.close_validation import require_closed_reconciliation
from ai_sdlc.core.stage_review.session_contracts import (
    CloseConsumptionStartCommand,
    CloseReceiptCommitCommand,
    ReconciledCloseCertificateCommand,
    SessionIntegrityError,
)


class CanonicalCloseTransactionAuthority:
    authority_id = "stage-close-authorizer.v1"

    def __init__(
        self,
        store: StageCloseStore,
        *,
        project_id: str,
        shared_state_binding_id: str,
    ) -> None:
        if type(store) is not StageCloseStore:
            raise TypeError("canonical StageCloseStore is required")
        binding = ensure_shared_state_binding(
            store.shared_root / "shared-state-binding.json",
            project_id,
        )
        if (
            store.project_id != project_id
            or binding.binding_id != shared_state_binding_id
        ):
            raise ValueError("close transaction authority binding is invalid")
        self._store = store
        self.project_id = project_id
        self.shared_state_binding_id = shared_state_binding_id

    @property
    def canonical_binding(self) -> tuple[str, str, str]:
        return (
            self.project_id,
            self.shared_state_binding_id,
            str(self._store.root.resolve()),
        )

    def require_close_claim_current(
        self,
        command: CloseConsumptionStartCommand,
    ) -> None:
        try:
            self._store.require_consumable_state(command.claim)
        except SharedStateIntegrityError as exc:
            raise SessionIntegrityError("close claim is not persisted and current") from exc

    def require_close_receipt_current(
        self,
        command: CloseReceiptCommitCommand,
    ) -> None:
        try:
            state = self._store.require_consumable_state(command.claim)
            receipt = self._store.read_receipt(command.claim.claim_id)
            committed = self._store.last_event(command.claim.certificate_id)
            if receipt != command.receipt or receipt is None:
                raise SharedStateIntegrityError("close receipt is not persisted")
            require_closed_reconciliation(command.claim, state, receipt, committed)
        except SharedStateIntegrityError as exc:
            raise SessionIntegrityError("close receipt is not persisted and committed") from exc

    def require_reconciled_claim_current(
        self,
        command: ReconciledCloseCertificateCommand,
    ) -> None:
        self.require_aborted_claim_current(command.aborted_claim)
        try:
            state = self._store.require_consumable_state(command.claim)
            if state.revision or command.claim == command.aborted_claim:
                raise SharedStateIntegrityError("new close claim is not pristine")
        except SharedStateIntegrityError as exc:
            raise SessionIntegrityError("new close claim is not persisted") from exc

    def require_aborted_claim_current(
        self,
        claim: CloseConsumptionClaim,
    ) -> None:
        try:
            state = self._store.require_consumable_state(claim)
            if state.status != "aborted":
                raise SharedStateIntegrityError("close claim is not aborted")
        except SharedStateIntegrityError as exc:
            raise SessionIntegrityError("aborted close claim is not current") from exc
