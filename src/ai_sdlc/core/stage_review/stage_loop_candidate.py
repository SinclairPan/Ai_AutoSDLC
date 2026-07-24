"""把普通 Loop close 输入物化为版本绑定的 Stage Candidate。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import cast

from ai_sdlc.core.loop_models import LoopRun
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
    _require_no_staged_changes(prepared.root)
    try:
        snapshot = build_source_snapshot(
            SourceSnapshotOptions(root=prepared.root, source_kind="local-unstaged")
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
        protected_source_set=tuple(snapshot.changed_files),
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


def _require_no_staged_changes(root: Path) -> None:
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--", "."],
        cwd=root,
        capture_output=True,
        check=False,
        timeout=5,
    )
    if result.returncode == 1:
        raise ValueError("stage candidate cannot omit staged changes")
    if result.returncode != 0:
        raise ValueError("staged change detection failed")


__all__: list[str] = []
