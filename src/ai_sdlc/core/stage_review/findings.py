from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeAlias

from ai_sdlc.core.stage_review import finding_lineage
from ai_sdlc.core.stage_review.activation_fence import (
    activation_safety_mutation_fence,
)
from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.finding_authorization import FindingAuthorizer
from ai_sdlc.core.stage_review.finding_command_models import (
    FindingAppendCommand,
    FindingInitialBatchCommand,
    FindingInitialDraft,
    FindingLineageAdvanceCommand,
)
from ai_sdlc.core.stage_review.finding_digests import (
    command_digest,
    initial_finding_batch_digest,
    scope_digest,
    stable_finding_id,
)
from ai_sdlc.core.stage_review.finding_event_builders import (
    build_initial_event,
    build_regular_event,
    build_seal_event,
)
from ai_sdlc.core.stage_review.finding_identity import FindingIdentityResolver
from ai_sdlc.core.stage_review.finding_mapping import require_trusted_mapping
from ai_sdlc.core.stage_review.finding_models import (
    FindingAppendResult,
    FindingEvent,
    FindingIdentityInput,
    FindingIdentityMapping,
    FindingLedger,
    FindingScope,
)
from ai_sdlc.core.stage_review.finding_reducer import (
    compare_progress,
    evaluate_closeability,
)
from ai_sdlc.core.stage_review.finding_replay import validate_finding_event_history
from ai_sdlc.core.stage_review.finding_service_support import (
    command_waiver,
    historical_replay_trust,
    trusted_command_evidence,
)
from ai_sdlc.core.stage_review.finding_store import FindingEventStore
from ai_sdlc.core.stage_review.finding_support_models import (
    FindingCloseability,
    ProgressComparison,
    ProgressSnapshot,
)
from ai_sdlc.core.stage_review.finding_trust_models import (
    FindingCloseContext,
    FindingTrustContext,
    FindingTrustResolver,
    FindingWaiver,
    TrustedEvidenceDescriptor,
    TrustedFindingAuthority,
)

_FindingMutation: TypeAlias = FindingAppendCommand | FindingInitialBatchCommand


