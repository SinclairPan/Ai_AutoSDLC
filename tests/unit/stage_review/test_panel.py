from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from itertools import permutations
from pathlib import Path
from time import perf_counter
from typing import cast

import pytest
from pydantic import ValidationError

from ai_sdlc.core.stage_review.contracts import (
    RiskFact,
    RiskSeverity,
    TaskRiskProfile,
    reconcile_risk_profile,
)
from ai_sdlc.core.stage_review.panel import (
    build_budget_policy,
    build_plan_request,
    build_planning_authorization,
    build_quorum_policy,
    build_role_option,
    panel_proposal_digest,
    plan_reviewer_panel,
    read_panel_proposal,
    validate_panel_proposal,
)
from ai_sdlc.core.stage_review.panel_authorization_models import (
    ReviewerPlanningAuthorization,
)
from ai_sdlc.core.stage_review.panel_digests import (
    plan_request_digest,
    planning_context_digest,
)
from ai_sdlc.core.stage_review.panel_finalization import PanelProposalReplayContext
from ai_sdlc.core.stage_review.panel_models import (
    ReviewerBudgetPolicy,
    ReviewerPlanRequest,
    ReviewerQuorumPolicy,
    ReviewerRoleOption,
    SlotKind,
)
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelResolution
from ai_sdlc.core.stage_review.registry import (
    CapabilityDefinition,
    ReviewerCapabilityRegistry,
    ReviewerRoleContract,
    ReviewerRoleModule,
    ReviewerSelectionPolicy,
    build_capability_registry,
    build_role_module,
    build_selection_policy,
    merge_role_modules,
)
from ai_sdlc.core.stage_review.registry_models import CapabilityConsumptionMode
from ai_sdlc.core.stage_review.resources import (
    ResourceGovernor,
    build_budget_envelope,
)


def test_request_identity_does_not_change_semantic_plan() -> None:
    fixture = _fixture(
        capabilities=[_cap("capability.a"), _cap("capability.b")],
        roles=[
            _role_spec("role.alpha", ["capability.a"], "dimension.alpha"),
            _role_spec("role.beta", ["capability.b"], "dimension.beta"),
        ],
    )
    first = _request(fixture, request_id="request.first")
    second = _request(fixture, request_id="request.second")

    assert first.request_digest != second.request_digest
    assert first.planning_context_digest == second.planning_context_digest
    first_result = _resolve(fixture, first)
    second_result = _resolve(fixture, second)
    assert first_result.proposal is not None
    assert second_result.proposal is not None
    assert (
        first_result.proposal.proposal_digest == second_result.proposal.proposal_digest
    )


def test_planner_returns_proposal_not_final_plan_before_reservation() -> None:
    fixture = _fixture(
        capabilities=[_cap("capability.a"), _cap("capability.b")],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
        ],
    )

    result = _resolve(fixture, _request(fixture))

    assert result.result_code == "resolved"
    assert result.proposal is not None
    assert result.proposal.artifact_kind == "reviewer-panel-proposal"
    assert result.proposal.resource_requirement.required_slot_count == 2
    assert not hasattr(result, "plan")


def test_resource_governor_is_only_official_panel_freeze_boundary(
    tmp_path: Path,
) -> None:
    fixture = _fixture(
        capabilities=[_cap("capability.a"), _cap("capability.b")],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
        ],
    )
    risk = _risk_profile(fixture, list(fixture.required_capability_ids))
    envelope = build_budget_envelope(
        project_id="project.shared",
        work_item_id="001-dynamic-review-gate",
        stage_review_session_id="session.panel-freeze",
        risk_level=risk.risk_level,
        budget_policy=fixture.budget_policy,
    )
    request = _build_request_with_profile(
        fixture,
        risk,
        budget_envelope_digest=envelope.envelope_digest,
    )
    resolution = _resolve(fixture, request, task_risk_profile=risk)
    assert resolution.proposal is not None
    proposal = resolution.proposal
    governor = ResourceGovernor(
        tmp_path,
        project_id="project.shared",
        foreground_capacity=envelope.hard_limits,
    )
    admission = governor.reserve_admission(
        envelope,
        budget_policy=fixture.budget_policy,
        lease_owner="owner.panel-freeze",
        operation_id="operation.panel-admission",
        lease_seconds=60,
    )
    assert admission.reservation is not None
    final = governor.finalize_reservation(
        admission.reservation.reservation_id,
        proposal=proposal,
        lease_owner="owner.panel-freeze",
        expected_fencing_token=admission.reservation.fencing_token,
        operation_id="operation.panel-final",
    )
    assert final.reservation is not None
    context = PanelProposalReplayContext(
        request=request,
        task_risk_profile=risk,
        registry=fixture.registry,
        selection_policy=fixture.selection_policy,
        quorum_policy=fixture.quorum_policy,
        budget_policy=fixture.budget_policy,
        planning_authorization=fixture.planning_authorization,
        role_options=fixture.role_options,
        module_catalog=fixture.modules,
    )

    plan = governor.freeze_panel_plan(
        final.reservation.reservation_id,
        proposal=proposal,
        replay_context=context,
        lease_owner="owner.panel-freeze",
        expected_fencing_token=final.reservation.fencing_token,
    )

    assert plan.proposal == proposal
    assert plan.final_reservation_digest == final.reservation.reservation_digest
    other_request = _build_request_with_profile(
        fixture,
        risk,
        request_id="request.other",
        budget_envelope_digest=envelope.envelope_digest,
    )
    with pytest.raises(ValueError, match="request lineage"):
        governor.freeze_panel_plan(
            final.reservation.reservation_id,
            proposal=proposal,
            replay_context=context.model_copy(update={"request": other_request}),
            lease_owner="owner.panel-freeze",
            expected_fencing_token=final.reservation.fencing_token,
        )


