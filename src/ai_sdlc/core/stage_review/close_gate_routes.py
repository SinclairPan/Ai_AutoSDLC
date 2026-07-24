"""既有 Loop 与 Local PR 状态到统一 Stage Close 输入的薄映射。"""

from __future__ import annotations

from pathlib import Path

from ai_sdlc.core.loop_models import LoopRun, LoopType
from ai_sdlc.core.pr_review_models import ReviewRun
from ai_sdlc.core.stage_review.close_gate_models import PreparedStageClose
from ai_sdlc.core.stage_review.close_gate_preparation import (
    StageCloseAdapter,
)
from ai_sdlc.core.stage_review.close_gate_preparation import (
    _build_prepared_stage_close as build_prepared_stage_close,
)
from ai_sdlc.core.stage_review.close_gate_preparation import (
    _require_stage_close_adapter as require_stage_close_adapter,
)

_GATE_CONTRACT_VERSION = "1.0.0"


def prepare_loop_stage_close(
    *,
    root: Path,
    adapter: StageCloseAdapter,
    loop_run: LoopRun,
    close_kind: str,
    target_status: str,
    close_artifact_path: Path,
) -> PreparedStageClose:
    """把四类 LoopRun 只读映射为 Gateway 输入。"""

    require_stage_close_adapter(adapter, loop_run.loop_type)
    return build_prepared_stage_close(
        root=root,
        adapter=adapter,
        loop_id=loop_run.loop_id,
        loop_round_number=max(loop_run.current_round, 1),
        stage_instance_id=loop_run.loop_id,
        work_item_id=loop_run.work_item_id,
        close_kind=close_kind,
        target_status=target_status,
        close_artifact_path=close_artifact_path,
        state=loop_run,
        gate_contract_version=_GATE_CONTRACT_VERSION,
    )


def prepare_local_pr_stage_close(
    *,
    root: Path,
    adapter: StageCloseAdapter,
    review_run: ReviewRun,
    work_item_id: str,
    close_kind: str,
    target_status: str,
    close_artifact_path: Path,
) -> PreparedStageClose:
    """把 Local PR ReviewRun 只读映射为同一 Gateway 输入。"""

    require_stage_close_adapter(adapter, LoopType.LOCAL_PR_REVIEW)
    return build_prepared_stage_close(
        root=root,
        adapter=adapter,
        loop_id=review_run.loop_id,
        loop_round_number=1,
        stage_instance_id=review_run.review_id,
        work_item_id=work_item_id,
        close_kind=close_kind,
        target_status=target_status,
        close_artifact_path=close_artifact_path,
        state=review_run,
        gate_contract_version=_GATE_CONTRACT_VERSION,
    )


__all__ = ["prepare_local_pr_stage_close", "prepare_loop_stage_close"]
