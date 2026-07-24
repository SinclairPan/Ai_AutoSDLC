"""可信、版本化且绑定 canonical shared state 的关闭治理 Authority。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from pydantic import ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.close_governance_models import (
    StageCloseAbortRequest,
    StageCloseGovernanceAuthorityBinding,
    StageCloseGovernanceDecision,
)
from ai_sdlc.core.stage_review.close_models import CloseConsumptionClaim
from ai_sdlc.core.stage_review.close_recovery_models import (
    StageCloseRecoveryDecision,
    StageCloseRecoveryRequest,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.session_budget_grant_authority_store import (
    ensure_shared_state_binding_id,
)
from ai_sdlc.core.stage_review.session_models import StageReviewSession
from ai_sdlc.core.stage_review.transaction_artifact_codec import (
    decode_transaction_artifact,
)


class GovernanceDecisionInvalidError(SharedStateIntegrityError):
    """治理请求、Authority Binding 或持久化决策不可信。"""


class StageCloseGovernanceAuthority:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        authority_id: str,
        authorized_actor_ids: Iterable[str],
        clock: Callable[[], str],
    ) -> None:
        self.shared_root = resolve_canonical_shared_state(root, project_id)
        self.project_id = project_id
        self.shared_state_binding_id = ensure_shared_state_binding_id(
            self.shared_root,
            project_id,
        )
        self._clock = clock
        self._root = self.shared_root / "stage-close-governance"
        self._binding = StageCloseGovernanceAuthorityBinding(
            project_id=project_id,
            shared_state_binding_id=self.shared_state_binding_id,
            authority_id=authority_id,
            authorized_actor_ids=tuple(sorted(set(authorized_actor_ids))),
        )
        self._bind_authority()

    @property
    def authority_id(self) -> str:
        return self._binding.authority_id

    @property
    def authority_binding_digest(self) -> str:
        return self._binding.binding_digest

    def validate_request(self, request: StageCloseAbortRequest) -> None:
        trusted = StageCloseAbortRequest.model_validate(request)
        self._require_binding()
        if trusted.actor_id not in self._binding.authorized_actor_ids:
            raise GovernanceDecisionInvalidError(
                "stage close governance actor is not authorized"
            )

    def issue_abort(
        self,
        request: StageCloseAbortRequest,
        claim: CloseConsumptionClaim,
    ) -> StageCloseGovernanceDecision:
        trusted = StageCloseAbortRequest.model_validate(request)
        self.validate_request(trusted)
        path = self._decision_path(claim)
        existing = self._read_decision(path) if path.exists() else None
        decided_at = existing.decided_at if existing is not None else self._clock()
        decision = self._build_decision(trusted, claim, decided_at)
        if existing is None and create_json_exclusive(
            path,
            decision.model_dump(mode="json"),
        ):
            return decision
        persisted = self._read_decision(path)
        expected = self._build_decision(trusted, claim, persisted.decided_at)
        if persisted != expected:
            raise GovernanceDecisionInvalidError(
                "stage close governance decision is already bound"
            )
        return persisted

    def find_abort(
        self,
        claim: CloseConsumptionClaim,
    ) -> StageCloseGovernanceDecision | None:
        path = self._decision_path(claim)
        if not path.exists():
            return None
        decision = self._read_decision(path)
        return self.require_abort(claim, decision.decision_digest)

    def require_abort(
        self,
        claim: CloseConsumptionClaim,
        decision_digest: str,
    ) -> StageCloseGovernanceDecision:
        self._require_binding()
        decision = self._read_decision(self._decision_path(claim))
        checks = (
            decision.compatibility_mode == "strict",
            decision.decision_kind == "abort_stage_close",
            decision.scope == claim.scope,
            decision.claim_id == claim.claim_id,
            decision.claim_digest == claim.claim_digest,
            decision.certificate_id == claim.certificate_id,
            decision.certificate_digest == claim.certificate_digest,
            decision.command_id == claim.command_id,
            decision.authority_id == self.authority_id,
            decision.authority_binding_digest == self.authority_binding_digest,
            decision.decision_digest == decision_digest,
        )
        if not all(checks):
            raise GovernanceDecisionInvalidError(
                "stage close governance decision lineage is invalid"
            )
        return decision

    def issue_recovery(
        self,
        request: StageCloseRecoveryRequest,
        claim: CloseConsumptionClaim,
        session: StageReviewSession,
    ) -> StageCloseRecoveryDecision:
        trusted = StageCloseRecoveryRequest.model_validate(request)
        self._validate_recovery_request(trusted, claim)
        path = self._recovery_decision_path(claim)
        existing = self._read_recovery_decision(path) if path.exists() else None
        if existing is None:
            _require_aborted_session(session, claim)
        decided_at = existing.decided_at if existing is not None else self._clock()
        revision = (
            existing.aborted_session_revision
            if existing is not None
            else session.revision
        )
        session_digest = (
            existing.aborted_session_digest
            if existing is not None
            else session.session_digest
        )
        decision = self._build_recovery_decision(
            trusted,
            claim,
            decided_at,
            revision,
            session_digest,
        )
        if existing is None and create_json_exclusive(
            path,
            decision.model_dump(mode="json"),
        ):
            return decision
        persisted = self._read_recovery_decision(path)
        expected = self._build_recovery_decision(
            trusted,
            claim,
            persisted.decided_at,
            persisted.aborted_session_revision,
            persisted.aborted_session_digest,
        )
        if persisted != expected:
            raise GovernanceDecisionInvalidError(
                "stage close recovery decision is already bound"
            )
        return persisted

    def require_recovery(
        self,
        claim: CloseConsumptionClaim,
        decision: StageCloseRecoveryDecision,
    ) -> StageCloseRecoveryDecision:
        self._require_binding()
        trusted = StageCloseRecoveryDecision.model_validate(decision)
        persisted = self._read_recovery_decision(
            self._recovery_decision_path(claim)
        )
        checks = (
            trusted.compatibility_mode == "strict",
            trusted == persisted,
            trusted.scope == claim.scope,
            trusted.aborted_claim_id == claim.claim_id,
            trusted.aborted_claim_digest == claim.claim_digest,
            trusted.aborted_certificate_id == claim.certificate_id,
            trusted.aborted_certificate_digest == claim.certificate_digest,
            trusted.authority_id == self.authority_id,
            trusted.authority_binding_digest == self.authority_binding_digest,
        )
        if not all(checks):
            raise GovernanceDecisionInvalidError(
                "stage close recovery decision lineage is invalid"
            )
        return persisted

    def _build_decision(
        self,
        request: StageCloseAbortRequest,
        claim: CloseConsumptionClaim,
        decided_at: str,
    ) -> StageCloseGovernanceDecision:
        return StageCloseGovernanceDecision(
            decision_id=stable_id(
                "stage-close-governance-decision",
                claim.claim_digest,
            ),
            scope=claim.scope,
            claim_id=claim.claim_id,
            claim_digest=claim.claim_digest,
            certificate_id=claim.certificate_id,
            certificate_digest=claim.certificate_digest,
            command_id=claim.command_id,
            actor_id=request.actor_id,
            idempotency_key=request.idempotency_key,
            reason_code=request.reason_code,
            reason=request.reason,
            authority_id=self.authority_id,
            authority_binding_digest=self.authority_binding_digest,
            decided_at=decided_at,
        )

    def _validate_recovery_request(
        self,
        request: StageCloseRecoveryRequest,
        claim: CloseConsumptionClaim,
    ) -> None:
        del claim
        self._require_binding()
        if request.actor_id not in self._binding.authorized_actor_ids:
            raise GovernanceDecisionInvalidError(
                "stage close recovery actor is not authorized"
            )

    def _build_recovery_decision(
        self,
        request: StageCloseRecoveryRequest,
        claim: CloseConsumptionClaim,
        decided_at: str,
        aborted_session_revision: int,
        aborted_session_digest: str,
    ) -> StageCloseRecoveryDecision:
        return StageCloseRecoveryDecision(
            decision_id=stable_id(
                "stage-close-recovery-decision",
                claim.claim_digest,
            ),
            scope=claim.scope,
            aborted_claim_id=claim.claim_id,
            aborted_claim_digest=claim.claim_digest,
            aborted_certificate_id=claim.certificate_id,
            aborted_certificate_digest=claim.certificate_digest,
            aborted_session_revision=aborted_session_revision,
            aborted_session_digest=aborted_session_digest,
            recovery_kind=request.recovery_kind,
            new_command_id=request.new_command_id,
            actor_id=request.actor_id,
            idempotency_key=request.idempotency_key,
            reason_code=request.reason_code,
            reason=request.reason,
            authority_id=self.authority_id,
            authority_binding_digest=self.authority_binding_digest,
            decided_at=decided_at,
        )

    def _bind_authority(self) -> None:
        bind_repository_project(self.shared_root, self.project_id)
        path = self._root / "authority-binding.json"
        if create_json_exclusive(path, self._binding.model_dump(mode="json")):
            return
        if self._read_binding(path) != self._binding:
            raise GovernanceDecisionInvalidError(
                "stage close governance authority binding changed"
            )

    def _require_binding(self) -> None:
        if self._read_binding(self._root / "authority-binding.json") != self._binding:
            raise GovernanceDecisionInvalidError(
                "stage close governance authority binding changed"
            )

    def _decision_path(self, claim: CloseConsumptionClaim) -> Path:
        decision_id = stable_id(
            "stage-close-governance-decision",
            claim.claim_digest,
        )
        return self._root / "decisions" / f"{decision_id}.json"

    def _recovery_decision_path(self, claim: CloseConsumptionClaim) -> Path:
        decision_id = stable_id(
            "stage-close-recovery-decision",
            claim.claim_digest,
        )
        return self._root / "recovery-decisions" / f"{decision_id}.json"

    @staticmethod
    def _read_binding(path: Path) -> StageCloseGovernanceAuthorityBinding:
        try:
            return decode_transaction_artifact(
                StageCloseGovernanceAuthorityBinding,
                read_json_object(path),
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise GovernanceDecisionInvalidError(
                "stage close governance authority binding is invalid"
            ) from exc

    @staticmethod
    def _read_decision(path: Path) -> StageCloseGovernanceDecision:
        try:
            return decode_transaction_artifact(
                StageCloseGovernanceDecision,
                read_json_object(path),
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise GovernanceDecisionInvalidError(
                "stage close governance decision artifact is invalid"
            ) from exc

    @staticmethod
    def _read_recovery_decision(path: Path) -> StageCloseRecoveryDecision:
        try:
            return decode_transaction_artifact(
                StageCloseRecoveryDecision,
                read_json_object(path),
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise GovernanceDecisionInvalidError(
                "stage close recovery decision artifact is invalid"
            ) from exc


def _require_aborted_session(
    session: StageReviewSession,
    claim: CloseConsumptionClaim,
) -> None:
    projection = session.projection
    checks = (
        session.scope == claim.scope,
        session.state == "needs_user",
        projection.close_failure_reason == "governed_close_abort",
        projection.active_close_certificate_id == claim.certificate_id,
        projection.active_close_certificate_digest == claim.certificate_digest,
        projection.active_close_claim_id == claim.claim_id,
        projection.active_close_claim_digest == claim.claim_digest,
    )
    if not all(checks):
        raise GovernanceDecisionInvalidError(
            "stage close recovery requires the canonical aborted session"
        )
