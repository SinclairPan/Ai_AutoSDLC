"""可组合且内容寻址的 Reviewer Role Profile 策略。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.registry_models import ReviewerRoleModule
from ai_sdlc.core.stage_review.resource_builders import stable_id


class RoleProfilePolicy(ArtifactCompatibility):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["reviewer-role-profile-policy.v1"] = (
        "reviewer-role-profile-policy.v1"
    )
    artifact_kind: Literal["reviewer-role-profile-policy"] = (
        "reviewer-role-profile-policy"
    )
    module_digests: tuple[str, ...]
    compositions: tuple[tuple[str, ...], ...]
    policy_digest: str = ""

    @field_validator("module_digests")
    @classmethod
    def _modules_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or value != tuple(sorted(set(value))):
            raise ValueError("role profile modules are not canonical")
        return value

    @model_validator(mode="after")
    def _verify_compositions(self) -> Self:
        if not self.compositions or self.compositions != tuple(
            sorted(set(self.compositions))
        ):
            raise ValueError("role profile compositions are not canonical")
        if any(not item or item != tuple(sorted(set(item))) for item in self.compositions):
            raise ValueError("role profile composition is not canonical")
        covered = {digest for item in self.compositions for digest in item}
        if covered != set(self.module_digests):
            raise ValueError("role profile compositions do not cover the module catalog")
        return fill_artifact_digest(self, "policy_digest")


def baseline_role_profile_policy(
    modules: tuple[ReviewerRoleModule, ...],
) -> RoleProfilePolicy:
    digests = tuple(sorted(item.module_digest for item in modules))
    return RoleProfilePolicy(
        module_digests=digests,
        compositions=tuple((item,) for item in digests),
    )


def role_profile_id(
    composition: tuple[str, ...],
    modules: tuple[ReviewerRoleModule, ...],
) -> str:
    by_digest = {item.module_digest: item for item in modules}
    try:
        module_ids = tuple(sorted(by_digest[item].module_id for item in composition))
    except KeyError as exc:
        raise ValueError("role profile composition references an unknown module") from exc
    if len(module_ids) == 1:
        return module_ids[0]
    return stable_id("role-profile", *module_ids)


__all__ = [
    "RoleProfilePolicy",
    "baseline_role_profile_policy",
    "role_profile_id",
]
