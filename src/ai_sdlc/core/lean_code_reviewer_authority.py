"""Resolve Lean exception reviewers through the canonical review authority."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_sdlc.core.lean_code_models import LeanReviewerDecisionArtifact
from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.binding_authority_validation import (
    _validate_binding_authority_snapshot,
)
from ai_sdlc.core.stage_review.binding_lineage import (
    dispatch_assignment_matches_binding,
)
from ai_sdlc.core.stage_review.binding_models import BindingAuthoritySnapshot
from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBinding,
    ReviewerBindingSet,
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.binding_store import BindingArtifactStore
from ai_sdlc.core.stage_review.certificate_receipt_store import (
    FilesystemReviewReceiptArtifactStore,
    ReceiptArtifactError,
)
from ai_sdlc.core.stage_review.certificate_receipt_validation import (
    validate_review_pass_receipts,
)
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan, ReviewerSlot
from ai_sdlc.core.stage_review.session_artifact_models import ReviewPass
from ai_sdlc.core.stage_review.session_models import StageReviewSession
from ai_sdlc.core.stage_review.session_store import SessionEventStore


@dataclass(frozen=True)
class TrustedLeanReviewerExecution:
    artifact: LeanReviewerDecisionArtifact
    session: StageReviewSession
    plan: ReviewerPanelPlan
    binding_set: ReviewerBindingSet
    binding: ReviewerBinding
    assignment: ReviewerDispatchAssignment
    review_pass: ReviewPass
    slot: ReviewerSlot


def resolve_reviewer_execution(
    root: Path,
    artifact: LeanReviewerDecisionArtifact,
) -> tuple[TrustedLeanReviewerExecution | None, str]:
    try:
        scope = _review_scope(artifact)
        session, review_pass = _session_pass(root, scope, artifact)
        plan = _review_plan(root, artifact)
        authority, binding_set, binding, assignment = _binding_authority(
            root, artifact, review_pass
        )
        slot = _required_slot(plan, review_pass.slot_id)
        _validate_binding_authority_snapshot(
            plan,
            authority,
            binding_set,
            (assignment,),
        )
        _require_execution_lineage(
            artifact, session, plan, binding_set, binding, assignment, review_pass, slot
        )
        _require_execution_receipts(
            root,
            artifact,
            review_pass,
            binding,
            assignment,
            authority,
        )
    except (
        OSError,
        ReceiptArtifactError,
        SharedStateIntegrityError,
        ValueError,
    ) as exc:
        return (
            None,
            f"reviewer execution authority is invalid: {artifact.reviewer_id}: {exc}",
        )
    return (
        TrustedLeanReviewerExecution(
            artifact, session, plan, binding_set, binding, assignment, review_pass, slot
        ),
        "",
    )


def reviewer_independence_issue(
    executions: list[TrustedLeanReviewerExecution],
) -> str:
    if len(executions) != 2:
        return "exactly two trusted reviewer executions are required"
    if len({_frozen_context(item) for item in executions}) != 1:
        return "reviewer executions do not share one frozen review context"
    identities = tuple(_execution_identity(item) for item in executions)
    for index, label in enumerate(_IDENTITY_LABELS):
        if len({identity[index] for identity in identities}) != 2:
            return f"reviewer executions share the same {label}"
    if len({item.slot.independence_key for item in executions}) != 2:
        return "reviewer plan independence keys are not distinct"
    if not _plan_difference_is_proven(executions[0], executions[1]):
        return "reviewer plan difference proof is missing"
    if not _binding_independence_is_proven(executions[0], executions[1]):
        return "reviewer binding independence proof is missing"
    return ""


_IDENTITY_LABELS = (
    "actor",
    "slot",
    "binding",
    "provider session",
    "assignment",
    "invocation",
    "review pass",
)


def _review_scope(artifact: LeanReviewerDecisionArtifact) -> FindingScope:
    return FindingScope(
        project_id=artifact.review_project_id,
        work_item_id=artifact.review_work_item_id,
        stage_instance_id=artifact.review_stage_instance_id,
        session_id=artifact.review_session_id,
    )


def _session_pass(
    root: Path,
    scope: FindingScope,
    artifact: LeanReviewerDecisionArtifact,
) -> tuple[StageReviewSession, ReviewPass]:
    store = SessionEventStore(root, project_id=scope.project_id)
    session = store.rebuild(scope)
    if session is None or session.state != "authorized":
        raise ValueError("review session is not authorized")
    refs = tuple(
        item for item in session.pass_refs if item.pass_id == artifact.review_pass_id
    )
    if len(refs) != 1 or refs[0].pass_digest != artifact.review_pass_digest:
        raise ValueError("review pass is not bound to the canonical session")
    review_pass = store.get_pass(scope, artifact.review_pass_id)
    active = (
        review_pass.pass_digest == artifact.review_pass_digest
        and review_pass.pass_id not in session.invalidated_pass_ids
        and review_pass.cohort_id in session.sealed_cohort_ids
        and review_pass.cohort_id not in session.superseded_cohort_ids
    )
    if not active:
        raise ValueError("review pass is not active in the sealed cohort")
    return session, review_pass


def _review_plan(
    root: Path, artifact: LeanReviewerDecisionArtifact
) -> ReviewerPanelPlan:
    shared = resolve_canonical_shared_state(root, artifact.review_project_id)
    path = shared / "shadow-planning" / artifact.review_session_id / "panel-plan.json"
    return ReviewerPanelPlan.model_validate(read_json_object(path))


def _binding_authority(
    root: Path,
    artifact: LeanReviewerDecisionArtifact,
    review_pass: ReviewPass,
) -> tuple[
    BindingAuthoritySnapshot,
    ReviewerBindingSet,
    ReviewerBinding,
    ReviewerDispatchAssignment,
]:
    store = BindingArtifactStore(
        root,
        project_id=artifact.review_project_id,
        lock_timeout_seconds=2,
    )
    assignment = store.find_dispatch_assignment(artifact.review_assignment_digest)
    if assignment is None:
        raise ValueError("reviewer dispatch assignment is unavailable")
    binding_set = store.find_binding_set_by_digest(assignment.binding_set_digest)
    if binding_set is None:
        raise ValueError("reviewer binding set is unavailable")
    authority = store._find_authority_by_digest(
        binding_set.authority_snapshot_digest
    )
    if authority is None:
        raise ValueError("reviewer binding authority is unavailable")
    bindings = tuple(
        item
        for item in binding_set.bindings
        if item.binding_digest == review_pass.binding_digest
    )
    if len(bindings) != 1:
        raise ValueError("reviewer binding is unavailable")
    return authority, binding_set, bindings[0], assignment


def _required_slot(plan: ReviewerPanelPlan, slot_id: str) -> ReviewerSlot:
    slots = tuple(
        item for item in plan.proposal.required_slots if item.slot_id == slot_id
    )
    if len(slots) != 1:
        raise ValueError("review pass is not owned by a required slot")
    return slots[0]


def _require_execution_lineage(
    artifact: LeanReviewerDecisionArtifact,
    session: StageReviewSession,
    plan: ReviewerPanelPlan,
    binding_set: ReviewerBindingSet,
    binding: ReviewerBinding,
    assignment: ReviewerDispatchAssignment,
    review_pass: ReviewPass,
    slot: ReviewerSlot,
) -> None:
    checks = (
        artifact.reviewer_id == review_pass.actor_id == binding.actor_id,
        artifact.reviewer_role == review_pass.role_profile_id == slot.role_profile_id,
        artifact.review_assignment_digest == review_pass.assignment_digest,
        artifact.review_assignment_digest == assignment.assignment_digest,
        session.active_plan_digest == plan.plan_digest == review_pass.plan_digest,
        session.active_binding_set_digest == binding_set.binding_set_digest,
        binding_set.binding_set_digest == review_pass.binding_set_digest,
        session.active_candidate_digest == review_pass.candidate_digest,
        session.policy_digest == review_pass.policy_digest,
        review_pass.scope == session.scope,
        review_pass.verdict == "passed" and review_pass.is_first_cohort_pass,
        artifact.decision_payload_digest in review_pass.evidence_digests,
        bool(review_pass.isolation_receipt_digests),
        bool(review_pass.execution_evidence_root_digest),
        binding.eligible_for_enforce_quorum,
    )
    if not all(checks):
        raise ValueError("review session, plan, pass, or actor lineage diverged")
    _require_binding_lineage(binding_set, binding, assignment, review_pass, slot)


def _require_binding_lineage(
    binding_set: ReviewerBindingSet,
    binding: ReviewerBinding,
    assignment: ReviewerDispatchAssignment,
    review_pass: ReviewPass,
    slot: ReviewerSlot,
) -> None:
    checks = (
        binding_set.execution_mode == "enforce_eligible",
        binding_set.enforcement_mode == "enforce",
        binding_set.plan_digest == review_pass.plan_digest,
        binding_set.candidate_manifest_digest == review_pass.candidate_digest,
        binding.slot_id == review_pass.slot_id == slot.slot_id == assignment.slot_id,
        binding.role_contract_digest == review_pass.role_contract_digest,
        binding.binding_id == review_pass.binding_id,
        binding.binding_digest
        == review_pass.binding_digest
        == assignment.binding_digest,
        binding.provider_id == review_pass.provider_id == assignment.provider_id,
        binding.model_family == review_pass.model_family == assignment.model_family,
        assignment.session_id == review_pass.scope.session_id,
        binding_set.stage_review_session_id == review_pass.scope.session_id,
        assignment.binding_set_digest == binding_set.binding_set_digest,
        assignment.cohort_id == review_pass.cohort_id,
        assignment.candidate_manifest_digest == review_pass.candidate_digest,
        dispatch_assignment_matches_binding(binding_set, binding, assignment),
    )
    if not all(checks):
        raise ValueError("review binding or assignment lineage diverged")


def _require_execution_receipts(
    root: Path,
    artifact: LeanReviewerDecisionArtifact,
    review_pass: ReviewPass,
    binding: ReviewerBinding,
    assignment: ReviewerDispatchAssignment,
    authority: BindingAuthoritySnapshot,
) -> None:
    resolver = FilesystemReviewReceiptArtifactStore(
        root,
        project_id=artifact.review_project_id,
    )
    validate_review_pass_receipts(
        review_pass,
        resolver,
        binding,
        assignment,
        authority,
    )


def _frozen_context(item: TrustedLeanReviewerExecution) -> tuple[str, ...]:
    value = item.review_pass
    return (
        value.scope.project_id,
        value.scope.work_item_id,
        value.scope.stage_instance_id,
        value.scope.session_id,
        value.cohort_id,
        value.candidate_digest,
        value.plan_digest,
        value.binding_set_digest,
        value.policy_digest,
    )


def _execution_identity(item: TrustedLeanReviewerExecution) -> tuple[str, ...]:
    value = item.review_pass
    return (
        value.actor_id,
        value.slot_id,
        value.binding_digest,
        item.binding.session_id,
        value.assignment_digest,
        value.invocation_id,
        value.pass_id,
    )


def _plan_difference_is_proven(
    left: TrustedLeanReviewerExecution,
    right: TrustedLeanReviewerExecution,
) -> bool:
    pair = {left.slot.slot_id, right.slot.slot_id}
    return any(
        {item.left_slot_id, item.right_slot_id} == pair and item.difference_dimensions
        for item in left.plan.proposal.difference_matrix
    )


def _binding_independence_is_proven(
    left: TrustedLeanReviewerExecution,
    right: TrustedLeanReviewerExecution,
) -> bool:
    pair = {left.binding.slot_id, right.binding.slot_id}
    return any(
        {item.left_slot_id, item.right_slot_id} == pair and item.reason_id
        for item in left.binding_set.independence_proofs
    )


__all__ = [
    "TrustedLeanReviewerExecution",
    "resolve_reviewer_execution",
    "reviewer_independence_issue",
]