@pytest.mark.parametrize("slot_count", [2, 3, 4, 5])
def test_planner_supports_dynamic_required_slot_counts(slot_count: int) -> None:
    capability_ids = [f"capability.c{index}" for index in range(slot_count)]
    fixture = _fixture(
        capabilities=[_cap(item) for item in capability_ids],
        roles=[
            _role_spec(
                f"role.r{index}",
                [capability_id],
                f"dimension.d{index}",
            )
            for index, capability_id in enumerate(capability_ids)
        ],
        maximum_slots=slot_count,
    )

    result = _resolve(fixture, _request(fixture))

    assert result.result_code == "resolved"
    assert result.proposal is not None
    assert len(result.proposal.required_slots) == slot_count
    assert result.proposal.quorum.minimum_pass_count == slot_count


def test_planner_uses_minimum_set_and_stable_tie_break() -> None:
    fixture = _fixture(
        capabilities=[_cap("capability.a"), _cap("capability.b")],
        roles=[
            _role_spec("role.a1", ["capability.a"], "dimension.alpha"),
            _role_spec("role.a2", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b1", ["capability.b"], "dimension.beta"),
            _role_spec("role.b2", ["capability.b"], "dimension.beta"),
        ],
    )

    plans = [
        _resolve(fixture, _request(fixture), role_options=list(order)).proposal
        for order in permutations(fixture.role_options)
    ]

    assert all(item is not None for item in plans)
    digests = {item.proposal_digest for item in plans if item is not None}
    assert len(digests) == 1
    selected = plans[0]
    assert selected is not None
    assert tuple(item.role_profile_id for item in selected.required_slots) == (
        "role.a1",
        "role.b1",
    )


def test_high_risk_capability_requires_independent_double_coverage() -> None:
    security = _cap("capability.security", authority_ceiling="block")
    fixture = _fixture(
        capabilities=[security],
        roles=[
            _role_spec(
                "role.security-a",
                ["capability.security"],
                "dimension.security",
                blocking=["capability.security"],
            ),
            _role_spec(
                "role.security-b",
                ["capability.security"],
                "dimension.delivery",
                blocking=["capability.security"],
            ),
        ],
        risk_level="high",
        blocking_capability_ids=["capability.security"],
    )

    plan = _resolve(fixture, _request(fixture)).proposal

    assert plan is not None
    proof = {item.capability_id: item for item in plan.coverage_proof}
    assert len(proof["capability.security"].required_slot_ids) == 2
    assert "capability.security" in plan.quorum.veto_authorities

    incomplete_options = list(fixture.role_options[:1])
    incomplete = _resolve(
        fixture,
        _request(fixture, role_options=incomplete_options),
        role_options=incomplete_options,
    )
    assert incomplete.result_code == "no_feasible_panel"


def test_optional_slot_requires_marginal_gain_and_remaining_budget() -> None:
    fixture = _fixture(
        capabilities=[
            _cap("capability.a"),
            _cap("capability.b"),
            _cap("capability.observability"),
        ],
        required_capability_ids=["capability.a", "capability.b"],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
            _role_spec(
                "role.optional",
                ["capability.observability"],
                "dimension.operations",
                eligible_slot_kinds=["optional"],
            ),
            _role_spec(
                "role.no-gain",
                ["capability.a"],
                "dimension.duplicate",
                eligible_slot_kinds=["optional"],
            ),
        ],
        maximum_slots=3,
        optional_slot_limit=1,
    )

    plan = _resolve(fixture, _request(fixture)).proposal

    assert plan is not None
    assert tuple(item.role_profile_id for item in plan.optional_slots) == (
        "role.optional",
    )
    assert set(plan.quorum.required_slot_ids) == {
        item.slot_id for item in plan.required_slots
    }


