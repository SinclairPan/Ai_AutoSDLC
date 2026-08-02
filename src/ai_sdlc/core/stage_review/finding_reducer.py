"""FindingEvent 的确定性投影、关闭判断与质量比较。"""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_sdlc.core.stage_review.finding_digests import ledger_digest
from ai_sdlc.core.stage_review.finding_mapping import identity_relation_from_event
from ai_sdlc.core.stage_review.finding_models import (
    FindingEvent,
    FindingIdentityRelation,
    FindingLedger,
    FindingRecord,
    FindingScope,
)
from ai_sdlc.core.stage_review.finding_support_models import (
    FindingCloseability,
    ProgressComparison,
    ProgressSnapshot,
)
from ai_sdlc.core.stage_review.finding_trust_models import FindingCloseContext
from ai_sdlc.core.stage_review.resource_builders import parse_utc

_STATE_BY_EVENT = {
    "initial_discovered": "open",
    "discovered": "open",
    "acknowledged": "acknowledged",
    "remediation_started": "remediation_started",
    "fixed": "fixed",
    "verification_failed": "verification_failed",
    "verified": "verified",
    "waived": "waived",
    "superseded": "superseded",
    "regressed": "regressed",
}
_QUALITY_FIELDS = (
    "p0_open",
    "required_test_failures",
    "integrity_failures",
    "reopened_or_regressed",
    "p1_open",
    "unreviewed_change",
)
_CRITICAL_REGRESSION_FIELDS = (
    "p0_open",
    "required_test_failures",
    "integrity_failures",
    "p1_open",
)
_ALLOWED_STATES = {
    "acknowledged": {"open", "regressed"},
    "remediation_started": {"open", "acknowledged", "verification_failed", "regressed"},
    "fixed": {"remediation_started"},
    "verification_failed": {"fixed"},
    "verified": {"fixed", "verification_failed"},
    "regressed": {"verified", "waived"},
    "waived": {"open", "acknowledged", "remediation_started", "fixed"},
    "superseded": {
        "open",
        "acknowledged",
        "remediation_started",
        "fixed",
        "verification_failed",
        "regressed",
    },
}


def finding_transition_allowed(event_type: str, current_state: str) -> bool:
    allowed = _ALLOWED_STATES.get(event_type)
    return allowed is None or current_state in allowed


@dataclass
class _ProjectionState:
    records: dict[str, FindingRecord] = field(default_factory=dict)
    advisories: set[str] = field(default_factory=set)
    violations: set[str] = field(default_factory=set)
    handoffs: set[str] = field(default_factory=set)
    relations: list[FindingIdentityRelation] = field(default_factory=list)
    initialized: bool = False
    seal_digest: str = ""

    def apply(self, event: FindingEvent) -> None:
        if event.event_type == "initial_ledger_sealed":
            self.initialized = True
            self.seal_digest = event.initial_review_seal_digest
            return
        if event.event_type == "cross_scope_critical_evidence":
            if event.handoff_id:
                self.handoffs.add(event.handoff_id)
            return
        if event.event_type == "cross_scope_handoff_resolved":
            if event.handoff_id and event.handoff_resolution == "accepted":
                self.handoffs.discard(event.handoff_id)
            return
        if event.finding_key is None:
            return
        if event.event_type in {"initial_discovered", "discovered"}:
            existing = self.records.get(event.finding_key)
            if (
                existing is not None
                and event.identity_mapping is not None
                and event.identity_mapping.mapping_kind == "alias"
            ):
                self.records[event.finding_key] = _alias_record(existing, event)
            else:
                self.records[event.finding_key] = _new_record(event)
            relation = identity_relation_from_event(event)
            if relation is not None and relation not in self.relations:
                self.relations.append(relation)
        elif event.finding_key in self.records:
            self.records[event.finding_key] = _transition(
                self.records[event.finding_key], event
            )
        if event.disposition == "advisory":
            self.advisories.add(event.finding_key)
        elif event.disposition == "protocol_violation":
            self.violations.add(event.finding_key)


