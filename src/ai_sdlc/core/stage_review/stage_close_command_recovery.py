"""只读验证并恢复已完整提交的 Stage Close 命令。"""

from __future__ import annotations

from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.close_models import (
    CloseArtifactContract,
    StageCloseAuthorization,
)
from ai_sdlc.core.stage_review.close_store import StageCloseStore
from ai_sdlc.core.stage_review.close_validation import require_closed_reconciliation


def recover_closed_command(
    root: Path,
    *,
    project_id: str,
    command_id: str,
    contract: CloseArtifactContract,
) -> StageCloseAuthorization | None:
    store = StageCloseStore(
        root,
        project_id=project_id,
        lock_timeout_seconds=2,
    )
    matches = store.claims_for_command(command_id)
    if not matches:
        return None
    if len(matches) != 1:
        raise SharedStateIntegrityError("close command has multiple claims")
    claim = matches[0]
    state = store.require_consumable_state(claim)
    if not state.closed:
        return None
    receipt = store.read_receipt(claim.claim_id)
    if receipt is None:
        raise SharedStateIntegrityError("closed command receipt is unavailable")
    store.require_artifact(contract, state.close_artifact_digest)
    require_closed_reconciliation(
        claim,
        state,
        receipt,
        store.last_event(claim.certificate_id),
    )
    return StageCloseAuthorization(
        status="closed",
        claim=claim,
        receipt=receipt,
        state=state,
    )


__all__ = ["recover_closed_command"]