def test_shadow_and_advisory_slots_never_enter_quorum() -> None:
    fixture = _fixture(
        capabilities=[
            _cap("capability.a"),
            _cap("capability.b"),
            _cap("capability.advice"),
            _cap("capability.future", maturity="shadow"),
        ],
        required_capability_ids=["capability.a", "capability.b"],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
            _role_spec(
                "role.advice",
                ["capability.advice"],
                "dimension.advice",
                eligible_slot_kinds=["advisory"],
            ),
            _role_spec(
                "role.future",
                ["capability.future"],
                "dimension.future",
                eligible_slot_kinds=["shadow"],
                capability_mode="shadow",
            ),
        ],
        maximum_slots=4,
        advisory_slot_limit=1,
        shadow_slot_limit=1,
    )

    plan = _resolve(fixture, _request(fixture)).proposal

    assert plan is not None
    assert len(plan.advisory_slots) == 1
    assert len(plan.shadow_slots) == 1
    assert all(item.counts_for_quorum for item in plan.required_slots)
    assert not plan.advisory_slots[0].counts_for_quorum
    assert not plan.shadow_slots[0].counts_for_quorum


def test_unknown_or_underfunded_requirements_fail_without_approximation() -> None:
    fixture = _fixture(
        capabilities=[_cap("capability.a"), _cap("capability.b")],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
        ],
    )
    unknown_request = _request(
        fixture,
        required_capability_ids=["capability.a", "capability.missing"],
    )
    assert _resolve(fixture, unknown_request).result_code == (
        "unsatisfied_required_capability"
    )

    constrained = replace(
        fixture,
        budget_policy=build_budget_policy(**_budget_values(maximum_slots=1)),
    )
    constrained_request = _request(constrained)
    assert _resolve(constrained, constrained_request).result_code == (
        "no_feasible_panel"
    )


def test_enforce_budget_requires_every_finite_hard_limit() -> None:
    values = _budget_values()
    values["hard_tokens"] = 0
    with pytest.raises(ValidationError, match="greater than 0"):
        build_budget_policy(**values)


def test_required_slot_cannot_be_declared_abstainable() -> None:
    with pytest.raises(ValidationError, match="cannot abstain"):
        build_quorum_policy(
            policy_id="quorum.invalid-abstention",
            version="1.0.0",
            minimum_pass_count=1,
            allowed_abstentions=["required"],
            owner="ai-sdlc",
            review_date=date.today().isoformat(),
        )


def test_tampered_governance_artifact_fails_closed() -> None:
    fixture = _fixture(
        capabilities=[_cap("capability.a"), _cap("capability.b")],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
        ],
    )
    tampered = replace(
        fixture,
        budget_policy=fixture.budget_policy.model_copy(update={"maximum_slots": 100}),
    )

    result = _resolve(tampered, _request(fixture))

    assert result.result_code == "policy_conflict"


def test_blocking_capability_requires_quorum_veto_authority() -> None:
    fixture = _fixture(
        capabilities=[
            _cap("capability.security", authority_ceiling="block"),
            _cap("capability.delivery"),
        ],
        roles=[
            _role_spec(
                "role.security",
                ["capability.security"],
                "dimension.security",
                blocking=["capability.security"],
            ),
            _role_spec(
                "role.delivery",
                ["capability.delivery"],
                "dimension.delivery",
            ),
        ],
        blocking_capability_ids=["capability.security"],
    )
    quorum = build_quorum_policy(
        policy_id="quorum.no-veto",
        version="1.0.0",
        minimum_pass_count=2,
        owner="ai-sdlc",
        review_date=date.today().isoformat(),
    )
    changed = replace(fixture, quorum_policy=quorum)

    result = _resolve(changed, _request(changed))

    assert result.result_code == "policy_conflict"


def test_panel_validator_rejects_recomputed_semantic_drift() -> None:
    fixture = _fixture(
        capabilities=[_cap("capability.a"), _cap("capability.b")],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
        ],
    )
    request = _request(fixture)
    plan = _resolve(fixture, request).proposal
    assert plan is not None
    changed = plan.model_copy(
        update={
            "planning_explanations": ("explanation.changed",),
            "proposal_digest": "",
        }
    )
    changed = changed.model_copy(
        update={"proposal_digest": panel_proposal_digest(changed)}
    )

    with pytest.raises(ValueError, match="replay"):
        validate_panel_proposal(
            changed,
            request=request,
            task_risk_profile=_risk_profile(
                fixture, list(request.required_capability_ids)
            ),
            registry=fixture.registry,
            selection_policy=fixture.selection_policy,
            quorum_policy=fixture.quorum_policy,
            budget_policy=fixture.budget_policy,
            planning_authorization=fixture.planning_authorization,
            role_options=fixture.role_options,
            module_catalog=fixture.modules,
        )


