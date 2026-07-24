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
from ai_sdlc.core.stage_review.candidate import CandidateManifest
from ai_sdlc.core.stage_review.close_gate_models import (
    GateApplicabilityDecision,
    PreparedStageClose,
)
from ai_sdlc.core.stage_review.optimization.runtime import (
    _resolve_active_optimization_snapshot as resolve_active_optimization_snapshot,
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
from ai_sdlc.core.stage_review.shadow_planning_store import (
    _persist_shadow_plan as persist_shadow_plan,
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
