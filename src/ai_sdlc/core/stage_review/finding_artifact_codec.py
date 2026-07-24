"""Finding 工件按 artifact kind 与 schema version 统一解码。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel, ValidationError

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.finding_models import FindingEvent, FindingLedger
from ai_sdlc.core.stage_review.finding_support_models import ProgressSnapshot
from ai_sdlc.core.stage_review.finding_trust_models import (
    FindingWaiver,
    InitialReviewSeal,
    TrustedEvidenceDescriptor,
    TrustedIdentityMappingDecision,
)


@dataclass(frozen=True)
class _DecoderSpec:
    model: type[BaseModel]
    digest_field: str


_COMPATIBILITY_FIELDS = (
    "canonicalization_version",
    "compatibility_mode",
    "extensions",
)
_LEGACY_PROOF_KEYS = frozenset({"source_digest", "missing_envelope_fields"})
_CURRENT_WRITE_VERSIONS = {
    "finding-event": "finding-event.v2",
    "finding-ledger": "finding-ledger.v2",
}


def decode_finding_artifact(
    artifact_kind: str,
    payload: dict[str, object],
) -> BaseModel:
    version = payload.get("schema_version")
    key = (artifact_kind, version) if isinstance(version, str) else None
    spec = _DECODERS.get(key) if key is not None else None
    if spec is None:
        raise SharedStateIntegrityError(f"{artifact_kind} schema is unsupported")
    if payload.get(spec.digest_field) != _payload_digest(payload, spec.digest_field):
        raise SharedStateIntegrityError(f"{artifact_kind} digest mismatch")
    try:
        return spec.model.model_validate(_compatible_payload(payload, spec))
    except ValidationError as exc:
        raise SharedStateIntegrityError(f"{version} is invalid") from exc


def validate_finding_artifact_for_write(
    artifact_kind: str,
    artifact: BaseModel,
) -> None:
    version = getattr(artifact, "schema_version", None)
    spec = _DECODERS.get((artifact_kind, version)) if isinstance(version, str) else None
    if spec is None:
        raise SharedStateIntegrityError(f"{artifact_kind} schema is unsupported")
    current = _CURRENT_WRITE_VERSIONS.get(artifact_kind, version)
    if version != current:
        raise SharedStateIntegrityError(f"{artifact_kind} previous schema is read-only")
    if getattr(artifact, "compatibility_mode", None) != "strict":
        raise SharedStateIntegrityError(f"{artifact_kind} is read-only legacy")
    payload = _strict_write_payload(artifact_kind, artifact, spec)
    if payload.get(spec.digest_field) != _payload_digest(payload, spec.digest_field):
        raise SharedStateIntegrityError(f"{artifact_kind} digest mismatch before write")


def _strict_write_payload(
    artifact_kind: str,
    artifact: BaseModel,
    spec: _DecoderSpec,
) -> dict[str, object]:
    if type(artifact) is not spec.model:
        raise SharedStateIntegrityError(f"{artifact_kind} model contract is invalid")
    raw = _raw_model_fields(artifact)
    try:
        validated = spec.model.model_validate(raw, strict=True)
    except ValidationError as exc:
        raise SharedStateIntegrityError(
            f"{artifact_kind} model contract is invalid before write"
        ) from exc
    if _raw_model_fields(validated) != raw:
        raise SharedStateIntegrityError(
            f"{artifact_kind} model contract changed during write validation"
        )
    return validated.model_dump(mode="json")


def _raw_model_fields(model: BaseModel) -> dict[str, object]:
    return {
        name: _raw_python_value(getattr(model, name))
        for name in type(model).model_fields
    }


def _raw_python_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return _raw_model_fields(value)
    if isinstance(value, Mapping):
        return {key: _raw_python_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_raw_python_value(item) for item in value)
    if isinstance(value, list):
        return [_raw_python_value(item) for item in value]
    return value


def _payload_digest(payload: dict[str, object], digest_field: str) -> str:
    policy = CanonicalizationPolicy(excluded_fields=frozenset({digest_field}))
    return canonical_digest(payload, policy)


def _compatible_payload(
    payload: dict[str, object],
    spec: _DecoderSpec,
) -> dict[str, object]:
    missing = tuple(field for field in _COMPATIBILITY_FIELDS if field not in payload)
    if not missing:
        if payload.get("compatibility_mode") != "strict":
            raise SharedStateIntegrityError("persisted compatibility mode is invalid")
        return payload
    normalized = dict(payload)
    extensions = normalized.get("extensions", {})
    if not isinstance(extensions, dict):
        raise SharedStateIntegrityError("legacy compatibility extensions are invalid")
    if _LEGACY_PROOF_KEYS.intersection(extensions):
        raise SharedStateIntegrityError("legacy compatibility proof keys conflict")
    normalized.setdefault("canonicalization_version", "canonical-json.v1")
    normalized["compatibility_mode"] = "read-only-legacy"
    normalized["extensions"] = {
        **extensions,
        "source_digest": payload[spec.digest_field],
        "missing_envelope_fields": list(missing),
    }
    return normalized


def finding_event_contracts_match(
    expected: FindingEvent,
    persisted: FindingEvent,
) -> bool:
    return _event_contract(expected, persisted) == _event_contract(persisted, persisted)


def _event_contract(
    event: FindingEvent,
    compatibility_reference: FindingEvent,
) -> dict[str, object]:
    payload = event.model_dump(exclude={"event_digest"}, mode="json")
    payload["schema_version"] = compatibility_reference.schema_version
    if compatibility_reference.compatibility_mode != "read-only-legacy":
        return payload
    missing = _legacy_missing_envelope(compatibility_reference)
    for field in missing:
        payload.pop(field, None)
    if "extensions" not in missing:
        extensions = cast(dict[str, object], payload["extensions"])
        payload["extensions"] = {
            key: value
            for key, value in extensions.items()
            if key not in _LEGACY_PROOF_KEYS
        }
    return payload


def _legacy_missing_envelope(event: FindingEvent) -> tuple[str, ...]:
    raw = event.extensions.get("missing_envelope_fields")
    source_digest = event.extensions.get("source_digest")
    if not isinstance(raw, (list, tuple)) or source_digest != event.event_digest:
        raise SharedStateIntegrityError("legacy event compatibility proof is invalid")
    missing = tuple(raw)
    if not missing or any(field not in _COMPATIBILITY_FIELDS for field in missing):
        raise SharedStateIntegrityError("legacy event compatibility fields are invalid")
    return cast(tuple[str, ...], missing)


def decode_finding_event(payload: dict[str, object]) -> FindingEvent:
    return cast(FindingEvent, decode_finding_artifact("finding-event", payload))


def decode_finding_ledger(payload: dict[str, object]) -> FindingLedger:
    return cast(FindingLedger, decode_finding_artifact("finding-ledger", payload))


def decode_finding_waiver(payload: dict[str, object]) -> FindingWaiver:
    return cast(FindingWaiver, decode_finding_artifact("finding-waiver", payload))


_DECODERS = {
    ("finding-event", "finding-event.v1"): _DecoderSpec(FindingEvent, "event_digest"),
    ("finding-event", "finding-event.v2"): _DecoderSpec(FindingEvent, "event_digest"),
    ("finding-ledger", "finding-ledger.v1"): _DecoderSpec(
        FindingLedger, "ledger_digest"
    ),
    ("finding-ledger", "finding-ledger.v2"): _DecoderSpec(
        FindingLedger, "ledger_digest"
    ),
    ("finding-waiver", "finding-waiver.v1"): _DecoderSpec(
        FindingWaiver, "waiver_digest"
    ),
    ("initial-review-seal", "initial-review-seal.v1"): _DecoderSpec(
        InitialReviewSeal, "seal_digest"
    ),
    ("finding-evidence", "finding-evidence.v1"): _DecoderSpec(
        TrustedEvidenceDescriptor, "descriptor_digest"
    ),
    ("identity-mapping-decision", "identity-mapping-decision.v1"): _DecoderSpec(
        TrustedIdentityMappingDecision, "decision_digest"
    ),
    ("progress-snapshot", "progress-snapshot.v1"): _DecoderSpec(
        ProgressSnapshot, "snapshot_digest"
    ),
}