def test_plan_reader_ignores_runtime_time_but_rejects_quorum_downgrade() -> None:
    fixture = _fixture(
        capabilities=[_cap("capability.a"), _cap("capability.b")],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
        ],
    )
    request = _request(fixture)
    risk = _risk_profile(fixture, list(request.required_capability_ids))
    plan = _resolve(fixture, request).proposal
    assert plan is not None
    runtime_changed = plan.model_copy(update={"created_at": "2099-01-01T00:00:00Z"})

    trusted = read_panel_proposal(
        runtime_changed.model_dump(mode="json"),
        request=request,
        task_risk_profile=risk,
        registry=fixture.registry,
        selection_policy=fixture.selection_policy,
        quorum_policy=fixture.quorum_policy,
        budget_policy=fixture.budget_policy,
        planning_authorization=fixture.planning_authorization,
        role_options=fixture.role_options,
        module_catalog=fixture.modules,
    )

    assert trusted.proposal_digest == plan.proposal_digest
    changed_quorum = plan.quorum.model_copy(update={"minimum_pass_count": 1})
    draft = plan.model_copy(update={"quorum": changed_quorum, "proposal_digest": ""})
    payload = draft.model_dump(mode="json")
    payload["proposal_digest"] = panel_proposal_digest(draft)
    with pytest.raises(ValidationError, match="every required slot"):
        type(plan).model_validate(payload)


def test_request_integrity_error_is_not_reported_as_schema_incompatible() -> None:
    fixture = _fixture(
        capabilities=[_cap("capability.a"), _cap("capability.b")],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
        ],
    )
    request = _request(fixture)
    tampered = request.model_copy(update={"loop_round_number": 2})

    result = _resolve(fixture, tampered)

    assert result.result_code == "invalid_input"
    assert result.reason_ids == ("panel.request-invalid",)


def test_request_schema_version_error_has_stable_incompatible_code() -> None:
    fixture = _fixture(
        capabilities=[_cap("capability.a"), _cap("capability.b")],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
        ],
    )
    tampered = _request(fixture).model_copy(update={"schema_version": "999"})

    result = _resolve(fixture, tampered)

    assert result.result_code == "incompatible_schema"
    assert result.reason_ids == ("panel.request-invalid",)


def test_request_reader_replays_blocking_capability_derivation() -> None:
    fixture = _fixture(
        capabilities=[_cap("capability.security", authority_ceiling="block")],
        roles=[
            _role_spec(
                "role.security-a",
                ["capability.security"],
                "dimension.security",
                blocking=["capability.security"],
            ),
            _role_spec(
                "role.security-b",
                ["capability.security"],
                "dimension.delivery",
                blocking=["capability.security"],
            ),
        ],
        risk_level="high",
        blocking_capability_ids=["capability.security"],
    )
    request = _request(fixture)
    changed = request.model_copy(
        update={
            "blocking_capability_ids": (),
            "planning_context_digest": "",
            "request_digest": "",
        }
    )
    draft = changed.model_copy(
        update={"planning_context_digest": planning_context_digest(changed)}
    )
    tampered = draft.model_copy(update={"request_digest": plan_request_digest(draft)})

    result = _resolve(fixture, tampered)

    assert result.result_code == "invalid_input"
    assert result.reason_ids == ("panel.request-lineage-mismatch",)


def test_proposal_reader_requires_exact_request_lineage() -> None:
    fixture = _fixture(
        capabilities=[_cap("capability.a"), _cap("capability.b")],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
        ],
    )
    request = _request(fixture)
    proposal = _resolve(fixture, request).proposal
    assert proposal is not None
    forged = proposal.model_copy(update={"request_digest": "sha256:forged-request"})

    with pytest.raises(ValueError, match="request lineage"):
        read_panel_proposal(
            forged.model_dump(mode="json"),
            request=request,
            task_risk_profile=_risk_profile(
                fixture, list(request.required_capability_ids)
            ),
            registry=fixture.registry,
            selection_policy=fixture.selection_policy,
            quorum_policy=fixture.quorum_policy,
            budget_policy=fixture.budget_policy,
            planning_authorization=fixture.planning_authorization,
            role_options=fixture.role_options,
            module_catalog=fixture.modules,
        )


def test_risk_profile_hard_capability_cannot_be_removed_by_model_copy() -> None:
    fixture = _fixture(
        capabilities=[_cap("capability.a"), _cap("capability.b")],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
        ],
    )
    profile = _risk_profile(fixture, ["capability.a", "capability.b"])
    tampered = profile.model_copy(update={"required_capability_ids": []})

    with pytest.raises(ValidationError, match="deterministic facts"):
        _build_request_with_profile(fixture, tampered)


