"""候选生成器唯一可见的 Train/Validation 数据能力视图。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.datasets import (
    DatasetPopulationEntry,
    OptimizationDatasetSnapshot,
)


class CandidateDatasetView(ArtifactCompatibility):
    schema_version: Literal["candidate-dataset-view.v1"] = "candidate-dataset-view.v1"
    artifact_kind: Literal["candidate-dataset-view"] = "candidate-dataset-view"
    project_id: str
    source_dataset_digest: str
    baseline_snapshot_digest: str
    train_session_ids: tuple[str, ...]
    population: tuple[DatasetPopulationEntry, ...]
    attributions: tuple[FindingAttribution, ...]
    view_digest: str = ""

    @field_validator("train_session_ids")
    @classmethod
    def _sessions_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("candidate view sessions must be canonical")
        return value

    @model_validator(mode="after")
    def _verify_isolation(self) -> Self:
        train = set(self.train_session_ids)
        population_ids = {item.session_id for item in self.population}
        attribution_ids = {item.session_id for item in self.attributions}
        if population_ids != train:
            raise ValueError("candidate view partition isolation diverged")
        if not attribution_ids <= train:
            raise ValueError("candidate view contains inaccessible attribution")
        if any(
            item.project_id != self.project_id
            or item.status != "candidate_authorized"
            for item in self.attributions
        ):
            raise ValueError("candidate view attribution is not authorized")
        return fill_artifact_digest(self, "view_digest")


def _build_candidate_dataset_view(
    dataset: OptimizationDatasetSnapshot,
    attributions: tuple[FindingAttribution, ...],
) -> CandidateDatasetView:
    train = dataset.partition_assignment["train"]
    eligible = set(train)
    population = tuple(
        sorted(
            (item for item in dataset.population if item.session_id in eligible),
            key=lambda item: item.session_id,
        )
    )
    trusted = tuple(
        sorted(
            (
                item
                for item in attributions
                if item.project_id == dataset.project_id
                and item.session_id in eligible
                and item.status == "candidate_authorized"
            ),
            key=lambda item: item.attribution_digest,
        )
    )
    return CandidateDatasetView(
        project_id=dataset.project_id,
        source_dataset_digest=dataset.dataset_digest,
        baseline_snapshot_digest=dataset.baseline_snapshot_digest,
        train_session_ids=train,
        population=population,
        attributions=trusted,
    )
