"""ResourceGovernor 工件的 canonical digest。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
    canonical_payload,
)

_RUNTIME_FIELDS = frozenset({"created_at", "created_by", "ai_sdlc_version"})
_ENVELOPE_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME_FIELDS | {"envelope_digest"}
)
_CONFIG_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME_FIELDS | {"config_digest"}
)
_RESERVATION_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME_FIELDS | {"reservation_digest"}
)
_EVENT_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME_FIELDS | {"event_digest"}
)
_NORMALIZED_POLICY = CanonicalizationPolicy()
_STATE_POLICY = CanonicalizationPolicy(excluded_fields=frozenset({"state_digest"}))
_RECONCILIATION_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME_FIELDS | {"reconciliation_digest"}
)
_OPERATION_EFFECT_POLICY = CanonicalizationPolicy()
_BUDGET_GRANT_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME_FIELDS | {"grant_digest", "idempotency_key", "grant_id"}
)
_BUDGET_GRANT_OPERATION_POLICY = CanonicalizationPolicy(
    excluded_fields=_RUNTIME_FIELDS | {"operation_digest"}
)


def budget_envelope_digest(value: object) -> str:
    return canonical_digest(value, _ENVELOPE_POLICY)


def resource_config_digest(value: object) -> str:
    return canonical_digest(value, _CONFIG_POLICY)


def reservation_digest(value: object) -> str:
    return canonical_digest(
        _without_empty_observed_overrun(canonical_payload(value, _RESERVATION_POLICY)),
        _NORMALIZED_POLICY,
    )


def resource_event_digest(value: object) -> str:
    payload = _without_empty_observed_overrun(canonical_payload(value, _EVENT_POLICY))
    if isinstance(payload, dict) and payload.get("reconciled_event_digest") == "":
        # 新字段为空时保持旧账本的规范字节不变；非空引用仍进入摘要。
        payload.pop("reconciled_event_digest")
    return canonical_digest(payload, _NORMALIZED_POLICY)


def resource_state_digest(value: object) -> str:
    return canonical_digest(
        _without_empty_observed_overrun(canonical_payload(value, _STATE_POLICY)),
        _NORMALIZED_POLICY,
    )


def reconciliation_digest(value: object) -> str:
    return canonical_digest(value, _RECONCILIATION_POLICY)


def resource_operation_effect_digest(
    operation_kind: str,
    semantic_input: object,
) -> str:
    return canonical_digest(
        {"operation_kind": operation_kind, "semantic_input": semantic_input},
        _OPERATION_EFFECT_POLICY,
    )


def budget_grant_digest(value: object) -> str:
    return canonical_digest(value, _BUDGET_GRANT_POLICY)


def budget_grant_operation_digest(value: object) -> str:
    return canonical_digest(value, _BUDGET_GRANT_OPERATION_POLICY)


def _without_empty_observed_overrun(value: object) -> object:
    if isinstance(value, list):
        return [_without_empty_observed_overrun(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized = {
        key: _without_empty_observed_overrun(item) for key, item in value.items()
    }
    overrun = normalized.get("observed_overrun")
    if isinstance(overrun, dict) and not any(overrun.values()):
        normalized.pop("observed_overrun")
    return normalized
