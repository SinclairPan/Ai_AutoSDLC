"""Finding 工件的确定性摘要与稳定标识。"""

from __future__ import annotations

import hashlib

from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_bytes,
    canonical_digest,
)

_EVENT_POLICY = CanonicalizationPolicy(excluded_fields=frozenset({"event_digest"}))
_LEDGER_POLICY = CanonicalizationPolicy(excluded_fields=frozenset({"ledger_digest"}))


def stable_finding_id(prefix: str, *parts: str) -> str:
    encoded = canonical_bytes(parts, CanonicalizationPolicy())
    return f"{prefix}." + hashlib.sha256(encoded).hexdigest()[:24]


def finding_key(identity_digest: str) -> str:
    return stable_finding_id("finding", identity_digest)


def mapped_finding_key(
    mapping_kind: str,
    source_keys: tuple[str, ...],
    identity_digest: str,
    resolver_version: str,
) -> str:
    return stable_finding_id(
        "finding",
        mapping_kind,
        *source_keys,
        identity_digest,
        resolver_version,
    )


def command_digest(command: object) -> str:
    return canonical_digest(command, CanonicalizationPolicy())


def scope_digest(scope: object) -> str:
    return canonical_digest(scope, CanonicalizationPolicy())


def initial_finding_batch_digest(findings: object) -> str:
    return canonical_digest(findings, CanonicalizationPolicy())


def event_digest(event: object) -> str:
    return canonical_digest(event, _EVENT_POLICY)


def persisted_event_digest(payload: dict[str, object]) -> str:
    return canonical_digest(payload, _EVENT_POLICY)


def ledger_digest(ledger: object) -> str:
    return canonical_digest(ledger, _LEDGER_POLICY)
