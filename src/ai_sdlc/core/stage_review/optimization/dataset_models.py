"""Optimization 数据集的版本化、内容寻址模型。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.resource_builders import parse_utc
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts

PartitionKind = Literal["train", "validation", "holdout", "prospective_shadow"]


class DatasetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    holdout_ratio: float = Field(default=0.2, gt=0, lt=1)
    minimum_holdout_size: int = Field(default=10, ge=1)
    validation_ratio: float = Field(default=0.2, gt=0, lt=1)


class DatasetPopulationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    session_id: str
    initial_candidate_digest: str
    stage_key: str
    risk_level: str
    candidate_size_bucket: str
    provider_ids: tuple[str, ...]
    active_snapshot_digest: str
    usage_estimation_policy_version: str
    usage_estimation_policy_digest: str
    control_sequence: int
    committed_at: str
    evaluable: bool
    terminal_outcome: str = ""
    censoring_reason: str = ""
    observation_digests: tuple[str, ...] = ()
    finding_event_digests: tuple[str, ...] = ()
    role_profile_ids: tuple[str, ...] = ()
    reviewer_slot_ids: tuple[str, ...] = ()
    capability_ids: tuple[str, ...] = ()
    binding_digests: tuple[str, ...] = ()
    binding_set_digest: str = ""
    resource_reservation_digest: str = ""
    risk_profile_digest: str = ""
    cohort_id: str = ""
    finding_ledger_digest: str = ""
    convergence_outcome_digest: str = ""
    label_source_digests: tuple[str, ...] = ()
    resource_usage: ResourceAmounts = Field(default_factory=ResourceAmounts)

    @field_validator(
        "provider_ids",
        "observation_digests",
        "finding_event_digests",
        "role_profile_ids",
        "reviewer_slot_ids",
        "capability_ids",
        "binding_digests",
        "label_source_digests",
    )
    @classmethod
    def _sets_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("dataset population lineage must be canonical")
        return value


class OptimizationDatasetSnapshot(ArtifactCompatibility):
    schema_version: Literal["optimization-dataset-snapshot.v1"] = (
        "optimization-dataset-snapshot.v1"
    )
    artifact_kind: Literal["optimization-dataset-snapshot"] = (
        "optimization-dataset-snapshot"
    )
    project_id: str
    epoch_started_at: str
    session_sequence_high_watermark: int = Field(ge=0)
    trigger_fingerprint: str
    constitution_digest: str
    baseline_snapshot_digest: str
    comparison_usage_estimation_policy_version: str
    comparison_usage_estimation_policy_digest: str
    holdout_generation_id: str
    population: tuple[DatasetPopulationEntry, ...]
    session_population_ids: tuple[str, ...]
    evaluable_session_ids: tuple[str, ...]
    censoring_reasons: dict[str, str]
    partition_assignment: dict[str, tuple[str, ...]]
    partition_exclusions: dict[str, str] = Field(default_factory=dict)
    finding_event_digests: tuple[str, ...] = ()
    finding_attribution_digests: tuple[str, ...] = ()
    late_critical_finding_event_digests: tuple[str, ...] = ()
    reviewer_coverage_leak_event_digests: tuple[str, ...] = ()
    label_source_digests: tuple[str, ...] = ()
    unknown_or_censored_rate: float = Field(ge=0, le=1)
    leakage_check_passed: bool
    data_integrity_digest: str
    dataset_digest: str = ""

    @field_validator(
        "finding_event_digests",
        "finding_attribution_digests",
        "late_critical_finding_event_digests",
        "reviewer_coverage_leak_event_digests",
        "label_source_digests",
    )
    @classmethod
    def _evidence_is_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("dataset evidence lineage must be canonical")
        return value

    @model_validator(mode="after")
    def _verify_snapshot(self) -> Self:
        parse_utc(self.epoch_started_at)
        assigned = [
            session_id
            for values in self.partition_assignment.values()
            for session_id in values
        ]
        if len(assigned) != len(set(assigned)) or not self.leakage_check_passed:
            raise ValueError("optimization dataset partition leakage detected")
        if set(assigned) & set(self.partition_exclusions):
            raise ValueError("excluded optimization session was assigned")
        if self.data_integrity_digest != _population_digest(self.population):
            raise ValueError("optimization dataset integrity digest is invalid")
        if any(
            item.evaluable
            and (
                item.usage_estimation_policy_version
                != self.comparison_usage_estimation_policy_version
                or item.usage_estimation_policy_digest
                != self.comparison_usage_estimation_policy_digest
            )
            for item in self.population
        ):
            raise ValueError("optimization dataset usage policy strata diverged")
        return fill_artifact_digest(self, "dataset_digest")


def _population_digest(population: tuple[DatasetPopulationEntry, ...]) -> str:
    return canonical_digest(population, CanonicalizationPolicy())
