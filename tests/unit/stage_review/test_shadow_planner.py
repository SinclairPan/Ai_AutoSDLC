from __future__ import annotations

from pathlib import Path

from ai_sdlc.core.stage_review.activation import baseline_activation_policy
from ai_sdlc.core.stage_review.candidate import CandidateManifest
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_optimization_snapshot as baseline_optimization_snapshot,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_session_budget_policies as baseline_session_budget_policies,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import (
    OptimizationSnapshot,
)
from ai_sdlc.core.stage_review.panel import build_budget_policy
from ai_sdlc.core.stage_review.panel_models import ReviewerBudgetPolicy
from ai_sdlc.core.stage_review.registry import (
    build_selection_policy,
    default_registry_bundle,
)
from ai_sdlc.core.stage_review.shadow_plan_reservation import (
    _hold_shadow_panel_plan as hold_shadow_panel_plan,
)
from ai_sdlc.core.stage_review.shadow_plan_reservation import (
    release_shadow_panel_plan,
)
from ai_sdlc.core.stage_review.shadow_planner import (
    _build_shadow_panel_proposal as build_shadow_panel_proposal,
)


def test_default_shadow_planner_produces_two_independent_low_risk_roles() -> None:
    candidate = _candidate("src/service.py")

    result = build_shadow_panel_proposal(
        candidate=candidate,
        activation_policy=baseline_activation_policy(),
    )

    assert result.resolution.result_code == "resolved"
    assert result.resolution.proposal is not None
    roles = result.resolution.proposal.required_slots
    assert len(roles) == 2
    assert len({item.independence_key for item in roles}) == 2
    assert result.risk_profile.risk_level == "low"
    assert result.request.enforcement_mode == "shadow"


def test_enforce_planner_request_records_enforce_lineage() -> None:
    candidate = _candidate("src/service.py")

    result = build_shadow_panel_proposal(
        candidate=candidate,
        activation_policy=baseline_activation_policy(),
        enforcement_mode="enforce",
    )

    assert result.request.enforcement_mode == "enforce"


def test_security_and_data_change_expands_the_required_panel() -> None:
    candidate = _candidate("src/security/auth_migration.py")

    result = build_shadow_panel_proposal(
        candidate=candidate,
        activation_policy=baseline_activation_policy(),
    )

    assert result.resolution.proposal is not None
    role_ids = {
        item.role_profile_id for item in result.resolution.proposal.required_slots
    }
    assert "role.security" in role_ids
    assert "role.data-integrity" in role_ids
    assert len(role_ids) >= 3
    assert result.risk_profile.risk_level == "high"


def test_shadow_plan_is_byte_stable_for_the_same_candidate() -> None:
    candidate = _candidate("src/service.py")
    policy = baseline_activation_policy()

    first = build_shadow_panel_proposal(candidate=candidate, activation_policy=policy)
    second = build_shadow_panel_proposal(candidate=candidate, activation_policy=policy)

    assert first.request.request_digest == second.request.request_digest
    assert first.resolution.proposal is not None
    assert second.resolution.proposal is not None
    assert (
        first.resolution.proposal.proposal_digest
        == second.resolution.proposal.proposal_digest
    )


def test_planner_consumes_the_frozen_optimization_snapshot_policy() -> None:
    candidate = _candidate("src/service.py")
    snapshot = _snapshot_with_minimum_slots(candidate.project_id, 3)

    result = build_shadow_panel_proposal(
        candidate=candidate,
        activation_policy=baseline_activation_policy(),
        optimization_snapshot=snapshot,
    )

    assert result.resolution.proposal is not None
    assert len(result.resolution.proposal.required_slots) == 3
    assert result.request.optimization_snapshot_digest == snapshot.snapshot_digest


def test_held_panel_keeps_final_reservation_until_execution_finishes(
    tmp_path: Path,
) -> None:
    proposal = build_shadow_panel_proposal(
        candidate=_candidate("src/service.py"),
        activation_policy=baseline_activation_policy(),
    )

    held = hold_shadow_panel_plan(tmp_path, proposal)

    current = held.governor.get_reservation(held.plan.final_reservation_id)
    assert current.state == "final"
    release_shadow_panel_plan(held)
    assert held.governor.get_reservation(current.reservation_id).state == "released"


def _candidate(path: str) -> CandidateManifest:
    return CandidateManifest(
        work_item_id="001-test",
        project_id="project.test",
        loop_id="loop.test",
        loop_round_number=1,
        stage_key="implementation",
        stage_instance_id="implementation.stage",
        review_session_id="session.shadow",
        adapter_id="stage-candidate.implementation",
        adapter_version="1.0.0",
        adapter_contract_digest="sha256:adapter:implementation",
        input_artifacts=[],
        input_digests={},
        output_artifacts=[],
        output_digests={},
        change_surface=[path],
        test_evidence_digests=["sha256:test"],
        policy_digests=["sha256:policy"],
        toolchain_ids=["python"],
        target_platform_ids=["linux"],
        protected_source_set=[path],
        review_artifact_exclusion_set=[
            ".ai-sdlc/state/stage-review/project.test/sessions/001-test/"
            "implementation.stage/session.shadow"
        ],
        source_snapshot_digest="sha256:source",
        source_tree_digest="sha256:tree",
        change_surface_digest="sha256:change",
    )


def _snapshot_with_minimum_slots(
    project_id: str,
    minimum_slots: int,
) -> OptimizationSnapshot:
    baseline = baseline_optimization_snapshot(project_id)
    policy = default_registry_bundle().policy
    values = policy.model_dump(
        mode="python",
        exclude={
            "ai_sdlc_version",
            "artifact_kind",
            "canonicalization_version",
            "compatibility_mode",
            "created_at",
            "created_by",
            "extensions",
            "policy_digest",
            "schema_version",
        },
    )
    values["minimum_slots"] = minimum_slots
    selection = build_selection_policy(**values)
    payload = dict(baseline.policy_payload)
    payload["selection_policy"] = selection.model_dump(
        mode="json",
        exclude={"created_at", "created_by", "ai_sdlc_version"},
    )
    budgets = dict(payload["budget_policy"])
    budgets["low"] = _expanded_low_budget().model_dump(mode="json")
    payload["budget_policy"] = budgets
    return OptimizationSnapshot(
        snapshot_id="optimization-snapshot.three-slots",
        project_id=project_id,
        parent_snapshot_digest=baseline.snapshot_digest,
        stable_fallback_digest=baseline.snapshot_digest,
        candidate_digest="sha256:selection-candidate",
        evaluation_report_digests=("sha256:selection-report",),
        policy_payload=payload,
        created_at="2026-07-22T00:00:00Z",
    )


def _expanded_low_budget() -> ReviewerBudgetPolicy:
    baseline = baseline_session_budget_policies()["low"]
    return build_budget_policy(
        created_at="2026-07-20T00:00:00Z",
        created_by="ai-sdlc",
        ai_sdlc_version="1.0.0",
        policy_id=baseline.policy_id,
        version=baseline.version,
        maximum_slots=3,
        hard_provider_calls=12,
        hard_review_passes=6,
        hard_tokens=450_000,
        hard_cost=15,
        hard_wall_clock=2_700,
        hard_parallelism=3,
        hard_role_replans=baseline.hard_role_replans,
        hard_provider_retries=baseline.hard_provider_retries,
        hard_binding_attempts=baseline.hard_binding_attempts,
        owner=baseline.owner,
        review_date=baseline.review_date,
    )
