"""稳定 Finding 身份解析；不拥有事件或 Session 状态。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.finding_digests import (
    finding_key,
    mapped_finding_key,
)
from ai_sdlc.core.stage_review.finding_models import (
    FindingIdentityDecision,
    FindingIdentityInput,
    FindingIdentityMapping,
)

RESOLVER_VERSION = "finding-identity.v1"
FINDING_KEY_VERSION = "finding-key.v1"
SEMANTIC_LOCATION_VERSION = "semantic-location.v1"


class FindingIdentityResolver:
    """基于结构签名与显式迁移证明解析唯一 FindingKey。"""

    def resolve(
        self,
        identity: FindingIdentityInput,
        *,
        known: tuple[FindingIdentityDecision, ...] = (),
        mapping: FindingIdentityMapping | None = None,
    ) -> FindingIdentityDecision:
        digest = identity.identity_digest
        expected = finding_key(digest)
        if (
            identity.finding_key_version != FINDING_KEY_VERSION
            or identity.semantic_location_version != SEMANTIC_LOCATION_VERSION
        ):
            return _needs_user(expected, digest, "finding.identity-version-unsupported")
        if mapping is None and (
            identity.asset_lineage_ref
            or identity.supersedes_finding_key
            or identity.identity_decision_evidence
        ):
            return _needs_user(expected, digest, "finding.identity-mapping-required")
        exact = tuple(item for item in known if item.identity_digest == digest)
        if exact:
            keys = {item.finding_key for item in exact}
            if len(keys) != 1:
                return _needs_user(expected, digest, "finding.identity-collision")
            return _matched(next(iter(keys)), digest)
        if any(item.finding_key == expected for item in known):
            return _needs_user(expected, digest, "finding.identity-collision")
        if mapping is not None:
            return self._resolve_mapping(digest, known, mapping)
        if known:
            return _needs_user(expected, digest, "finding.identity-lineage-unknown")
        return FindingIdentityDecision(
            finding_key=expected,
            identity_digest=digest,
            status="new",
            resolver_version=RESOLVER_VERSION,
        )

    def _resolve_mapping(
        self,
        digest: str,
        known: tuple[FindingIdentityDecision, ...],
        mapping: FindingIdentityMapping,
    ) -> FindingIdentityDecision:
        known_keys = {item.finding_key for item in known}
        if mapping.resolver_version != RESOLVER_VERSION:
            return _needs_user(
                finding_key(digest), digest, "finding.identity-resolver-version"
            )
        if digest not in mapping.target_identity_digests:
            return _needs_user(
                finding_key(digest), digest, "finding.identity-target-unmapped"
            )
        if not set(mapping.source_keys).issubset(known_keys):
            return _needs_user(
                finding_key(digest), digest, "finding.identity-source-missing"
            )
        if mapping.mapping_kind == "alias":
            return FindingIdentityDecision(
                finding_key=mapping.source_keys[0],
                identity_digest=digest,
                status="matched",
                resolver_version=RESOLVER_VERSION,
                source_keys=mapping.source_keys,
                mapping_evidence_digest=mapping.evidence_digest,
            )
        key = mapped_finding_key(
            mapping.mapping_kind,
            mapping.source_keys,
            digest,
            mapping.resolver_version,
        )
        if key in mapping.source_keys:
            return _needs_user(key, digest, "finding.identity-cycle")
        return FindingIdentityDecision(
            finding_key=key,
            identity_digest=digest,
            status="new",
            resolver_version=RESOLVER_VERSION,
            source_keys=mapping.source_keys,
            mapping_evidence_digest=mapping.evidence_digest,
        )


def _matched(key: str, digest: str) -> FindingIdentityDecision:
    return FindingIdentityDecision(
        finding_key=key,
        identity_digest=digest,
        status="matched",
        resolver_version=RESOLVER_VERSION,
    )


def _needs_user(key: str, digest: str, reason: str) -> FindingIdentityDecision:
    return FindingIdentityDecision(
        finding_key=key,
        identity_digest=digest,
        status="needs_user",
        resolver_version=RESOLVER_VERSION,
        reason_id=reason,
    )
