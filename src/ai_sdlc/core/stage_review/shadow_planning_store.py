"""Shadow Planner 不可变工件的跨 Worktree 内容寻址存储。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from ai_sdlc.core.source_snapshot import SourceSnapshot
from ai_sdlc.core.stage_review.artifacts import (
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.candidate import (
    CandidateManifest,
    candidate_binding_digest,
)
from ai_sdlc.core.stage_review.contracts import TaskRiskProfile
from ai_sdlc.core.stage_review.optimization.snapshot_models import OptimizationSnapshot
from ai_sdlc.core.stage_review.panel_models import ReviewerPlanRequest
from ai_sdlc.core.stage_review.panel_plan_models import (
    ReviewerPanelPlan,
    ReviewerPanelProposal,
)
from ai_sdlc.core.stage_review.shadow_planner import ShadowPanelProposal
from ai_sdlc.core.stage_review.source_binding import (
    _source_snapshot_binding_digest as source_snapshot_binding_digest,
)

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_ArtifactEntry = tuple[str, BaseModel, str, Callable[[dict[str, object]], str]]


def _persist_shadow_plan(
    root: Path,
    value: ShadowPanelProposal,
    plan: ReviewerPanelPlan,
    source_snapshot: SourceSnapshot,
) -> dict[str, str]:
    candidate = value.candidate
    logical_root = candidate.review_artifact_exclusion_set[0]
    physical_root = _physical_root(root, candidate)
    artifacts = _artifact_entries(value, plan, source_snapshot)
    for name, artifact, digest, digest_reader in artifacts:
        _persist_artifact(physical_root / name, artifact, digest, digest_reader)
    return {name: f"{logical_root}/{name}" for name, *_rest in artifacts}


def _artifact_entries(
    value: ShadowPanelProposal,
    plan: ReviewerPanelPlan,
    source_snapshot: SourceSnapshot,
) -> tuple[_ArtifactEntry, ...]:
    candidate = value.candidate
    return (
        (
            "source-snapshot.json",
            source_snapshot,
            candidate.source_snapshot_digest,
            lambda item: source_snapshot_binding_digest(
                SourceSnapshot.model_validate(item),
                exclusions=candidate.review_artifact_exclusion_set,
                protected_source_set=candidate.protected_source_set,
                policy_digests=candidate.policy_digests,
            ),
        ),
        (
            "candidate.json",
            candidate,
            candidate_binding_digest(candidate),
            lambda item: candidate_binding_digest(
                CandidateManifest.model_validate(item)
            ),
        ),
        *_planning_entries(value, plan),
    )


def _planning_entries(
    value: ShadowPanelProposal,
    plan: ReviewerPanelPlan,
) -> tuple[_ArtifactEntry, ...]:
    proposal = _required_proposal(value)
    return (
        (
            "risk-profile.json",
            value.risk_profile,
            value.risk_profile.profile_digest,
            lambda item: TaskRiskProfile.model_validate(item).profile_digest,
        ),
        (
            "plan-request.json",
            value.request,
            value.request.request_digest,
            lambda item: ReviewerPlanRequest.model_validate(item).request_digest,
        ),
        (
            "panel-proposal.json",
            proposal,
            proposal.proposal_digest,
            lambda item: ReviewerPanelProposal.model_validate(item).proposal_digest,
        ),
        (
            "panel-plan.json",
            plan,
            plan.plan_digest,
            lambda item: ReviewerPanelPlan.model_validate(item).plan_digest,
        ),
        (
            "optimization-snapshot.json",
            value.optimization_snapshot,
            value.optimization_snapshot.snapshot_digest,
            lambda item: OptimizationSnapshot.model_validate(item).snapshot_digest,
        ),
    )


def _physical_root(root: Path, candidate: CandidateManifest) -> Path:
    shared = resolve_canonical_shared_state(root, candidate.project_id)
    bind_repository_project(shared, candidate.project_id)
    return shared / "shadow-planning" / candidate.review_session_id


def _persist_artifact(
    path: Path,
    artifact: BaseModel,
    digest: str,
    digest_reader: Callable[[dict[str, object]], str],
) -> None:
    payload = artifact.model_dump(mode="json")
    if create_json_exclusive(path, payload):
        return
    if digest_reader(read_json_object(path)) != digest:
        raise ValueError("shadow planning artifact content address diverged")


def _required_proposal(value: ShadowPanelProposal) -> ReviewerPanelProposal:
    proposal = value.resolution.proposal
    if proposal is None:
        raise ValueError(
            f"shadow planner did not resolve: {value.resolution.result_code}"
        )
    return proposal
