"""离线优化固定流水线的不可变阶段结果存储。"""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    CandidateGenerationResult,
    PipelineHoldoutResult,
    PipelinePromotionPackage,
    PipelinePublicationResult,
    PipelineReplayResult,
    PipelineShadowResult,
    PipelineSnapshotResult,
)
from ai_sdlc.core.stage_review.registry_versions import require_machine_id

T = TypeVar("T", bound=BaseModel)


class OptimizationPipelineStore:
    _MODELS: dict[str, type[BaseModel]] = {
        "snapshotting": PipelineSnapshotResult,
        "generating": CandidateGenerationResult,
        "replaying": PipelineReplayResult,
        "holdout_evaluating": PipelineHoldoutResult,
        "shadow_observing": PipelineShadowResult,
        "evaluating": PipelinePromotionPackage,
        "promoting": PipelinePublicationResult,
    }

    def __init__(self, root: Path, *, project_id: str) -> None:
        self.project_id = require_machine_id(project_id, "project_id")
        shared = resolve_canonical_shared_state(root, self.project_id)
        bind_repository_project(shared, self.project_id)
        self.root = shared / "offline-optimization" / "pipeline"

    def read(self, epoch_id: str, stage: str, model: type[T]) -> T | None:
        _verify_model(stage, model)
        path = self._path(epoch_id, stage)
        if not path.is_file():
            return None
        return model.model_validate(read_json_object(path))

    def write(self, epoch_id: str, stage: str, value: T) -> T:
        expected = type(value)
        _verify_model(stage, expected)
        trusted = expected.model_validate(value.model_dump(mode="json"))
        path = self._path(epoch_id, stage)
        if create_json_exclusive(path, trusted.model_dump(mode="json")):
            return trusted
        existing = expected.model_validate(read_json_object(path))
        if existing != trusted:
            raise SharedStateIntegrityError("optimization pipeline stage diverged")
        return existing

    def _path(self, epoch_id: str, stage: str) -> Path:
        stable = require_machine_id(epoch_id, "epoch_id")
        if stage not in self._MODELS:
            raise ValueError("optimization pipeline stage is unknown")
        return self.root / stable / f"{stage}.json"


def _verify_model(stage: str, model: type[BaseModel]) -> None:
    if OptimizationPipelineStore._MODELS.get(stage) is not model:
        raise ValueError("optimization pipeline stage model is invalid")