def test_high_risk_double_coverage_requires_operational_difference() -> None:
    fixture = _fixture(
        capabilities=[_cap("capability.security")],
        roles=[
            _role_spec(
                "role.security-a",
                ["capability.security"],
                "dimension.security",
            ),
            _role_spec(
                "role.security-b",
                ["capability.security"],
                "dimension.delivery",
            ),
        ],
        risk_level="high",
    )
    first, second = fixture.role_options
    duplicate = build_role_option(
        role_contract=second.role_contract,
        eligible_slot_kinds=["required"],
        prompt_template_digest=first.prompt_template_digest,
        tool_permission_ids=first.tool_permission_ids,
        evidence_source_ids=first.evidence_source_ids,
        estimated_provider_calls=1,
        estimated_review_passes=1,
        estimated_tokens=1000,
        estimated_cost=1,
        estimated_wall_clock=10,
    )
    options = [first, duplicate]

    result = _resolve(
        fixture,
        _request(fixture, role_options=options),
        role_options=options,
    )

    assert result.result_code == "no_feasible_panel"
    assert "panel.operational-difference-gap" in result.reason_ids


def test_role_option_catalog_change_requires_new_request_lineage() -> None:
    fixture = _fixture(
        capabilities=[_cap("capability.a"), _cap("capability.b")],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
        ],
    )
    request = _request(fixture)
    first, second = fixture.role_options
    changed = build_role_option(
        role_contract=second.role_contract,
        eligible_slot_kinds=["optional"],
        prompt_template_digest=second.prompt_template_digest,
        tool_permission_ids=second.tool_permission_ids,
        evidence_source_ids=second.evidence_source_ids,
        estimated_provider_calls=1,
        estimated_review_passes=1,
        estimated_tokens=1000,
        estimated_cost=1,
        estimated_wall_clock=10,
    )

    result = _resolve(fixture, request, role_options=[first, changed])

    assert result.result_code == "invalid_input"
    assert result.reason_ids == ("panel.request-lineage-mismatch",)


def test_frozen_planning_authorization_rejects_promoted_role_catalog() -> None:
    fixture = _fixture(
        capabilities=[
            _cap("capability.a"),
            _cap("capability.b"),
            _cap("capability.optional"),
        ],
        required_capability_ids=["capability.a", "capability.b"],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
            _role_spec(
                "role.optional",
                ["capability.optional"],
                "dimension.optional",
                eligible_slot_kinds=["optional"],
            ),
        ],
    )
    promoted = build_role_option(
        role_contract=fixture.role_options[2].role_contract,
        eligible_slot_kinds=["required"],
        prompt_template_digest=fixture.role_options[2].prompt_template_digest,
        tool_permission_ids=fixture.role_options[2].tool_permission_ids,
        evidence_source_ids=fixture.role_options[2].evidence_source_ids,
        estimated_provider_calls=1,
        estimated_review_passes=1,
        estimated_tokens=1000,
        estimated_cost=1,
        estimated_wall_clock=10,
    )
    changed = [*fixture.role_options[:2], promoted]

    with pytest.raises(ValueError, match="planning authorization"):
        _request(
            fixture,
            role_options=changed,
            planning_authorization=fixture.planning_authorization,
        )


def test_slot_abstention_follows_frozen_quorum_policy() -> None:
    fixture = _fixture(
        capabilities=[
            _cap("capability.a"),
            _cap("capability.b"),
            _cap("capability.extra"),
        ],
        required_capability_ids=["capability.a", "capability.b"],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
            _role_spec(
                "role.extra",
                ["capability.extra"],
                "dimension.extra",
                eligible_slot_kinds=["optional"],
            ),
        ],
        maximum_slots=3,
        optional_slot_limit=1,
        allowed_abstentions=[],
    )

    plan = _resolve(fixture, _request(fixture)).proposal

    assert plan is not None
    assert len(plan.optional_slots) == 1
    assert not plan.optional_slots[0].allows_abstain
    assert plan.quorum.allowed_abstentions == ()


def test_no_feasible_panel_explains_budget_and_dimension_gaps() -> None:
    fixture = _fixture(
        capabilities=[_cap("capability.a"), _cap("capability.b")],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
        ],
    )
    budget_values = _budget_values()
    budget_values["hard_tokens"] = 1000
    underfunded = replace(
        fixture,
        budget_policy=build_budget_policy(**budget_values),
    )
    budget_result = _resolve(underfunded, _request(underfunded))
    dimension_fixture = _fixture(
        capabilities=[_cap("capability.a"), _cap("capability.b")],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
        ],
        minimum_distinct_primary_dimensions=3,
    )
    dimension_result = _resolve(dimension_fixture, _request(dimension_fixture))

    assert "panel.budget-gap:tokens" in budget_result.reason_ids
    assert "panel.primary-dimension-gap" in dimension_result.reason_ids
    assert budget_result.reason_ids != dimension_result.reason_ids


