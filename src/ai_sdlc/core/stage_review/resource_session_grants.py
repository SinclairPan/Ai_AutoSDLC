"""Session 专用的受控 ResourceGovernor BudgetGrant 适配。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from pydantic import ValidationError

from ai_sdlc.core.stage_review.resource_grant_models import (
    BudgetGrant,
    BudgetGrantDecisionClaim,
    BudgetGrantDecisionKind,
    BudgetGrantOperation,
    BudgetGrantResourceError,
)
from ai_sdlc.core.stage_review.resources import ResourceGovernor
from ai_sdlc.core.stage_review.session_budget_grant_models import (
    BudgetGrantResourceApplication,
)
from ai_sdlc.core.stage_review.session_budget_grant_request import (
    BudgetGrantRequestProof,
)
from ai_sdlc.core.stage_review.session_budget_reconciliation_models import (
    BudgetGrantResourceReconciliation,
)
from ai_sdlc.core.stage_review.session_models import StageReviewSession


class ResourceBudgetGrantCoordinator:
    """通过 ResourceGovernor 窄门面提交、复核和补偿 Grant。"""

    def __init__(
        self,
        governor: ResourceGovernor,
        *,
        now: datetime | None = None,
    ) -> None:
        self._governor = governor
        self._now = now

    def apply(
        self,
        grant: BudgetGrant,
        session: StageReviewSession,
        request_proof: BudgetGrantRequestProof,
    ) -> BudgetGrantResourceApplication:
        try:
            operation = self._governor.apply_session_budget_grant(
                grant,
                request_proof,
                now=self._now,
            )
            return _application(operation, request_proof.proof_digest)
        except BudgetGrantResourceError:
            raise
        except (ValidationError, ValueError, KeyError) as exc:
            raise BudgetGrantResourceError("state_corrupt") from exc

    def verify(
        self,
        application: BudgetGrantResourceApplication,
        session: StageReviewSession,
        request_proof: BudgetGrantRequestProof,
    ) -> BudgetGrantResourceApplication:
        current = self._governor.verify_session_budget_grant(
            application.resource_operation,
            request_proof,
        )
        valid = (
            application.request_proof_digest == request_proof.proof_digest,
            application.previous_reservation_digest
            == session.resource_reservation_digest,
            current == application.reservation,
        )
        if not all(valid):
            raise BudgetGrantResourceError("state_corrupt")
        return application

    def decide(
        self,
        application: BudgetGrantResourceApplication,
        request_proof: BudgetGrantRequestProof,
        desired_kind: BudgetGrantDecisionKind,
    ) -> BudgetGrantDecisionClaim:
        return self._governor.decide_session_budget_grant(
            application.resource_operation,
            request_proof,
            desired_kind,
            now=self._now,
        )

    @contextmanager
    def hold_apply_commit(
        self,
        application: BudgetGrantResourceApplication,
        decision: BudgetGrantDecisionClaim,
        session: StageReviewSession,
        request_proof: BudgetGrantRequestProof,
    ) -> Iterator[None]:
        valid = (
            application.request_proof_digest == request_proof.proof_digest,
            application.previous_reservation_digest
            == session.resource_reservation_digest,
            decision.grant.grant_digest == application.grant.grant_digest,
        )
        if not all(valid):
            raise BudgetGrantResourceError("state_corrupt")
        try:
            with self._governor.hold_session_budget_grant_apply(
                application.resource_operation,
                decision,
                request_proof,
                now=self._now,
            ):
                yield
        except BudgetGrantResourceError:
            raise
        except (ValidationError, ValueError, KeyError) as exc:
            raise BudgetGrantResourceError("state_corrupt") from exc

    def reconcile(
        self,
        application: BudgetGrantResourceApplication,
        decision: BudgetGrantDecisionClaim,
        request_proof: BudgetGrantRequestProof,
        apply_command_id: str,
    ) -> BudgetGrantResourceReconciliation:
        try:
            operation = self._governor.reconcile_session_budget_grant(
                application.resource_operation,
                decision,
                request_proof,
                apply_command_id,
                now=self._now,
            )
            return BudgetGrantResourceReconciliation(
                application=application,
                decision=decision,
                resource_operation=operation,
            )
        except BudgetGrantResourceError:
            raise
        except (ValidationError, ValueError, KeyError) as exc:
            raise BudgetGrantResourceError("state_corrupt") from exc


def _application(
    operation: BudgetGrantOperation,
    request_proof_digest: str,
) -> BudgetGrantResourceApplication:
    trusted = BudgetGrantOperation.model_validate(operation)
    return BudgetGrantResourceApplication(
        grant=trusted.grant,
        request_proof_digest=request_proof_digest,
        previous_reservation_digest=trusted.expected_reservation_digest,
        reservation=trusted.target_event.reservation,
        resource_operation=trusted,
    )
