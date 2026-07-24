"""Reviewer Binding 工件的确定性摘要。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)

_RUNTIME = frozenset({"created_at", "created_by", "ai_sdlc_version"})


def _artifact_digest(value: object, field: str) -> str:
    return canonical_digest(
        value,
        CanonicalizationPolicy(excluded_fields=_RUNTIME | {field}),
    )


def provider_descriptor_digest(value: object) -> str:
    return _artifact_digest(value, "descriptor_digest")


def binding_authority_digest(value: object) -> str:
    model_dump = getattr(value, "model_dump", None)
    descriptors = getattr(value, "provider_descriptors", ())
    if model_dump is None:
        raise TypeError("binding authority digest requires a model artifact")
    payload = model_dump(mode="json")
    payload["provider_descriptors"] = tuple(
        descriptor.descriptor_digest for descriptor in descriptors
    )
    return canonical_digest(
        payload,
        CanonicalizationPolicy(excluded_fields=_RUNTIME | {"snapshot_digest"}),
    )


def provider_availability_digest(value: object) -> str:
    return _artifact_digest(value, "attestation_digest")


def host_capability_digest(value: object) -> str:
    return _artifact_digest(value, "snapshot_digest")


def runtime_allocation_digest(value: object) -> str:
    return _artifact_digest(value, "allocation_digest")


def isolation_evidence_digest(value: object) -> str:
    return _artifact_digest(value, "isolation_evidence_digest")


def binding_attempt_request_digest(value: object) -> str:
    return _artifact_digest(value, "request_digest")


def binding_attempt_operation_digest(value: object) -> str:
    return _artifact_digest(value, "operation_digest")


def reviewer_binding_digest(value: object) -> str:
    return canonical_digest(value, CanonicalizationPolicy())


def reviewer_binding_set_digest(value: object) -> str:
    return _artifact_digest(value, "binding_set_digest")


def rebind_directive_digest(value: object) -> str:
    return canonical_digest(
        value,
        CanonicalizationPolicy(excluded_fields=frozenset({"directive_digest"})),
    )


def binding_result_digest(value: object) -> str:
    return _artifact_digest(value, "result_digest")


def dispatch_assignment_digest(value: object) -> str:
    digest = _artifact_digest(value, "assignment_digest")
    return f"reviewer-assignment:{digest}"
