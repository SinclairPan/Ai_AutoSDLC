"""把真实 Stage Close 输入物化为 Candidate 与只读 Shadow Panel。"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from ai_sdlc.core.loop_models import LoopRun, LoopType
from ai_sdlc.core.pr_review_models import ReviewPack, ReviewRun
from ai_sdlc.core.source_snapshot import (
    SourceSnapshot,
    SourceSnapshotOptions,
    build_source_snapshot,
)
from ai_sdlc.core.stage_review.activation_policy_store import (
    current_activation_policy,
)
from ai_sdlc.core.stage_review.adapters import (
    LocalPRAdapterFacts,
    LocalPRReviewStageAdapter,
)
from ai_sdlc.core.stage_review.artifacts import resolve_repository_project_id
from ai_sdlc.core.stage_review.candidate import (
    CandidateManifest,
    candidate_binding_digest,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.close_gate_models import (
    CandidateBindingState,
    GateApplicabilityDecision,
    PreparedStageClose,
    ShadowPlanningState,
)
from ai_sdlc.core.stage_review.contracts import TaskRiskProfile
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.risk_extractor import (
    _extract_task_risk_profile as extract_task_risk_profile,
)
from ai_sdlc.core.stage_review.stage_adapter_registry import (
    default_stage_candidate_adapter_registry,
)
from ai_sdlc.core.stage_review.stage_loop_candidate import _loop_candidate
from ai_sdlc.core.stage_review.stage_review_execution import (
    StageReviewExecutionOutcome,
    StageReviewExecutor,
)
from ai_sdlc.core.stage_review.stage_review_plan_runtime import (
    HeldStageReviewPlan,
    hold_stage_review_plan,
    release_stage_review_plan,
)


@dataclass(frozen=True, slots=True)
class ShadowPlanningOutcome:
    candidate: CandidateBindingState
    planning: ShadowPlanningState
    review_status: Literal["not_run", "completed", "needs_user", "blocked"] = (
        "not_run"
    )
    review_reason_code: str = ""
    review_session_digest: str = ""
    review_completion_digest: str = ""


@dataclass(frozen=True, slots=True)
class ShadowPlanningPreflight:
    candidate: CandidateManifest | None
    source_snapshot: SourceSnapshot | None
    risk_profile: TaskRiskProfile | None
    failure: ShadowPlanningOutcome | None


def _preflight_shadow_planning(
    prepared: PreparedStageClose,
) -> ShadowPlanningPreflight:
    policy = current_activation_policy(prepared.root)
    try:
        candidate, source_snapshot = _build_candidate(
            prepared, policy.policy_digest
        )
        risk = extract_task_risk_profile(candidate)
    except Exception as exc:
        return ShadowPlanningPreflight(
            candidate=None,
            source_snapshot=None,
            risk_profile=None,
            failure=_candidate_failure(exc),
        )
    return ShadowPlanningPreflight(
        candidate=candidate,
        source_snapshot=source_snapshot,
        risk_profile=risk,
        failure=None,
    )


def _observe_shadow_planning(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    preflight: ShadowPlanningPreflight | None = None,
    executor: StageReviewExecutor | None = None,
) -> ShadowPlanningOutcome:
    frozen = preflight or _preflight_shadow_planning(prepared)
    if frozen.failure is not None:
        return frozen.failure
    if frozen.candidate is None or frozen.source_snapshot is None:
        raise ValueError("shadow planning preflight is incomplete")
    return _observe_candidate_plan(
        prepared,
        decision,
        frozen.candidate,
        frozen.source_snapshot,
        executor,
    )


def _observe_candidate_plan(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    candidate: CandidateManifest,
    source_snapshot: SourceSnapshot,
    executor: StageReviewExecutor | None,
) -> ShadowPlanningOutcome:
    try:
        runtime, execution = _run_candidate_review(
            prepared,
            decision,
            candidate,
            source_snapshot,
            executor,
        )
        return _resolved_outcome(
            candidate=_candidate_state(candidate, runtime.refs["candidate.json"]),
            planning=_planning_state(runtime),
            execution=execution,
        )
    except Exception as exc:
        return ShadowPlanningOutcome(
            candidate=_candidate_state(candidate),
            planning=ShadowPlanningState(
                status="failed",
                reason_code=f"planner-{type(exc).__name__.lower()}",
            ),
        )


def _run_candidate_review(
    prepared: PreparedStageClose,
    decision: GateApplicabilityDecision,
    candidate: CandidateManifest,
    source_snapshot: SourceSnapshot,
    executor: StageReviewExecutor | None,
) -> tuple[HeldStageReviewPlan, StageReviewExecutionOutcome]:
    runtime = hold_stage_review_plan(
        prepared,
        decision,
        candidate,
        source_snapshot,
    )
    try:
        execution = _execute_review(executor, runtime, decision.mode)
    finally:
        release_stage_review_plan(runtime)
    return runtime, execution


def _planning_state(runtime: HeldStageReviewPlan) -> ShadowPlanningState:
    planned = runtime.planned
    proposal = planned.resolution.proposal
    if proposal is None:
        raise ValueError("resolved stage review proposal disappeared")
    return ShadowPlanningState(
        status="resolved",
        risk_level=planned.risk_profile.risk_level,
        risk_profile_ref=runtime.refs["risk-profile.json"],
        risk_profile_digest=planned.risk_profile.profile_digest,
        plan_request_ref=runtime.refs["plan-request.json"],
        plan_request_digest=planned.request.request_digest,
        panel_proposal_ref=runtime.refs["panel-proposal.json"],
        panel_proposal_digest=proposal.proposal_digest,
        panel_plan_ref=runtime.refs["panel-plan.json"],
        panel_plan_digest=runtime.held.plan.plan_digest,
        final_reservation_digest=runtime.held.plan.final_reservation_digest,
        required_role_profile_ids=tuple(
            item.role_profile_id for item in proposal.required_slots
        ),
        required_slot_count=len(proposal.required_slots),
    )


def _execute_review(
    executor: StageReviewExecutor | None,
    runtime: HeldStageReviewPlan,
    mode: Literal["shadow", "enforce", "grandfathered"],
) -> StageReviewExecutionOutcome:
    if executor is None:
        return StageReviewExecutionOutcome(
            status="needs_user",
            reason_code="review-executor-unavailable",
        )
    if mode == "grandfathered":
        return StageReviewExecutionOutcome(
            status="needs_user",
            reason_code="review-not-required-for-grandfathered-close",
        )
    if mode not in {"shadow", "enforce"}:
        raise ValueError("grandfathered stage review cannot execute")
    return executor.execute(runtime.execution_request(mode=mode))


def _resolved_outcome(
    *,
    candidate: CandidateBindingState,
    planning: ShadowPlanningState,
    execution: StageReviewExecutionOutcome,
) -> ShadowPlanningOutcome:
    return ShadowPlanningOutcome(
        candidate=candidate,
        planning=planning,
        review_status=execution.status,
        review_reason_code=execution.reason_code,
        review_session_digest=execution.review_session_digest,
        review_completion_digest=execution.review_completion_digest,
    )


def _candidate_failure(error: Exception) -> ShadowPlanningOutcome:
    reason = f"candidate-{type(error).__name__.lower()}"
    return ShadowPlanningOutcome(
        candidate=CandidateBindingState(status="not_materialized", reason_code=reason),
        planning=ShadowPlanningState(status="not_run", reason_code=reason),
    )


def _build_candidate(
    prepared: PreparedStageClose,
    policy_digest: str,
) -> tuple[CandidateManifest, SourceSnapshot]:
    _require_frozen_input(prepared)
    project_id = resolve_repository_project_id(prepared.root)
    session_id = stable_id("session", prepared.stage_input_digest, policy_digest)
    state = prepared.stage_state
    registration = default_stage_candidate_adapter_registry().resolve_prepared(
        adapter_id=prepared.adapter_id,
        adapter_version=prepared.adapter_version,
        adapter_contract_digest=prepared.adapter_contract_digest,
        loop_type=prepared.stage_key,
    )
    adapter = registration.create()
    if isinstance(state, ReviewRun):
        return _local_pr_candidate(
            prepared,
            state,
            cast(LocalPRReviewStageAdapter, adapter),
            project_id,
            session_id,
            policy_digest,
        )
    if not isinstance(state, LoopRun):
        raise ValueError("unsupported stage close state")
    return _loop_candidate(
        prepared,
        state,
        registration,
        adapter,
        project_id,
        session_id,
        policy_digest,
    )


def _local_pr_candidate(
    prepared: PreparedStageClose,
    run: ReviewRun,
    adapter: LocalPRReviewStageAdapter,
    project_id: str,
    session_id: str,
    policy_digest: str,
) -> tuple[CandidateManifest, SourceSnapshot]:
    if prepared.stage_key != LoopType.LOCAL_PR_REVIEW.value:
        raise ValueError("local PR stage candidate adapter route is invalid")
    pack = _load_review_pack(prepared.root, run.review_pack_path)
    source = run.diff_source
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(
            root=prepared.root,
            source_kind=str(source.source_kind),
            base_ref=source.base_ref,
            head_ref=source.head_ref,
            patch_file=source.patch_file,
        )
    )
    facts = LocalPRAdapterFacts(
        review_run=run,
        review_pack=pack,
        work_item_id=prepared.work_item_id,
        project_id=project_id,
        review_session_id=session_id,
        adapter_id=prepared.adapter_id,
        adapter_version=prepared.adapter_version,
        adapter_contract_digest=prepared.adapter_contract_digest,
        test_evidence_digests=(),
        policy_digests=(policy_digest,),
        toolchain_ids=("ai-sdlc", "git"),
        target_platform_ids=(sys.platform,),
        protected_source_set=tuple(snapshot.changed_files),
    )
    return (
        adapter.build_candidate(
            root=prepared.root,
            source_snapshot=snapshot,
            facts=facts,
        ),
        snapshot,
    )


def _require_frozen_input(prepared: PreparedStageClose) -> None:
    current = canonical_digest(prepared.stage_state, CanonicalizationPolicy())
    if current != prepared.stage_input_digest:
        raise ValueError("stage close input changed after preparation")


def _load_review_pack(root: Path, path_text: str) -> ReviewPack:
    target = (root / path_text).resolve()
    target.relative_to(root.resolve())
    return ReviewPack.model_validate(json.loads(target.read_text(encoding="utf-8")))


def _candidate_state(
    candidate: CandidateManifest,
    candidate_ref: str = "",
) -> CandidateBindingState:
    reference = (
        candidate_ref or f"{candidate.review_artifact_exclusion_set[0]}/candidate.json"
    )
    return CandidateBindingState(
        status="materialized",
        candidate_ref=reference,
        candidate_manifest_digest=candidate_binding_digest(candidate),
        source_snapshot_digest=candidate.source_snapshot_digest,
        adapter_contract_digest=candidate.adapter_contract_digest,
    )
