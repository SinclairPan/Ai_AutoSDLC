"""把普通 Loop close 输入物化为版本绑定的 Stage Candidate。"""

from __future__ import annotations

import sys
from typing import cast

from ai_sdlc.core.loop_models import LoopRun
from ai_sdlc.core.source_change_capture import affected_paths
from ai_sdlc.core.source_snapshot import (
    SourceSnapshot,
    SourceSnapshotOptions,
    build_source_snapshot,
)
from ai_sdlc.core.stage_review.adapters import (
    DesignContractStageAdapter,
    FrontendEvidenceStageAdapter,
    ImplementationStageAdapter,
    RequirementStageAdapter,
    StageAdapterFacts,
)
from ai_sdlc.core.stage_review.candidate import CandidateManifest
from ai_sdlc.core.stage_review.close_gate_models import PreparedStageClose
from ai_sdlc.core.stage_review.stage_adapter_registry import (
    StageCandidateAdapterRegistration,
    StageCloseAdapter,
)


def _loop_candidate(
    prepared: PreparedStageClose,
    state: LoopRun,
    registration: StageCandidateAdapterRegistration,
    adapter: StageCloseAdapter,
    project_id: str,
    session_id: str,
    policy_digest: str,
) -> tuple[CandidateManifest, SourceSnapshot]:
    try:
        snapshot = build_source_snapshot(
            SourceSnapshotOptions(root=prepared.root, source_kind="local-worktree")
        )
    except ValueError as exc:
        if str(exc) != "source snapshot contains no changed files":
            raise
        snapshot = build_source_snapshot(
            SourceSnapshotOptions(root=prepared.root, source_kind="loop-artifacts")
        )
    if registration.contract.input_kind != "loop-run":
        raise ValueError("stage candidate adapter input contract is invalid")
    facts = StageAdapterFacts(
        loop_run=state,
        project_id=project_id,
        review_session_id=session_id,
        adapter_id=prepared.adapter_id,
        adapter_version=prepared.adapter_version,
        adapter_contract_digest=prepared.adapter_contract_digest,
        test_evidence_digests=(),
        policy_digests=(policy_digest,),
        toolchain_ids=("ai-sdlc",),
        target_platform_ids=(sys.platform,),
        protected_source_set=tuple(affected_paths(snapshot)),
    )
    builder = cast(
        RequirementStageAdapter
        | DesignContractStageAdapter
        | ImplementationStageAdapter
        | FrontendEvidenceStageAdapter,
        adapter,
    )
    return builder.build_candidate(
        root=prepared.root,
        source_snapshot=snapshot,
        facts=facts,
    ), snapshot
__all__: list[str] = []