def reduce_finding_events(
    scope: FindingScope,
    events: tuple[FindingEvent, ...],
) -> FindingLedger:
    state = _ProjectionState()
    for event in events:
        state.apply(event)
    visible_records, pending_targets = _visible_projection(state)
    head = events[-1] if events else None
    draft = FindingLedger.model_construct(
        scope=scope,
        initialized=state.initialized,
        revision=len(events),
        head_event_id=head.event_id if head else "",
        head_event_digest=head.event_digest if head else "",
        initial_review_seal_digest=state.seal_digest,
        candidate_digest=head.candidate_digest if head else "",
        policy_digest=head.policy_digest if head else "",
        plan_digest=head.plan_digest if head else "",
        binding_set_digest=head.binding_set_digest if head else "",
        cohort_id=head.cohort_id if head else "",
        lineage_contract_version=(
            "explicit-v2"
            if any(item.schema_version == "finding-event.v2" for item in events)
            else "implicit-v1"
        ),
        records=visible_records,
        identity_relations=tuple(state.relations) if state.initialized else (),
        advisory_keys=tuple(sorted(state.advisories)),
        protocol_violation_keys=tuple(sorted(state.violations)),
        pending_handoff_ids=tuple(sorted(state.handoffs)),
        pending_identity_target_keys=pending_targets,
        integrity_ok=True,
        ledger_digest="",
    )
    payload = draft.model_dump(mode="json", warnings=False)
    payload["ledger_digest"] = ledger_digest(draft)
    return FindingLedger.model_validate(payload)


def _visible_projection(
    state: _ProjectionState,
) -> tuple[tuple[FindingRecord, ...], tuple[str, ...]]:
    if not state.initialized:
        state.advisories.clear()
        state.violations.clear()
        return (), ()
    records = tuple(sorted(state.records.values(), key=lambda item: item.finding_key))
    pending_targets = tuple(
        sorted(
            key
            for relation in state.relations
            for key in relation.target_keys
            if key not in state.records
        )
    )
    return records, pending_targets


def evaluate_closeability(
    ledger: FindingLedger,
    context: FindingCloseContext,
) -> FindingCloseability:
    reasons: set[str] = set()
    if not ledger.initialized:
        reasons.add("finding.ledger-not-initialized")
    if not ledger.integrity_ok:
        reasons.add("finding.ledger-integrity-unknown")
    if ledger.pending_handoff_ids:
        reasons.add("finding.authority-handoff-pending")
    if ledger.pending_identity_target_keys:
        reasons.add("finding.identity-target-pending")
    if (
        ledger.candidate_digest != context.candidate_digest
        or ledger.policy_digest != context.policy_digest
        or ledger.binding_set_digest != context.binding_set_digest
    ):
        reasons.add("finding.close-context-lineage-mismatch")
    by_key = {item.finding_key: item for item in ledger.records}
    unresolved = tuple(
        sorted(
            key
            for key, record in by_key.items()
            if record.blocking and not _is_resolved(record, by_key, context, set())
        )
    )
    if unresolved:
        reasons.add("finding.unresolved-blocker")
    return FindingCloseability(
        closeable=not reasons,
        reason_ids=tuple(sorted(reasons)),
        unresolved_finding_keys=unresolved,
    )


def compare_progress(
    previous: ProgressSnapshot,
    current: ProgressSnapshot,
) -> ProgressComparison:
    if previous.comparison_policy_digest != current.comparison_policy_digest:
        return ProgressComparison(outcome="uncomparable")
    worsened = next(
        (
            name
            for name in _CRITICAL_REGRESSION_FIELDS
            if getattr(current, name) > getattr(previous, name)
        ),
        None,
    )
    if worsened is not None:
        return ProgressComparison(outcome="regressed", decisive_dimension=worsened)
    for name in _QUALITY_FIELDS:
        before = getattr(previous, name)
        after = getattr(current, name)
        if before == after:
            continue
        return ProgressComparison(
            outcome="improved" if after < before else "regressed",
            decisive_dimension=name,
        )
    return ProgressComparison(outcome="same")


def _new_record(event: FindingEvent) -> FindingRecord:
    if event.identity is None or event.finding_key is None:
        raise ValueError("discovered finding identity is missing")
    return FindingRecord(
        finding_key=event.finding_key,
        identity_digest=event.identity.identity_digest,
        category=event.category or event.identity.category,
        severity=event.severity or "P3",
        state="open",
        disposition=event.disposition or "advisory",
        blocking=event.blocking,
        candidate_digest=event.candidate_digest,
        evidence_bundle_digests=(event.evidence_bundle_digest,),
        regression_of=event.regression_of,
        late_origin=event.late_origin,
    )


