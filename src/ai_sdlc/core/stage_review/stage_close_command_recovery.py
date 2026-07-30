"""只读验证并恢复已完整提交的 Stage Close 命令。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.close_models import (
    CloseArtifactContract,
    CloseConsumptionClaim,
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


def _prepared_close_command_is_recoverable(
    root: Path,
    *,
    project_id: str,
    command_id: str,
    claim_matches: Callable[[CloseConsumptionClaim], bool],
) -> bool:
    """只接受由 canonical Authorizer 持久化且尚未调用完 writer 的命令。"""

    try:
        store = StageCloseStore(root, project_id=project_id, lock_timeout_seconds=2)
        matches = store.claims_for_command(command_id)
        if len(matches) != 1 or not claim_matches(matches[0]):
            return False
        claim = matches[0]
        state = store.require_consumable_state(claim)
        last = store.last_event(claim.certificate_id)
        receipt = store.read_receipt(claim.claim_id)
    except (OSError, SharedStateIntegrityError, ValueError):
        return False
    return bool(
        state.status == "consuming"
        and state.revision == 1
        and state.event_kinds == ("prepared",)
        and not state.closed
        and not state.close_artifact_digest
        and not state.receipt_digest
        and last is not None
        and last.event_kind == "prepared"
        and last.event_digest == state.head_event_digest
        and receipt is None
    )


__all__ = ["recover_closed_command"]
