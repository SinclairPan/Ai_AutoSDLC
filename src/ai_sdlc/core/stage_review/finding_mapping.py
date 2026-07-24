"""可信身份迁移决策的单一校验入口。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.finding_digests import mapped_finding_key
from ai_sdlc.core.stage_review.finding_models import (
    FindingEvent,
    FindingIdentityMapping,
    FindingIdentityRelation,
    FindingScope,
)
from ai_sdlc.core.stage_review.finding_trust_models import FindingTrustResolver


def require_trusted_mapping(
    mapping: FindingIdentityMapping | None,
    scope: FindingScope,
    candidate_digest: str,
    resolver: FindingTrustResolver,
) -> None:
    if mapping is None:
        return
    decision = resolver.resolve_mapping(scope, mapping.evidence_digest)
    actual = (
        mapping.mapping_kind,
        mapping.source_keys,
        mapping.target_identity_digests,
        mapping.resolver_version,
    )
    expected = (
        (
            decision.mapping_kind,
            decision.source_keys,
            decision.target_identity_digests,
            decision.resolver_version,
        )
        if decision is not None
        else None
    )
    if (
        decision is None
        or decision.decision_digest != mapping.evidence_digest
        or decision.scope != scope
        or decision.candidate_digest != candidate_digest
        or actual != expected
    ):
        raise ValueError("finding identity mapping needs trusted decision")


def identity_relation_from_event(
    event: FindingEvent,
) -> FindingIdentityRelation | None:
    mapping = event.identity_mapping
    if mapping is None:
        return None
    assert event.identity is not None and event.finding_key is not None
    return FindingIdentityRelation(
        mapping_kind=mapping.mapping_kind,
        source_keys=mapping.source_keys,
        target_keys=tuple(
            mapped_finding_key(
                mapping.mapping_kind,
                mapping.source_keys,
                digest,
                mapping.resolver_version,
            )
            if mapping.mapping_kind != "alias"
            else mapping.source_keys[0]
            for digest in mapping.target_identity_digests
        ),
        target_identity_digests=mapping.target_identity_digests,
        evidence_digest=mapping.evidence_digest,
        resolver_version=mapping.resolver_version,
    )
