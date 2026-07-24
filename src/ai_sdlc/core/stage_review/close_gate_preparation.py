"""把既有阶段状态映射为统一 Stage Close Gateway 输入。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from ai_sdlc.core.loop_models import LoopType
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
    normalize_repo_path,
)
from ai_sdlc.core.stage_review.close_gate_models import PreparedStageClose
from ai_sdlc.core.stage_review.stage_adapter_registry import (
    StageCloseAdapter,
    default_stage_candidate_adapter_registry,
)


def _build_prepared_stage_close(
    *,
    root: Path,
    adapter: StageCloseAdapter,
    loop_id: str,
    loop_round_number: int,
    stage_instance_id: str,
    work_item_id: str,
    close_kind: str,
    target_status: str,
    close_artifact_path: Path,
    state: BaseModel,
    gate_contract_version: str,
) -> PreparedStageClose:
    registration = default_stage_candidate_adapter_registry().resolve_instance(adapter)
    contract = registration.contract
    resolved = root.resolve()
    relative = close_artifact_path.resolve().relative_to(resolved).as_posix()
    return PreparedStageClose(
        root=resolved,
        adapter_id=contract.adapter_id,
        adapter_version=contract.adapter_version,
        adapter_contract_digest=contract.contract_digest,
        stage_key=adapter.stage_key,
        loop_id=loop_id,
        loop_round_number=loop_round_number,
        stage_instance_id=stage_instance_id,
        work_item_id=work_item_id,
        close_kind=close_kind,
        target_status=target_status,
        stage_status=str(getattr(state, "status", "")),
        close_artifact_path=normalize_repo_path(relative),
        stage_input_digest=canonical_digest(state, CanonicalizationPolicy()),
        loop_created_at=str(getattr(state, "created_at", "")),
        gate_contract_version=gate_contract_version,
        risk_level="unclassified",
        stage_state=state,
    )


def _require_stage_close_adapter(
    adapter: StageCloseAdapter,
    loop_type: LoopType | str,
) -> None:
    registration = default_stage_candidate_adapter_registry().resolve_instance(adapter)
    expected = str(loop_type)
    contract = registration.contract
    if str(contract.loop_type) != expected or contract.stage_key != expected:
        raise ValueError("stage close adapter does not match close route")
