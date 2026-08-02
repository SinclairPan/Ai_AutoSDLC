"""随包治理输入驱动的确定性 Phase 1 Shadow Panel 规划。"""

from __future__ import annotations

from dataclasses import dataclass

from ai_sdlc.core.stage_review.activation import StageGateActivationPolicy
from ai_sdlc.core.stage_review.candidate import (
    CandidateManifest,
    candidate_binding_digest,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.capability_mapping import CapabilityMappingPolicy
from ai_sdlc.core.stage_review.contracts import TaskRiskProfile
from ai_sdlc.core.stage_review.optimization.defaults import (
    _baseline_optimization_snapshot as baseline_optimization_snapshot,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import OptimizationSnapshot
from ai_sdlc.core.stage_review.panel import (
    build_plan_request,
    build_planning_authorization,
    build_quorum_policy,
    build_role_option,
    plan_reviewer_panel,
)
from ai_sdlc.core.stage_review.panel_authorization_models import (
    ReviewerPlanningAuthorization,
)
from ai_sdlc.core.stage_review.panel_models import (
    PANEL_SOLVER_VERSION,
    EnforcementMode,
    ReviewerBudgetPolicy,
    ReviewerPlanRequest,
    ReviewerQuorumPolicy,
    ReviewerRoleOption,
)
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelResolution
from ai_sdlc.core.stage_review.registry import (
    default_registry_bundle,
    merge_role_modules,
    validate_registry_bundle,
)
from ai_sdlc.core.stage_review.registry_defaults import ReviewerRegistryBundle
from ai_sdlc.core.stage_review.registry_models import (
    ReviewerRoleModule,
    ReviewerSelectionPolicy,
)
from ai_sdlc.core.stage_review.resource_builders import build_budget_envelope, stable_id
from ai_sdlc.core.stage_review.resource_models import BudgetEnvelope
from ai_sdlc.core.stage_review.risk_extractor import (
    _extract_task_risk_profile as extract_task_risk_profile,
)
from ai_sdlc.core.stage_review.role_profiles import RoleProfilePolicy, role_profile_id


@dataclass(frozen=True, slots=True)
class ShadowPanelProposal:
    candidate: CandidateManifest
    risk_profile: TaskRiskProfile
    registry_bundle: ReviewerRegistryBundle
    role_options: tuple[ReviewerRoleOption, ...]
    quorum_policy: ReviewerQuorumPolicy
    budget_policy: ReviewerBudgetPolicy
    planning_authorization: ReviewerPlanningAuthorization
    budget_envelope: BudgetEnvelope
    optimization_snapshot: OptimizationSnapshot
    request: ReviewerPlanRequest
    resolution: ReviewerPanelResolution


def _build_shadow_panel_proposal(
    *,
    candidate: CandidateManifest,
    activation_policy: StageGateActivationPolicy,
    optimization_snapshot: OptimizationSnapshot | None = None,
    enforcement_mode: EnforcementMode = "shadow",
) -> ShadowPanelProposal:
    trusted = CandidateManifest.model_validate(candidate.model_dump(mode="json"))
    risk = extract_task_risk_profile(trusted)
    snapshot = optimization_snapshot or baseline_optimization_snapshot(
        trusted.project_id
    )
    governance = _shadow_governance(trusted, risk, snapshot)
    request = _plan_request(
        trusted,
        risk,
        activation_policy,
        *governance,
        enforcement_mode,
    )
    bundle, options, quorum, budget, authorization, envelope, snapshot = governance
    resolution = plan_reviewer_panel(
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
    return ShadowPanelProposal(
        candidate=trusted,
        risk_profile=risk,
        registry_bundle=bundle,
        role_options=options,
        quorum_policy=quorum,
        budget_policy=budget,
        planning_authorization=authorization,
        budget_envelope=envelope,
        optimization_snapshot=snapshot,
        request=request,
        resolution=resolution,
    )


def _shadow_governance(
    candidate: CandidateManifest,
    risk: TaskRiskProfile,
    snapshot: OptimizationSnapshot,
) -> tuple[
    ReviewerRegistryBundle,
    tuple[ReviewerRoleOption, ...],
    ReviewerQuorumPolicy,
    ReviewerBudgetPolicy,
    ReviewerPlanningAuthorization,
    BudgetEnvelope,
    OptimizationSnapshot,
]:
    bundle = _snapshot_registry_bundle(candidate, snapshot)
    options = _role_options(bundle, snapshot)
    quorum = _quorum_policy(bundle)
    budget = _budget_policy(risk, snapshot)
    authorization = build_planning_authorization(
        registry=bundle.registry,
        role_options=options,
        selection_policy=bundle.policy,
        quorum_policy=quorum,
        budget_policy=budget,
    )
    envelope = build_budget_envelope(
        project_id=candidate.project_id,
        work_item_id=candidate.work_item_id,
        stage_review_session_id=candidate.review_session_id,
        risk_level=risk.risk_level,
        budget_policy=budget,
    )
    return bundle, options, quorum, budget, authorization, envelope, snapshot


def _snapshot_registry_bundle(
    candidate: CandidateManifest,
    snapshot: OptimizationSnapshot,
) -> ReviewerRegistryBundle:
    if snapshot.project_id != candidate.project_id:
        raise ValueError("optimization snapshot project identity diverged")
    baseline = default_registry_bundle()
    mapping = snapshot.policy_payload.get("capability_mapping")
    selection = snapshot.policy_payload.get("selection_policy")
    profiles = snapshot.policy_payload.get("role_profiles")
    if not isinstance(mapping, dict) or not isinstance(selection, dict):
        raise ValueError("optimization snapshot governance is incomplete")
    governed_mapping = CapabilityMappingPolicy.model_validate(mapping)
    if governed_mapping.registry_digest != baseline.registry.registry_digest:
        raise ValueError("optimization snapshot registry is unavailable")
    policy = ReviewerSelectionPolicy.model_validate(selection)
    modules = _snapshot_modules(baseline, profiles)
    bundle = ReviewerRegistryBundle(baseline.registry, policy, modules)
    validate_registry_bundle(
        registry=bundle.registry,
        policy=bundle.policy,
        module_catalog=bundle.role_modules,
    )
    return bundle


def _snapshot_modules(
    baseline: ReviewerRegistryBundle,
    profiles: object,
) -> tuple[ReviewerRoleModule, ...]:
    if not isinstance(profiles, dict):
        raise ValueError("optimization snapshot role profiles are unavailable")
    raw_digests = profiles.get("module_digests")
    if not isinstance(raw_digests, (list, tuple)):
        raise ValueError("optimization snapshot role profile set is invalid")
    digests = tuple(sorted({str(item) for item in raw_digests}))
    modules = tuple(
        item for item in baseline.role_modules if item.module_digest in digests
    )
    if tuple(sorted(item.module_digest for item in modules)) != digests:
        raise ValueError("optimization snapshot role module is unavailable")
    return modules


def _role_options(
    bundle: ReviewerRegistryBundle,
    snapshot: OptimizationSnapshot,
) -> tuple[ReviewerRoleOption, ...]:
    profiles = RoleProfilePolicy.model_validate(
        snapshot.policy_payload.get("role_profiles")
    )
    by_digest = {item.module_digest: item for item in bundle.role_modules}
    contracts = tuple(
        merge_role_modules(
            role_profile_id=role_profile_id(composition, bundle.role_modules),
            version="1.0.0",
            modules=tuple(by_digest[item] for item in composition),
            registry=bundle.registry,
            policy=bundle.policy,
            module_catalog=bundle.role_modules,
        )
        for composition in profiles.compositions
    )
    return tuple(
        build_role_option(
            role_contract=contract,
            eligible_slot_kinds=("required",),
            prompt_template_digest=canonical_digest(
                {"role": contract.role_profile_id, "template": "shadow-review.v1"},
                CanonicalizationPolicy(),
            ),
            tool_permission_ids=("tool.read-candidate",),
            evidence_source_ids=tuple(contract.required_evidence),
            estimated_provider_calls=2,
            estimated_review_passes=2,
            estimated_tokens=100_000,
            estimated_cost=1.0,
            estimated_wall_clock=300.0,
        )
        for contract in contracts
    )


def _quorum_policy(bundle: ReviewerRegistryBundle) -> ReviewerQuorumPolicy:
    return build_quorum_policy(
        policy_id="quorum.ai-sdlc-default",
        version="1.0.0",
        minimum_pass_count=2,
        veto_authorities=bundle.policy.allowed_blocking_authority_ids,
        allowed_abstentions=("optional", "advisory", "shadow"),
        owner="ai-sdlc",
        review_date="2026-07-20",
    )


def _budget_policy(
    profile: TaskRiskProfile,
    snapshot: OptimizationSnapshot,
) -> ReviewerBudgetPolicy:
    key = "high" if profile.risk_level in {"high", "critical"} else profile.risk_level
    policies = snapshot.policy_payload.get("budget_policy")
    if not isinstance(policies, dict) or not isinstance(policies.get(key), dict):
        raise ValueError("optimization snapshot budget policy is unavailable")
    return ReviewerBudgetPolicy.model_validate(policies[key])


def _plan_request(
    candidate: CandidateManifest,
    risk: TaskRiskProfile,
    activation: StageGateActivationPolicy,
    bundle: ReviewerRegistryBundle,
    options: tuple[ReviewerRoleOption, ...],
    quorum: ReviewerQuorumPolicy,
    budget: ReviewerBudgetPolicy,
    authorization: ReviewerPlanningAuthorization,
    envelope: BudgetEnvelope,
    snapshot: OptimizationSnapshot,
    enforcement_mode: EnforcementMode,
) -> ReviewerPlanRequest:
    root = _logical_session_root(candidate)
    return build_plan_request(
        request_id=stable_id(
            "reviewer-plan-request",
            candidate_binding_digest(candidate),
            risk.profile_digest,
        ),
        work_item_id=candidate.work_item_id,
        loop_id=candidate.loop_id,
        loop_round_number=candidate.loop_round_number,
        stage_instance_id=candidate.stage_instance_id,
        candidate_manifest_ref=f"{root}/candidate.json",
        candidate_manifest_digest=candidate_binding_digest(candidate),
        task_risk_profile_ref=f"{root}/risk-profile.json",
        task_risk_profile=risk,
        change_surface_digest=candidate.change_surface_digest,
        registry_ref=".ai-sdlc/policies/reviewer-registry.json",
        registry=bundle.registry,
        role_catalog_ref=".ai-sdlc/policies/reviewer-role-catalog.json",
        role_options=options,
        selection_policy_ref=".ai-sdlc/policies/reviewer-selection-policy.json",
        selection_policy=bundle.policy,
        quorum_policy_ref=".ai-sdlc/policies/reviewer-quorum-policy.json",
        quorum_policy=quorum,
        budget_policy_ref=f".ai-sdlc/policies/{budget.policy_id}.json",
        budget_policy=budget,
        budget_envelope_digest=envelope.envelope_digest,
        planning_authorization=authorization,
        solver_version=PANEL_SOLVER_VERSION,
        optimization_snapshot_ref=f"{root}/optimization-snapshot.json",
        optimization_snapshot_digest=snapshot.snapshot_digest,
        enforcement_mode=enforcement_mode,
    )


def _logical_session_root(candidate: CandidateManifest) -> str:
    return candidate.review_artifact_exclusion_set[0]
