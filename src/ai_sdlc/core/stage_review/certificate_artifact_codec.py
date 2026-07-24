"""关闭证书 current/previous-major 路由与只读迁移。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.certificate_models import StageCloseCertificate

_CURRENT_VERSION = "stage-close-certificate.v1"
_PREVIOUS_VERSION = "stage-close-certificate.v0"


def _decode_certificate_artifact(
    payload: dict[str, object],
) -> StageCloseCertificate:
    version = str(payload.get("schema_version", ""))
    if version == _CURRENT_VERSION:
        return StageCloseCertificate.model_validate(payload)
    if version != _PREVIOUS_VERSION:
        raise ValueError(f"unknown stage close certificate schema: {version}")
    return StageCloseCertificate.model_validate(_migrate_previous(payload))


def _migrate_previous(payload: dict[str, object]) -> dict[str, object]:
    source_digest = str(payload.get("certificate_digest", ""))
    protected = {
        key: value for key, value in payload.items() if key != "certificate_digest"
    }
    if (
        not source_digest
        or canonical_digest(protected, CanonicalizationPolicy()) != source_digest
    ):
        raise ValueError("previous stage close certificate digest is invalid")
    extensions = payload.get("extensions", {})
    if not isinstance(extensions, dict):
        raise ValueError("previous stage close certificate extensions are invalid")
    return {
        **payload,
        "schema_version": _CURRENT_VERSION,
        "canonicalization_version": "canonical-json.v1",
        "compatibility_mode": "read-only-legacy",
        "extensions": {
            **extensions,
            "source_schema_version": _PREVIOUS_VERSION,
            "source_digest": source_digest,
        },
    }
