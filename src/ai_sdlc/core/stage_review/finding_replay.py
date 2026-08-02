"""从不可变事件重放授权和状态机，拒绝自洽伪造历史。"""

from __future__ import annotations

from collections.abc import Mapping

from ai_sdlc.core.stage_review import finding_lineage
from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.finding_artifact_codec import (
    finding_event_contracts_match,
)
from ai_sdlc.core.stage_review.finding_authority import required_terminal_authority
from ai_sdlc.core.stage_review.finding_authorization import FindingAuthorizer
from ai_sdlc.core.stage_review.finding_command_models import (
    FindingAppendCommand,
    FindingInitialBatchCommand,
    FindingInitialDraft,
)
from ai_sdlc.core.stage_review.finding_digests import (
    command_digest,
    initial_finding_batch_digest,
)
from ai_sdlc.core.stage_review.finding_event_builders import (
    build_initial_event,
    build_regular_event,
    build_seal_event,
)
from ai_sdlc.core.stage_review.finding_handoff import validate_replayed_handoff
from ai_sdlc.core.stage_review.finding_identity import FindingIdentityResolver
from ai_sdlc.core.stage_review.finding_mapping import require_trusted_mapping
from ai_sdlc.core.stage_review.finding_models import FindingEvent
from ai_sdlc.core.stage_review.finding_reducer import (
    finding_transition_allowed,
    reduce_finding_events,
)
from ai_sdlc.core.stage_review.finding_trust_models import (
    FindingTrustContext,
    FindingTrustResolver,
    TrustedEvidenceDescriptor,
    TrustedFindingAuthority,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc


def validate_finding_event_history(
    events: tuple[FindingEvent, ...],
    trust: FindingTrustContext,
    resolver: FindingTrustResolver,
) -> None:
    sealed = False
    initial_events: list[FindingEvent] = []
    prefix: list[FindingEvent] = []
    for event in events:
        finding_lineage.validate_event_schema_sequence(event, prefix)
        if event.event_type == "initial_ledger_sealed":
            _validate_initial_seal(event, initial_events, trust, sealed)
            sealed = True
            prefix.append(event)
            continue
        if event.event_type == "ledger_lineage_advanced":
            if not sealed:
                raise SharedStateIntegrityError("finding lineage appears before initial seal")
            finding_lineage.validate_lineage_event(event, tuple(prefix), trust, resolver)
            prefix.append(event)
            continue
        if not resolver.event_is_trusted(event):
            raise SharedStateIntegrityError("finding event trust proof is invalid")
        if event.event_type == "initial_discovered":
            if sealed:
                raise SharedStateIntegrityError("initial finding appears after seal")
            _validate_initial_event(event, initial_events, trust, resolver)
            initial_events.append(event)
        else:
            if not sealed:
                raise SharedStateIntegrityError("finding mutation appears before seal")
            _validate_regular_event(event, tuple(prefix), trust, resolver)
        prefix.append(event)
    if events and not sealed:
        return


def _validate_initial_seal(
    event: FindingEvent,
    initial_events: list[FindingEvent],
    trust: FindingTrustContext,
    already_sealed: bool,
) -> None:
    seal = trust.initial_review_seal
    command = _initial_command(event, "seal")
    if (
        already_sealed
        or event.initial_review_seal_digest != seal.seal_digest
        or command_digest(command) != event.command_digest
        or event.expected_revision != 0
        or not _initial_seal_lineage_matches(event, trust)
    ):
        raise SharedStateIntegrityError("finding initial seal is invalid")
    expected = build_seal_event(command, trust, initial_events)
    if not finding_event_contracts_match(expected, event):
        raise SharedStateIntegrityError("finding initial seal contract is inconsistent")
    _validate_initial_batch(command, event, initial_events, seal.finding_batch_digest)


def _initial_seal_lineage_matches(
    event: FindingEvent,
    trust: FindingTrustContext,
) -> bool:
    seal = trust.initial_review_seal
    return (
        event.scope == trust.scope
        and event.candidate_digest == seal.initial_candidate_digest
        and event.policy_digest == seal.policy_digest
        and event.plan_digest == seal.plan_digest
        and event.binding_set_digest == seal.binding_set_digest
        and event.cohort_id == seal.initial_cohort_id
        and event.evidence_bundle_digest == seal.seal_digest
        and event.actor_id == "system.initial-seal"
        and event.authority_kind == "deterministic_gate"
        and event.binding_digest == seal.seal_digest
    )


def _validate_initial_batch(
    command: FindingInitialBatchCommand,
    event: FindingEvent,
    initial_events: list[FindingEvent],
    sealed_batch_digest: str,
) -> None:
    drafts = tuple(
        FindingInitialDraft(
            identity=item.identity,
            severity=item.severity,
            evidence_bundle_digest=item.evidence_bundle_digest,
            actor_id=item.actor_id,
            slot_id=item.slot_id,
            capability_id=item.capability_id,
        )
        for item in initial_events
        if item.identity is not None and item.severity is not None
    )
    if len(drafts) != len(initial_events) or (
        initial_finding_batch_digest(drafts) != sealed_batch_digest
    ):
        raise SharedStateIntegrityError("finding initial seal batch mismatch")
    if initial_finding_batch_digest(command.findings) != sealed_batch_digest or any(
        item.command_digest != event.command_digest
        or item.command_payload != event.command_payload
        for item in initial_events
    ):
        raise SharedStateIntegrityError("finding initial command batch mismatch")


def _validate_initial_event(
    event: FindingEvent,
    prior: list[FindingEvent],
    trust: FindingTrustContext,
    resolver: FindingTrustResolver,
) -> None:
    if event.identity is None or event.finding_key is None:
        raise SharedStateIntegrityError("initial finding identity is missing")
    decision = FindingIdentityResolver().resolve(event.identity)
    command = _initial_command(event, "finding")
    index = len(prior)
    draft, authority, evidence = _initial_event_proofs(
        event, command, index, trust, resolver
    )
    expected = build_initial_event(
        command,
        trust,
        draft,
        decision,
        authority,
        evidence,
        index,
        prior,
    )
    if (
        event.finding_key != decision.finding_key
        or command_digest(command) != event.command_digest
        or event.expected_revision != 0
        or event.disposition != "blocking"
        or not event.blocking
        or any(item.finding_key == event.finding_key for item in prior)
    ):
        raise SharedStateIntegrityError("initial finding semantics are invalid")
    if not finding_event_contracts_match(expected, event):
        raise SharedStateIntegrityError("initial finding contract is inconsistent")


def _initial_command(
    event: FindingEvent,
    subject: str,
) -> FindingInitialBatchCommand:
    try:
        return FindingInitialBatchCommand.model_validate(event.command_payload)
    except ValueError as exc:
        raise SharedStateIntegrityError(
            f"finding initial {subject} command is invalid"
        ) from exc


def _initial_event_proofs(
    event: FindingEvent,
    command: FindingInitialBatchCommand,
    index: int,
    trust: FindingTrustContext,
    resolver: FindingTrustResolver,
) -> tuple[FindingInitialDraft, TrustedFindingAuthority, TrustedEvidenceDescriptor]:
    if index >= len(command.findings):
        raise SharedStateIntegrityError("initial finding command index is invalid")
    draft = command.findings[index]
    authority = _frozen_authority(event)
    try:
        FindingAuthorizer(FindingIdentityResolver()).require_reviewer_at(
            authority, draft.capability_id, trust.initial_review_seal.sealed_at
        )
    except (PermissionError, ValueError) as exc:
        raise SharedStateIntegrityError("initial finding authority is invalid") from exc
    evidence = resolver.resolve_evidence(event.scope, draft.evidence_bundle_digest)
    if (
        evidence is None
        or evidence.evidence_bundle_digest != draft.evidence_bundle_digest
    ):
        raise SharedStateIntegrityError("initial finding evidence is unavailable")
    return draft, authority, evidence


def _validate_regular_event(
    event: FindingEvent,
    prefix: tuple[FindingEvent, ...],
    trust: FindingTrustContext,
    resolver: FindingTrustResolver,
) -> None:
    if event.schema_version == "finding-event.v2":
        finding_lineage.require_regular_lineage(event, prefix[-1])
    _validate_replay_authorization(event, prefix, trust, resolver)
    _validate_regular_shape(event, prefix, trust)


def _validate_replay_authorization(
    event: FindingEvent,
    prefix: tuple[FindingEvent, ...],
    trust: FindingTrustContext,
    resolver: FindingTrustResolver,
) -> None:
    evidence_scope = event.evidence_scope or event.scope
    evidence = resolver.resolve_evidence(evidence_scope, event.evidence_bundle_digest)
    if (
        evidence is None
        or evidence.evidence_bundle_digest != event.evidence_bundle_digest
    ):
        raise SharedStateIntegrityError("finding replay evidence is unavailable")
    command = _event_command(event)
    historical_trust = _historical_trust(event, trust)
    try:
        require_trusted_mapping(
            command.identity_mapping,
            event.scope,
            event.candidate_digest,
            resolver,
        )
        authorized = FindingAuthorizer(FindingIdentityResolver()).authorize(
            command, historical_trust, prefix, evidence
        )
    except (AssertionError, PermissionError, ValueError) as exc:
        raise SharedStateIntegrityError("finding replay authorization failed") from exc
    if authorized[0].actor_id != event.actor_id or authorized[1:] != (
        event.finding_key,
        event.disposition,
        event.blocking,
    ):
        raise SharedStateIntegrityError("finding replay outcome is inconsistent")
    expected = build_regular_event(
        command,
        historical_trust,
        prefix,
        authorized[0],
        evidence,
        *authorized[1:],
    )
    if not finding_event_contracts_match(expected, event):
        raise SharedStateIntegrityError("finding replay event contract is inconsistent")


def _historical_trust(
    event: FindingEvent,
    current: FindingTrustContext,
) -> FindingTrustContext:
    authority = _frozen_authority(event)
    return current.model_copy(
        update={
            "candidate_digest": event.candidate_digest,
            "policy_digest": event.policy_digest,
            "plan_digest": event.plan_digest,
            "binding_set_digest": event.binding_set_digest,
            "cohort_id": event.cohort_id,
            "authorities": (authority,),
            "non_waivable_categories": (),
            "evaluation_at": event.authorized_at,
        }
    )


def _frozen_authority(event: FindingEvent) -> TrustedFindingAuthority:
    return TrustedFindingAuthority(
        actor_id=event.actor_id,
        slot_id=event.slot_id,
        slot_kind=event.authority_slot_kind,
        authority_kind=event.authority_kind,
        capability_ids=event.authority_capability_ids,
        blocking_authorities=event.authority_blocking_authorities,
        role_profile_id=event.authority_role_profile_id,
        role_contract_digest=event.role_contract_digest,
        binding_digest=event.binding_digest,
        eligible_for_enforce_quorum=event.authority_eligible_for_enforce_quorum,
        valid_until=event.authority_valid_until,
        capability_coverage_digest=event.authority_capability_coverage_digest,
    )


def _validate_regular_shape(
    event: FindingEvent,
    prefix: tuple[FindingEvent, ...],
    trust: FindingTrustContext,
) -> None:
    if event.expected_revision != len(prefix):
        raise SharedStateIntegrityError("finding replay CAS revision is invalid")
    ledger = reduce_finding_events(event.scope, prefix)
    records = {item.finding_key: item for item in ledger.records}
    if event.event_type == "discovered":
        _validate_discovery(event, records, trust)
        return
    if event.event_type in {
        "cross_scope_critical_evidence",
        "cross_scope_handoff_resolved",
    }:
        validate_replayed_handoff(event, prefix)
        return
    record = records.get(event.finding_key or "")
    if record is None or not finding_transition_allowed(event.event_type, record.state):
        raise SharedStateIntegrityError("finding replay transition is invalid")
    if event.event_type == "fixed" and not event.remediation_batch_id:
        raise SharedStateIntegrityError("finding fixed event lacks remediation batch")
    if event.event_type == "verified" and not _required_verifier(event):
        raise SharedStateIntegrityError("finding verified authority is invalid")
    if event.event_type == "waived" and not event.waiver_digest:
        raise SharedStateIntegrityError("finding waived event lacks immutable waiver")
    if event.event_type == "superseded" and not (
        event.replacement_keys or event.macro_rebaseline_evidence_digest
    ):
        raise SharedStateIntegrityError("finding supersede responsibility is missing")


def _event_command(event: FindingEvent) -> FindingAppendCommand:
    try:
        command = FindingAppendCommand.model_validate(event.command_payload)
    except ValueError as exc:
        raise SharedStateIntegrityError("finding replay command is invalid") from exc
    if command_digest(command) != event.command_digest:
        raise SharedStateIntegrityError("finding replay command digest is invalid")
    return command


def _validate_discovery(
    event: FindingEvent,
    records: Mapping[str, object],
    trust: FindingTrustContext,
) -> None:
    if event.finding_key in records and event.identity_mapping is None:
        raise SharedStateIntegrityError("existing finding was rediscovered")
    origin = event.late_origin
    if origin == "protocol_or_required_test_failure" and event.evidence_kind not in {
        "required_test_failure",
        "protocol_integrity_failure",
    }:
        raise SharedStateIntegrityError("protocol blocker proof is invalid")
    if origin == "new_critical_evidence" and (
        event.evidence_initial_visibility != "not_visible"
        or parse_utc(event.evidence_first_visible_at)
        <= parse_utc(trust.initial_review_seal.sealed_at)
    ):
        raise SharedStateIntegrityError("new critical evidence timing is invalid")
    if origin == "late_confirmed_p0_p1" and (
        event.evidence_confirmation_result != "confirmed"
        or event.evidence_initial_visibility != "visible"
        or parse_utc(event.evidence_first_visible_at)
        > parse_utc(trust.initial_review_seal.sealed_at)
    ):
        raise SharedStateIntegrityError("late critical confirmation is invalid")
    if origin == "regression_of" and (
        event.evidence_related_finding_key != event.regression_of
    ):
        raise SharedStateIntegrityError("regression lineage proof is invalid")


def _required_verifier(event: FindingEvent) -> bool:
    return required_terminal_authority(
        _frozen_authority(event),
        event.capability_id,
    )
