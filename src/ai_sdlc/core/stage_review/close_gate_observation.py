"""Stage Close Gateway 的结果分类与重观测构造。"""

from __future__ import annotations

from pydantic import BaseModel

from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.close_gate_models import (
    PreparedStageClose,
    StageCloseGateOperation,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id


def stage_close_operation_id(prepared: PreparedStageClose) -> str:
    return stable_id(
        "stage-close-gate-operation",
        prepared.stage_key,
        prepared.loop_id,
        prepared.stage_instance_id,
        prepared.close_kind,
        prepared.close_artifact_path,
    )


def _result_closes_stage(result: dict[str, object]) -> bool:
    status = str(result.get("status", "")).lower()
    if status not in {"closed", "ready"}:
        return False
    loop_status = str(result.get("loop_status", "")).lower()
    return not loop_status or loop_status == "closed"


def _reconciled_result(
    prepared: PreparedStageClose,
    artifact_digest: str,
) -> dict[str, object] | None:
    if prepared.stage_status != "closed":
        return None
    return {
        "status": "reconciled",
        "loop_status": "closed",
        "close_artifact_path": prepared.close_artifact_path,
        "close_artifact_digest": artifact_digest,
    }


def _observation_result(
    prepared: PreparedStageClose,
    result: dict[str, object],
    artifact_digest: str,
) -> dict[str, object] | None:
    if _result_closes_stage(result):
        return result
    return _reconciled_result(prepared, artifact_digest)


def _stage_close_result_payload(result: object) -> dict[str, object]:
    if isinstance(result, BaseModel):
        return dict(result.model_dump(mode="json"))
    if isinstance(result, dict):
        return dict(result)
    return {"status": type(result).__name__, "value": str(result)}


def _build_reobservation_operation(
    prepared: PreparedStageClose,
    previous: StageCloseGateOperation,
    result: dict[str, object],
    artifact_digest: str,
    *,
    supersedes_attestation_id: str,
) -> StageCloseGateOperation:
    result_digest = canonical_digest(result, CanonicalizationPolicy())
    return StageCloseGateOperation(
        operation_id=stable_id(
            "stage-close-gate-reobservation",
            previous.operation_id,
            prepared.stage_input_digest,
            result_digest,
            artifact_digest,
        ),
        stage_key=prepared.stage_key,
        loop_id=prepared.loop_id,
        close_kind=prepared.close_kind,
        state="original_completed",
        stage_input_digest=prepared.stage_input_digest,
        result_digest=result_digest,
        result_status=str(result.get("status", "unknown")),
        result_loop_status=str(result.get("loop_status", "")),
        close_artifact_digest=artifact_digest,
        supersedes_attestation_id=supersedes_attestation_id,
        artifact_existed_before=True,
    )
