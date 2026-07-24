"""Stage Review 顶级工件与嵌入式风险合同。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.loop_models import LoopArtifactModel
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
    canonical_payload,
)

RiskSeverity = Literal["low", "medium", "high", "critical"]
_CompatibilityMode = Literal["strict", "read-only-legacy"]
_CANONICALIZATION_VERSION = "stage-review-canonical/v1"
_CURRENT_SCHEMA_VERSION = "1"
_PREVIOUS_SCHEMA_VERSION = "0"
_ArtifactValue = TypeVar("_ArtifactValue", bound=BaseModel)
_SEVERITY_ORDER: dict[str, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}
_RISK_PROFILE_POLICY = CanonicalizationPolicy(
    excluded_fields=frozenset(
        {"created_at", "created_by", "ai_sdlc_version", "profile_digest"}
    ),
    set_like_fields=frozenset(
        {"risk_facts", "unconfirmed_risk_suggestions", "required_capability_ids"}
    ),
)
_RISK_FACT_POLICY = CanonicalizationPolicy(
    set_like_fields=frozenset({"required_capability_ids"}),
)
_SEMANTIC_SUGGESTION_POLICY = CanonicalizationPolicy(
    set_like_fields=frozenset({"suggested_capability_ids"}),
)


class StageReviewArtifactModel(LoopArtifactModel):
    """Stage Review 顶级持久化工件的兼容保留字段。"""

    schema_version: str = _CURRENT_SCHEMA_VERSION
    extensions: dict[str, object] = Field(default_factory=dict)
    canonicalization_version: str = _CANONICALIZATION_VERSION
    compatibility_mode: _CompatibilityMode = "strict"

    identity_fields: ClassVar[tuple[str, ...]] = ()
    digest_covered_fields: ClassVar[tuple[str, ...]] = ()
    migration_policy: ClassVar[str] = "current-and-previous-major"
    artifact_classification: ClassVar[
        Literal["immutable-fact", "rebuildable-projection"]
    ] = "immutable-fact"

    @field_validator("extensions")
    @classmethod
    def _freeze_extensions(cls, value: dict[str, object]) -> dict[str, object]:
        """深复制为可规范化 JSON，切断 Adapter 的可变对象别名。"""

        try:
            payload = canonical_payload(value, CanonicalizationPolicy())
        except (RecursionError, TypeError, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        if not isinstance(payload, dict):  # pragma: no cover - 字段类型已限制。
            raise ValueError("extensions must be a JSON object")
        return payload

    @field_validator("schema_version")
    @classmethod
    def _require_schema_version(cls, value: str) -> str:
        if value != _CURRENT_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {value}")
        return value

    @field_validator("canonicalization_version")
    @classmethod
    def _require_canonicalization_version(cls, value: str) -> str:
        if value != _CANONICALIZATION_VERSION:
            raise ValueError(f"unsupported canonicalization_version: {value}")
        return value


class RiskFact(BaseModel):
    """由确定性提取器生成、语义模型不能降级的嵌入事实。"""

    model_config = ConfigDict(extra="forbid")

    risk_fact_id: str
    source_ref: str
    extractor_version: str
    confidence: float = Field(ge=0, le=1)
    severity: RiskSeverity
    required_capability_ids: list[str] = Field(default_factory=list)
    evidence_digest: str

    @field_validator("required_capability_ids")
    @classmethod
    def _normalize_capabilities(cls, values: list[str]) -> list[str]:
        return _stable_strings(values)

    @field_validator("risk_fact_id", "source_ref", "extractor_version", "evidence_digest")
    @classmethod
    def _require_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("risk fact identity values must not be empty")
        return value.strip()


class SemanticRiskSuggestion(BaseModel):
    """模型产生的未确认建议；不拥有 Blocking Authority。"""

    model_config = ConfigDict(extra="forbid")

    suggestion_id: str
    evidence_ref: str
    confidence: float = Field(ge=0, le=1)
    severity: RiskSeverity
    suggested_capability_ids: list[str] = Field(default_factory=list)
    targets_risk_fact_id: str = ""

    @field_validator("suggested_capability_ids")
    @classmethod
    def _normalize_capabilities(cls, values: list[str]) -> list[str]:
        return _stable_strings(values)

    @field_validator("suggestion_id", "evidence_ref")
    @classmethod
    def _require_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("semantic suggestion identity values must not be empty")
        return value.strip()

    @field_validator("targets_risk_fact_id")
    @classmethod
    def _normalize_target(cls, value: str) -> str:
        return value.strip()


class TaskRiskProfile(StageReviewArtifactModel):
    """Stage Baseline、确定性事实和未确认建议的冻结视图。"""

    artifact_kind: Literal["task-risk-profile"] = "task-risk-profile"
    identity_fields: ClassVar[tuple[str, ...]] = ("work_item_id", "stage_key")
    digest_covered_fields: ClassVar[tuple[str, ...]] = (
        "schema_version",
        "artifact_kind",
        "extensions",
        "canonicalization_version",
        "compatibility_mode",
        "work_item_id",
        "stage_key",
        "risk_level",
        "risk_facts",
        "unconfirmed_risk_suggestions",
        "required_capability_ids",
    )
    work_item_id: str
    stage_key: str
    risk_level: RiskSeverity
    risk_facts: list[RiskFact] = Field(default_factory=list)
    unconfirmed_risk_suggestions: list[SemanticRiskSuggestion] = Field(
        default_factory=list
    )
    required_capability_ids: list[str] = Field(default_factory=list)
    profile_digest: str

    @field_validator("work_item_id", "stage_key")
    @classmethod
    def _require_profile_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("risk profile identity values must not be empty")
        return value.strip()

    @field_validator("risk_facts")
    @classmethod
    def _normalize_facts(cls, values: list[RiskFact]) -> list[RiskFact]:
        return _dedupe_facts(values)

    @field_validator("unconfirmed_risk_suggestions")
    @classmethod
    def _normalize_suggestions(
        cls, values: list[SemanticRiskSuggestion]
    ) -> list[SemanticRiskSuggestion]:
        return _dedupe_suggestions(values)

    @field_validator("required_capability_ids")
    @classmethod
    def _normalize_required(cls, values: list[str]) -> list[str]:
        return _stable_strings(values)

    @model_validator(mode="after")
    def _verify_floor_and_digest(self) -> TaskRiskProfile:
        required = {
            item for fact in self.risk_facts for item in fact.required_capability_ids
        }
        if required != set(self.required_capability_ids):
            raise ValueError("risk profile capabilities require deterministic facts")
        floor = _risk_floor(self.risk_facts)
        if self.risk_level != floor:
            raise ValueError("risk profile risk level requires deterministic facts")
        if self.profile_digest != _risk_profile_digest(self):
            raise ValueError("risk profile digest does not match protected content")
        return self


def reconcile_risk_profile(
    *,
    work_item_id: str,
    stage_key: str,
    deterministic_facts: list[RiskFact],
    semantic_suggestions: list[SemanticRiskSuggestion],
) -> TaskRiskProfile:
    """冻结确定性风险下界，并将模型输出保留为未确认建议。"""

    facts = _dedupe_facts(deterministic_facts)
    suggestions = _dedupe_suggestions(semantic_suggestions)
    required = sorted(
        {item for fact in facts for item in fact.required_capability_ids}
    )
    draft = TaskRiskProfile.model_construct(
        work_item_id=work_item_id,
        stage_key=stage_key,
        risk_level=_risk_floor(facts),
        risk_facts=facts,
        unconfirmed_risk_suggestions=suggestions,
        required_capability_ids=required,
        profile_digest="pending",
    )
    return TaskRiskProfile.model_validate(
        {
            **draft.model_dump(mode="json"),
            "profile_digest": _risk_profile_digest(draft),
        }
    )


def read_task_risk_profile(
    payload: Mapping[str, object],
    *,
    expected_legacy_digest: str | None = None,
) -> TaskRiskProfile:
    """按 kind/version 路由并验证风险摘要。"""

    version = str(payload.get("schema_version", ""))
    kind = str(payload.get("artifact_kind", ""))
    if kind != "task-risk-profile" or version not in {
        _CURRENT_SCHEMA_VERSION,
        _PREVIOUS_SCHEMA_VERSION,
    }:
        raise ValueError(f"unknown stage-review schema: {kind}/{version}")
    if version == _PREVIOUS_SCHEMA_VERSION:
        from ai_sdlc.core.stage_review.legacy import _read_legacy_risk_profile

        legacy = _read_legacy_risk_profile(payload, expected_legacy_digest)
        draft = TaskRiskProfile.model_construct(
            work_item_id=legacy.work_item_id,
            stage_key=legacy.stage_key,
            risk_level=legacy.risk_level,
            risk_facts=legacy.risk_facts,
            unconfirmed_risk_suggestions=legacy.unconfirmed_risk_suggestions,
            required_capability_ids=legacy.required_capability_ids,
            compatibility_mode="read-only-legacy",
            extensions={
                "migrated_from_schema_version": _PREVIOUS_SCHEMA_VERSION,
                "migrated_from_digest": legacy.legacy_digest,
            },
            profile_digest="pending",
        )
        return TaskRiskProfile.model_validate(
            {
                **draft.model_dump(mode="json"),
                "profile_digest": _risk_profile_digest(draft),
            }
        )
    return TaskRiskProfile.model_validate(payload)


def _stable_strings(values: list[str]) -> list[str]:
    return sorted({value.strip() for value in values if value.strip()})


def _risk_profile_digest(value: object) -> str:
    return canonical_digest(value, _RISK_PROFILE_POLICY)


def _risk_floor(facts: list[RiskFact]) -> RiskSeverity:
    return max(
        (fact.severity for fact in facts),
        key=lambda severity: _SEVERITY_ORDER[severity],
        default="low",
    )


def _dedupe_facts(facts: list[RiskFact]) -> list[RiskFact]:
    return _dedupe_by_id(
        facts,
        identity="risk_fact_id",
        policy=_RISK_FACT_POLICY,
        label="deterministic risk fact",
    )


def _dedupe_suggestions(
    suggestions: list[SemanticRiskSuggestion],
) -> list[SemanticRiskSuggestion]:
    return _dedupe_by_id(
        suggestions,
        identity="suggestion_id",
        policy=_SEMANTIC_SUGGESTION_POLICY,
        label="semantic risk suggestion",
    )


def _dedupe_by_id(
    values: list[_ArtifactValue],
    *,
    identity: str,
    policy: CanonicalizationPolicy,
    label: str,
) -> list[_ArtifactValue]:
    indexed: dict[str, tuple[str, _ArtifactValue]] = {}
    for value in values:
        item_id = str(getattr(value, identity))
        digest = canonical_digest(value, policy)
        current = indexed.get(item_id)
        if current is not None and current[0] != digest:
            raise ValueError(f"conflicting {label}: {item_id}")
        indexed[item_id] = (digest, value)
    return [indexed[item_id][1] for item_id in sorted(indexed)]
