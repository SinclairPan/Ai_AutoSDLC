"""StageReviewSession 创建、Snapshot 冻结与终态观测接线。"""

from __future__ import annotations

from collections.abc import Callable

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.binding_result_models import ReviewerBindingSet
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.contracts import TaskRiskProfile
from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBinding,
    CommittedSessionBindingStore,
    ObservationKind,
    OptimizationObservationStore,
    TerminalObservationLineage,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    _build_terminal_observation as build_terminal_observation,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import (
    SessionSnapshotBindingOperation,
    SnapshotSelectionToken,
)
from ai_sdlc.core.stage_review.optimization.snapshots import SnapshotControlService
from ai_sdlc.core.stage_review.resource_builders import stable_id
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

    def bind_start(self, command: SessionStartCommand) -> None:
        token = self.snapshots.resolve_snapshot()
        if command.optimization_snapshot_digest != token.active_snapshot_digest:
            raise SessionIntegrityError("session start snapshot selection is stale")
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


def _binding_operation(
    command: SessionStartCommand,
    token: SnapshotSelectionToken,
    profile: TaskRiskProfile,
    binding_set: ReviewerBindingSet,
    *,
    candidate_size: str,
    created_at: str,
) -> SessionSnapshotBindingOperation:
    bindings = binding_set.bindings
    capabilities = {
        capability for item in bindings for capability in item.capability_ids
    }
    return SessionSnapshotBindingOperation(
        operation_id=stable_id(
            "session-snapshot-binding", command.scope.project_id, command.scope.session_id
        ),
        project_id=command.scope.project_id,
        session_id=command.scope.session_id,
        initial_candidate_digest=command.candidate_digest,
        stage_key=profile.stage_key,
        risk_level=profile.risk_level,
        candidate_size_bucket=candidate_size,
        provider_ids=tuple(sorted({item.provider_id for item in bindings})),
        binding_set_digest=binding_set.binding_set_digest,
        role_profile_ids=tuple(sorted({item.role_profile_id for item in bindings})),
        reviewer_slot_ids=tuple(sorted({item.slot_id for item in bindings})),
        capability_ids=tuple(sorted(capabilities)),
        binding_digests=tuple(sorted({item.binding_digest for item in bindings})),
        resource_reservation_digest=binding_set.final_reservation_digest,
        risk_profile_digest=profile.profile_digest,
        created_at=created_at,
        target_snapshot_digest=token.active_snapshot_digest,
        expected_head_sequence=token.head_sequence,
        expected_head_digest=token.head_digest,
        expected_pointer_revision=token.pointer_revision,
        expected_revocation_generation=token.revocation_generation,
    )


def _binding_for(
    bindings: tuple[CommittedSessionBinding, ...],
    session_id: str,
) -> CommittedSessionBinding:
    matches = tuple(item for item in bindings if item.session_id == session_id)
    if len(matches) != 1:
        raise SharedStateIntegrityError("session optimization binding is unavailable")
    return matches[0]


def _convergence_digest(session: StageReviewSession) -> str:
    records = session.projection.progress_records
    return (
        canonical_digest(records, CanonicalizationPolicy()) if records else ""
    )


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
