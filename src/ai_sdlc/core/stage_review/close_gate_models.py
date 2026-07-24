"""Stage Close Gateway 的适用性、操作与证明合同。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.canonical import normalize_repo_path
from ai_sdlc.core.stage_review.finding_models import FindingScope

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class GateApplicabilityDecision(ArtifactCompatibility):
    """受保护策略对单次阶段关闭的不可变适用性决定。"""

    model_config = _MODEL_CONFIG
    schema_version: Literal["gate-applicability-decision.v1"] = (
        "gate-applicability-decision.v1"
    )
    artifact_kind: Literal["gate-applicability-decision"] = (
        "gate-applicability-decision"
    )
    decision_id: str
    gate_id: str
    stage_key: str
    loop_id: str
    mode: Literal["shadow", "enforce", "grandfathered"]
    policy_id: str
    policy_version: str
    policy_digest: str
    reason_code: str
    decision_digest: str = ""

    @model_validator(mode="after")
    def _validate_decision(self) -> GateApplicabilityDecision:
        required = (
            self.decision_id,
            self.gate_id,
            self.stage_key,
            self.loop_id,
            self.policy_id,
            self.policy_version,
            self.policy_digest,
            self.reason_code,
        )
        if any(not value.strip() or value != value.strip() for value in required):
            raise ValueError("gate applicability identity is invalid")
        return fill_artifact_digest(self, "decision_digest")


class StageCloseGateOperation(BaseModel):
    """Shadow 关闭观测的可恢复投影；不替代正式关闭状态机。"""

    model_config = _MODEL_CONFIG
    schema_version: Literal["stage-close-gate-operation.v1"] = (
        "stage-close-gate-operation.v1"
    )
    operation_id: str
    stage_key: str
    loop_id: str
    close_kind: str
    state: Literal["prepared", "original_completed", "shadow_observed"]
    stage_input_digest: str
    result_digest: str = ""
    result_status: str = ""
    result_loop_status: str = ""
    close_artifact_digest: str = ""
    supersedes_attestation_id: str = ""
    attestation_id: str = ""
    attestation_digest: str = ""
    artifact_existed_before: bool = False
    last_error_code: str = ""

    @model_validator(mode="after")
    def _validate_state_payload(self) -> StageCloseGateOperation:
        completed = self.state in {"original_completed", "shadow_observed"}
        completion = (
            self.result_digest,
            self.result_status,
            self.close_artifact_digest,
        )
        if completed != all(value.strip() for value in completion):
            raise ValueError("stage close completion payload contradicts state")
        observed = self.state == "shadow_observed"
        attestation = (self.attestation_id, self.attestation_digest)
        if observed != all(value.strip() for value in attestation):
            raise ValueError("stage close attestation payload contradicts state")
        return self


class CandidateBindingState(BaseModel):
    """区分尚未物化 Candidate 与遗漏绑定。"""

    model_config = _MODEL_CONFIG
    schema_version: Literal["candidate-binding-state.v1"] = "candidate-binding-state.v1"
    status: Literal["not_materialized", "materialized"]
    reason_code: str = ""
    candidate_ref: str = ""
    candidate_manifest_digest: str = ""
    source_snapshot_digest: str = ""
    adapter_contract_digest: str = ""

    @model_validator(mode="after")
    def _validate_binding(self) -> CandidateBindingState:
        materialized = self.status == "materialized"
        bindings = (
            self.candidate_ref,
            self.candidate_manifest_digest,
            self.source_snapshot_digest,
            self.adapter_contract_digest,
        )
        if materialized != all(value.strip() for value in bindings):
            raise ValueError("candidate binding state is incomplete")
        if not materialized and not self.reason_code.strip():
            raise ValueError("unmaterialized candidate requires a reason code")
        return self


class ShadowPlanningState(BaseModel):
    """记录 Phase 1 Planner 是否针对物化 Candidate 得到确定性方案。"""

    model_config = _MODEL_CONFIG
    schema_version: Literal["shadow-planning-state.v1"] = "shadow-planning-state.v1"
    status: Literal["not_run", "resolved", "failed"]
    reason_code: str = ""
    risk_level: str = ""
    risk_profile_ref: str = ""
    risk_profile_digest: str = ""
    plan_request_ref: str = ""
    plan_request_digest: str = ""
    panel_proposal_ref: str = ""
    panel_proposal_digest: str = ""
    panel_plan_ref: str = ""
    panel_plan_digest: str = ""
    final_reservation_digest: str = ""
    required_role_profile_ids: tuple[str, ...] = ()
    required_slot_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate_planning(self) -> ShadowPlanningState:
        resolved = self.status == "resolved"
        refs = (
            self.risk_level,
            self.risk_profile_ref,
            self.risk_profile_digest,
            self.plan_request_ref,
            self.plan_request_digest,
            self.panel_proposal_ref,
            self.panel_proposal_digest,
            self.panel_plan_ref,
            self.panel_plan_digest,
            self.final_reservation_digest,
        )
        if resolved != all(value.strip() for value in refs):
            raise ValueError("shadow planning binding is incomplete")
        if resolved != bool(
            self.required_role_profile_ids and self.required_slot_count
        ):
            raise ValueError("shadow planning required slots contradict status")
        if not resolved and not self.reason_code.strip():
            raise ValueError("unresolved shadow planning requires a reason code")
        return self


class StageCloseGateAttestation(ArtifactCompatibility):
    """统一记录 Shadow 关闭事实，不宣称已完成 Enforce 授权。"""

    model_config = _MODEL_CONFIG
    schema_version: Literal["stage-close-gate-attestation.v2"] = (
        "stage-close-gate-attestation.v2"
    )
    artifact_kind: Literal["stage-close-gate-attestation"] = (
        "stage-close-gate-attestation"
    )
    attestation_id: str
    operation_id: str
    gate_id: str
    adapter_id: str
    adapter_version: str
    adapter_contract_digest: str
    stage_key: str
    loop_id: str
    loop_round_number: int = Field(ge=1)
    stage_instance_id: str
    work_item_id: str = ""
    close_kind: str
    target_status: str
    close_artifact_path: str
    close_artifact_digest: str
    stage_input_digest: str
    result_digest: str
    result_status: str
    result_loop_status: str = ""
    applicability: GateApplicabilityDecision
    candidate: CandidateBindingState
    planning: ShadowPlanningState
    review_status: Literal["not_run", "completed", "needs_user", "blocked"] = (
        "not_run"
    )
    review_reason_code: str = ""
    review_session_digest: str = ""
    review_completion_digest: str = ""
    review_scope: FindingScope | None = None
    authorizing: Literal[False] = False
    certificate_required: bool = False
    observation_origin: Literal["close_execution", "closed_reconciliation"]
    supersedes_attestation_id: str = ""
    attestation_digest: str = ""

    @field_validator("close_artifact_path")
    @classmethod
    def _normalize_artifact_path(cls, value: str) -> str:
        return normalize_repo_path(value)

    @model_validator(mode="after")
    def _validate_attestation(self) -> StageCloseGateAttestation:
        has_completion = bool(
            self.review_session_digest and self.review_completion_digest
        )
        if bool(self.review_session_digest) != bool(self.review_completion_digest):
            raise ValueError("stage close review completion binding is incomplete")
        if has_completion != (self.review_status == "completed"):
            raise ValueError("stage close review status contradicts completion")
        if has_completion != (self.review_scope is not None):
            raise ValueError("stage close review scope contradicts completion")
        if self.review_scope is not None and (
            self.review_scope.work_item_id != self.work_item_id
            or self.review_scope.stage_instance_id != self.stage_instance_id
        ):
            raise ValueError("stage close review scope identity diverged")
        if self.review_status in {"needs_user", "blocked"} and not (
            self.review_reason_code.strip()
        ):
            raise ValueError("incomplete stage review requires a reason code")
        if self.review_status in {"not_run", "completed"} and self.review_reason_code:
            raise ValueError("stage close review reason contradicts status")
        if self.certificate_required != (self.applicability.mode == "enforce"):
            raise ValueError("certificate requirement contradicts applicability")
        if (
            self.applicability.mode == "enforce"
            and self.candidate.status != "materialized"
        ):
            raise ValueError("enforce attestation requires a materialized candidate")
        if (
            self.candidate.status == "materialized"
            and self.candidate.adapter_contract_digest
            != self.adapter_contract_digest
        ):
            raise ValueError("stage close candidate adapter binding is inconsistent")
        required = (
            self.attestation_id,
            self.operation_id,
            self.gate_id,
            self.adapter_id,
            self.adapter_version,
            self.adapter_contract_digest,
            self.stage_key,
            self.loop_id,
            self.stage_instance_id,
            self.close_kind,
            self.target_status,
            self.close_artifact_digest,
            self.stage_input_digest,
            self.result_digest,
            self.result_status,
        )
        if any(not value.strip() or value != value.strip() for value in required):
            raise ValueError("stage close attestation identity is invalid")
        return fill_artifact_digest(self, "attestation_digest")


@dataclass(frozen=True, slots=True)
class PreparedStageClose:
    root: Path
    adapter_id: str
    adapter_version: str
    adapter_contract_digest: str
    stage_key: str
    loop_id: str
    loop_round_number: int
    stage_instance_id: str
    work_item_id: str
    close_kind: str
    target_status: str
    stage_status: str
    close_artifact_path: str
    stage_input_digest: str
    loop_created_at: str
    gate_contract_version: str
    risk_level: str
    stage_state: BaseModel
