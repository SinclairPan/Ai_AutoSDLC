"""在同一 Design Contract Loop 内安全推进已检查的 scope authority 快照。"""

from __future__ import annotations

from pathlib import Path

from ai_sdlc.core.design_contract_models import DesignContractInput
from ai_sdlc.core.scope_authority_store import (
    DesignScopeAuthorityAnchor,
    ScopeAuthorityIntegrityError,
    _anchor_path,
    _design_anchor,
    _read_anchor,
    _record_design_scope_authority,
)
from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
    ShortFileLock,
    atomic_write_json,
)


def _advance_design_scope_authority(
    root: Path,
    contract_input: DesignContractInput,
    *,
    previous_input: DesignContractInput | None,
    previous_loop_input_digest: str,
) -> DesignScopeAuthorityAnchor:
    """仅在旧快照仍精确匹配时，把 authority 推进到修复后的输入。"""

    if previous_input is None:
        return _record_design_scope_authority(root, contract_input)
    previous = _design_anchor(previous_input)
    updated = _design_anchor(contract_input)
    if _authority_identity(previous) != _authority_identity(updated):
        raise ScopeAuthorityIntegrityError("design scope authority identity changed")
    previous_digest_matches = (
        bool(previous_loop_input_digest)
        and previous_loop_input_digest == previous.input_digest
    )
    path = _anchor_path(root, "design", contract_input.loop_id)
    if path.is_symlink():
        raise ScopeAuthorityIntegrityError("scope authority anchor diverged")
    try:
        with ShortFileLock(path.with_suffix(".lock"), timeout_seconds=5):
            if path.is_symlink():
                raise ScopeAuthorityIntegrityError("scope authority anchor diverged")
            current = _read_anchor(path, DesignScopeAuthorityAnchor)
            if current == updated:
                return updated
            if current == previous:
                atomic_write_json(path, updated.model_dump(mode="json"))
            elif not previous_digest_matches:
                raise ScopeAuthorityIntegrityError(
                    "design checked authority snapshot changed"
                )
            else:
                raise ScopeAuthorityIntegrityError("scope authority anchor diverged")
    except (
        OSError,
        ValueError,
        ResourceLockUnavailableError,
        SharedStateIntegrityError,
    ) as exc:
        raise ScopeAuthorityIntegrityError(
            "scope authority anchor is unavailable"
        ) from exc
    return updated


def _authority_identity(anchor: DesignScopeAuthorityAnchor) -> dict[str, object]:
    return anchor.model_dump(exclude={"anchor_digest", "input_digest"})