class FindingLedgerService:
    """追加可信 Finding 事实；Ledger 始终可从事件链重建。"""

    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        trust_resolver: FindingTrustResolver,
        lock_timeout_seconds: float = 2,
        fault_hook: Callable[[str], None] | None = None,
        event_observer: Callable[[FindingEvent], None] | None = None,
    ) -> None:
        self._root = root
        self._project_id = project_id
        self._trust_resolver = trust_resolver
        self._identity = FindingIdentityResolver()
        self._authorizer = FindingAuthorizer(self._identity)
        self._fault_hook = fault_hook
        self._event_observer = event_observer
        self._store = FindingEventStore(
            root,
            project_id=project_id,
            lock_timeout_seconds=lock_timeout_seconds,
        )

    def append(self, command: _FindingMutation) -> FindingAppendResult:
        with activation_safety_mutation_fence(
            self._root,
            self._project_id,
        ):
            if isinstance(command, FindingInitialBatchCommand):
                return self._initialize(command)
            return self._append(command)

    def read(self, scope: FindingScope) -> FindingLedger:
        with self._store.lock(scope):
            self._store.bind_project()
            trust = self._trusted(scope)
            return self._rebuild(scope, trust)

    def advance_lineage(
        self, command: FindingLineageAdvanceCommand
    ) -> FindingAppendResult:
        with activation_safety_mutation_fence(
            self._root,
            self._project_id,
        ):
            return finding_lineage.advance_finding_lineage(
                self._store, self._trust_resolver, command
            )

    def _initialize(self, command: FindingInitialBatchCommand) -> FindingAppendResult:
        with self._store.lock(command.scope):
            self._store.bind_project()
            trust = self._trusted(command.scope)
            self._validate_lineage(command, trust)
            self._validate_initial_snapshot(trust)
            if (
                initial_finding_batch_digest(command.findings)
                != trust.initial_review_seal.finding_batch_digest
            ):
                raise SharedStateIntegrityError("finding initial batch seal mismatch")
            operation_id = stable_finding_id(
                "finding-operation",
                command.scope.session_id,
                command.command_id,
                scope_digest(command.scope),
            )
            payload = command.model_dump(mode="json")
            existing = self._store.read_operation(operation_id)
            if existing is not None and existing != payload:
                raise SharedStateIntegrityError("finding initial command fork")
            if existing is None:
                self._store.create_operation(operation_id, payload)
            events = self._store.load_events(command.scope)
            validate_finding_event_history(
                events,
                historical_replay_trust(trust, self._store.load_event_waivers(events)),
                self._trust_resolver,
            )
            if events and events[-1].event_type == "initial_ledger_sealed":
                return FindingAppendResult(
                    event=events[-1],
                    ledger=self._rebuild(command.scope, trust),
                    idempotent_replay=True,
                )
            if command.expected_revision != 0 or (
                events
                and not all(
                    item.command_id.startswith(command.command_id) for item in events
                )
            ):
                raise SharedStateIntegrityError("finding initial revision mismatch")
            events = self._complete_initial_batch(command, trust, events)
            ledger = self._rebuild(command.scope, trust)
            return FindingAppendResult(event=events[-1], ledger=ledger)

    def _complete_initial_batch(
        self,
        command: FindingInitialBatchCommand,
        trust: FindingTrustContext,
        events: tuple[FindingEvent, ...],
    ) -> tuple[FindingEvent, ...]:
        built = list(events)
        seen_keys: set[str] = set()
        for index, draft in enumerate(command.findings):
            authority = self._authorizer.authority(trust, draft.actor_id, draft.slot_id)
            self._authorizer.require_reviewer_at(
                authority,
                draft.capability_id,
                trust.initial_review_seal.sealed_at,
            )
            evidence = self._require_trusted_evidence(
                command.scope,
                draft.evidence_bundle_digest,
                trust.candidate_digest,
            )
            if evidence.subject_identity_digest != draft.identity.identity_digest:
                raise PermissionError(
                    "initial finding evidence identity is not trusted"
                )
            decision = self._identity.resolve(draft.identity)
            if decision.finding_key in seen_keys:
                raise SharedStateIntegrityError("finding initial identity collision")
            seen_keys.add(decision.finding_key)
            if index < len(built):
                expected_id = stable_finding_id(
                    "finding-event",
                    command.scope.session_id,
                    f"{command.command_id}.{index}",
                )
                if built[index].event_id != expected_id:
                    raise SharedStateIntegrityError("finding initial batch event fork")
                continue
            event = build_initial_event(
                command, trust, draft, decision, authority, evidence, index, built
            )
            committed = self._store.append_event(event)
            if len(built) <= index:
                built.append(committed)
            self._fault("after_initial_event")
        seal = build_seal_event(command, trust, built)
        if not built or built[-1].event_type != "initial_ledger_sealed":
            built.append(self._store.append_event(seal))
        self._fault("after_initial_seal")
        return tuple(built)

    def _append(self, command: FindingAppendCommand) -> FindingAppendResult:
        with self._store.lock(command.scope):
            self._store.bind_project()
            trust = self._trusted(command.scope)
            events = self._store.load_events(command.scope)
            validate_finding_event_history(
                events,
                historical_replay_trust(trust, self._store.load_event_waivers(events)),
                self._trust_resolver,
            )
            replay = self._find_replay(events, command)
            if replay is not None:
                result = self._replay_result(replay, command.scope, trust)
            else:
                self._validate_mutation(command, trust, events)
                require_trusted_mapping(
                    command.identity_mapping,
                    command.scope,
                    trust.candidate_digest,
                    self._trust_resolver,
                )
                evidence = trusted_command_evidence(
                    command,
                    trust,
                    self._trust_resolver,
                )
                authority, key, disposition, blocking = self._authorizer.authorize(
                    command, trust, events, evidence
                )
                waiver = command_waiver(command, trust)
                if waiver is not None:
                    self._store.persist_waiver(waiver)
                event = build_regular_event(
                    command,
                    trust,
                    events,
                    authority,
                    evidence,
                    key,
                    disposition,
                    blocking,
                )
                committed = self._store.append_event(event)
                self._fault("after_event_commit")
                ledger = self._rebuild(command.scope, trust)
                result = FindingAppendResult(event=committed, ledger=ledger)
        self._observe_event(result.event)
        return result

    def _replay_result(
        self,
        event: FindingEvent,
        scope: FindingScope,
        trust: FindingTrustContext,
    ) -> FindingAppendResult:
        return FindingAppendResult(
            event=event,
            ledger=self._rebuild(scope, trust),
            idempotent_replay=True,
        )

    def _trusted(self, scope: FindingScope) -> FindingTrustContext:
        trust = FindingTrustContext.model_validate(
            self._trust_resolver.resolve(scope).model_dump(mode="json")
        )
        if trust.scope != scope:
            raise SharedStateIntegrityError("finding trusted scope mismatch")
        return trust

    def _rebuild(
        self,
        scope: FindingScope,
        trust: FindingTrustContext,
    ) -> FindingLedger:
        return self._store.rebuild(
            scope,
            lambda events: validate_finding_event_history(
                events,
                historical_replay_trust(trust, self._store.load_event_waivers(events)),
                self._trust_resolver,
            ),
        )

    def _validate_lineage(
        self,
        command: FindingAppendCommand | FindingInitialBatchCommand,
        trust: FindingTrustContext,
    ) -> None:
        actual = (
            command.candidate_digest,
            command.policy_digest,
            command.plan_digest,
            command.binding_set_digest,
            command.session_fencing_epoch,
        )
        expected = (
            trust.candidate_digest,
            trust.policy_digest,
            trust.plan_digest,
            trust.binding_set_digest,
            trust.session_fencing_epoch,
        )
        if actual != expected:
            raise SharedStateIntegrityError("finding trusted lineage mismatch")
        if isinstance(command, FindingInitialBatchCommand) and (
            command.initial_review_seal_digest != trust.initial_review_seal_digest
        ):
            raise SharedStateIntegrityError("finding initial seal lineage mismatch")

    def _validate_initial_snapshot(self, trust: FindingTrustContext) -> None:
        seal = trust.initial_review_seal
        actual = (
            trust.candidate_digest,
            trust.policy_digest,
            trust.plan_digest,
            trust.binding_set_digest,
            trust.cohort_id,
        )
        expected = (
            seal.initial_candidate_digest,
            seal.policy_digest,
            seal.plan_digest,
            seal.binding_set_digest,
            seal.initial_cohort_id,
        )
        if actual != expected:
            raise SharedStateIntegrityError(
                "finding initial snapshot differs from seal"
            )

    def _validate_mutation(
        self,
        command: FindingAppendCommand,
        trust: FindingTrustContext,
        events: tuple[FindingEvent, ...],
    ) -> None:
        self._validate_lineage(command, trust)
        if (
            not events
            or events[-1].event_type != "initial_ledger_sealed"
            and not any(item.event_type == "initial_ledger_sealed" for item in events)
        ):
            raise SharedStateIntegrityError("finding ledger is not initialized")
        finding_lineage.require_regular_lineage(trust, events[-1])
        if command.expected_revision is not None and command.expected_revision != len(
            events
        ):
            raise SharedStateIntegrityError("finding stale expected revision")
        digest = command_digest(command)
        for event in events:
            if (
                event.idempotency_key == command.idempotency_key
                and event.command_digest != digest
            ):
                raise SharedStateIntegrityError("finding idempotency key fork")

    def _find_replay(
        self,
        events: tuple[FindingEvent, ...],
        command: FindingAppendCommand,
    ) -> FindingEvent | None:
        matches = tuple(
            item for item in events if item.command_id == command.command_id
        )
        if not matches:
            return None
        digest = command_digest(command)
        if len(matches) != 1 or matches[0].command_digest != digest:
            raise SharedStateIntegrityError("finding command idempotency fork")
        return matches[0]

    def _fault(self, point: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(point)

    def _observe_event(self, event: FindingEvent) -> None:
        if self._event_observer is None:
            return
        try:
            self._event_observer(event)
        except Exception:
            # FindingEvent 已提交；派生优化事实由后续维护从事件真值补录。
            return

    def _require_trusted_evidence(
        self,
        scope: FindingScope,
        evidence_bundle_digest: str,
        candidate_digest: str | None,
    ) -> TrustedEvidenceDescriptor:
        evidence = self._trust_resolver.resolve_evidence(scope, evidence_bundle_digest)
        if (
            evidence is None
            or evidence.scope != scope
            or evidence.evidence_bundle_digest != evidence_bundle_digest
            or (
                candidate_digest is not None
                and evidence.candidate_digest != candidate_digest
            )
        ):
            raise PermissionError("finding evidence is not trusted")
        return TrustedEvidenceDescriptor.model_validate(
            evidence.model_dump(mode="json")
        )


__all__ = [
    "FindingAppendCommand",
    "FindingCloseContext",
    "FindingCloseability",
    "FindingIdentityInput",
    "FindingIdentityMapping",
    "FindingIdentityResolver",
    "FindingInitialBatchCommand",
    "FindingInitialDraft",
    "FindingLedgerService",
    "FindingScope",
    "FindingTrustContext",
    "FindingWaiver",
    "ProgressComparison",
    "ProgressSnapshot",
    "TrustedFindingAuthority",
    "compare_progress",
    "evaluate_closeability",
]
