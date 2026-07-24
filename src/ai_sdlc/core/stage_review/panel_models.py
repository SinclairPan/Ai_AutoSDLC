"""Reviewer Panel 规划请求、策略、Slot 与结果合同。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SkipValidation,
    field_validator,
    model_validator,
)

from ai_sdlc.core.stage_review.canonical import normalize_repo_path
from ai_sdlc.core.stage_review.contracts import RiskSeverity, StageReviewArtifactModel
from ai_sdlc.core.stage_review.registry_models import (
    ReviewerRoleModule,
    StageKey,
)
from ai_sdlc.core.stage_review.registry_versions import (
    normalize_machine_ids,
    require_iso_date,
    require_machine_id,
    require_version,
)
from ai_sdlc.core.stage_review.role_contract_models import ReviewerRoleContract

EnforcementMode = Literal["shadow", "enforce", "grandfathered"]
SlotKind = Literal["required", "optional", "advisory", "shadow"]
PlannerResultCode = Literal[
    "resolved",
    "invalid_input",
    "incompatible_schema",
    "registry_unavailable",
    "unsatisfied_required_capability",
    "policy_conflict",
    "role_contract_conflict",
    "no_feasible_panel",
]
PANEL_SOLVER_VERSION = "panel-solver.v1"


class PanelPlanningError(ValueError):
    """携带稳定结果码的规划失败。"""

    def __init__(
        self,
        result_code: PlannerResultCode,
        reason_id: str,
        *,
        missing: Sequence[str] = (),
        reason_ids: Sequence[str] = (),
    ) -> None:
        super().__init__(reason_id)
        self.result_code = result_code
        self.reason_id = reason_id
        self.reason_ids = tuple(dict.fromkeys((reason_id, *reason_ids)))
        self.missing = tuple(sorted(set(missing)))


class ReviewerBudgetPolicy(StageReviewArtifactModel):
    """Planner 使用、后续由 ResourceGovernor 落实的 Hard Budget。"""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    artifact_kind: Literal["reviewer-budget-policy"] = "reviewer-budget-policy"
    policy_id: str
    version: str
    maximum_slots: int = Field(gt=0)
    hard_provider_calls: int = Field(gt=0)
    hard_review_passes: int = Field(gt=0)
    hard_tokens: int = Field(gt=0)
    hard_cost: float = Field(gt=0)
    hard_wall_clock: float = Field(gt=0)
    hard_parallelism: int = Field(gt=0)
    hard_role_replans: int = Field(gt=0)
    hard_provider_retries: int = Field(gt=0)
    hard_binding_attempts: int = Field(gt=0)
    owner: str
    review_date: str
    policy_digest: str

    @field_validator("policy_id")
    @classmethod
    def _policy_id_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "budget policy id")

    @field_validator("version")
    @classmethod
    def _version_is_supported(cls, value: str) -> str:
        return require_version(value)

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
        from ai_sdlc.core.stage_review.panel_digests import budget_policy_digest

        if self.policy_digest != budget_policy_digest(self):
            raise ValueError("reviewer budget policy digest does not match content")
        return self


class ReviewerQuorumPolicy(StageReviewArtifactModel):
    """动态 Panel 冻结前使用的版本化 Quorum 模板。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["reviewer-quorum-policy"] = "reviewer-quorum-policy"
    policy_id: str
    version: str
    minimum_pass_count: int = Field(gt=0)
    veto_authorities: tuple[str, ...] = Field(default_factory=tuple)
    allowed_abstentions: tuple[SlotKind, ...] = (
        "optional",
        "advisory",
        "shadow",
    )
    substitutable_required_role_groups: tuple[tuple[str, ...], ...] = Field(
        default_factory=tuple
    )
    owner: str
    review_date: str
    policy_digest: str

    @field_validator("policy_id")
    @classmethod
    def _policy_id_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "quorum policy id")

    @field_validator("version")
    @classmethod
    def _version_is_supported(cls, value: str) -> str:
        return require_version(value)

    @field_validator("veto_authorities", mode="before")
    @classmethod
    def _normalize_authorities(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_machine_ids(value))

    @field_validator("allowed_abstentions", mode="before")
    @classmethod
    def _normalize_abstentions(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple, set, frozenset)):
            raise ValueError("allowed_abstentions must be a collection")
        return tuple(sorted({str(item) for item in value}))

    @field_validator("substitutable_required_role_groups", mode="before")
    @classmethod
    def _normalize_substitutions(cls, value: object) -> tuple[tuple[str, ...], ...]:
        if not isinstance(value, (list, tuple, set, frozenset)):
            raise ValueError("substitutable groups must be a collection")
        return tuple(sorted(tuple(normalize_machine_ids(item)) for item in value))

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
        from ai_sdlc.core.stage_review.panel_digests import quorum_policy_digest

        if "required" in self.allowed_abstentions:
            raise ValueError("required reviewer slot cannot abstain")
        if self.policy_digest != quorum_policy_digest(self):
            raise ValueError("reviewer quorum policy digest does not match content")
        return self


class CapabilityCoverageRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_id: str
    minimum_required_slots: int = Field(ge=1)

    @field_validator("capability_id")
    @classmethod
    def _capability_id_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "capability id")


