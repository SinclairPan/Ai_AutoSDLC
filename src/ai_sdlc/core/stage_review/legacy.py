"""Stage Review 上一主版本的显式只读迁移合同。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.contracts import (
    RiskFact,
    RiskSeverity,
    SemanticRiskSuggestion,
)

if TYPE_CHECKING:
    from ai_sdlc.core.source_snapshot import SourceSnapshot
    from ai_sdlc.core.stage_review.candidate import (
        CandidateBuildContext,
        CandidateManifest,
    )

_LEGACY_POLICY = CanonicalizationPolicy(
    excluded_fields=frozenset({"legacy_digest"}),
    set_like_fields=frozenset(
        {
            "input_artifacts",
            "output_artifacts",
            "change_surface",
            "test_evidence_digests",
            "policy_digests",
            "toolchain_ids",
            "target_platform_ids",
            "protected_source_set",
            "review_artifact_exclusion_set",
            "risk_facts",
            "unconfirmed_risk_suggestions",
            "required_capability_ids",
        }
    ),
)
_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_LegacyValue = TypeVar("_LegacyValue", bound="_LegacyBase")


class _LegacyBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0"] = "0"
    canonicalization_version: Literal["stage-review-canonical/v0"] = (
        "stage-review-canonical/v0"
    )
    legacy_digest: str

    @model_validator(mode="after")
    def _verify_digest(self) -> _LegacyBase:
        if self.legacy_digest != _legacy_digest(self):
            raise ValueError("legacy artifact digest does not match protected content")
        return self


class _LegacyCandidateManifest(_LegacyBase):
    artifact_kind: Literal["candidate-manifest"] = "candidate-manifest"
    work_item_id: str
    loop_id: str
    loop_round_number: int = Field(ge=1)
    stage_key: str
    stage_instance_id: str
    input_artifacts: list[str]
    input_digests: dict[str, str]
    output_artifacts: list[str]
    output_digests: dict[str, str]
    change_surface: list[str]
    test_evidence_digests: list[str]
    policy_digests: list[str]
    toolchain_ids: list[str]
    target_platform_ids: list[str]
    protected_source_set: list[str]
    review_artifact_exclusion_set: list[str]
    source_tree_digest: str
    change_surface_digest: str

    @field_validator("work_item_id", "loop_id", "stage_key", "stage_instance_id")
    @classmethod
    def _require_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("legacy candidate identity values must not be empty")
        return value.strip()


class _LegacyTaskRiskProfile(_LegacyBase):
    artifact_kind: Literal["task-risk-profile"] = "task-risk-profile"
    work_item_id: str
    stage_key: str
    risk_level: RiskSeverity
    risk_facts: list[RiskFact]
    unconfirmed_risk_suggestions: list[SemanticRiskSuggestion]
    required_capability_ids: list[str]

    @model_validator(mode="after")
    def _verify_deterministic_authority(self) -> _LegacyTaskRiskProfile:
        required = {
            item for fact in self.risk_facts for item in fact.required_capability_ids
        }
        floor: RiskSeverity = max(
            (fact.severity for fact in self.risk_facts),
            key=lambda severity: _SEVERITY_ORDER[severity],
            default="low",
        )
        if required != set(self.required_capability_ids) or floor != self.risk_level:
            raise ValueError("legacy risk authority is not backed by deterministic facts")
        return self


def _read_legacy_candidate(
    payload: object,
    expected_digest: str | None,
) -> _LegacyCandidateManifest:
    return _read_legacy(_LegacyCandidateManifest, payload, expected_digest)


def _read_legacy_risk_profile(
    payload: object,
    expected_digest: str | None,
) -> _LegacyTaskRiskProfile:
    return _read_legacy(_LegacyTaskRiskProfile, payload, expected_digest)


def _migrate_legacy_candidate(
    payload: Mapping[str, object],
    root: Path,
    snapshot: SourceSnapshot,
    context: CandidateBuildContext,
    expected_digest: str | None,
) -> CandidateManifest:
    from ai_sdlc.core.stage_review.candidate import (
        _build_candidate_manifest,
    )

    legacy = _read_legacy_candidate(payload, expected_digest)
    identity = (
        legacy.work_item_id,
        legacy.loop_id,
        legacy.loop_round_number,
        legacy.stage_key,
        legacy.stage_instance_id,
    )
    expected_identity = (
        context.work_item_id,
        context.loop_id,
        context.loop_round_number,
        context.stage_key,
        context.stage_instance_id,
    )
    if identity != expected_identity:
        raise ValueError("legacy candidate does not match current review context")
    rebuilt = _build_candidate_manifest(root, snapshot, context)
    if _legacy_candidate_mismatch(legacy, rebuilt):
        raise ValueError("legacy candidate does not match current source truth")
    return rebuilt.model_copy(
        update={
            "compatibility_mode": "read-only-legacy",
            "extensions": {
                **rebuilt.extensions,
                "migrated_from_schema_version": "0",
                "migrated_from_digest": legacy.legacy_digest,
            },
        }
    )


def _legacy_candidate_mismatch(
    legacy: _LegacyCandidateManifest,
    rebuilt: CandidateManifest,
) -> bool:
    preserved = {
        "input_artifacts": legacy.input_artifacts,
        "input_digests": legacy.input_digests,
        "output_artifacts": legacy.output_artifacts,
        "output_digests": legacy.output_digests,
        "change_surface": legacy.change_surface,
        "test_evidence_digests": legacy.test_evidence_digests,
        "policy_digests": legacy.policy_digests,
        "toolchain_ids": legacy.toolchain_ids,
        "target_platform_ids": legacy.target_platform_ids,
        "protected_source_set": legacy.protected_source_set,
        "review_artifact_exclusion_set": legacy.review_artifact_exclusion_set,
        "source_tree_digest": legacy.source_tree_digest,
        "change_surface_digest": legacy.change_surface_digest,
    }
    return any(getattr(rebuilt, field) != value for field, value in preserved.items())


def _read_legacy(
    model: type[_LegacyValue],
    payload: object,
    expected_digest: str | None,
) -> _LegacyValue:
    if not expected_digest:
        raise ValueError("legacy artifact requires an expected lineage digest")
    artifact = model.model_validate(payload)
    if artifact.legacy_digest != expected_digest:
        raise ValueError("legacy artifact does not match expected lineage digest")
    return artifact


def _legacy_digest(value: object) -> str:
    return canonical_digest(value, _LEGACY_POLICY)
