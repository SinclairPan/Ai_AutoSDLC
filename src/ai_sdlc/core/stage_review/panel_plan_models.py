"""Reviewer Panel 的冻结 Slot、证明、Quorum 与结果合同。"""

from __future__ import annotations

from itertools import combinations
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_sdlc.core.stage_review.contracts import StageReviewArtifactModel
from ai_sdlc.core.stage_review.panel_models import PlannerResultCode, SlotKind


class ReviewerSlot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    slot_id: str
    slot_kind: SlotKind
    role_profile_id: str
    role_contract_digest: str
    capability_ids: tuple[str, ...]
    blocking_authority: tuple[str, ...]
    primary_dimensions: tuple[str, ...]
    prompt_template_digest: str
    provider_constraints: tuple[str, ...]
    tool_permission_ids: tuple[str, ...]
    evidence_source_ids: tuple[str, ...]
    independence_key: str
    counts_for_quorum: bool
    allows_abstain: bool
    selection_reason_ids: tuple[str, ...]
    estimated_provider_calls: int
    estimated_review_passes: int
    estimated_tokens: int
    estimated_cost: float
    estimated_wall_clock: float


class CapabilityCoverageProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_id: str
    required_slot_ids: tuple[str, ...]
    minimum_required_slots: int
    blocking_slot_ids: tuple[str, ...]


class ReviewerDifference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left_slot_id: str
    right_slot_id: str
    difference_dimensions: tuple[str, ...]


class FrozenQuorumPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required_slot_ids: tuple[str, ...]
    required_capability_expressions: tuple[str, ...]
    minimum_pass_count: int
    veto_authorities: tuple[str, ...]
    allowed_abstentions: tuple[SlotKind, ...]
    source_policy_digest: str


class PanelResourceRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    required_slot_count: int
    total_slot_count: int
    required_provider_calls: int
    total_provider_calls: int
    required_review_passes: int
    total_review_passes: int
    required_tokens: int
    total_tokens: int
    required_cost: float
    total_cost: float
    required_wall_clock: float
    total_wall_clock: float
    parallelism: int


class ReviewerPanelProposal(StageReviewArtifactModel):
    """FinalReservation 前的动态 N-Slot 确定性求解结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["reviewer-panel-proposal"] = "reviewer-panel-proposal"
    request_digest: str
    planning_context_digest: str
    solver_version: str
    registry_digest: str
    role_catalog_digest: str
    selection_policy_digest: str
    quorum_policy_digest: str
    budget_policy_digest: str
    budget_envelope_digest: str
    optimization_snapshot_digest: str
    required_slots: tuple[ReviewerSlot, ...]
    optional_slots: tuple[ReviewerSlot, ...]
    advisory_slots: tuple[ReviewerSlot, ...]
    shadow_slots: tuple[ReviewerSlot, ...]
    coverage_proof: tuple[CapabilityCoverageProof, ...]
    difference_matrix: tuple[ReviewerDifference, ...]
    quorum: FrozenQuorumPolicy
    resource_requirement: PanelResourceRequirement
    rejected_role_reasons: tuple[str, ...]
    planning_explanations: tuple[str, ...]
    proposal_digest: str

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        from ai_sdlc.core.stage_review.panel_digests import panel_proposal_digest

        _verify_plan_shape(self)
        if self.proposal_digest != panel_proposal_digest(self):
            raise ValueError("reviewer panel proposal digest does not match content")
        return self


class ReviewerPanelPlan(StageReviewArtifactModel):
    """FinalReservation 成功后冻结的正式执行计划。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["reviewer-panel-plan"] = "reviewer-panel-plan"
    proposal: ReviewerPanelProposal
    proposal_lineage_digest: str
    final_reservation_id: str
    final_reservation_digest: str
    resource_fencing_token: int = Field(ge=1)
    plan_digest: str
    finalization_digest: str

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        from ai_sdlc.core.stage_review.panel_digests import (
            reviewer_panel_finalization_digest,
            reviewer_panel_plan_digest,
        )

        if self.plan_digest != reviewer_panel_plan_digest(self):
            raise ValueError("reviewer panel plan digest does not match content")
        if self.finalization_digest != reviewer_panel_finalization_digest(self):
            raise ValueError("reviewer panel finalization digest does not match binding")
        return self