def _alias_record(record: FindingRecord, event: FindingEvent) -> FindingRecord:
    assert event.identity is not None
    evidence = tuple(
        sorted(set((*record.evidence_bundle_digests, event.evidence_bundle_digest)))
    )
    updates = {
        "identity_digest": event.identity.identity_digest,
        "candidate_digest": event.candidate_digest,
        "evidence_bundle_digests": evidence,
    }
    if record.candidate_digest != event.candidate_digest:
        updates.update(
            state="open",
            verification_actor_id="",
            verification_slot_id="",
            verification_capability_id="",
            verification_binding_digest="",
        )
    return record.model_copy(update=updates)


def _transition(record: FindingRecord, event: FindingEvent) -> FindingRecord:
    state = _STATE_BY_EVENT.get(event.event_type, record.state)
    evidence = tuple(
        sorted(set((*record.evidence_bundle_digests, event.evidence_bundle_digest)))
    )
    return record.model_copy(
        update={
            "state": state,
            "candidate_digest": event.candidate_digest,
            "evidence_bundle_digests": evidence,
            "waiver_id": event.waiver_id or record.waiver_id,
            "waiver_digest": event.waiver_digest or record.waiver_digest,
            "replacement_keys": event.replacement_keys or record.replacement_keys,
            "macro_rebaseline_evidence_digest": (
                event.macro_rebaseline_evidence_digest
                or record.macro_rebaseline_evidence_digest
            ),
            "regression_of": event.regression_of or record.regression_of,
            "verification_actor_id": (
                event.actor_id if event.event_type == "verified" else ""
            ),
            "verification_slot_id": (
                event.slot_id if event.event_type == "verified" else ""
            ),
            "verification_capability_id": (
                event.capability_id if event.event_type == "verified" else ""
            ),
            "verification_binding_digest": (
                event.binding_digest if event.event_type == "verified" else ""
            ),
        }
    )


def _is_resolved(
    record: FindingRecord,
    by_key: dict[str, FindingRecord],
    context: FindingCloseContext,
    visiting: set[str],
) -> bool:
    if record.state == "verified":
        return record.candidate_digest == context.candidate_digest and (
            _valid_verification(record, context)
        )
    if record.state == "waived":
        return record.candidate_digest == context.candidate_digest and _valid_waiver(
            record, context
        )
    if record.state != "superseded":
        return False
    if record.macro_rebaseline_evidence_digest:
        return True
    if not record.replacement_keys:
        return False
    if record.finding_key in visiting:
        return False
    nested = {*visiting, record.finding_key}
    replacements = [by_key.get(key) for key in record.replacement_keys]
    return all(
        item is not None and _is_resolved(item, by_key, context, nested)
        for item in replacements
    )


def _valid_waiver(record: FindingRecord, context: FindingCloseContext) -> bool:
    if record.category in context.non_waivable_categories or not record.waiver_id:
        return False
    waiver = next(
        (
            item
            for item in context.waivers
            if item.waiver_id == record.waiver_id
            and item.waiver_digest == record.waiver_digest
        ),
        None,
    )
    if waiver is None or (
        waiver.finding_key != record.finding_key
        or waiver.candidate_digest != context.candidate_digest
        or waiver.policy_digest != context.policy_digest
    ):
        return False
    moment = parse_utc(context.evaluation_at)
    return parse_utc(waiver.issued_at) <= moment < parse_utc(waiver.expires_at)


def _valid_verification(
    record: FindingRecord,
    context: FindingCloseContext,
) -> bool:
    authority = next(
        (
            item
            for item in context.authorities
            if item.actor_id == record.verification_actor_id
            and item.slot_id == record.verification_slot_id
            and item.binding_digest == record.verification_binding_digest
        ),
        None,
    )
    if authority is None or authority.slot_kind != "required":
        return False
    capability = record.verification_capability_id
    return (
        authority.eligible_for_enforce_quorum
        and capability in authority.capability_ids
        and capability in authority.blocking_authorities
        and parse_utc(authority.valid_until) > parse_utc(context.evaluation_at)
    )
