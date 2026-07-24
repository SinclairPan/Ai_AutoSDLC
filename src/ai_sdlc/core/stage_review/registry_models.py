"""Capability Registry、Role Module 与 Policy 的机器合同。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from ai_sdlc.core.stage_review.contracts import StageReviewArtifactModel
from ai_sdlc.core.stage_review.registry_versions import (
    normalize_machine_ids,
    normalize_text_set,
    require_iso_date,
    require_machine_id,
    require_version,
    validate_version_range,
)

AuthorityLevel = Literal["observe", "advise", "block"]
CapabilityMaturity = Literal["shadow", "active", "deprecated"]
CapabilityConsumptionMode = Literal["active", "shadow"]
RoleModuleKind = Literal["base", "stage", "risk"]
StageKey = Literal[
    "requirement",
    "design-contract",
    "implementation",
    "frontend-evidence",
    "local-pr-review",
]
RiskLevel = Literal["low", "medium", "high", "critical"]

class CapabilityDefinition(BaseModel):
    """一个稳定、原子的 Reviewer Capability。"""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    capability_id: str
    version: str
    parent: str = ""
    implies: tuple[str, ...] = Field(default_factory=tuple)
    conflicts: tuple[str, ...] = Field(default_factory=tuple)
    applicable_stage: tuple[StageKey, ...]
    applicable_risk: tuple[RiskLevel, ...]
    authority_ceiling: AuthorityLevel
    required_evidence_types: tuple[str, ...] = Field(default_factory=tuple)
    maturity: CapabilityMaturity
    superseded_by: str = ""
    compatibility_range: str
    owner: str
    review_date: str

    @field_validator("capability_id")
    @classmethod
    def _capability_id_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "capability_id")

    @field_validator("parent", "superseded_by")
    @classmethod
    def _optional_identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "capability reference", optional=True)

    @field_validator("implies", "conflicts", "required_evidence_types", mode="before")
    @classmethod
    def _normalize_identifiers(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_machine_ids(value))

    @field_validator("applicable_stage", "applicable_risk", mode="before")
    @classmethod
    def _normalize_enums(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_text_set(value))

    @field_validator("version")
    @classmethod
    def _version_is_supported(cls, value: str) -> str:
        return require_version(value)

    @field_validator("compatibility_range")
    @classmethod
    def _compatibility_is_valid(cls, value: str) -> str:
        validate_version_range(value)
        return value

    @field_validator("owner")
    @classmethod
    def _owner_is_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("owner is required")
        return value.strip()

    @field_validator("review_date")
    @classmethod
    def _review_date_is_iso(cls, value: str) -> str:
        return require_iso_date(value)


class ReviewerCapabilityRegistry(StageReviewArtifactModel):
    """版本化、内容寻址的能力注册表。"""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    artifact_kind: Literal["reviewer-capability-registry"] = (
        "reviewer-capability-registry"
    )
    registry_id: str
    registry_version: str
    contract_version: str = "1.0.0"
    capabilities: tuple[CapabilityDefinition, ...]
    registry_digest: str

    @field_validator("registry_id")
    @classmethod
    def _registry_id_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "registry_id")

    @field_validator("registry_version", "contract_version")
    @classmethod
    def _versions_are_supported(cls, value: str) -> str:
        return require_version(value)

    @model_validator(mode="after")
    def _verify_registry(self) -> Self:
        from ai_sdlc.core.stage_review.registry_validation import (
            verify_registry_artifact,
        )

        verify_registry_artifact(self)
        return self


class ReviewerRoleModule(BaseModel):
    """基础角色、Stage Module 或 Risk Module 的可交换贡献。"""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    module_id: str
    version: str
    module_kind: RoleModuleKind
    capability_ids: tuple[str, ...] = Field(default_factory=tuple)
    primary_dimensions: tuple[str, ...] = Field(default_factory=tuple)
    in_scope: tuple[str, ...] = Field(default_factory=tuple)
    out_of_scope: tuple[str, ...] = Field(default_factory=tuple)
    blocking_authority: tuple[str, ...] = Field(default_factory=tuple)
    authority_ceiling: AuthorityLevel
    required_evidence: tuple[str, ...] = Field(default_factory=tuple)
    forbidden_actions: tuple[str, ...] = Field(default_factory=tuple)
    provider_constraints: tuple[str, ...] = Field(default_factory=tuple)
    isolation_requirements: tuple[str, ...] = Field(default_factory=tuple)
    cost_ceiling: float = Field(ge=0)
    merge_semantics_version: str
    compatibility_range: str
    owner: str
    review_date: str
    module_digest: str

    @field_validator("module_id")
    @classmethod
    def _module_id_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "module_id")

    @field_validator(
        "capability_ids",
        "primary_dimensions",
        "in_scope",
        "out_of_scope",
        "blocking_authority",
        "required_evidence",
        "forbidden_actions",
        "provider_constraints",
        "isolation_requirements",
        mode="before",
    )
    @classmethod
    def _normalize_machine_fields(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_machine_ids(value))

    @field_validator("version")
    @classmethod
    def _version_is_supported(cls, value: str) -> str:
        return require_version(value)

    @field_validator("compatibility_range")
    @classmethod
    def _compatibility_is_valid(cls, value: str) -> str:
        validate_version_range(value)
        return value

    @field_validator("merge_semantics_version")
    @classmethod
    def _merge_version_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "merge_semantics_version")

    @field_validator("owner")
    @classmethod
    def _owner_is_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("owner is required")
        return value.strip()

    @field_validator("review_date")
    @classmethod
    def _review_date_is_iso(cls, value: str) -> str:
        return require_iso_date(value)

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        from ai_sdlc.core.stage_review.registry_digests import role_module_digest

        if self.module_digest != role_module_digest(self):
            raise ValueError("reviewer role module digest does not match content")
        return self


class CapabilityRequirementRule(BaseModel):
    """由 Stage/Risk Policy 提供的确定性能力覆盖要求。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    stage_keys: tuple[StageKey, ...]
    risk_levels: tuple[RiskLevel, ...]
    capability_ids: tuple[str, ...]
    coverage_count: int = Field(default=1, ge=1)

    @field_validator("rule_id")
    @classmethod
    def _rule_id_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "rule_id")

    @field_validator("stage_keys", "risk_levels", mode="before")
    @classmethod
    def _normalize_rule_enums(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_text_set(value))

    @field_validator("capability_ids", mode="before")
    @classmethod
    def _normalize_rule_capabilities(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_machine_ids(value))


