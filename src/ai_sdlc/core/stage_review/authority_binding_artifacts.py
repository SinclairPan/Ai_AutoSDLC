"""Canonical shared state 与 BudgetGrant authority 的版本化绑定工件。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self, TypeVar
from uuid import uuid4

from pydantic import ConfigDict, ValidationError, field_validator, model_validator

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    create_json_exclusive,
    read_json_object,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.contracts import StageReviewArtifactModel
from ai_sdlc.core.stage_review.resource_builders import stable_id

_BINDING_POLICY = CanonicalizationPolicy(excluded_fields=frozenset({"binding_digest"}))


class _AuthorityBindingArtifact(StageReviewArtifactModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    project_id: str
    binding_digest: str

    @field_validator("project_id", "binding_digest")
    @classmethod
    def _identity_is_present(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("authority binding identity cannot be empty")
        return value

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        if self.binding_digest != canonical_digest(self, _BINDING_POLICY):
            raise ValueError("authority binding digest does not match content")
        return self


class SharedStateBindingArtifact(_AuthorityBindingArtifact):
    artifact_kind: Literal["shared-state-binding"] = "shared-state-binding"
    binding_id: str

    @field_validator("binding_id")
    @classmethod
    def _binding_id_is_present(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("shared state binding identity cannot be empty")
        return value


class BudgetGrantAuthorityBinding(_AuthorityBindingArtifact):
    artifact_kind: Literal["budget-grant-authority-binding"] = (
        "budget-grant-authority-binding"
    )
    shared_state_binding_id: str
    authority_id: str

    @field_validator("shared_state_binding_id", "authority_id")
    @classmethod
    def _authority_identity_is_present(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("budget grant authority identity cannot be empty")
        return value


_BindingArtifact = TypeVar(
    "_BindingArtifact",
    SharedStateBindingArtifact,
    BudgetGrantAuthorityBinding,
)


def ensure_shared_state_binding(path: Path, project_id: str) -> SharedStateBindingArtifact:
    draft = SharedStateBindingArtifact.model_construct(
        project_id=project_id,
        binding_id=stable_id("canonical-shared-state", project_id, uuid4().hex),
        binding_digest="",
    )
    candidate = _complete_binding(draft, SharedStateBindingArtifact)
    if create_json_exclusive(path, candidate.model_dump(mode="json")):
        return candidate
    current = _read_binding(path, SharedStateBindingArtifact)
    if current.project_id != project_id:
        raise SharedStateIntegrityError("shared state binding artifact is invalid")
    return current


def _ensure_budget_grant_authority_binding(
    path: Path,
    expected: BudgetGrantAuthorityBinding,
) -> None:
    if create_json_exclusive(path, expected.model_dump(mode="json")):
        return
    current = _read_binding(path, BudgetGrantAuthorityBinding)
    if current != expected:
        raise SharedStateIntegrityError("budget grant authority binding changed")


def _build_budget_grant_authority_binding(
    *,
    project_id: str,
    shared_state_binding_id: str,
    authority_id: str,
) -> BudgetGrantAuthorityBinding:
    draft = BudgetGrantAuthorityBinding.model_construct(
        project_id=project_id,
        shared_state_binding_id=shared_state_binding_id,
        authority_id=authority_id,
        binding_digest="",
    )
    return _complete_binding(draft, BudgetGrantAuthorityBinding)


def _complete_binding(
    draft: _BindingArtifact,
    model: type[_BindingArtifact],
) -> _BindingArtifact:
    payload = draft.model_dump(mode="json", warnings=False)
    payload["binding_digest"] = canonical_digest(draft, _BINDING_POLICY)
    return model.model_validate(payload)


def _read_binding(path: Path, model: type[_BindingArtifact]) -> _BindingArtifact:
    try:
        return model.model_validate(read_json_object(path))
    except (ValidationError, ValueError) as exc:
        raise SharedStateIntegrityError("authority binding artifact is invalid") from exc
