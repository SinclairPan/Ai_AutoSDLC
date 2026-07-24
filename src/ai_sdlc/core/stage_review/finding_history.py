"""Finding 事件历史的纯查询与替代图判定。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.finding_models import (
    FindingEvent,
    FindingIdentityDecision,
    FindingRecord,
)
from ai_sdlc.core.stage_review.finding_reducer import reduce_finding_events


def record_for(
    events: tuple[FindingEvent, ...],
    finding_key: str | None,
    *,
    required: bool = True,
) -> FindingRecord | None:
    if finding_key is None:
        if required:
            raise ValueError("finding reference is required")
        return None
    ledger = reduce_finding_events(events[0].scope, events) if events else None
    record = (
        next((item for item in ledger.records if item.finding_key == finding_key), None)
        if ledger
        else None
    )
    if required and record is None:
        raise ValueError("finding reference does not exist")
    return record


def known_identity_decisions(
    events: tuple[FindingEvent, ...],
) -> tuple[FindingIdentityDecision, ...]:
    records = reduce_finding_events(events[0].scope, events).records
    return tuple(
        FindingIdentityDecision(
            finding_key=item.finding_key,
            identity_digest=item.identity_digest,
            status="matched",
            resolver_version="finding-identity.v1",
        )
        for item in records
    )


def replacement_cycle_exists(
    events: tuple[FindingEvent, ...],
    source_key: str,
    replacement_keys: tuple[str, ...],
) -> bool:
    records = {
        item.finding_key: item
        for item in reduce_finding_events(events[0].scope, events).records
    }
    return any(_reaches(key, source_key, records, set()) for key in replacement_keys)


def required_replacement_keys(
    events: tuple[FindingEvent, ...],
    source_key: str,
) -> tuple[str, ...]:
    relations = reduce_finding_events(events[0].scope, events).identity_relations
    targets = {
        key
        for relation in relations
        if relation.mapping_kind in {"split", "merge", "supersede"}
        and source_key in relation.source_keys
        for key in relation.target_keys
    }
    return tuple(sorted(targets))


def _reaches(
    start: str,
    target: str,
    records: dict[str, FindingRecord],
    visited: set[str],
) -> bool:
    if start == target:
        return True
    if start in visited or start not in records:
        return False
    return any(
        _reaches(key, target, records, {*visited, start})
        for key in records[start].replacement_keys
    )
