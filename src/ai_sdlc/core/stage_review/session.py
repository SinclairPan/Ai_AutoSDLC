"""LoopRound 内唯一 StageReviewSession 编排门面。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.optimization.observations import ObservationKind
from ai_sdlc.core.stage_review.optimization.session_coordinator import (
    SessionOptimizationCoordinator,
)
from ai_sdlc.core.stage_review.resource_builders import utc_iso
from ai_sdlc.core.stage_review.resource_runtime import utc_now
from ai_sdlc.core.stage_review.session_artifact_models import (
    CoverageDeclaration,
    ReviewCohort,
    ReviewerPlanRevocation,
    ReviewPass,
)
from ai_sdlc.core.stage_review.session_budget_grant_authority_contracts import (
    BudgetGrantApprovalResolver,
)
from ai_sdlc.core.stage_review.session_budget_grant_authority_store import (
    ensure_shared_state_binding_id,
)
from ai_sdlc.core.stage_review.session_budget_grant_coordinator_contracts import (
    SessionBudgetGrantCoordinator,
)
from ai_sdlc.core.stage_review.session_budget_grants import SessionBudgetGrantOps
from ai_sdlc.core.stage_review.session_builders import review_submission_digest
from ai_sdlc.core.stage_review.session_certificate_inputs import (
    _SessionCertificateInputsMixin as SessionCertificateInputsMixin,
)
from ai_sdlc.core.stage_review.session_change_authority import (
    capability_gap,
    resolve_risk_profile,
)
from ai_sdlc.core.stage_review.session_close_authorities import (
    SessionCloseAbortAuthority,
    SessionCloseStartAuthority,
)
from ai_sdlc.core.stage_review.session_close_ops import SessionCloseOps
from ai_sdlc.core.stage_review.session_contracts import (
    BudgetGrantApplyCommand,
    BudgetGrantFailureCommand,
    BudgetGrantReconcileCommand,
    BudgetGrantRequestCommand,
    CandidateUpdateCommand,
    CloseConsumptionStartCommand,
    CloseReceiptCommitCommand,
    FindingInitialBatchWriter,
    GovernedCloseAbortCommand,
    MacroRebaselineCommand,
    PlanRevocationCommand,
    ProgressCommand,
    ProviderRebindCommand,
    RiskEnrichmentCommand,
    RoleGapCommand,
    SessionCasConflictError,
    SessionIntegrityError,
    SessionStartCommand,
    SessionTrustResolver,
    SubmitReviewPassCommand,
)
from ai_sdlc.core.stage_review.session_governance_ops import SessionGovernanceOps
from ai_sdlc.core.stage_review.session_models import (
    SessionEvent,
    SessionMutationResult,
    StageReviewSession,
)
from ai_sdlc.core.stage_review.session_pending_recovery import (
    _resume_pending_session as resume_pending_session,
)
from ai_sdlc.core.stage_review.session_replacement_ops import SessionReplacementOps
from ai_sdlc.core.stage_review.session_review_ops import SessionReviewOps
from ai_sdlc.core.stage_review.session_role_gap_ops import SessionRoleGapOps
from ai_sdlc.core.stage_review.session_runtime import SessionRuntime
from ai_sdlc.core.stage_review.session_store import SessionEventStore

__all__ = [
    "BudgetGrantRequestCommand", "CandidateUpdateCommand", "CoverageDeclaration",
    "MacroRebaselineCommand", "PlanRevocationCommand", "ProgressCommand",
    "ProviderRebindCommand", "ReviewerPlanRevocation", "RiskEnrichmentCommand",
    "RoleGapCommand", "SessionCasConflictError", "SessionIntegrityError",
    "SessionStartCommand", "StageReviewSessionService", "SubmitReviewPassCommand",
    "review_submission_digest",
]


class StageReviewSessionService(SessionCertificateInputsMixin):
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        trust_resolver: SessionTrustResolver,
        finding_ledger_writer: FindingInitialBatchWriter,
        budget_grant_coordinator: SessionBudgetGrantCoordinator | None = None,
        budget_grant_approval_resolver: BudgetGrantApprovalResolver | None = None,
        optimization_coordinator: SessionOptimizationCoordinator | None = None,
        clock: Callable[[], str] | None = None,
        lock_timeout_seconds: float = 2,
    ) -> None:
        store = SessionEventStore(
            root,
            project_id=project_id,
            lock_timeout_seconds=lock_timeout_seconds,
            session_observer=(
                None
                if optimization_coordinator is None
                else optimization_coordinator.observe_session
            ),
        )
        runtime = SessionRuntime(
            store=store,
            resolver=trust_resolver,
            finding_ledger_writer=finding_ledger_writer,
            clock=clock or (lambda: utc_iso(utc_now(None))),
        )
        self._runtime = runtime
        self._store = store
        self._review = SessionReviewOps(runtime)
        self._governance = SessionGovernanceOps(runtime)
        self._replacement = SessionReplacementOps(runtime)
        self._role_gap = SessionRoleGapOps(runtime)
        self._close = SessionCloseOps(runtime)
        self._budget = SessionBudgetGrantOps(
            runtime,
            budget_grant_coordinator,
            budget_grant_approval_resolver,
        )
        self._optimization = optimization_coordinator

    def start(self, command: SessionStartCommand) -> SessionMutationResult:
        if self._optimization is not None:
            self._optimization.bind_start(command)
        self._resume_pending(command.scope, command.command_id)
        return self._review.start(command)

    def observe_optimization_outcome(
        self,
        scope: FindingScope,
        observation_kind: ObservationKind,
        *,
        terminal_reason: str,
        finding_event_digests: tuple[str, ...] = (),
    ) -> None:
        if self._optimization is None:
            return
        self._optimization.observe_runtime_outcome(
            scope.session_id,
            observation_kind,
            terminal_reason=terminal_reason,
            finding_event_digests=finding_event_digests,
        )

    def submit_pass(
        self,
        command: SubmitReviewPassCommand,
    ) -> SessionMutationResult:
        self._resume_pending(command.scope, command.command_id)
        return self._review.submit_pass(command)

    def record_progress(self, command: ProgressCommand) -> SessionMutationResult:
        self._resume_pending(command.scope, command.command_id)
        return self._review.record_progress(command)

    def update_candidate(
        self,
        command: CandidateUpdateCommand,
    ) -> SessionMutationResult:
        self._resume_pending(command.scope, command.command_id)
        return self._replacement.update_candidate(command)

    def handle_role_gap(self, command: RoleGapCommand) -> SessionMutationResult:
        self._resume_pending(command.scope, command.command_id)
        return self._role_gap.handle(command)

    def rebind_provider(
        self,
        command: ProviderRebindCommand,
    ) -> SessionMutationResult:
        self._resume_pending(command.scope, command.command_id)
        return self._replacement.rebind_provider(command)

    def enrich_risk(
        self,
        command: RiskEnrichmentCommand,
    ) -> SessionMutationResult:
        self._resume_pending(command.scope, command.command_id)
        return self._enrich_risk(command)

    def _enrich_risk(self, command: RiskEnrichmentCommand) -> SessionMutationResult:
        completed = self._store.completed_session(command)
        if completed is not None:
            return SessionMutationResult(session=completed, idempotent_replay=True)
        operation = self._store.get_operation(command.scope, command.command_id)
        current = self._require_session(command.scope)
        profile = resolve_risk_profile(
            self._runtime.resolver,
            current,
            command.risk_profile_digest,
        )
        if (
            operation is None
            and profile.profile_digest == current.active_risk_profile_digest
        ):
            raise SessionIntegrityError("risk enrichment requires a new risk profile")
        previous = resolve_risk_profile(
            self._runtime.resolver,
            current,
            current.active_risk_profile_digest,
        )
        if not set(previous.required_capability_ids) <= set(
            profile.required_capability_ids
        ):
            raise SessionIntegrityError(
                "risk enrichment cannot remove required capability"
            )
        if command.macro_change_kind is not None or command.evidence_digest:
            return self._macro_risk(command)
        if (
            operation is not None
            and "role_gap_detected" in operation.expected_event_kinds
        ):
            missing = current.pending_role_gap_capability_ids or capability_gap(
                self._runtime.resolver,
                current,
                tuple(profile.required_capability_ids),
            )
            return self._role_gap.handle_enrichment(command, missing)
        missing = capability_gap(
            self._runtime.resolver,
            current,
            tuple(profile.required_capability_ids),
        )
        if missing:
            return self._role_gap.handle_enrichment(command, missing)
        return self._replacement.enrich_covered(command)

    def _macro_risk(self, command: RiskEnrichmentCommand) -> SessionMutationResult:
        if command.macro_change_kind is None or not command.evidence_digest:
            raise ValueError("macro risk enrichment requires change kind and evidence")
        trusted = self._runtime.resolver.macro_evidence_is_trusted(
            command.risk_profile_digest,
            command.macro_change_kind,
            command.evidence_digest,
        )
        if not trusted:
            raise SessionIntegrityError("macro risk evidence is not trusted")
        return self._governance.request_macro(
            command,
            change_kind=command.macro_change_kind,
            evidence_digest=command.evidence_digest,
        )

    def request_macro_rebaseline(
        self,
        command: MacroRebaselineCommand,
    ) -> SessionMutationResult:
        self._resume_pending(command.scope, command.command_id)
        return self._governance.request_macro_command(command)

    def revoke_plan(self, command: PlanRevocationCommand) -> SessionMutationResult:
        self._resume_pending(command.scope, command.command_id)
        return self._governance.revoke_plan(command)

    def extend_budget(
        self,
        command: BudgetGrantRequestCommand,
    ) -> SessionMutationResult:
        self._resume_pending(command.scope, command.command_id)
        return self._budget.extend(command)

    def bind_close_authority(self, authority: SessionCloseStartAuthority) -> None:
        self._close.bind_authority(authority)

    def bind_close_abort_authority(self, authority: SessionCloseAbortAuthority) -> None:
        self._close.bind_abort_authority(authority)

    def begin_close(
        self,
        command: CloseConsumptionStartCommand,
    ) -> SessionMutationResult:
        self._resume_pending(command.scope, command.command_id)
        return self._close.start(command)

    def commit_close(self, command: CloseReceiptCommitCommand) -> SessionMutationResult:
        self._resume_pending(command.scope, command.command_id)
        return self._close.commit(command)

    def abort_close(
        self,
        command: GovernedCloseAbortCommand,
    ) -> SessionMutationResult:
        self._resume_pending(command.scope, command.command_id)
        return self._close.abort(command)

    def get(self, scope: FindingScope) -> StageReviewSession:
        self._resume_pending(scope)
        return self._require_session(scope)

    @property
    def project_id(self) -> str:
        return self._store.project_id

    @property
    def shared_state_root(self) -> Path:
        return self._store.shared_root

    @property
    def shared_state_binding_id(self) -> str:
        return self._store.shared_state_binding_id

    def require_shared_state_binding(self) -> str:
        current = ensure_shared_state_binding_id(
            self._store.shared_root,
            self._store.project_id,
        )
        if current != self._store.shared_state_binding_id:
            raise SessionIntegrityError("session shared state binding changed")
        return current

    def maybe_get(self, scope: FindingScope) -> StageReviewSession | None:
        self._resume_pending(scope)
        return self._store.rebuild(scope)

    def events(self, scope: FindingScope) -> tuple[SessionEvent, ...]:
        self._resume_pending(scope)
        return self._store.load_events(scope)

    def active_cohort(self, scope: FindingScope) -> ReviewCohort:
        session = self.get(scope)
        return self._store.get_cohort(scope, session.active_cohort_id)

    def visible_passes(
        self,
        scope: FindingScope,
        cohort_id: str,
        requesting_slot_id: str,
    ) -> tuple[ReviewPass, ...]:
        session = self.get(scope)
        refs = tuple(item for item in session.pass_refs if item.cohort_id == cohort_id)
        if cohort_id not in session.sealed_cohort_ids:
            refs = tuple(item for item in refs if item.slot_id == requesting_slot_id)
        values = (self._store.get_pass(scope, item.pass_id) for item in refs)
        return tuple(sorted(values, key=lambda item: item.slot_id))

    def projection_path(self, scope: FindingScope) -> Path:
        return self._store.projection_path(scope)

    def _resume_pending(self, scope: FindingScope, incoming_id: str = "") -> None:
        resume_pending_session(
            self._store, self._budget, self._dispatch, scope, incoming_id
        )

    def _dispatch(self, command: object) -> SessionMutationResult:
        close_result = self._close.dispatch(command)
        if close_result is not None:
            return close_result
        if isinstance(command, SessionStartCommand):
            return self._review.start(command)
        if isinstance(command, SubmitReviewPassCommand):
            return self._review.submit_pass(command)
        if isinstance(command, ProgressCommand):
            return self._review.record_progress(command)
        if isinstance(command, CandidateUpdateCommand):
            return self._replacement.update_candidate(command)
        if isinstance(command, RoleGapCommand):
            return self._role_gap.handle(command)
        if isinstance(command, ProviderRebindCommand):
            return self._replacement.rebind_provider(command)
        if isinstance(command, RiskEnrichmentCommand):
            return self._enrich_risk(command)
        if isinstance(command, MacroRebaselineCommand):
            return self._governance.request_macro_command(command)
        if isinstance(command, PlanRevocationCommand):
            return self._governance.revoke_plan(command)
        if isinstance(command, BudgetGrantApplyCommand):
            return self._budget.resume_apply(command)
        if isinstance(command, BudgetGrantReconcileCommand):
            return self._budget.reconcile(command)
        if isinstance(command, BudgetGrantFailureCommand):
            return self._budget.fail(command)
        if isinstance(command, BudgetGrantRequestCommand):
            return self._budget.extend(command)
        raise SessionIntegrityError("session operation command cannot be resumed")

    def _require_session(self, scope: FindingScope) -> StageReviewSession:
        if (session := self._store.rebuild(scope)) is None:
            raise KeyError(scope.session_id)
        return session