def _verify_plan_shape(plan: ReviewerPanelProposal) -> None:
    groups = (
        ("required", plan.required_slots),
        ("optional", plan.optional_slots),
        ("advisory", plan.advisory_slots),
        ("shadow", plan.shadow_slots),
    )
    slots = tuple(item for _, values in groups for item in values)
    if len({item.slot_id for item in slots}) != len(slots):
        raise ValueError("reviewer panel contains duplicate slot identity")
    if len({item.role_profile_id for item in slots}) != len(slots):
        raise ValueError("reviewer panel contains duplicate role identity")
    if any(item.slot_kind != kind for kind, values in groups for item in values):
        raise ValueError("reviewer panel slot kind does not match collection")
    _verify_slot_authority(plan, slots)
    _verify_coverage(plan)
    _verify_difference_matrix(plan, slots)
    if plan.resource_requirement.required_slot_count != len(plan.required_slots):
        raise ValueError("reviewer panel required resource count mismatch")
    if plan.resource_requirement.total_slot_count != len(slots):
        raise ValueError("reviewer panel total resource count mismatch")


def _verify_slot_authority(
    plan: ReviewerPanelProposal,
    slots: tuple[ReviewerSlot, ...],
) -> None:
    required_ids = tuple(item.slot_id for item in plan.required_slots)
    if plan.quorum.required_slot_ids != required_ids:
        raise ValueError("reviewer panel quorum required slots mismatch")
    if plan.quorum.minimum_pass_count != len(required_ids):
        raise ValueError("reviewer panel quorum must require every required slot")
    for item in slots:
        is_required = item.slot_kind == "required"
        if item.counts_for_quorum != is_required:
            raise ValueError("reviewer panel slot quorum authority mismatch")
        expected_abstain = item.slot_kind in plan.quorum.allowed_abstentions
        if item.allows_abstain != expected_abstain:
            raise ValueError("reviewer panel slot abstention policy mismatch")
        if not is_required and item.blocking_authority:
            raise ValueError("non-required reviewer slot cannot block")


def _verify_coverage(plan: ReviewerPanelProposal) -> None:
    required_ids = {item.slot_id for item in plan.required_slots}
    capability_ids: set[str] = set()
    for proof in plan.coverage_proof:
        if proof.capability_id in capability_ids:
            raise ValueError("reviewer panel duplicate capability proof")
        capability_ids.add(proof.capability_id)
        owners = set(proof.required_slot_ids)
        if not owners <= required_ids or len(owners) < proof.minimum_required_slots:
            raise ValueError("reviewer panel capability proof is insufficient")
        if not set(proof.blocking_slot_ids) <= owners:
            raise ValueError("reviewer panel blocking proof is not an owner")


def _verify_difference_matrix(
    plan: ReviewerPanelProposal,
    slots: tuple[ReviewerSlot, ...],
) -> None:
    expected = {(left.slot_id, right.slot_id) for left, right in combinations(slots, 2)}
    actual = {
        (item.left_slot_id, item.right_slot_id) for item in plan.difference_matrix
    }
    if actual != expected or any(
        not item.difference_dimensions for item in plan.difference_matrix
    ):
        raise ValueError("reviewer panel operational difference proof is incomplete")


class ReviewerPanelResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result_code: PlannerResultCode
    proposal: ReviewerPanelProposal | None = None
    reason_ids: tuple[str, ...] = Field(default_factory=tuple)
    missing_capability_ids: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _verify_shape(self) -> Self:
        if (self.result_code == "resolved") != (self.proposal is not None):
            raise ValueError("resolved planner result requires exactly one proposal")
        return self