def test_request_normalizes_equivalent_windows_and_posix_paths() -> None:
    fixture = _fixture(
        capabilities=[_cap("capability.a"), _cap("capability.b")],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
        ],
    )

    windows = _request(fixture, candidate_manifest_ref="artifacts\\candidate.json")
    posix = _request(fixture, candidate_manifest_ref="artifacts/candidate.json")

    assert windows.candidate_manifest_ref == "artifacts/candidate.json"
    assert windows.request_digest == posix.request_digest
    assert windows.planning_context_digest == posix.planning_context_digest


def test_unknown_solver_version_fails_closed() -> None:
    fixture = _fixture(
        capabilities=[_cap("capability.a"), _cap("capability.b")],
        roles=[
            _role_spec("role.a", ["capability.a"], "dimension.alpha"),
            _role_spec("role.b", ["capability.b"], "dimension.beta"),
        ],
    )

    with pytest.raises(ValidationError, match="unsupported solver_version"):
        _request(fixture, solver_version="panel-solver.v999")


def test_later_policy_shrink_does_not_mutate_frozen_plan() -> None:
    capabilities = [_cap(f"capability.c{index}") for index in range(5)]
    roles = [
        _role_spec(
            f"role.r{index}",
            [f"capability.c{index}"],
            f"dimension.d{index}",
        )
        for index in range(5)
    ]
    original = _fixture(
        capabilities=capabilities,
        roles=roles,
        required_capability_ids=["capability.c0", "capability.c1"],
        minimum_slots=5,
        maximum_slots=5,
    )
    original_plan = _resolve(original, _request(original)).proposal
    assert original_plan is not None
    frozen_payload = original_plan.model_dump(mode="json")
    reduced = _fixture(
        capabilities=capabilities,
        roles=roles,
        required_capability_ids=["capability.c0", "capability.c1"],
        minimum_slots=2,
        maximum_slots=2,
    )

    reduced_plan = _resolve(reduced, _request(reduced)).proposal

    assert reduced_plan is not None
    assert len(original_plan.required_slots) == 5
    assert len(reduced_plan.required_slots) == 2
    assert original_plan.model_dump(mode="json") == frozen_payload


def test_required_search_prunes_large_candidate_catalog() -> None:
    capabilities = [_cap(f"capability.c{index}") for index in range(5)]
    roles = [
        _role_spec(
            f"role.c{capability_index}.{role_index:02d}",
            [f"capability.c{capability_index}"],
            f"dimension.d{capability_index}",
        )
        for capability_index in range(5)
        for role_index in range(20)
    ]
    fixture = _fixture(
        capabilities=capabilities,
        roles=roles,
        minimum_slots=5,
        maximum_slots=5,
        minimum_distinct_primary_dimensions=5,
    )
    started = perf_counter()

    result = _resolve(fixture, _request(fixture))

    assert result.result_code == "resolved"
    assert perf_counter() - started < 5


@dataclass(frozen=True, slots=True)
class _Fixture:
    registry: ReviewerCapabilityRegistry
    selection_policy: ReviewerSelectionPolicy
    quorum_policy: ReviewerQuorumPolicy
    budget_policy: ReviewerBudgetPolicy
    planning_authorization: ReviewerPlanningAuthorization
    modules: tuple[ReviewerRoleModule, ...]
    roles: tuple[ReviewerRoleContract, ...]
    role_options: tuple[ReviewerRoleOption, ...]
    required_capability_ids: tuple[str, ...]
    risk_level: RiskSeverity


@dataclass(frozen=True, slots=True)
class _RoleSpec:
    role_id: str
    capability_ids: tuple[str, ...]
    dimension: str
    blocking: tuple[str, ...]
    eligible_slot_kinds: tuple[SlotKind, ...]
    capability_mode: CapabilityConsumptionMode


