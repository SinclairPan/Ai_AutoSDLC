"""把 Requirement 与 Design 的 scope authority 锚定到共享可信状态。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self, TypeVar

from pydantic import ConfigDict, ValidationError, model_validator

from ai_sdlc.core.design_contract_models import DesignContractInput
from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
    resolve_repository_project_id,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
    normalize_repo_path,
)

_CONFIG = ConfigDict(extra="forbid", frozen=True)
_AnchorT = TypeVar("_AnchorT", bound=ArtifactCompatibility)


class ScopeAuthorityIntegrityError(RuntimeError):
    """scope authority 的共享锚缺失、分叉或与本地工件不一致。"""


class RequirementScopeAuthorityAnchor(ArtifactCompatibility):
    model_config = _CONFIG

    schema_version: Literal["requirement-scope-authority-anchor.v1"] = (
        "requirement-scope-authority-anchor.v1"
    )
    artifact_kind: Literal["requirement-scope-authority-anchor"] = (
        "requirement-scope-authority-anchor"
    )
    loop_id: str
    work_item_id: str = ""
    intake_path: str
    intake_digest: str
    freeze_path: str
    freeze_digest: str
    accepted_by: str
    accepted_at: str
    stage_close_operation_id: str
    anchor_digest: str = ""

    @model_validator(mode="after")
    def _validate_anchor(self) -> Self:
        required = (
            self.loop_id,
            self.intake_path,
            self.intake_digest,
            self.freeze_path,
            self.freeze_digest,
            self.accepted_by,
            self.accepted_at,
            self.stage_close_operation_id,
        )
        if any(not value.strip() or value != value.strip() for value in required):
            raise ValueError("requirement scope authority anchor is incomplete")
        return fill_artifact_digest(self, "anchor_digest")


class DesignScopeAuthorityAnchor(ArtifactCompatibility):
    model_config = _CONFIG

    schema_version: Literal["design-scope-authority-anchor.v1"] = (
        "design-scope-authority-anchor.v1"
    )
    artifact_kind: Literal["design-scope-authority-anchor"] = (
        "design-scope-authority-anchor"
    )
    loop_id: str
    work_item_id: str
    requirement_loop_id: str
    authorized_scope_families: tuple[str, ...] = ()
    scope_authority_ref: str
    scope_authority_digest: str
    input_digest: str
    anchor_digest: str = ""

    @model_validator(mode="after")
    def _validate_anchor(self) -> Self:
        required = (
            self.loop_id,
            self.work_item_id,
            self.requirement_loop_id,
            self.scope_authority_ref,
            self.scope_authority_digest,
            self.input_digest,
        )
        if any(not value.strip() or value != value.strip() for value in required):
            raise ValueError("design scope authority anchor is incomplete")
        if tuple(sorted(set(self.authorized_scope_families))) != (
            self.authorized_scope_families
        ):
            raise ValueError("design scope authority families are not canonical")
        return fill_artifact_digest(self, "anchor_digest")


def _record_requirement_scope_authority_intent(
    root: Path,
    *,
    loop_id: str,
    work_item_id: str,
    intake_path: str,
    intake_digest: str,
    freeze_path: str,
    freeze_digest: str,
    accepted_by: str,
    accepted_at: str,
    stage_close_operation_id: str,
) -> RequirementScopeAuthorityAnchor:
    anchor = _requirement_anchor(
        loop_id=loop_id,
        work_item_id=work_item_id,
        intake_path=intake_path,
        intake_digest=intake_digest,
        freeze_path=freeze_path,
        freeze_digest=freeze_digest,
        accepted_by=accepted_by,
        accepted_at=accepted_at,
        stage_close_operation_id=stage_close_operation_id,
    )
    path = _anchor_path(root, "requirement-intent", loop_id)
    try:
        _write_once(path, anchor)
    except ScopeAuthorityIntegrityError:
        current = _read_anchor(path, RequirementScopeAuthorityAnchor)
        excluded = {"anchor_digest", "accepted_by", "accepted_at", "freeze_digest"}
        if current.model_dump(exclude=excluded) != anchor.model_dump(exclude=excluded):
            raise
        return current
    return anchor


def _commit_requirement_scope_authority(
    root: Path,
    *,
    loop_id: str,
    work_item_id: str,
    intake_path: str,
    intake_digest: str,
    freeze_path: str,
    freeze_digest: str,
    accepted_by: str,
    accepted_at: str,
    stage_close_operation_id: str,
) -> RequirementScopeAuthorityAnchor:
    anchor = _requirement_anchor(
        loop_id=loop_id,
        work_item_id=work_item_id,
        intake_path=intake_path,
        intake_digest=intake_digest,
        freeze_path=freeze_path,
        freeze_digest=freeze_digest,
        accepted_by=accepted_by,
        accepted_at=accepted_at,
        stage_close_operation_id=stage_close_operation_id,
    )
    intent = _read_anchor(
        _anchor_path(root, "requirement-intent", loop_id),
        RequirementScopeAuthorityAnchor,
    )
    if intent != anchor:
        raise ScopeAuthorityIntegrityError(
            "requirement scope authority intent changed"
        )
    _write_once(_anchor_path(root, "requirement", loop_id), anchor)
    return anchor


def _requirement_anchor(
    *,
    loop_id: str,
    work_item_id: str,
    intake_path: str,
    intake_digest: str,
    freeze_path: str,
    freeze_digest: str,
    accepted_by: str,
    accepted_at: str,
    stage_close_operation_id: str,
) -> RequirementScopeAuthorityAnchor:
    return RequirementScopeAuthorityAnchor(
        loop_id=loop_id,
        work_item_id=work_item_id,
        intake_path=normalize_repo_path(intake_path),
        intake_digest=intake_digest,
        freeze_path=normalize_repo_path(freeze_path),
        freeze_digest=freeze_digest,
        accepted_by=accepted_by,
        accepted_at=accepted_at,
        stage_close_operation_id=stage_close_operation_id,
    )


def _verify_requirement_scope_authority_intent(
    root: Path,
    *,
    loop_id: str,
    work_item_id: str,
    intake_path: str,
    intake_digest: str,
    freeze_path: str,
    freeze_digest: str,
    accepted_by: str,
    accepted_at: str,
    stage_close_operation_id: str,
) -> RequirementScopeAuthorityAnchor:
    anchor = _read_anchor(
        _anchor_path(root, "requirement-intent", loop_id),
        RequirementScopeAuthorityAnchor,
    )
    expected = _requirement_anchor(
        loop_id=loop_id,
        work_item_id=work_item_id,
        intake_path=intake_path,
        intake_digest=intake_digest,
        freeze_path=freeze_path,
        freeze_digest=freeze_digest,
        accepted_by=accepted_by,
        accepted_at=accepted_at,
        stage_close_operation_id=stage_close_operation_id,
    )
    if anchor != expected:
        raise ScopeAuthorityIntegrityError(
            "requirement scope authority intent changed"
        )
    return anchor


def _requirement_scope_authority_intent_approval(
    root: Path,
    loop_id: str,
) -> tuple[str, str] | None:
    path = _anchor_path(root, "requirement-intent", loop_id)
    if not path.exists() and not path.is_symlink():
        return None
    anchor = _read_anchor(path, RequirementScopeAuthorityAnchor)
    return anchor.accepted_by, anchor.accepted_at


def _verify_requirement_scope_authority(
    root: Path,
    *,
    loop_id: str,
    work_item_id: str,
    intake_path: str,
    intake_digest: str,
    freeze_path: str,
    freeze_digest: str,
) -> RequirementScopeAuthorityAnchor:
    anchor = _read_anchor(
        _anchor_path(root, "requirement", loop_id),
        RequirementScopeAuthorityAnchor,
    )
    current = (
        loop_id,
        work_item_id,
        normalize_repo_path(intake_path),
        intake_digest,
        normalize_repo_path(freeze_path),
        freeze_digest,
    )
    anchored = (
        anchor.loop_id,
        anchor.work_item_id,
        anchor.intake_path,
        anchor.intake_digest,
        anchor.freeze_path,
        anchor.freeze_digest,
    )
    if current != anchored:
        raise ScopeAuthorityIntegrityError(
            "requirement committed scope authority changed"
        )
    return anchor


def _design_scope_input_digest(contract_input: DesignContractInput) -> str:
    return canonical_digest(
        contract_input.model_dump(mode="json", exclude={"created_at"}),
        CanonicalizationPolicy(),
    )


def _record_design_scope_authority(
    root: Path,
    contract_input: DesignContractInput,
) -> DesignScopeAuthorityAnchor:
    anchor = _design_anchor(contract_input)
    _write_once(_anchor_path(root, "design", contract_input.loop_id), anchor)
    return anchor


def _verify_design_scope_authority(
    root: Path,
    contract_input: DesignContractInput,
    *,
    loop_input_digest: str,
    expected_loop_id: str,
    expected_work_item_id: str,
) -> DesignScopeAuthorityAnchor:
    if (
        contract_input.loop_id != expected_loop_id
        or contract_input.work_item_id != expected_work_item_id
    ):
        raise ScopeAuthorityIntegrityError(
            "design checked identity does not match the confirmed loop"
        )
    anchor = _read_anchor(
        _anchor_path(root, "design", expected_loop_id),
        DesignScopeAuthorityAnchor,
    )
    current = _design_anchor(contract_input)
    if (
        not loop_input_digest
        or loop_input_digest != current.input_digest
        or current.model_dump(exclude={"anchor_digest"})
        != anchor.model_dump(exclude={"anchor_digest"})
    ):
        raise ScopeAuthorityIntegrityError(
            "design checked authority snapshot changed"
        )
    return anchor


def _design_anchor(
    contract_input: DesignContractInput,
) -> DesignScopeAuthorityAnchor:
    return DesignScopeAuthorityAnchor(
        loop_id=contract_input.loop_id,
        work_item_id=contract_input.work_item_id,
        requirement_loop_id=contract_input.requirement_loop_id,
        authorized_scope_families=tuple(
            sorted(set(contract_input.authorized_scope_families))
        ),
        scope_authority_ref=contract_input.scope_authority_ref,
        scope_authority_digest=contract_input.scope_authority_digest,
        input_digest=_design_scope_input_digest(contract_input),
    )


def _anchor_path(root: Path, family: str, loop_id: str) -> Path:
    if not loop_id or Path(loop_id).name != loop_id:
        raise ScopeAuthorityIntegrityError("scope authority loop id is invalid")
    project_id = resolve_repository_project_id(root)
    shared_root = resolve_canonical_shared_state(root, project_id)
    bind_repository_project(shared_root, project_id)
    return shared_root / "scope-authority" / family / f"{loop_id}.json"


def _write_once(path: Path, anchor: ArtifactCompatibility) -> None:
    payload = anchor.model_dump(mode="json")
    try:
        if create_json_exclusive(path, payload):
            return
        if read_json_object(path) != payload:
            raise ScopeAuthorityIntegrityError("scope authority anchor diverged")
    except (OSError, ValueError, SharedStateIntegrityError) as exc:
        if isinstance(exc, ScopeAuthorityIntegrityError):
            raise
        raise ScopeAuthorityIntegrityError(
            "scope authority anchor is unavailable"
        ) from exc


def _read_anchor(path: Path, model: type[_AnchorT]) -> _AnchorT:
    try:
        return model.model_validate(read_json_object(path))
    except (
        FileNotFoundError,
        OSError,
        ValueError,
        ValidationError,
        SharedStateIntegrityError,
    ) as exc:
        raise ScopeAuthorityIntegrityError(
            "scope authority anchor is unavailable"
        ) from exc


__all__ = [
    "ScopeAuthorityIntegrityError",
]
