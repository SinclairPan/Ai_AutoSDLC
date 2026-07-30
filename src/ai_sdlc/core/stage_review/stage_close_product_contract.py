"""产品 Stage Close 的关闭合同与中断恢复判定。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.candidate import (
    CandidateManifest,
    candidate_binding_digest,
)
from ai_sdlc.core.stage_review.certificate_models import StageCloseIntent
from ai_sdlc.core.stage_review.close_gate_models import (
    GateApplicabilityDecision,
    PreparedStageClose,
)
from ai_sdlc.core.stage_review.close_gate_observation import stage_close_operation_id
from ai_sdlc.core.stage_review.close_models import CloseArtifactContract
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.repo_write_lease import canonical_worktree_identity
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.stage_close_command_recovery import (
    _prepared_close_command_is_recoverable,
)
from ai_sdlc.core.stage_review.stage_close_result_codec import product_result_path


def _product_close_intent(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    candidate: CandidateManifest,
) -> StageCloseIntent:
    operation_id = stage_close_operation_id(prepared)
    return StageCloseIntent(
        scope=_product_close_scope(candidate),
        gate_id=decision.gate_id,
        close_kind=prepared.close_kind,
        target_status=prepared.target_status,
        command_id=stable_id("stage-close-command", operation_id),
        idempotency_key=stable_id("stage-close-key", operation_id),
        loop_id=prepared.loop_id,
        loop_round_number=prepared.loop_round_number,
    )


def _product_close_scope(candidate: CandidateManifest) -> FindingScope:
    return FindingScope(
        project_id=candidate.project_id,
        work_item_id=candidate.work_item_id,
        stage_instance_id=candidate.stage_instance_id,
        session_id=candidate.review_session_id,
    )


def _product_close_marker_contract(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    candidate: CandidateManifest,
) -> CloseArtifactContract:
    operation_id = stage_close_operation_id(prepared)
    path = f".ai-sdlc/state/stage-close-authorizations/{operation_id}.json"
    result_path = product_result_path(prepared).relative_to(prepared.root).as_posix()
    return CloseArtifactContract(
        artifact_path=path,
        payload={
            "schema_version": "stage-close-authorization.v1",
            "artifact_kind": "stage-close-authorization",
            "operation_id": operation_id,
            "stage_key": prepared.stage_key,
            "close_kind": prepared.close_kind,
            "target_status": prepared.target_status,
            "stage_input_digest": prepared.stage_input_digest,
            "product_close_artifact_path": prepared.close_artifact_path,
            "product_result_artifact_path": result_path,
            "candidate_manifest_digest": candidate_binding_digest(candidate),
            "gate_decision_digest": decision.decision_digest,
        },
    )


def _enforce_partial_stage_close_is_recoverable(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    candidate: CandidateManifest,
) -> bool:
    """验证 Enforce writer 中断前已存在同输入的 canonical prepared claim。"""

    intent = _product_close_intent(prepared, decision, candidate)
    marker = _product_close_marker_contract(prepared, decision, candidate)
    return _prepared_close_command_is_recoverable(
        prepared.root,
        project_id=candidate.project_id,
        command_id=intent.command_id,
        claim_matches=lambda claim: all(
            (
                claim.scope == intent.scope,
                claim.command_id == intent.command_id,
                claim.idempotency_key == intent.idempotency_key,
                claim.close_intent_digest == intent.close_intent_digest,
                claim.candidate_manifest_digest == candidate_binding_digest(candidate),
                claim.artifact_path == marker.artifact_path,
                claim.content_contract_digest == marker.content_contract_digest,
                claim.worktree_identity == canonical_worktree_identity(prepared.root),
            )
        ),
    )
