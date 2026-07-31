"""Shadow 与 Enforce 共用的候选规划和资源持有边界。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_sdlc.core.source_snapshot import SourceSnapshot
from ai_sdlc.core.stage_review.activation import StageGateActivationPolicy
from ai_sdlc.core.stage_review.activation_models import GateMode
from ai_sdlc.core.stage_review.activation_policy_store import (
    current_activation_policy,
)
from ai_sdlc.core.stage_review.artifacts import (
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.candidate import (
    CandidateManifest,
    candidate_binding_digest,
)
from ai_sdlc.core.stage_review.close_gate_models import (
    GateApplicabilityDecision,
    PreparedStageClose,
)
from ai_sdlc.core.stage_review.contracts import TaskRiskProfile
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_foreground_capacity as baseline_foreground_capacity,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    baseline_offline_capacity,
)
from ai_sdlc.core.stage_review.optimization.runtime import (
    _resolve_active_optimization_snapshot as resolve_active_optimization_snapshot,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import OptimizationSnapshot
from ai_sdlc.core.stage_review.panel import validate_panel_proposal
from ai_sdlc.core.stage_review.panel_models import ReviewerPlanRequest
from ai_sdlc.core.stage_review.panel_plan_models import (
    ReviewerPanelPlan,
    ReviewerPanelProposal,
    ReviewerPanelResolution,
)
from ai_sdlc.core.stage_review.resources import ResourceGovernor
from ai_sdlc.core.stage_review.risk_extractor import (
    _extract_task_risk_profile as extract_task_risk_profile,
)
from ai_sdlc.core.stage_review.shadow_plan_reservation import (
    HeldShadowPanelPlan,
    release_shadow_panel_plan,
)
from ai_sdlc.core.stage_review.shadow_plan_reservation import (
    _hold_shadow_panel_plan as hold_shadow_panel_plan,
)
from ai_sdlc.core.stage_review.shadow_planner import (
    ShadowPanelProposal,
)
from ai_sdlc.core.stage_review.shadow_planner import (
    _build_shadow_panel_proposal as build_shadow_panel_proposal,
)
from ai_sdlc.core.stage_review.shadow_planner import (
    _shadow_governance as shadow_governance,
)
from ai_sdlc.core.stage_review.shadow_planning_store import (
    _persist_shadow_plan as persist_shadow_plan,
)
from ai_sdlc.core.stage_review.source_binding import (
    _source_snapshot_binding_digest as source_snapshot_binding_digest,
)
from ai_sdlc.core.stage_review.stage_review_execution import (
    StageReviewExecutionRequest,
)


@dataclass(frozen=True, slots=True)
class HeldStageReviewPlan:
    planned: ShadowPanelProposal
    held: HeldShadowPanelPlan
    source_snapshot: SourceSnapshot
    refs: dict[str, str]

    def execution_request(self, *, mode: GateMode) -> StageReviewExecutionRequest:
        return StageReviewExecutionRequest(
            candidate=self.planned.candidate,
            source_snapshot=self.source_snapshot,
            proposal=self.planned,
            plan=self.held.plan,
            budget_policy=self.planned.budget_policy,
            governor=self.held.governor,
            lease_owner=self.held.lease_owner,
            mode=mode,
        )


def hold_stage_review_plan(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    candidate: CandidateManifest,
    source_snapshot: SourceSnapshot,
) -> HeldStageReviewPlan:
    snapshot = resolve_active_optimization_snapshot(
        prepared.root,
        project_id=candidate.project_id,
    )
    planned = build_shadow_panel_proposal(
        candidate=candidate,
        activation_policy=_policy_from_decision(prepared.root, decision),
        optimization_snapshot=snapshot,
        enforcement_mode=decision.mode,
    )
    if planned.resolution.proposal is None:
        raise ValueError(f"planner-{planned.resolution.result_code}")
    held = hold_shadow_panel_plan(prepared.root, planned)
    try:
        refs = persist_shadow_plan(
            prepared.root,
            planned,
            held.plan,
            source_snapshot,
        )
    except Exception:
        release_shadow_panel_plan(held)
        raise
    return HeldStageReviewPlan(planned, held, source_snapshot, refs)


def release_stage_review_plan(value: HeldStageReviewPlan) -> None:
    release_shadow_panel_plan(value.held)


def _recover_stage_review_plan(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    candidate: CandidateManifest,
    source_snapshot: SourceSnapshot,
) -> HeldStageReviewPlan:
    """从不可变计划与资源事件链恢复已提交 close 的原执行上下文。"""

    _policy_from_decision(prepared.root, decision)
    shared = resolve_canonical_shared_state(prepared.root, candidate.project_id)
    plan = ReviewerPanelPlan.model_validate(
        read_json_object(
            shared / "shadow-planning" / candidate.review_session_id / "panel-plan.json"
        )
    )
    planned = _recover_planned_context(
        shared,
        candidate,
        source_snapshot,
        decision,
        plan.proposal,
    )
    proposal = planned.resolution.proposal
    if proposal is None or plan.proposal != proposal:
        raise ValueError("recovered stage review proposal diverged")
    governor = ResourceGovernor(
        prepared.root,
        project_id=candidate.project_id,
        foreground_capacity=baseline_foreground_capacity(),
        offline_optimization_capacity=baseline_offline_capacity(),
    )
    ancestor = governor.get_reservation_ancestor(
        plan.final_reservation_id,
        plan.final_reservation_digest,
    )
    if (
        ancestor is None
        or ancestor.state != "final"
        or ancestor.stage_review_session_id != candidate.review_session_id
        or ancestor.proposal_digest != proposal.proposal_digest
    ):
        raise ValueError("recovered stage review reservation diverged")
    refs = persist_shadow_plan(prepared.root, planned, plan, source_snapshot)
    held = HeldShadowPanelPlan(
        plan=plan,
        governor=governor,
        lease_owner=f"shadow-planner.{candidate.review_session_id}",
    )
    return HeldStageReviewPlan(planned, held, source_snapshot, refs)


def _recover_planned_context(
    shared: Path,
    candidate: CandidateManifest,
    source_snapshot: SourceSnapshot,
    decision: GateApplicabilityDecision,
    frozen_proposal: ReviewerPanelProposal,
) -> ShadowPanelProposal:
    root = shared / "shadow-planning" / candidate.review_session_id
    risk, request, snapshot = _read_recovered_planning_inputs(root)
    _verify_recovered_planning_inputs(
        candidate,
        source_snapshot,
        decision,
        risk,
        request,
        snapshot,
    )
    bundle, options, quorum, budget, authorization, envelope, snapshot = (
        shadow_governance(candidate, risk, snapshot)
    )
    planning_inputs: dict[str, object] = dict(
        request=request,
        task_risk_profile=risk,
        registry=bundle.registry,
        selection_policy=bundle.policy,
        quorum_policy=quorum,
        budget_policy=budget,
        planning_authorization=authorization,
        role_options=options,
        module_catalog=bundle.role_modules,
    )
    validate_panel_proposal(frozen_proposal, **planning_inputs)
    return ShadowPanelProposal(
        candidate,
        risk,
        bundle,
        options,
        quorum,
        budget,
        authorization,
        envelope,
        snapshot,
        request,
        ReviewerPanelResolution(result_code="resolved", proposal=frozen_proposal),
    )


def _read_recovered_planning_inputs(
    root: Path,
) -> tuple[TaskRiskProfile, ReviewerPlanRequest, OptimizationSnapshot]:
    risk = TaskRiskProfile.model_validate(read_json_object(root / "risk-profile.json"))
    request = ReviewerPlanRequest.model_validate(
        read_json_object(root / "plan-request.json")
    )
    snapshot = OptimizationSnapshot.model_validate(
        read_json_object(root / "optimization-snapshot.json")
    )
    return risk, request, snapshot


def _verify_recovered_planning_inputs(
    candidate: CandidateManifest,
    source_snapshot: SourceSnapshot,
    decision: GateApplicabilityDecision,
    risk: TaskRiskProfile,
    request: ReviewerPlanRequest,
    snapshot: OptimizationSnapshot,
) -> None:
    if (
        risk.profile_digest != extract_task_risk_profile(candidate).profile_digest
        or not all(
            (
                request.candidate_manifest_digest == candidate_binding_digest(candidate),
                request.task_risk_profile_digest == risk.profile_digest,
                request.change_surface_digest == candidate.change_surface_digest,
                request.optimization_snapshot_digest == snapshot.snapshot_digest,
                request.enforcement_mode == decision.mode,
                source_snapshot_binding_digest(
                    source_snapshot,
                    exclusions=candidate.review_artifact_exclusion_set,
                    protected_source_set=candidate.protected_source_set,
                    policy_digests=candidate.policy_digests,
                )
                == candidate.source_snapshot_digest,
            )
        )
    ):
        raise ValueError("recovered stage review planning input diverged")


def _policy_from_decision(
    root: Path,
    decision: GateApplicabilityDecision,
) -> StageGateActivationPolicy:
    policy = current_activation_policy(root)
    if policy.policy_digest != decision.policy_digest:
        raise ValueError("gate decision does not bind the active activation policy")
    return policy


__all__ = [
    "HeldStageReviewPlan",
    "hold_stage_review_plan",
    "release_stage_review_plan",
]
