"""Session 工件 current/previous-major 路由与只读迁移。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar, cast

from pydantic import BaseModel

from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.session_artifact_models import (
    ReviewCohort,
    ReviewerPlanRevocation,
    ReviewPass,
)
from ai_sdlc.core.stage_review.session_contracts import SessionIntegrityError
from ai_sdlc.core.stage_review.session_models import (
    SessionEvent,
    SessionOperation,
    StageReviewSession,
)

_ModelT = TypeVar("_ModelT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class _CodecSpec:
    current_version: str
    previous_version: str
    digest_field: str


_SPECS: dict[type[BaseModel], _CodecSpec] = {
    StageReviewSession: _CodecSpec(
        "stage-review-session.v1",
        "stage-review-session.v0",
        "session_digest",
    ),
    SessionEvent: _CodecSpec(
        "stage-review-session-event.v1",
        "stage-review-session-event.v0",
        "event_digest",
    ),
    SessionOperation: _CodecSpec(
        "stage-review-operation.v1",
        "stage-review-operation.v0",
        "operation_digest",
    ),
    ReviewCohort: _CodecSpec("review-cohort.v1", "review-cohort.v0", "cohort_digest"),
    ReviewPass: _CodecSpec("review-pass.v1", "review-pass.v0", "pass_digest"),
    ReviewerPlanRevocation: _CodecSpec(
        "reviewer-plan-revocation.v1",
        "reviewer-plan-revocation.v0",
        "revocation_digest",
    ),
}


def decode_session_artifact(
    model_type: type[_ModelT],
    payload: dict[str, object],
) -> _ModelT:
    spec = _SPECS.get(model_type)
    if spec is None:
        return model_type.model_validate(payload)
    version = str(payload.get("schema_version", ""))
    if version == spec.current_version:
        return model_type.model_validate(payload)
    if version != spec.previous_version:
        raise SessionIntegrityError(
            f"unknown session artifact schema: {model_type.__name__}/{version}"
        )
    return cast(_ModelT, _migrate_previous(model_type, payload, spec))


def _migrate_previous(
    model_type: type[BaseModel],
    payload: dict[str, object],
    spec: _CodecSpec,
) -> BaseModel:
    source_digest = str(payload.get(spec.digest_field, ""))
    protected = {
        key: value for key, value in payload.items() if key != spec.digest_field
    }
    if (
        not source_digest
        or canonical_digest(
            protected,
            CanonicalizationPolicy(),
        )
        != source_digest
    ):
        raise SessionIntegrityError("previous session artifact digest is invalid")
    migrated = _previous_payload(model_type, payload, spec, source_digest)
    return model_type.model_validate(migrated)


def _previous_payload(
    model_type: type[BaseModel],
    payload: dict[str, object],
    spec: _CodecSpec,
    source_digest: str,
) -> dict[str, object]:
    extensions = payload.get("extensions", {})
    if not isinstance(extensions, dict):
        raise SessionIntegrityError("previous session artifact extensions are invalid")
    common: dict[str, object] = {
        **payload,
        "schema_version": spec.current_version,
        "canonicalization_version": "canonical-json.v1",
        "compatibility_mode": "read-only-legacy",
        "extensions": {
            **extensions,
            "source_schema_version": spec.previous_version,
            "source_digest": source_digest,
        },
    }
    if model_type is SessionOperation:
        return _previous_operation(common)
    if model_type in {
        StageReviewSession,
        SessionEvent,
        ReviewCohort,
        ReviewPass,
        ReviewerPlanRevocation,
    }:
        return common
    raise SessionIntegrityError("previous session artifact migration is missing")


def _previous_operation(payload: dict[str, object]) -> dict[str, object]:
    """v0 Operation 没有恢复载荷，只允许作为已完成历史事实读取。"""

    return {
        **payload,
        "command_type": "PreviousMajorCommand",
        "command_payload": {},
    }
