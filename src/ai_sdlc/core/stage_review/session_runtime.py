"""Session 各命令处理器共享的存储、事件和状态辅助。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ai_sdlc.core.stage_review.session_artifact_models import (
    ArtifactRef,
    ReviewCohort,
)
from ai_sdlc.core.stage_review.session_authority import SessionAuthority
from ai_sdlc.core.stage_review.session_builders import build_cohort, build_session_event
from ai_sdlc.core.stage_review.session_contracts import (
    FindingInitialBatchWriter,
    SessionCommand,
    SessionEventKind,
    SessionIntegrityError,
    SessionState,
    SessionTrustResolver,
)
from ai_sdlc.core.stage_review.session_models import (
    SessionEvent,
    SessionOperation,
    SessionProjectionData,
    StageReviewSession,
)
from ai_sdlc.core.stage_review.session_store import SessionEventStore

EventSpec = tuple[
    SessionEventKind,
    SessionProjectionData,
    tuple[ArtifactRef, ...],
]


@dataclass(frozen=True, slots=True)
class SessionRuntime:
    store: SessionEventStore
    resolver: SessionTrustResolver
    finding_ledger_writer: FindingInitialBatchWriter
    clock: Callable[[], str]

    def operation_kinds(
        self,
        command: SessionCommand,
        defaults: tuple[SessionEventKind, ...],
    ) -> tuple[SessionEventKind, ...]:
        operation = self.store.get_operation(command.scope, command.command_id)
        return operation.expected_event_kinds if operation is not None else defaults

    def events(
        self,
        base: StageReviewSession | None,
        operation: SessionOperation,
        specs: tuple[EventSpec, ...],
    ) -> tuple[SessionEvent, ...]:
        sequence = base.revision + 1 if base is not None else 1
        previous_id = base.head_event_id if base is not None else ""
        previous_digest = base.head_event_digest if base is not None else ""
        values: list[SessionEvent] = []
        for kind, projection, refs in specs:
            event = build_session_event(
                operation,
                kind=kind,
                sequence=sequence,
                previous_event_id=previous_id,
                previous_event_digest=previous_digest,
                projection=projection,
                artifact_refs=refs,
            )
            values.append(event)
            sequence += 1
            previous_id = event.event_id
            previous_digest = event.event_digest
        return tuple(values)

    def replacement_cohort(
        self,
        session: StageReviewSession,
        authority: SessionAuthority,
        *,
        candidate_digest: str,
        risk_profile_digest: str,
        activation_reason: str,
        created_at: str,
    ) -> ReviewCohort:
        return build_cohort(
            session.scope,
            authority,
            candidate_digest=candidate_digest,
            risk_profile_digest=risk_profile_digest,
            risk_profile_lineage_id=session.risk_profile_lineage_id,
            policy_digest=session.policy_digest,
            optimization_snapshot_digest=session.optimization_snapshot_digest,
            ordinal=len(session.cohort_refs) + 1,
            initial_pass_head_digest=session.head_event_digest,
            predecessor_cohort_id=session.active_cohort_id,
            activation_reason=activation_reason,
            created_at=created_at,
        )

    @staticmethod
    def require_base(base: StageReviewSession | None) -> StageReviewSession:
        if base is None:
            raise SessionIntegrityError("session does not exist")
        return base

    @staticmethod
    def require_active(session: StageReviewSession) -> None:
        inactive: set[SessionState] = {
            "needs_user",
            "blocked",
            "superseded",
            "consuming",
            "consumed",
        }
        if session.state in inactive:
            raise SessionIntegrityError("session is not active")
