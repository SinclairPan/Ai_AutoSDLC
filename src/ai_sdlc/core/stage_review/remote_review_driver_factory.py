"""从持久化 Dispatch 与隔离布局构造远端 Reviewer Driver。"""

from __future__ import annotations

from pathlib import Path

from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.bindings import ReviewerBindingService
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.isolation_runtime_layout import AllocationPathResolver
from ai_sdlc.core.stage_review.provider_execution_registry import (
    FrozenProviderExecutionRegistry,
    ProviderExecutionUnavailableError,
)
from ai_sdlc.core.stage_review.provider_journal_driver import ProviderInvocationDriver
from ai_sdlc.core.stage_review.provider_journal_models import ProviderInvocationRequest
from ai_sdlc.core.stage_review.remote_review_driver import RemoteReviewDriver


class RemoteReviewDriverUnavailableError(RuntimeError):
    """当前 Binding 没有可证明的可信远端执行路径。"""


class RemoteReviewDriverFactory:
    def __init__(
        self,
        *,
        bindings: ReviewerBindingService,
        allocation_path_resolver: AllocationPathResolver,
        executions: FrozenProviderExecutionRegistry,
    ) -> None:
        self._bindings = bindings
        self._paths = allocation_path_resolver
        self._executions = executions

    def build(
        self,
        request: ProviderInvocationRequest,
        *,
        payload: dict[str, object],
        assignment: ReviewerDispatchAssignment,
    ) -> ProviderInvocationDriver:
        if request.assignment_digest != assignment.assignment_digest:
            raise ValueError("remote review assignment diverged")
        resolved = self._bindings.get_dispatch_isolation_context(
            assignment.assignment_digest
        )
        if resolved is None:
            raise RemoteReviewDriverUnavailableError(
                "remote review isolation context is unavailable"
            )
        allocation, peers, _evidence, _host = resolved
        try:
            execution = self._executions.resolve_reviewer(
                request, assignment, allocation
            )
        except ProviderExecutionUnavailableError as exc:
            raise RemoteReviewDriverUnavailableError(str(exc)) from exc
        if not execution.transport.remote_provider_available:
            raise RemoteReviewDriverUnavailableError(
                "remote review transport is unavailable"
            )
        layout = self._paths.resolve(
            allocation,
            peer_allocations=peers,
            assignment_digest=assignment.assignment_digest,
        )
        return RemoteReviewDriver(
            request,
            payload=payload,
            execution=execution,
            output_root=Path(layout.output_root),
            credential_view_digest=_credential_view_digest(
                allocation.disposable_credential_view_id
            ),
            layout_digest=layout.layout_digest,
        )


def _credential_view_digest(view_id: str) -> str:
    return canonical_digest(
        {"credential_view_id": view_id},
        CanonicalizationPolicy(),
    )


__all__ = ["RemoteReviewDriverFactory", "RemoteReviewDriverUnavailableError"]
