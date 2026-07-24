"""Shadow Planner 也通过正式两阶段资源协议冻结 PanelPlan。"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_foreground_capacity as baseline_foreground_capacity,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    baseline_offline_capacity,
)
from ai_sdlc.core.stage_review.panel_finalization import PanelProposalReplayContext
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.resources import ResourceGovernor
from ai_sdlc.core.stage_review.shadow_planner import ShadowPanelProposal


@dataclass(frozen=True, slots=True)
class HeldShadowPanelPlan:
    plan: ReviewerPanelPlan
    governor: ResourceGovernor
    lease_owner: str


def _freeze_shadow_panel_plan(
    root: Path,
    value: ShadowPanelProposal,
) -> ReviewerPanelPlan:
    held = _hold_shadow_panel_plan(root, value)
    release_shadow_panel_plan(held)
    return held.plan


def _hold_shadow_panel_plan(
    root: Path,
    value: ShadowPanelProposal,
) -> HeldShadowPanelPlan:
    proposal = value.resolution.proposal
    if proposal is None:
        raise ValueError(
            f"shadow planner did not resolve: {value.resolution.result_code}"
        )
    governor = ResourceGovernor(
        root,
        project_id=value.candidate.project_id,
        foreground_capacity=baseline_foreground_capacity(),
        offline_optimization_capacity=baseline_offline_capacity(),
    )
    lease_owner = f"shadow-planner.{value.candidate.review_session_id}"
    admission = governor.reserve_admission(
        value.budget_envelope,
        budget_policy=value.budget_policy,
        lease_owner=lease_owner,
        operation_id=f"operation.admission.{value.candidate.review_session_id}",
        lease_seconds=60,
    )
    if admission.reservation is None:
        raise ValueError(f"shadow planner admission failed: {admission.result_code}")
    reservation = admission.reservation
    try:
        plan, reservation = _finalize_and_freeze(
            governor,
            value,
            lease_owner,
            reservation.reservation_id,
            reservation.fencing_token,
        )
        return HeldShadowPanelPlan(plan, governor, lease_owner)
    except Exception:
        with suppress(Exception):
            _release(
                governor,
                reservation.reservation_id,
                lease_owner,
                reservation.fencing_token,
            )
        raise


def release_shadow_panel_plan(held: HeldShadowPanelPlan) -> None:
    current = held.governor.get_reservation(held.plan.final_reservation_id)
    if current.state != "final":
        return
    _release(
        held.governor,
        current.reservation_id,
        held.lease_owner,
        current.fencing_token,
    )


def _finalize_and_freeze(
    governor: ResourceGovernor,
    value: ShadowPanelProposal,
    lease_owner: str,
    reservation_id: str,
    fencing_token: int,
) -> tuple[ReviewerPanelPlan, ResourceReservation]:
    proposal = value.resolution.proposal
    if proposal is None:
        raise ValueError("shadow planner proposal disappeared before finalization")
    final = governor.finalize_reservation(
        reservation_id,
        proposal=proposal,
        lease_owner=lease_owner,
        expected_fencing_token=fencing_token,
        operation_id=f"operation.final.{value.candidate.review_session_id}",
    )
    if final.reservation is None:
        raise ValueError(f"shadow planner finalization failed: {final.result_code}")
    reservation = final.reservation
    plan = governor.freeze_panel_plan(
        reservation.reservation_id,
        proposal=proposal,
        replay_context=_replay_context(value),
        lease_owner=lease_owner,
        expected_fencing_token=reservation.fencing_token,
    )
    return plan, reservation


def _replay_context(value: ShadowPanelProposal) -> PanelProposalReplayContext:
    return PanelProposalReplayContext(
        request=value.request,
        task_risk_profile=value.risk_profile,
        registry=value.registry_bundle.registry,
        selection_policy=value.registry_bundle.policy,
        quorum_policy=value.quorum_policy,
        budget_policy=value.budget_policy,
        planning_authorization=value.planning_authorization,
        role_options=value.role_options,
        module_catalog=value.registry_bundle.role_modules,
    )


def _release(
    governor: ResourceGovernor,
    reservation_id: str,
    lease_owner: str,
    fencing_token: int,
) -> None:
    result = governor.release_reservation(
        reservation_id,
        lease_owner=lease_owner,
        expected_fencing_token=fencing_token,
        operation_id=f"operation.release.{reservation_id}",
    )
    if result.result_code != "released":
        raise ValueError(f"shadow planner release failed: {result.result_code}")
