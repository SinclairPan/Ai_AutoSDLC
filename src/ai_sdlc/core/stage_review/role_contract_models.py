"""合并后 Reviewer Role 的绑定与可信读取合同。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from ai_sdlc.core.stage_review.contracts import StageReviewArtifactModel
from ai_sdlc.core.stage_review.registry_models import (
    AuthorityLevel,
    CapabilityConsumptionMode,
    RoleModuleKind,
)
from ai_sdlc.core.stage_review.registry_versions import (
    normalize_machine_ids,
    require_machine_id,
    require_version,
)


class RoleModuleBinding(BaseModel):
    """Role 合同绑定的不可变 Module 版本与内容摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    module_id: str
    version: str
    module_kind: RoleModuleKind
    module_digest: str

    @field_validator("module_id")
    @classmethod
    def _module_id_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "module_id")

    @field_validator("version")
    @classmethod
    def _version_is_supported(cls, value: str) -> str:
        return require_version(value)


class ReviewerRoleContract(StageReviewArtifactModel):
    """模块合并后的唯一逻辑 Reviewer Role 合同。"""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    artifact_kind: Literal["reviewer-role-contract"] = "reviewer-role-contract"
    role_profile_id: str
    version: str
    capability_mode: CapabilityConsumptionMode
    registry_digest: str
    policy_digest: str
    source_profile_ids: tuple[str, ...]
    source_module_ids: tuple[str, ...]
    source_module_bindings: tuple[RoleModuleBinding, ...]
    capability_ids: tuple[str, ...]
    primary_dimensions: tuple[str, ...]
    in_scope: tuple[str, ...]
    out_of_scope: tuple[str, ...]
    blocking_authority: tuple[str, ...]
    authority_ceiling: AuthorityLevel
    required_evidence: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    provider_constraints: tuple[str, ...]
    isolation_requirements: tuple[str, ...]
    cost_ceiling: float = Field(ge=0)
    merge_semantics_version: str
    role_contract_digest: str

    @field_validator("role_profile_id", "merge_semantics_version")
    @classmethod
    def _role_identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "role identity")

    @field_validator("version")
    @classmethod
    def _version_is_supported(cls, value: str) -> str:
        return require_version(value)

    @field_validator(
        "source_profile_ids",
        "source_module_ids",
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
    def _normalize_role_ids(cls, value: object) -> tuple[str, ...]:
        return tuple(normalize_machine_ids(value))

    @model_validator(mode="after")
    def _verify_contract(self, info: ValidationInfo) -> Self:
        from ai_sdlc.core.stage_review.registry_digests import role_contract_digest
        from ai_sdlc.core.stage_review.registry_validation import (
            verify_role_contract_lineage,
        )

        self._verify_local_boundaries()
        if self.role_contract_digest != role_contract_digest(self):
            raise ValueError("reviewer role contract digest does not match content")
        context = info.context
        if not isinstance(context, dict) or not {
            "registry",
            "policy",
            "module_catalog",
        } <= context.keys():
            raise ValueError("reviewer role contract requires registry context")
        verify_role_contract_lineage(
            self,
            registry=context["registry"],
            policy=context["policy"],
            module_catalog=context["module_catalog"],
        )
        return self

    def _verify_local_boundaries(self) -> None:
        if set(self.in_scope) & set(self.out_of_scope):
            raise ValueError("reviewer role scope conflict")
        if set(self.blocking_authority) - set(self.capability_ids):
            raise ValueError("reviewer role blocking authority is not owned by capability")
        if self.blocking_authority and self.authority_ceiling != "block":
            raise ValueError("reviewer role blocking authority exceeds authority ceiling")
        if self.capability_mode == "shadow" and self.blocking_authority:
            raise ValueError("shadow reviewer role cannot hold blocking authority")
        base_ids = {
            item.module_id
            for item in self.source_module_bindings
            if item.module_kind == "base"
        }
        module_ids = {
            item.module_id
            for item in self.source_module_bindings
            if item.module_kind != "base"
        }
        if base_ids != set(self.source_profile_ids) or len(base_ids) != 1:
            raise ValueError("reviewer role requires exactly one bound base profile")
        if module_ids != set(self.source_module_ids):
            raise ValueError("reviewer role module bindings do not match source ids")