def _fixture(
    *,
    capabilities: list[CapabilityDefinition],
    roles: list[_RoleSpec],
    required_capability_ids: list[str] | None = None,
    risk_level: RiskSeverity = "medium",
    blocking_capability_ids: list[str] | None = None,
    maximum_slots: int = 5,
    optional_slot_limit: int = 0,
    advisory_slot_limit: int = 0,
    shadow_slot_limit: int = 0,
    minimum_slots: int = 2,
    minimum_distinct_primary_dimensions: int = 2,
    allowed_abstentions: list[SlotKind] | None = None,
) -> _Fixture:
    registry = build_capability_registry(
        registry_id="registry.panel-test",
        registry_version="1.0.0",
        capabilities=capabilities,
    )
    modules = tuple(_module_from_spec(item) for item in roles)
    selection_policy = build_selection_policy(
        policy_id="policy.panel-test",
        version="1.0.0",
        registry_compatibility_range=">=1.0.0,<2.0.0",
        merge_semantics_version="role-merge.v1",
        allowed_blocking_authority_ids=blocking_capability_ids or [],
        enabled_module_ids=[item.module_id for item in modules],
        minimum_slots=minimum_slots,
        minimum_distinct_primary_dimensions=minimum_distinct_primary_dimensions,
        optional_slot_limit=optional_slot_limit,
        advisory_slot_limit=advisory_slot_limit,
        shadow_slot_limit=shadow_slot_limit,
        double_coverage_risk_levels=["high", "critical"],
        owner="ai-sdlc",
        review_date=date.today().isoformat(),
    )
    role_contracts = tuple(
        merge_role_modules(
            role_profile_id=module.module_id,
            version="1.0.0",
            modules=[module],
            registry=registry,
            policy=selection_policy,
            module_catalog=modules,
            capability_mode=spec.capability_mode,
        )
        for module, spec in zip(modules, roles, strict=True)
    )
    options = tuple(
        build_role_option(
            role_contract=role,
            eligible_slot_kinds=spec.eligible_slot_kinds,
            prompt_template_digest=f"sha256:prompt-{role.role_profile_id}",
            tool_permission_ids=[f"tool.{role.role_profile_id.split('.')[-1]}"],
            evidence_source_ids=list(role.required_evidence) or ["evidence.source"],
            estimated_provider_calls=1,
            estimated_review_passes=1,
            estimated_tokens=1000,
            estimated_cost=1,
            estimated_wall_clock=10,
        )
        for role, spec in zip(role_contracts, roles, strict=True)
    )
    required = tuple(
        required_capability_ids
        if required_capability_ids is not None
        else [item.capability_id for item in capabilities if item.maturity != "shadow"]
    )
    quorum_policy = build_quorum_policy(
        policy_id="quorum.panel-test",
        version="1.0.0",
        minimum_pass_count=2,
        veto_authorities=blocking_capability_ids or [],
        allowed_abstentions=(
            allowed_abstentions
            if allowed_abstentions is not None
            else ["optional", "advisory", "shadow"]
        ),
        owner="ai-sdlc",
        review_date=date.today().isoformat(),
    )
    budget_policy = build_budget_policy(**_budget_values(maximum_slots=maximum_slots))
    planning_authorization = build_planning_authorization(
        registry=registry,
        role_options=options,
        selection_policy=selection_policy,
        quorum_policy=quorum_policy,
        budget_policy=budget_policy,
    )
    return _Fixture(
        registry=registry,
        selection_policy=selection_policy,
        quorum_policy=quorum_policy,
        budget_policy=budget_policy,
        planning_authorization=planning_authorization,
        modules=modules,
        roles=role_contracts,
        role_options=options,
        required_capability_ids=required,
        risk_level=risk_level,
    )


def _request(
    fixture: _Fixture,
    *,
    request_id: str = "request.panel",
    required_capability_ids: list[str] | None = None,
    role_options: list[ReviewerRoleOption] | None = None,
    planning_authorization: ReviewerPlanningAuthorization | None = None,
    candidate_manifest_ref: str = "candidate.json",
    solver_version: str = "panel-solver.v1",
) -> ReviewerPlanRequest:
    required = required_capability_ids or list(fixture.required_capability_ids)
    profile = _risk_profile(fixture, required)
    return _build_request_with_profile(
        fixture,
        profile,
        request_id=request_id,
        role_options=role_options,
        planning_authorization=planning_authorization,
        candidate_manifest_ref=candidate_manifest_ref,
        solver_version=solver_version,
    )


def _build_request_with_profile(
    fixture: _Fixture,
    profile: TaskRiskProfile,
    *,
    request_id: str = "request.panel",
    role_options: list[ReviewerRoleOption] | None = None,
    planning_authorization: ReviewerPlanningAuthorization | None = None,
    candidate_manifest_ref: str = "candidate.json",
    solver_version: str = "panel-solver.v1",
    budget_envelope_digest: str = "sha256:budget-envelope",
) -> ReviewerPlanRequest:
    options = tuple(role_options) if role_options is not None else fixture.role_options
    authorization = planning_authorization or _planning_authorization(fixture, options)
    return build_plan_request(
        request_id=request_id,
        work_item_id="001-dynamic-review-gate",
        loop_id="implementation-loop",
        loop_round_number=1,
        stage_instance_id="implementation.stage-1",
        candidate_manifest_ref=candidate_manifest_ref,
        candidate_manifest_digest="sha256:candidate",
        task_risk_profile_ref="risk-profile.json",
        task_risk_profile=profile,
        change_surface_digest="sha256:change-surface",
        registry_ref="registry.json",
        registry=fixture.registry,
        role_catalog_ref="role-catalog.json",
        role_options=options,
        selection_policy_ref="selection-policy.json",
        selection_policy=fixture.selection_policy,
        quorum_policy_ref="quorum-policy.json",
        quorum_policy=fixture.quorum_policy,
        budget_policy_ref="budget-policy.json",
        budget_policy=fixture.budget_policy,
        budget_envelope_digest=budget_envelope_digest,
        planning_authorization=authorization,
        solver_version=solver_version,
        optimization_snapshot_ref="optimization-snapshot.json",
        optimization_snapshot_digest="sha256:optimization-snapshot",
        enforcement_mode="enforce",
    )


