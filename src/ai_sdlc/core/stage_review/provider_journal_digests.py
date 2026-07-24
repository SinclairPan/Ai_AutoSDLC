"""Provider Journal 工件的兼容规范化与内容摘要。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
    canonical_payload,
)

_RUNTIME = frozenset({"created_at", "created_by", "ai_sdlc_version"})
_REQUEST_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME | {"request_artifact_digest"}
)
_LEGACY_REQUEST_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME | {"request_artifact_digest", "authorization_scope"}
)
_SUBMISSION_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME | {"submission_digest"}
)
_EVENT_POLICY = CanonicalizationPolicy(excluded_fields=_RUNTIME | {"event_digest"})
_LEGACY_EVENT_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME | {"event_digest", "request.authorization_scope"}
)
_PROJECTION_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME | {"projection_digest"}
)
_LEGACY_PROJECTION_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME | {"projection_digest", "request.authorization_scope"}
)
_OUTPUT_POLICY = CanonicalizationPolicy()


def request_artifact_digest(value: object) -> str:
    policy = _LEGACY_REQUEST_POLICY if _uses_legacy_request(value) else _REQUEST_POLICY
    return cast(str, canonical_digest(value, policy))


def submission_digest(value: object) -> str:
    return cast(str, canonical_digest(value, _SUBMISSION_POLICY))


def event_digest(value: object) -> str:
    policy = _LEGACY_EVENT_POLICY if _uses_legacy_request(value) else _EVENT_POLICY
    return cast(str, canonical_digest(value, policy))


def projection_digest(value: object) -> str:
    policy = (
        _LEGACY_PROJECTION_POLICY if _uses_legacy_request(value) else _PROJECTION_POLICY
    )
    return cast(str, canonical_digest(value, policy))


def canonical_provider_output(value: object) -> dict[str, object]:
    payload = canonical_payload(value, _OUTPUT_POLICY)
    if not isinstance(payload, dict):
        raise ValueError("provider output must be a JSON object")
    return payload


def provider_output_digest(value: object) -> str:
    return cast(str, canonical_digest(value, _OUTPUT_POLICY))


def _uses_legacy_request(value: object) -> bool:
    request = getattr(value, "request", value)
    if hasattr(request, "authorization_scope"):
        return request.authorization_scope is None
    if isinstance(request, Mapping):
        nested = request.get("request", request)
        return isinstance(nested, Mapping) and nested.get("authorization_scope") is None
    return False


__all__: list[str] = []
