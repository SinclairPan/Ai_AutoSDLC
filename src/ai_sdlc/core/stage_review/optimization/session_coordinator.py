"""StageReviewSession 创建、Snapshot 冻结与终态观测接线。"""

from __future__ import annotations

from collections.abc import Callable

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.binding_result_models import ReviewerBindingSet
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.contracts import TaskRiskProfile
from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBindingStore,
    ObservationKind,
    OptimizationObservationStore,
    TerminalObservationLineage,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    _build_terminal_observation as build_terminal_observation,
)
from ai_sdlc.core.stage_review.optimization.session_start_binding import (
    _binding_for,
    _binding_operation,
    _operation_token,
    _verify_recovered_operation,
    _verify_recovered_population,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import (
    SessionSnapshotBindingOperation,
    SnapshotControlEvent,
)
from ai_sdlc.core.stage_review.optimization.snapshots import SnapshotControlService
from ai_sdlc.core.stage_review.session_contracts import (
    SessionIntegrityError,
    SessionStartCommand,
    SessionTrustResolver,
)
from ai_sdlc.core.stage_review.session_models import StageReviewSession

_TERMINAL_OBSERVATIONS: dict[str, ObservationKind] = {
    "consumed": "consumed",
    "needs_user": "needs_user",
    "blocked": "blocked",
    "superseded": "superseded",
}
_RUNTIME_TERMINAL_OBSERVATIONS = frozenset(
    {
        "needs_user",
        "blocked",
        "crashed",
        "timed_out",
        "abandoned",
        "integrity_failure",
        "open_censored",
    }
)


class SessionOptimizationCoordinator:
    def __init__(
        self,
        *,
        snapshots: SnapshotControlService,
        resolver: SessionTrustResolver,
        binding_store: CommittedSessionBindingStore,
        observation_store: OptimizationObservationStore,
        candidate_size_classifier: Callable[[str], str],
        clock: Callable[[], str],
        trigger_refresher: Callable[[], object] | None = None,
        finding_event_source: Callable[[str], tuple[str, ...]] | None = None,
    ) -> None:
        self.snapshots = snapshots
        self.resolver = resolver
        self.binding_store = binding_store
        self.observation_store = observation_store
        self.candidate_size_classifier = candidate_size_classifier
        self.clock = clock
        self.trigger_refresher = trigger_refresher
        self.finding_event_source = finding_event_source

    def _ensure_start_binding(
        self,
        command: SessionStartCommand,
        *,
        recovery_required: bool,
    ) -> None:
        try:
            lineage = self.snapshots.session_binding_lineage(command.scope.session_id)
        except SharedStateIntegrityError as exc:
            raise SessionIntegrityError(
                "session optimization binding lineage is unavailable"
            ) from exc
        if lineage is None:
            if recovery_required:
                raise SessionIntegrityError(
                    "session optimization binding lineage is unavailable"
                )
            self.bind_start(command)
            return
        self._recover_start_binding(command, *lineage)

    def _recover_start_binding(
        self,
        command: SessionStartCommand,
        operation: SessionSnapshotBindingOperation,
        event: SnapshotControlEvent,
    ) -> None:
        profile, binding_set = self._resolve_start_inputs(command)
        expected_operation = _binding_operation(
            command,
            _operation_token(operation),
            profile,
            binding_set,
            candidate_size=self.candidate_size_classifier(command.candidate_digest),
            created_at=operation.created_at,
        )
        _verify_recovered_operation(command, operation, expected_operation)
        try:
            self.snapshots.recover_session_population(
                binding_store=self.binding_store,
                observation_store=self.observation_store,
            )
            binding = _binding_for(
                self.binding_store.read_all(),
                command.scope.session_id,
            )
            created = tuple(
                item
                for item in self.observation_store.read_session(
                    command.scope.session_id
                )
                if item.observation_kind == "created"
            )
        except SharedStateIntegrityError as exc:
            raise SessionIntegrityError(
                "session optimization binding lineage is unavailable"
            ) from exc
        _verify_recovered_population(
            operation,
            event,
            binding,
            created,
        )

    def bind_start(self, command: SessionStartCommand) -> None:
        token = self.snapshots.resolve_snapshot()
        if command.optimization_snapshot_digest != token.active_snapshot_digest:
            raise SessionIntegrityError("session start snapshot selection is stale")
        profile, binding_set = self._resolve_start_inputs(command)
        operation = _binding_operation(
            command,
            token,
            profile,
            binding_set,
            candidate_size=self.candidate_size_classifier(command.candidate_digest),
            created_at=self.clock(),
        )
        self.snapshots.bind_session(operation, token)
        self.snapshots.recover_session_population(
            binding_store=self.binding_store,
            observation_store=self.observation_store,
        )
        self._refresh_trigger()

    def _resolve_start_inputs(
        self,
        command: SessionStartCommand,
    ) -> tuple[TaskRiskProfile, ReviewerBindingSet]:
        profile = self.resolver.resolve_risk_profile(command.risk_profile_digest)
        binding_set = self.resolver.resolve_binding_set(command.binding_set_digest)
        if profile is None or binding_set is None:
            raise SessionIntegrityError("session optimization lineage is unavailable")
        if (
            profile.work_item_id != command.scope.work_item_id
            or binding_set.project_id != command.scope.project_id
            or binding_set.stage_review_session_id != command.scope.session_id
        ):
            raise SessionIntegrityError("session optimization scope diverged")
        return profile, binding_set

    def observe_session(self, session: StageReviewSession) -> None:
        observation_kind = _session_observation_kind(session)
        if observation_kind is None:
            return
        appended = self._append_terminal(
            session.scope.session_id,
            observation_kind,
            terminal_reason=observation_kind,
            minimum_sequence=session.revision,
            lineage=TerminalObservationLineage(
                binding_set_digest=session.active_binding_set_digest,
                risk_profile_digest=session.active_risk_profile_digest,
                cohort_id=session.active_cohort_id,
                finding_ledger_digest=session.finding_ledger_digest,
                convergence_outcome_digest=_convergence_digest(session),
                label_source_digests=_session_label_sources(session),
                resource_usage=session.resource_usage,
            ),
        )
        if appended:
            self._refresh_trigger()

    def observe_runtime_outcome(
        self,
        session_id: str,
        observation_kind: ObservationKind,
        *,
        terminal_reason: str,
        finding_event_digests: tuple[str, ...] = (),
    ) -> None:
        if observation_kind not in _RUNTIME_TERMINAL_OBSERVATIONS:
            raise ValueError("runtime observation kind is not externally recordable")
        appended = self._append_terminal(
            session_id,
            observation_kind,
            terminal_reason=terminal_reason,
            finding_event_digests=finding_event_digests,
        )
        if appended:
            self._refresh_trigger()

    def _append_terminal(
        self,
        session_id: str,
        observation_kind: ObservationKind,
        *,
        terminal_reason: str,
        finding_event_digests: tuple[str, ...] = (),
        minimum_sequence: int = 0,
        lineage: TerminalObservationLineage | None = None,
    ) -> bool:
        existing = self.observation_store.read_session(session_id)
        if any(item.observation_kind == observation_kind for item in existing):
            return False
        binding = _binding_for(self.binding_store.read_all(), session_id)
        source_digests = (
            self.finding_event_source(session_id)
            if self.finding_event_source is not None
            else ()
        )
        finding_event_digests = tuple(
            sorted(set((*source_digests, *finding_event_digests)))
        )
        sequence = max(
            binding.control_sequence + minimum_sequence,
            max((item.sequence for item in existing), default=0) + 1,
        )
        self.observation_store.append(
            build_terminal_observation(
                binding,
                observation_kind,
                sequence=sequence,
                occurred_at=self.clock(),
                terminal_reason=terminal_reason,
                finding_event_digests=finding_event_digests,
                lineage=lineage,
            )
        )
        return True

    def _refresh_trigger(self) -> None:
        if self.trigger_refresher is not None:
            try:
                self.trigger_refresher()
            except Exception:
                return


def _session_observation_kind(
    session: StageReviewSession,
) -> ObservationKind | None:
    if session.state == "needs_user" and session.budget_resume_state is not None:
        return "hard_budget_exhausted"
    if session.state == "blocked" and session.budget_grant_failure_code:
        return "integrity_failure"
    return _TERMINAL_OBSERVATIONS.get(session.state)


# Start binding 的构造与恢复校验必须先于人口物化，由专职模块统一执行。
def _convergence_digest(session: StageReviewSession) -> str:
    records = session.projection.progress_records
    return canonical_digest(records, CanonicalizationPolicy()) if records else ""


def _session_label_sources(session: StageReviewSession) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                session.head_event_digest,
                session.finding_ledger_digest,
                session.active_risk_profile_digest,
                session.active_plan_digest,
                session.active_binding_set_digest,
            }
            - {""}
        )
    )
