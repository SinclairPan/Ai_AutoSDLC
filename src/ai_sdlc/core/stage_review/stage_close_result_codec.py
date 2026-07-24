"""持久化产品关闭返回值，供 Exactly-Once 重试只读恢复。"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, field_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    JsonValue,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    create_json_exclusive,
    read_json_object,
)
from ai_sdlc.core.stage_review.close_gate_models import PreparedStageClose
from ai_sdlc.core.stage_review.close_gate_observation import stage_close_operation_id

_ALLOWED_RESULT_TYPES = frozenset(
    {
        "ai_sdlc.core.design_contract_loop.DesignContractCommandResult",
        "ai_sdlc.core.frontend_evidence_loop.FrontendEvidenceCommandResult",
        "ai_sdlc.core.implementation_models.ImplementationCommandResult",
        "ai_sdlc.core.pr_review_service.PRReviewAttestResult",
        "ai_sdlc.core.pr_review_service.PRReviewCloseResult",
        "ai_sdlc.core.requirement_loop.RequirementLoopCommandResult",
    }
)


class StageCloseProductResult(ArtifactCompatibility):
    schema_version: Literal["stage-close-product-result.v1"] = (
        "stage-close-product-result.v1"
    )
    artifact_kind: Literal["stage-close-product-result"] = (
        "stage-close-product-result"
    )
    operation_id: str
    adapter_id: str
    close_kind: str
    result_kind: Literal["mapping", "model"]
    result_type: str = ""
    payload: dict[str, JsonValue]
    result_digest: str = ""

    @field_validator("result_type")
    @classmethod
    def _type_is_governed(cls, value: str) -> str:
        if value and value not in _ALLOWED_RESULT_TYPES:
            raise ValueError("stage close result type is not governed")
        return value

    def model_post_init(self, __context: object) -> None:
        fill_artifact_digest(self, "result_digest")


def product_result_path(prepared: PreparedStageClose) -> Path:
    operation_id = stage_close_operation_id(prepared)
    return (
        prepared.root
        / ".ai-sdlc/state/stage-close-results"
        / f"{operation_id}.json"
    )


def persist_product_result(
    prepared: PreparedStageClose,
    result: object,
) -> StageCloseProductResult:
    envelope = _encode(prepared, result)
    path = product_result_path(prepared)
    if create_json_exclusive(path, envelope.model_dump(mode="json")):
        return envelope
    existing = StageCloseProductResult.model_validate(read_json_object(path))
    if existing != envelope:
        raise SharedStateIntegrityError("stage close product result diverged")
    return existing


def recover_product_result(prepared: PreparedStageClose) -> object:
    path = product_result_path(prepared)
    if not path.is_file():
        raise SharedStateIntegrityError("stage close product result is unavailable")
    envelope = StageCloseProductResult.model_validate(read_json_object(path))
    expected = (stage_close_operation_id(prepared), prepared.adapter_id, prepared.close_kind)
    if (
        envelope.operation_id,
        envelope.adapter_id,
        envelope.close_kind,
    ) != expected:
        raise SharedStateIntegrityError("stage close product result lineage diverged")
    if envelope.result_kind == "mapping":
        return dict(envelope.payload)
    model = _result_model(envelope.result_type)
    return model.model_validate(envelope.payload)


def _encode(
    prepared: PreparedStageClose,
    result: object,
) -> StageCloseProductResult:
    if isinstance(result, BaseModel):
        result_type = f"{type(result).__module__}.{type(result).__qualname__}"
        if result_type not in _ALLOWED_RESULT_TYPES:
            raise ValueError("stage close result model is not governed")
        payload = cast(dict[str, JsonValue], result.model_dump(mode="json"))
        kind: Literal["mapping", "model"] = "model"
    elif isinstance(result, dict):
        result_type = ""
        payload = cast(dict[str, JsonValue], dict(result))
        kind = "mapping"
    else:
        raise ValueError("stage close result must be a model or mapping")
    return StageCloseProductResult(
        operation_id=stage_close_operation_id(prepared),
        adapter_id=prepared.adapter_id,
        close_kind=prepared.close_kind,
        result_kind=kind,
        result_type=result_type,
        payload=payload,
    )


def _result_model(type_name: str) -> type[BaseModel]:
    if type_name not in _ALLOWED_RESULT_TYPES:
        raise SharedStateIntegrityError("stage close result type is not governed")
    module_name, class_name = type_name.rsplit(".", 1)
    value = getattr(import_module(module_name), class_name, None)
    if not isinstance(value, type) or not issubclass(value, BaseModel):
        raise SharedStateIntegrityError("stage close result model is unavailable")
    return value


__all__ = [
    "StageCloseProductResult",
    "persist_product_result",
    "product_result_path",
    "recover_product_result",
]
