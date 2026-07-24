"""ResourceGovernor 可调用的 canonical Session 请求权威读取。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol, cast

from ai_sdlc.core.stage_review.artifacts import ShortFileLock
from ai_sdlc.core.stage_review.authority_binding_artifacts import (
    ensure_shared_state_binding,
)
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.session_budget_approval_models import (
    BudgetGrantApproval,
    BudgetGrantApprovalState,
)
from ai_sdlc.core.stage_review.session_budget_grant_authority_contracts import (
    BudgetGrantApprovalResolver,
)
from ai_sdlc.core.stage_review.session_budget_grant_request import (
    BudgetGrantRequestProof,
)
from ai_sdlc.core.stage_review.session_contracts import SessionIntegrityError
from ai_sdlc.core.stage_review.session_models import (
    SessionEvent,
    SessionOperation,
    StageReviewSession,
)

BudgetGrantApplyStatus = Literal["committed", "pending", "superseded"]


def ensure_shared_state_binding_id(shared_root: Path, project_id: str) -> str:
    binding = ensure_shared_state_binding(
        shared_root / "shared-state-binding.json",
        project_id,
    )
    return binding.binding_id


class BudgetGrantRequestStore(Protocol):
    project_id: str
    shared_state_binding_id: str

    def verify_budget_grant_request(self, proof: BudgetGrantRequestProof) -> None: ...

    def budget_grant_apply_status(
        self,
        proof: BudgetGrantRequestProof,
        apply_command_id: str,
    ) -> BudgetGrantApplyStatus: ...


class BudgetGrantRequestAuthority(BudgetGrantRequestStore, Protocol):
    authority_id: str

    def approval_state(
        self,
        proof: BudgetGrantRequestProof,
    ) -> BudgetGrantApprovalState: ...


class BoundBudgetGrantRequestAuthority:
    """绑定 canonical Session Store 与同一审批治理 Authority。"""

    def __init__(
        self,
        store: BudgetGrantRequestStore,
        approval_resolver: BudgetGrantApprovalResolver,
    ) -> None:
        self._store = store
        self._approval_resolver = approval_resolver
        self.project_id = store.project_id
        self.shared_state_binding_id = store.shared_state_binding_id
        self.authority_id = stable_id(
            "budget-grant-request-authority",
            store.project_id,
            store.shared_state_binding_id,
            approval_resolver.authority_id,
        )

    def verify_budget_grant_request(self, proof: BudgetGrantRequestProof) -> None:
        self._store.verify_budget_grant_request(proof)
        resolved = self._approval_resolver.resolve(proof.approval.approval_digest)
        state = self.approval_state(proof)
        if resolved != proof.approval or state.approval_digest != resolved.approval_digest:
            raise SessionIntegrityError("budget grant approval authority diverged")

    def approval_state(
        self,
        proof: BudgetGrantRequestProof,
    ) -> BudgetGrantApprovalState:
        state = self._approval_resolver.approval_state(
            proof.approval.approval_digest
        )
        if (
            state is None
            or state.authority_id != self._approval_resolver.authority_id
            or state.approval_digest != proof.approval.approval_digest
        ):
            raise SessionIntegrityError("budget grant approval state is missing")
        return state

    def budget_grant_apply_status(
        self,
        proof: BudgetGrantRequestProof,
        apply_command_id: str,
    ) -> BudgetGrantApplyStatus:
        return self._store.budget_grant_apply_status(
            proof,
            apply_command_id,
        )


def _canonical_budget_grant_authority(
    root: Path,
    project_id: str,
    resolver: BudgetGrantApprovalResolver | None,
    lock_timeout_seconds: float,
) -> BudgetGrantRequestAuthority | None:
    if resolver is None:
        return None
    from ai_sdlc.core.stage_review.session_store import SessionEventStore

    store = SessionEventStore(
        root,
        project_id=project_id,
        lock_timeout_seconds=lock_timeout_seconds,
    )
    return BoundBudgetGrantRequestAuthority(store, resolver)


class SessionBudgetGrantAuthorityStoreHost(Protocol):
    def _lock(self, scope: FindingScope) -> ShortFileLock: ...

    def get_operation(
        self,
        scope: FindingScope,
        command_id: str,
    ) -> SessionOperation | None: ...

    def get_budget_grant_approval(
        self,
        scope: FindingScope,
        approval_id: str,
    ) -> BudgetGrantApproval: ...

    def get_budget_grant_request_proof(
        self,
        proof: BudgetGrantRequestProof,
    ) -> BudgetGrantRequestProof: ...

    def load_events(self, scope: FindingScope) -> tuple[SessionEvent, ...]: ...

    def rebuild(self, scope: FindingScope) -> StageReviewSession | None: ...

    def operation_was_rejected(
        self,
        scope: FindingScope,
        command_id: str,
    ) -> bool: ...


class _SessionBudgetGrantAuthorityStoreMixin:

    def verify_budget_grant_request(self, proof: BudgetGrantRequestProof) -> None:
        host = cast(SessionBudgetGrantAuthorityStoreHost, self)
        scope = proof.approval.scope
        operation = host.get_operation(scope, proof.request_operation.command_id)
        approval = host.get_budget_grant_approval(scope, proof.approval.approval_id)
        persisted = host.get_budget_grant_request_proof(proof)
        events = tuple(
            event
            for event in host.load_events(scope)
            if event.command_id == proof.request_operation.command_id
            and event.event_kind == "budget_grant_requested"
        )
        valid = (
            operation == proof.request_operation,
            approval == proof.approval,
            persisted == proof,
            events == (proof.requested_event,),
            host.rebuild(scope) is not None,
        )
        if not all(valid):
            raise SessionIntegrityError("budget grant request is not authoritative")

    def budget_grant_apply_status(
        self,
        proof: BudgetGrantRequestProof,
        apply_command_id: str,
    ) -> BudgetGrantApplyStatus:
        host = cast(SessionBudgetGrantAuthorityStoreHost, self)
        scope = proof.approval.scope
        with host._lock(scope):
            self.verify_budget_grant_request(proof)
            events = host.load_events(scope)
            if any(
                event.command_id == apply_command_id
                and event.event_kind == "budget_grant_applied"
                for event in events
            ):
                return "committed"
            if apply_command_id and host.operation_was_rejected(
                scope, apply_command_id
            ):
                return "superseded"
            session = host.rebuild(scope)
            superseded = session is None or (
                session.revision != proof.requested_event.sequence
                or session.pending_budget_grant_command_id
                != proof.request_operation.command_id
            )
            return "superseded" if superseded else "pending"
