"""Binding 工件 create-exclusive、bundle 与摘要分派。"""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    create_json_exclusive,
    read_json_object,
)
from ai_sdlc.core.stage_review.binding_availability_models import (
    ProviderAvailabilityAttestation,
)
from ai_sdlc.core.stage_review.binding_models import (
    BindingAttemptOperation,
    BindingAuthoritySnapshot,
    HostCapabilitySnapshot,
    IsolationExecutionEvidence,
    ReviewerRuntimeAllocation,
)
from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBindingResult,
    ReviewerBindingSet,
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_BUNDLE_POLICY = CanonicalizationPolicy(excluded_fields=frozenset({"bundle_digest"}))


def persist_model(
    path: Path,
    value: BaseModel,
    model_type: type[_ModelT],
    expected_digest: str,
    label: str,
) -> None:
    if create_json_exclusive(path, value.model_dump(mode="json")):
        return
    existing = read_model(path, model_type, label)
    if existing is None:
        raise SharedStateIntegrityError(f"{label} create result is inconsistent")
    if model_digest(existing) != expected_digest:
        raise SharedStateIntegrityError(f"{label} immutable identity fork")


def persist_bundle(
    path: Path,
    operation_id: str,
    values: tuple[_ModelT, ...],
    model_type: type[_ModelT],
    label: str,
) -> tuple[_ModelT, ...]:
    ordered = tuple(sorted(values, key=model_digest))
    payload = {
        "operation_id": operation_id,
        "items": [item.model_dump(mode="json") for item in ordered],
    }
    payload["bundle_digest"] = canonical_digest(payload, _BUNDLE_POLICY)
    if create_json_exclusive(path, payload):
        return ordered
    existing = read_bundle(path, operation_id, model_type, label)
    if tuple(map(model_digest, existing)) != tuple(map(model_digest, ordered)):
        raise SharedStateIntegrityError(f"{label} immutable identity fork")
    return existing


def read_bundle(
    path: Path,
    operation_id: str,
    model_type: type[_ModelT],
    label: str,
) -> tuple[_ModelT, ...]:
    if not path.exists():
        return ()
    try:
        payload = read_json_object(path)
        expected = payload.get("bundle_digest")
        items = tuple(model_type.model_validate(item) for item in payload["items"])
    except (KeyError, TypeError, ValidationError, ValueError) as exc:
        raise SharedStateIntegrityError(f"{label} bundle is invalid") from exc
    if payload.get("operation_id") != operation_id:
        raise SharedStateIntegrityError(f"{label} operation identity diverged")
    if expected != canonical_digest(payload, _BUNDLE_POLICY):
        raise SharedStateIntegrityError(f"{label} bundle digest diverged")
    return items


def read_model(
    path: Path,
    model_type: type[_ModelT],
    label: str,
) -> _ModelT | None:
    if not path.exists():
        return None
    try:
        return model_type.model_validate(read_json_object(path))
    except (ValidationError, ValueError) as exc:
        raise SharedStateIntegrityError(f"{label} is invalid") from exc


def model_digest(value: object) -> str:
    if isinstance(value, IsolationExecutionEvidence):
        return value.isolation_evidence_digest
    if isinstance(value, ReviewerRuntimeAllocation):
        return value.allocation_digest
    if isinstance(value, ReviewerBindingSet):
        return value.binding_set_digest
    if isinstance(value, ReviewerBindingResult):
        return value.result_digest
    if isinstance(value, ReviewerDispatchAssignment):
        return value.assignment_digest
    if isinstance(value, (BindingAuthoritySnapshot, HostCapabilitySnapshot)):
        return value.snapshot_digest
    if isinstance(value, ProviderAvailabilityAttestation):
        return value.attestation_digest
    if isinstance(value, BindingAttemptOperation):
        return value.operation_digest
    raise SharedStateIntegrityError("binding artifact has no immutable digest")