class ReviewerPlanRequest(StageReviewArtifactModel):
    """排除动态 Reservation 的 canonical 规划输入。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["reviewer-plan-request"] = "reviewer-plan-request"
    request_id: str
    work_item_id: str
    loop_id: str
    loop_round_number: int = Field(ge=1)
    stage_key: StageKey
    stage_instance_id: str
    risk_level: RiskSeverity
    required_capability_ids: tuple[str, ...]
    coverage_requirements: tuple[CapabilityCoverageRequirement, ...]
    blocking_capability_ids: tuple[str, ...]
    planning_context_digest: str
    candidate_manifest_ref: str
    candidate_manifest_digest: str
    task_risk_profile_ref: str
    task_risk_profile_digest: str
    change_surface_digest: str
    registry_ref: str
    registry_digest: str
    registry_version: str
    role_catalog_ref: str
    role_catalog_digest: str
    selection_policy_ref: str
    selection_policy_digest: str
    selection_policy_version: str
    quorum_policy_ref: str
    quorum_policy_digest: str
    quorum_policy_version: str
    budget_policy_ref: str
    budget_policy_digest: str
    budget_envelope_digest: str
    planning_authorization_digest: str
    solver_version: str
    optimization_snapshot_ref: str
    optimization_snapshot_digest: str
    enforcement_mode: EnforcementMode
    request_digest: str

    @field_validator(
        "required_capability_ids",
        "blocking_capability_ids",
        mode="before",
    )
    @classmethod
    def _normalize_capabilities(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_machine_ids(value))

    @field_validator(
        "request_id",
        "work_item_id",
        "loop_id",
        "stage_instance_id",
        "candidate_manifest_digest",
        "task_risk_profile_digest",
        "change_surface_digest",
        "registry_digest",
        "role_catalog_digest",
        "selection_policy_digest",
        "quorum_policy_digest",
        "budget_policy_digest",
        "budget_envelope_digest",
        "planning_authorization_digest",
        "solver_version",
        "optimization_snapshot_digest",
    )
    @classmethod
    def _require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reviewer plan request identity cannot be empty")
        return value.strip()

    @field_validator(
        "candidate_manifest_ref",
        "task_risk_profile_ref",
        "registry_ref",
        "role_catalog_ref",
        "selection_policy_ref",
        "quorum_policy_ref",
        "budget_policy_ref",
        "optimization_snapshot_ref",
    )
    @classmethod
    def _normalize_refs(cls, value: str) -> str:
        return normalize_repo_path(value)

    @field_validator("solver_version")
    @classmethod
    def _solver_version_is_supported(cls, value: str) -> str:
        if value != PANEL_SOLVER_VERSION:
            raise ValueError(f"unsupported solver_version: {value}")
        return value

    @field_validator(
        "registry_version",
        "selection_policy_version",
        "quorum_policy_version",
    )
    @classmethod
    def _version_is_supported(cls, value: str) -> str:
        return require_version(value)

    @model_validator(mode="after")
    def _verify_request(self) -> Self:
        from ai_sdlc.core.stage_review.panel_digests import (
            plan_request_digest,
            planning_context_digest,
        )

        if set(self.blocking_capability_ids) - set(self.required_capability_ids):
            raise ValueError("blocking capabilities must be required")
        if {item.capability_id for item in self.coverage_requirements} != set(
            self.required_capability_ids
        ):
            raise ValueError("coverage requirements must match required capabilities")
        if self.planning_context_digest != planning_context_digest(self):
            raise ValueError("planning context digest does not match content")
        if self.request_digest != plan_request_digest(self):
            raise ValueError("reviewer plan request digest does not match content")
        return self


class ReviewerRoleOption(BaseModel):
    """逻辑 Role 与差异/资源元数据，不包含运行时 Provider 绑定。"""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    # Role 的完整血缘必须由持有 Registry 上下文的 Planner 重放，Option 仅封装候选。
    role_contract: SkipValidation[ReviewerRoleContract]
    eligible_slot_kinds: tuple[SlotKind, ...]
    prompt_template_digest: str
    tool_permission_ids: tuple[str, ...]
    evidence_source_ids: tuple[str, ...]
    independence_key: str
    estimated_provider_calls: int = Field(gt=0)
    estimated_review_passes: int = Field(gt=0)
    estimated_tokens: int = Field(gt=0)
    estimated_cost: float = Field(gt=0)
    estimated_wall_clock: float = Field(gt=0)

    @field_validator(
        "tool_permission_ids",
        "evidence_source_ids",
        mode="before",
    )
    @classmethod
    def _normalize_ids(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_machine_ids(value))

    @field_validator("eligible_slot_kinds", mode="before")
    @classmethod
    def _normalize_kinds(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple, set, frozenset)):
            raise ValueError("eligible slot kinds must be a collection")
        return tuple(sorted({str(item) for item in value}))

    @field_validator("prompt_template_digest", "independence_key")
    @classmethod
    def _require_option_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("role option identity cannot be empty")
        return value.strip()

    @model_validator(mode="after")
    def _verify_mode(self) -> Self:
        from ai_sdlc.core.stage_review.panel_digests import (
            role_option_independence_key,
        )

        if self.role_contract.capability_mode == "shadow":
            if self.eligible_slot_kinds != ("shadow",):
                raise ValueError("shadow role is eligible only for shadow slot")
        elif "shadow" in self.eligible_slot_kinds:
            raise ValueError("active role cannot become shadow slot")
        if self.independence_key != role_option_independence_key(self):
            raise ValueError("role option independence key does not match semantics")
        return self


RoleCatalog = tuple[ReviewerRoleOption, ...]
ModuleCatalog = tuple[ReviewerRoleModule, ...]
