"""Session 启动与优化快照冻结恢复编排。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.optimization.session_coordinator import (
    SessionOptimizationCoordinator,
)
from ai_sdlc.core.stage_review.session_contracts import SessionStartCommand
from ai_sdlc.core.stage_review.session_models import SessionMutationResult
from ai_sdlc.core.stage_review.session_review_ops import SessionReviewOps
from ai_sdlc.core.stage_review.session_store import SessionEventStore


class _SessionStartRuntimeMixin:
    _store: SessionEventStore
    _review: SessionReviewOps
    _optimization: SessionOptimizationCoordinator | None

    def start(self, command: SessionStartCommand) -> SessionMutationResult:
        self._resume_pending(command.scope, command.command_id)
        start_operation = self._store.get_operation(
            command.scope,
            command.command_id,
        )
        if start_operation is not None:
            self._store.is_operation_complete(command, ("session_started",))
        if self._optimization is not None:
            # 新 Session 绑定当前快照；恢复路径必须证明完整的历史冻结链。
            self._optimization._ensure_start_binding(
                command,
                recovery_required=start_operation is not None,
            )
        return self._review.start(command)

    def _resume_pending(self, scope: FindingScope, incoming_id: str = "") -> None:
        raise NotImplementedError
