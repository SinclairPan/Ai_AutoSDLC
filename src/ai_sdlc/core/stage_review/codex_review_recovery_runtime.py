"""用已持久化证据恢复 Codex Review，不重新接触 Provider 运行时。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from ai_sdlc.core.stage_review.binding_invocations import ReviewerInvocationCoordinator
from ai_sdlc.core.stage_review.binding_models import (
    BindingAuthoritySnapshot,
    HostCapabilitySnapshot,
    IsolationExecutionEvidence,
    ReviewerRuntimeAllocation,
)
from ai_sdlc.core.stage_review.binding_result_models import ReviewerDispatchAssignment
from ai_sdlc.core.stage_review.binding_store import BindingArtifactStore
from ai_sdlc.core.stage_review.bindings import ReviewerBindingService
from ai_sdlc.core.stage_review.candidate import candidate_binding_digest
from ai_sdlc.core.stage_review.canonical_stage_review_executor import (
    CanonicalStageReviewExecutor,
)
from ai_sdlc.core.stage_review.canonical_stage_review_support import execution_scope
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan
from ai_sdlc.core.stage_review.provider_journal import ProviderInvocationJournal
from ai_sdlc.core.stage_review.provider_journal_driver import ProviderInvocationDriver
from ai_sdlc.core.stage_review.provider_journal_models import ProviderInvocationRequest
from ai_sdlc.core.stage_review.session import (
    SessionIntegrityError,
    StageReviewSessionService,
)
from ai_sdlc.core.stage_review.session_store import SessionEventStore
from ai_sdlc.core.stage_review.stage_review_execution import StageReviewExecutionRequest


class _PersistedAuthorityResolver:
    def __init__(self, authority: BindingAuthoritySnapshot) -> None:
        self._authority = authority

    def resolve(self, plan: ReviewerPanelPlan) -> BindingAuthoritySnapshot:
        if plan.plan_digest != self._authority.plan_digest:
            raise SessionIntegrityError("recovery binding authority plan diverged")
        return self._authority


class _RecoveryOnlyBoundary:
    def probe(self, previous_snapshot_digest: str = "") -> HostCapabilitySnapshot:
        del previous_snapshot_digest
        raise SessionIntegrityError("recovery attempted a live host probe")

    def allocate(
        self,
        operation_id: str,
        plan: ReviewerPanelPlan,
        authority: BindingAuthoritySnapshot,
    ) -> tuple[ReviewerRuntimeAllocation, ...]:
        del operation_id, plan, authority
        raise SessionIntegrityError("recovery attempted a live runtime allocation")

    def prepare(
        self,
        operation_id: str,
        allocations: tuple[ReviewerRuntimeAllocation, ...],
        host_snapshot: HostCapabilitySnapshot,
        visibility_barrier_id: str,
    ) -> tuple[IsolationExecutionEvidence, ...]:
        del operation_id, allocations, host_snapshot, visibility_barrier_id
        raise SessionIntegrityError("recovery attempted new isolation evidence")


class _RecoveryOnlyDriverFactory:
    def build(
        self,
        request: ProviderInvocationRequest,
        *,
        payload: dict[str, object],
        assignment: ReviewerDispatchAssignment,
    ) -> ProviderInvocationDriver:
        del request, payload, assignment
        raise SessionIntegrityError("recovery attempted a Provider invocation")


def _build_codex_recovery_executor(
    root: Path,
    request: StageReviewExecutionRequest,
    *,
    on_authorized: Callable[[StageReviewSessionService], None] | None = None,
) -> CanonicalStageReviewExecutor:
    authority = _recover_binding_authority(root, request)
    boundary = _RecoveryOnlyBoundary()
    bindings = ReviewerBindingService(
        root,
        project_id=request.candidate.project_id,
        resource_governor=request.governor,
        authority_resolver=_PersistedAuthorityResolver(authority),
        host_probe=boundary,
        runtime_broker=boundary,
        isolation_adapter=boundary,
    )
    journal = ProviderInvocationJournal(
        root,
        project_id=request.candidate.project_id,
        resource_governor=request.governor,
    )
    return CanonicalStageReviewExecutor(
        root,
        bindings=bindings,
        binding_authority=authority,
        journal=journal,
        invocations=ReviewerInvocationCoordinator(bindings, journal),
        drivers=_RecoveryOnlyDriverFactory(),
        clock=lambda: datetime.now(UTC),
        on_authorized=on_authorized,
    )


def _recover_binding_authority(
    root: Path,
    request: StageReviewExecutionRequest,
) -> BindingAuthoritySnapshot:
    session = SessionEventStore(
        root,
        project_id=request.candidate.project_id,
    ).rebuild(execution_scope(request))
    if session is None or session.state not in {"consuming", "consumed"}:
        raise SessionIntegrityError("recoverable review session is unavailable")
    store = BindingArtifactStore(
        root,
        project_id=request.candidate.project_id,
        lock_timeout_seconds=2,
    )
    binding = store.find_binding_set_by_digest(session.active_binding_set_digest)
    if binding is None:
        raise SessionIntegrityError("recovery binding set is unavailable")
    operation = store.get_operation(binding.attempt_operation_id)
    if operation is None:
        raise SessionIntegrityError("recovery binding operation is unavailable")
    authority = operation.authority_snapshot
    if not all(
        (
            binding.project_id == request.candidate.project_id,
            binding.work_item_id == request.candidate.work_item_id,
            binding.stage_review_session_id == request.candidate.review_session_id,
            binding.candidate_manifest_digest
            == candidate_binding_digest(request.candidate),
            binding.plan_digest == request.plan.plan_digest,
            binding.attempt_operation_digest == operation.operation_digest,
            binding.authority_snapshot_digest == authority.snapshot_digest,
            authority.plan_digest == request.plan.plan_digest,
        )
    ):
        raise SessionIntegrityError("recovery binding lineage diverged")
    return authority
