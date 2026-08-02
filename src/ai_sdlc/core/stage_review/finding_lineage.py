"""Finding Ledger 与已提交 Session Cohort 的不可变血缘锚定。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.finding_artifact_codec import (
    finding_event_contracts_match,
)
from ai_sdlc.core.stage_review.finding_command_models import (
    FindingLineageAdvanceCommand,
)
from ai_sdlc.core.stage_review.finding_digests import command_digest
from ai_sdlc.core.stage_review.finding_event_builders import build_lineage_event
from ai_sdlc.core.stage_review.finding_models import (
    FindingAppendResult,
    FindingEvent,
    FindingLedger,
)
from ai_sdlc.core.stage_review.finding_reducer import reduce_finding_events
from ai_sdlc.core.stage_review.finding_service_support import historical_replay_trust
from ai_sdlc.core.stage_review.finding_store import FindingEventStore
from ai_sdlc.core.stage_review.finding_trust_models import (
    FindingTrustContext,
    FindingTrustResolver,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc


def advance_finding_lineage(
    store: FindingEventStore,
    resolver: FindingTrustResolver,
    command: FindingLineageAdvanceCommand,
) -> FindingAppendResult:
    with store.lock(command.scope):
        store.bind_project()
        trust = _trusted(resolver, command)
        events = store.load_events(command.scope)
        _validate_history(store, resolver, trust, events)
        replay = _lineage_replay(events, command)
        if replay is not None:
            return FindingAppendResult(
                event=replay,
                ledger=_rebuild(store, resolver, trust),
                idempotent_replay=True,
            )
        ledger = reduce_finding_events(command.scope, events)
        _validate_advance(command, trust, ledger)
        event = build_lineage_event(command, trust, events)
        if not resolver.session_lineage_is_trusted(event):
            raise SharedStateIntegrityError(
                "finding session lineage proof is not trusted"
            )
        committed = store.append_event(event)
        return FindingAppendResult(
            event=committed,
            ledger=_rebuild(store, resolver, trust),
        )


def validate_lineage_event(
    event: FindingEvent,
    prefix: tuple[FindingEvent, ...],
    current: FindingTrustContext,
    resolver: FindingTrustResolver,
) -> None:
    command = _lineage_command(event)
    trust = current.model_copy(
        update={
            "candidate_digest": command.candidate_digest,
            "policy_digest": command.policy_digest,
            "plan_digest": command.plan_digest,
            "binding_set_digest": command.binding_set_digest,
            "cohort_id": command.cohort_id,
            "session_fencing_epoch": command.session_fencing_epoch,
            "evaluation_at": command.advanced_at,
        }
    )
    ledger = reduce_finding_events(event.scope, prefix)
    _validate_advance(command, trust, ledger)
    expected = build_lineage_event(command, trust, prefix)
    valid = (
        event.event_type == "ledger_lineage_advanced",
        event.finding_key is None,
        command_digest(command) == event.command_digest,
        resolver.session_lineage_is_trusted(event),
        finding_event_contracts_match(expected, event),
    )
    if not all(valid):
        raise SharedStateIntegrityError("finding lineage event is invalid")


def require_regular_lineage(
    current: FindingEvent | FindingTrustContext,
    previous: FindingEvent,
) -> None:
    if previous.schema_version == "finding-event.v1":
        raise SharedStateIntegrityError(
            "finding mutation requires a trusted lineage advance"
        )
    fields = (
        "candidate_digest",
        "policy_digest",
        "plan_digest",
        "binding_set_digest",
        "cohort_id",
        "session_fencing_epoch",
    )
    if tuple(getattr(current, field) for field in fields) != tuple(
        getattr(previous, field) for field in fields
    ):
        raise SharedStateIntegrityError(
            "finding mutation requires a trusted lineage advance"
        )


def validate_event_schema_sequence(
    event: FindingEvent,
    prefix: list[FindingEvent],
) -> None:
    if (
        event.event_type == "ledger_lineage_advanced"
        and event.schema_version != "finding-event.v2"
    ):
        raise SharedStateIntegrityError("finding lineage event schema is invalid")
    if event.schema_version == "finding-event.v1" and any(
        item.schema_version == "finding-event.v2" for item in prefix
    ):
        raise SharedStateIntegrityError("finding event schema downgrade")


def _validate_advance(
    command: FindingLineageAdvanceCommand,
    trust: FindingTrustContext,
    ledger: FindingLedger,
) -> None:
    lineage = (
        ledger.initialized,
        command.expected_revision == ledger.revision,
        command.previous_ledger_digest == ledger.ledger_digest,
        command.scope == trust.scope,
        command.candidate_digest == trust.candidate_digest,
        command.policy_digest == trust.policy_digest,
        command.plan_digest == trust.plan_digest,
        command.binding_set_digest == trust.binding_set_digest,
        command.cohort_id == trust.cohort_id,
        command.session_fencing_epoch == trust.session_fencing_epoch,
        bool(command.session_event_digest),
    )
    parse_utc(command.advanced_at)
    if not all(lineage):
        raise SharedStateIntegrityError("finding lineage advance is invalid")


def _trusted(
    resolver: FindingTrustResolver,
    command: FindingLineageAdvanceCommand,
) -> FindingTrustContext:
    return FindingTrustContext.model_validate(
        resolver.resolve(command.scope).model_dump(mode="json")
    )


def _validate_history(
    store: FindingEventStore,
    resolver: FindingTrustResolver,
    trust: FindingTrustContext,
    events: tuple[FindingEvent, ...],
) -> None:
    from ai_sdlc.core.stage_review.finding_replay import (
        validate_finding_event_history,
    )

    validate_finding_event_history(
        events,
        historical_replay_trust(trust, store.load_event_waivers(events)),
        resolver,
    )


def _rebuild(
    store: FindingEventStore,
    resolver: FindingTrustResolver,
    trust: FindingTrustContext,
) -> FindingLedger:
    return store.rebuild(
        trust.scope,
        lambda events: _validate_history(store, resolver, trust, events),
    )


def _lineage_replay(
    events: tuple[FindingEvent, ...],
    command: FindingLineageAdvanceCommand,
) -> FindingEvent | None:
    matches = tuple(item for item in events if item.command_id == command.command_id)
    if not matches:
        return None
    if len(matches) != 1 or matches[0].command_digest != command_digest(command):
        raise SharedStateIntegrityError("finding lineage command fork")
    return matches[0]


def _lineage_command(event: FindingEvent) -> FindingLineageAdvanceCommand:
    try:
        return FindingLineageAdvanceCommand.model_validate(event.command_payload)
    except ValueError as exc:
        raise SharedStateIntegrityError(
            "finding lineage command is invalid"
        ) from exc