class ReviewerSelectionPolicy(StageReviewArtifactModel):
    """Role 合并与阻断权限的受治理版本化策略。"""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    artifact_kind: Literal["reviewer-selection-policy"] = "reviewer-selection-policy"
    policy_id: str
    version: str
    registry_compatibility_range: str
    merge_semantics_version: str
    allowed_blocking_authority_ids: tuple[str, ...] = Field(default_factory=tuple)
    enabled_module_ids: tuple[str, ...] = Field(default_factory=tuple)
    constraint_conflicts: tuple[tuple[str, str], ...] = Field(default_factory=tuple)
    minimum_slots: int = Field(default=2, ge=1)
    minimum_distinct_primary_dimensions: int = Field(default=2, ge=1)
    optional_slot_limit: int = Field(default=0, ge=0)
    advisory_slot_limit: int = Field(default=0, ge=0)
    shadow_slot_limit: int = Field(default=0, ge=0)
    double_coverage_risk_levels: tuple[RiskLevel, ...] = ("high", "critical")
    capability_requirement_rules: tuple[CapabilityRequirementRule, ...] = Field(
        default_factory=tuple
    )
    owner: str
    review_date: str
    policy_digest: str

    @field_validator("policy_id", "merge_semantics_version")
    @classmethod
    def _policy_identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "policy identity")

    @field_validator("version")
    @classmethod
    def _version_is_supported(cls, value: str) -> str:
        return require_version(value)

    @field_validator("registry_compatibility_range")
    @classmethod
    def _compatibility_is_valid(cls, value: str) -> str:
        validate_version_range(value)
        return value

    @field_validator(
        "allowed_blocking_authority_ids", "enabled_module_ids", mode="before"
    )
    @classmethod
    def _normalize_policy_ids(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_machine_ids(value))

    @field_validator("double_coverage_risk_levels", mode="before")
    @classmethod
    def _normalize_double_coverage(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_text_set(value))

    @field_validator("capability_requirement_rules", mode="before")
    @classmethod
    def _normalize_requirement_rules(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, (list, tuple, set, frozenset)):
            raise ValueError("capability_requirement_rules must be a collection")
        return tuple(value)

    @field_validator("constraint_conflicts", mode="before")
    @classmethod
    def _normalize_conflicts(cls, value: object) -> tuple[tuple[str, str], ...]:
        if not isinstance(value, (list, tuple, set, frozenset)):
            raise ValueError("constraint_conflicts must be a collection")
        pairs: set[tuple[str, str]] = set()
        for item in value:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError("constraint conflict must contain two identities")
            left = require_machine_id(str(item[0]), "constraint identity")
            right = require_machine_id(str(item[1]), "constraint identity")
            if left == right:
                raise ValueError("constraint conflict identities must differ")
            pairs.add((left, right) if left < right else (right, left))
        return tuple(sorted(pairs))

    @field_validator("owner")
    @classmethod
    def _owner_is_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("owner is required")
        return value.strip()

    @field_validator("review_date")
    @classmethod
    def _review_date_is_iso(cls, value: str) -> str:
        return require_iso_date(value)

    @model_validator(mode="after")
    def _verify_policy(self) -> Self:
        from ai_sdlc.core.stage_review.registry_validation import (
            verify_policy_artifact,
        )

        verify_policy_artifact(self)
        return self