def _risk_profile(
    fixture: _Fixture,
    required: list[str],
) -> TaskRiskProfile:
    facts = [
        RiskFact(
            risk_fact_id=f"risk.{capability_id}",
            source_ref=f"source.{capability_id}",
            extractor_version="extractor.v1",
            confidence=1,
            severity=fixture.risk_level,
            required_capability_ids=[capability_id],
            evidence_digest=f"sha256:evidence-{capability_id}",
        )
        for capability_id in required
    ]
    return reconcile_risk_profile(
        work_item_id="001-dynamic-review-gate",
        stage_key="implementation",
        deterministic_facts=facts,
        semantic_suggestions=[],
    )


def _resolve(
    fixture: _Fixture,
    request: ReviewerPlanRequest,
    *,
    role_options: list[ReviewerRoleOption] | None = None,
    task_risk_profile: TaskRiskProfile | None = None,
) -> ReviewerPanelResolution:
    risk = task_risk_profile or _risk_profile(
        fixture, list(request.required_capability_ids)
    )
    options = tuple(role_options) if role_options is not None else fixture.role_options
    return plan_reviewer_panel(
        request=request,
        task_risk_profile=risk,
        registry=fixture.registry,
        selection_policy=fixture.selection_policy,
        quorum_policy=fixture.quorum_policy,
        budget_policy=fixture.budget_policy,
        planning_authorization=_planning_authorization(fixture, options),
        role_options=options,
        module_catalog=fixture.modules,
    )


def _planning_authorization(
    fixture: _Fixture,
    role_options: tuple[ReviewerRoleOption, ...],
) -> ReviewerPlanningAuthorization:
    return build_planning_authorization(
        registry=fixture.registry,
        role_options=role_options,
        selection_policy=fixture.selection_policy,
        quorum_policy=fixture.quorum_policy,
        budget_policy=fixture.budget_policy,
    )


def _cap(
    capability_id: str,
    **updates: object,
) -> CapabilityDefinition:
    values: dict[str, object] = {
        "capability_id": capability_id,
        "version": "1.0.0",
        "applicable_stage": ["implementation"],
        "applicable_risk": ["low", "medium", "high", "critical"],
        "authority_ceiling": "advise",
        "required_evidence_types": [],
        "maturity": "active",
        "compatibility_range": ">=1.0.0,<2.0.0",
        "owner": "ai-sdlc",
        "review_date": date.today().isoformat(),
    }
    values.update(updates)
    return CapabilityDefinition.model_validate(values)


def _role_spec(
    role_id: str,
    capability_ids: list[str],
    dimension: str,
    *,
    blocking: list[str] | None = None,
    eligible_slot_kinds: list[str] | None = None,
    capability_mode: CapabilityConsumptionMode = "active",
) -> _RoleSpec:
    return _RoleSpec(
        role_id=role_id,
        capability_ids=tuple(capability_ids),
        dimension=dimension,
        blocking=tuple(blocking or ()),
        eligible_slot_kinds=tuple(
            cast(list[SlotKind], eligible_slot_kinds or ["required"])
        ),
        capability_mode=capability_mode,
    )


def _module_from_spec(spec: _RoleSpec) -> ReviewerRoleModule:
    blocking = list(spec.blocking)
    return build_role_module(
        module_id=spec.role_id,
        version="1.0.0",
        module_kind="base",
        capability_ids=list(spec.capability_ids),
        primary_dimensions=[spec.dimension],
        blocking_authority=blocking,
        authority_ceiling="block" if blocking else "advise",
        cost_ceiling=100,
        merge_semantics_version="role-merge.v1",
        compatibility_range=">=1.0.0,<2.0.0",
        owner="ai-sdlc",
        review_date=date.today().isoformat(),
    )


def _budget_values(*, maximum_slots: int = 5) -> dict[str, object]:
    return {
        "policy_id": "budget.panel-test",
        "version": "1.0.0",
        "maximum_slots": maximum_slots,
        "hard_provider_calls": 20,
        "hard_review_passes": 20,
        "hard_tokens": 100000,
        "hard_cost": 100,
        "hard_wall_clock": 3600,
        "hard_parallelism": 5,
        "hard_role_replans": 1,
        "hard_provider_retries": 2,
        "hard_binding_attempts": 3,
        "owner": "ai-sdlc",
        "review_date": date.today().isoformat(),
    }
