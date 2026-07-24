"""Session Close 状态转换依赖的可信 Authority 端口。"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from ai_sdlc.core.stage_review.close_governance_models import (
    StageCloseGovernanceDecision,
)
from ai_sdlc.core.stage_review.close_models import CloseConsumptionClaim
from ai_sdlc.core.stage_review.close_recovery_models import StageCloseRecoveryDecision
from ai_sdlc.core.stage_review.session_close_recovery_ops import (
    ReconciledCertificateGuard,
)
from ai_sdlc.core.stage_review.session_contracts import (
    CloseConsumptionStartCommand,
    CloseReceiptCommitCommand,
    ReconciledCloseCertificateCommand,
)


class SessionCloseStartAuthority(Protocol):
    def require_close_start_current(
        self,
        command: CloseConsumptionStartCommand,
    ) -> None: ...

    def require_reconciled_artifacts(
        self,
        command: ReconciledCloseCertificateCommand,
    ) -> None: ...

    def hold_reconciled_close_current(
        self,
        command: ReconciledCloseCertificateCommand,
    ) -> AbstractContextManager[ReconciledCertificateGuard]: ...


class SessionCloseAbortAuthority(Protocol):
    project_id: str
    shared_state_binding_id: str

    @property
    def authority_id(self) -> str: ...

    @property
    def authority_binding_digest(self) -> str: ...

    def require_abort(
        self,
        claim: CloseConsumptionClaim,
        decision_digest: str,
    ) -> StageCloseGovernanceDecision: ...

    def require_recovery(
        self,
        claim: CloseConsumptionClaim,
        decision: StageCloseRecoveryDecision,
    ) -> StageCloseRecoveryDecision: ...


class SessionCloseTransactionAuthority(Protocol):
    project_id: str
    shared_state_binding_id: str
    authority_id: str

    def require_close_claim_current(
        self,
        command: CloseConsumptionStartCommand,
    ) -> None: ...

    def require_close_receipt_current(
        self,
        command: CloseReceiptCommitCommand,
    ) -> None: ...

    def require_reconciled_claim_current(
        self,
        command: ReconciledCloseCertificateCommand,
    ) -> None: ...

    def require_aborted_claim_current(
        self,
        claim: CloseConsumptionClaim,
    ) -> None: ...
