"""把既有五类 LoopRound 只读投影为统一 Candidate。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from ai_sdlc.core.loop_models import LoopRound, LoopRun, LoopType
from ai_sdlc.core.pr_review_models import DiffSourceKind, ReviewPack, ReviewRun
from ai_sdlc.core.source_snapshot import SourceSnapshot, is_runtime_artifact_path
from ai_sdlc.core.stage_review.adapter_artifacts import (
    local_pr_inputs,
    require_bound_review_inputs,
    require_persisted_review_pack,
)
from ai_sdlc.core.stage_review.candidate import (
    CandidateBuildContext,
    CandidateManifest,
    build_candidate_manifest,
)


@dataclass(frozen=True, slots=True)
class StageAdapterFacts:
    """Adapter 从阶段真值之外接收的共享只读证据。"""

    loop_run: LoopRun
    project_id: str
    review_session_id: str
    adapter_id: str
    adapter_version: str
    adapter_contract_digest: str
    test_evidence_digests: tuple[str, ...]
    policy_digests: tuple[str, ...]
    toolchain_ids: tuple[str, ...]
    target_platform_ids: tuple[str, ...]
    protected_source_set: tuple[str, ...]
    extensions: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class LocalPRAdapterFacts:
    """Local PR 以真实 ReviewRun/ReviewPack 作为阶段输入。"""

    review_run: ReviewRun
    review_pack: ReviewPack
    work_item_id: str
    project_id: str
    review_session_id: str
    adapter_id: str
    adapter_version: str
    adapter_contract_digest: str
    test_evidence_digests: tuple[str, ...]
    policy_digests: tuple[str, ...]
    toolchain_ids: tuple[str, ...]
    target_platform_ids: tuple[str, ...]
    protected_source_set: tuple[str, ...]
    extensions: Mapping[str, object] | None = None


class _ReadOnlyStageAdapter:
    loop_type: ClassVar[LoopType]
    stage_key: ClassVar[str]

    def candidate_context(self, facts: StageAdapterFacts) -> CandidateBuildContext:
        """读取当前 LoopRound；不推进轮次，也不执行关闭或评审。"""

        loop_run = facts.loop_run
        if loop_run.loop_type != self.loop_type:
            raise ValueError(f"stage adapter loop type mismatch: {loop_run.loop_type}")
        loop_round = _current_round(loop_run)
        if not loop_run.work_item_id.strip():
            raise ValueError("stage adapter requires a work item identity")
        return CandidateBuildContext(
            work_item_id=loop_run.work_item_id,
            project_id=facts.project_id,
            loop_id=loop_run.loop_id,
            loop_round_number=loop_round.round_number,
            stage_key=self.stage_key,
            stage_instance_id=loop_run.loop_id,
            review_session_id=facts.review_session_id,
            adapter_id=facts.adapter_id,
            adapter_version=facts.adapter_version,
            adapter_contract_digest=facts.adapter_contract_digest,
            input_artifacts=self._input_artifacts(loop_round),
            output_artifacts=tuple(loop_round.output_artifacts),
            test_evidence_digests=facts.test_evidence_digests,
            policy_digests=facts.policy_digests,
            toolchain_ids=facts.toolchain_ids,
            target_platform_ids=facts.target_platform_ids,
            protected_source_set=facts.protected_source_set,
            extensions=facts.extensions,
        )

    def _input_artifacts(self, loop_round: LoopRound) -> tuple[str, ...]:
        return tuple(loop_round.input_artifacts)

    def build_candidate(
        self,
        *,
        root: Path,
        source_snapshot: SourceSnapshot,
        facts: StageAdapterFacts,
    ) -> CandidateManifest:
        """经统一 Candidate 边界构建阶段候选。"""

        return build_candidate_manifest(
            root=root,
            source_snapshot=source_snapshot,
            context=self.candidate_context(facts),
        )


class RequirementStageAdapter(_ReadOnlyStageAdapter):
    loop_type = LoopType.REQUIREMENT
    stage_key = LoopType.REQUIREMENT.value

    def _input_artifacts(self, loop_round: LoopRound) -> tuple[str, ...]:
        inputs = loop_round.input_artifacts
        if "inline-idea" not in inputs:
            return tuple(inputs)
        if inputs != ["inline-idea"]:
            raise ValueError("inline requirement input must be the only sentinel")
        intake_paths = [
            path
            for path in loop_round.output_artifacts
            if path.endswith("/requirement-intake.json")
        ]
        if len(intake_paths) != 1:
            raise ValueError("inline requirement requires one persisted intake artifact")
        return (intake_paths[0],)


class DesignContractStageAdapter(_ReadOnlyStageAdapter):
    loop_type = LoopType.DESIGN_CONTRACT
    stage_key = LoopType.DESIGN_CONTRACT.value


class ImplementationStageAdapter(_ReadOnlyStageAdapter):
    loop_type = LoopType.IMPLEMENTATION
    stage_key = LoopType.IMPLEMENTATION.value


class FrontendEvidenceStageAdapter(_ReadOnlyStageAdapter):
    loop_type = LoopType.FRONTEND_EVIDENCE
    stage_key = LoopType.FRONTEND_EVIDENCE.value


class LocalPRReviewStageAdapter:
    loop_type = LoopType.LOCAL_PR_REVIEW
    stage_key = LoopType.LOCAL_PR_REVIEW.value

    def candidate_context(
        self,
        facts: LocalPRAdapterFacts,
        *,
        source_snapshot: SourceSnapshot,
    ) -> CandidateBuildContext:
        """从真实 ReviewRun/ReviewPack 构造最终整体 Diff Candidate。"""

        review_run = facts.review_run
        review_pack = facts.review_pack
        if review_run.loop_type != LoopType.LOCAL_PR_REVIEW:
            raise ValueError("local PR adapter requires local-pr-review loop type")
        if (
            review_run.review_id != review_pack.review_id
            or review_run.loop_id != review_pack.loop_id
        ):
            raise ValueError("local PR review identity does not match review pack")
        if not review_run.review_pack_path.strip():
            raise ValueError("local PR adapter requires a persisted review pack")
        _validate_local_pr_source_scope(review_run, review_pack, source_snapshot)
        _validate_local_pr_work_item(facts)
        return CandidateBuildContext(
            work_item_id=facts.work_item_id,
            project_id=facts.project_id,
            loop_id=review_run.loop_id,
            loop_round_number=1,
            stage_key=self.stage_key,
            stage_instance_id=review_run.review_id,
            review_session_id=facts.review_session_id,
            adapter_id=facts.adapter_id,
            adapter_version=facts.adapter_version,
            adapter_contract_digest=facts.adapter_contract_digest,
            input_artifacts=local_pr_inputs(review_run, review_pack),
            output_artifacts=tuple(
                path
                for path in source_snapshot.changed_files
                if path not in set(source_snapshot.deleted_files)
            ),
            test_evidence_digests=facts.test_evidence_digests,
            policy_digests=facts.policy_digests,
            toolchain_ids=facts.toolchain_ids,
            target_platform_ids=facts.target_platform_ids,
            protected_source_set=facts.protected_source_set,
            extensions=facts.extensions,
        )

    def build_candidate(
        self,
        *,
        root: Path,
        source_snapshot: SourceSnapshot,
        facts: LocalPRAdapterFacts,
    ) -> CandidateManifest:
        if Path(facts.review_pack.repo_root).resolve() != root.resolve():
            raise ValueError("local PR review pack belongs to another repository")
        require_persisted_review_pack(root, facts.review_run, facts.review_pack)
        require_bound_review_inputs(root, facts.review_pack)
        return build_candidate_manifest(
            root=root,
            source_snapshot=source_snapshot,
            context=self.candidate_context(
                facts,
                source_snapshot=source_snapshot,
            ),
        )


def _current_round(loop_run: LoopRun) -> LoopRound:
    if loop_run.current_round < 1:
        raise ValueError("stage adapter requires a current round")
    matches = [
        item
        for item in loop_run.rounds
        if item.round_number == loop_run.current_round
    ]
    if len(matches) != 1:
        raise ValueError("stage adapter current round must be unique")
    return matches[0]


def _validate_local_pr_source_scope(
    review_run: ReviewRun,
    review_pack: ReviewPack,
    snapshot: SourceSnapshot,
) -> None:
    _validate_local_pr_run_scope(review_run, review_pack)
    _validate_local_pr_descriptor(review_pack)
    _validate_local_pr_snapshot(review_pack, snapshot)


def _validate_local_pr_run_scope(
    review_run: ReviewRun,
    review_pack: ReviewPack,
) -> None:
    run_scope = (
        review_run.source_adapter,
        review_run.source_access_status,
        review_run.source_resolution_path,
        review_run.base_ref,
        review_run.head_ref,
        review_run.base_commit,
        review_run.head_commit,
        review_run.diff_source,
    )
    pack_scope = (
        review_pack.source_adapter,
        review_pack.source_access_status,
        review_pack.source_resolution_path,
        review_pack.base_ref,
        review_pack.head_ref,
        review_pack.base_commit,
        review_pack.head_commit,
        review_pack.diff_source,
    )
    if run_scope != pack_scope:
        raise ValueError("local PR review run and pack source scope do not match")


def _validate_local_pr_descriptor(review_pack: ReviewPack) -> None:
    descriptor = review_pack.diff_source
    if not descriptor.repo_root.strip():
        raise ValueError("local PR review descriptor requires repository identity")
    descriptor_scope = (
        descriptor.adapter_id,
        descriptor.access_status,
        Path(descriptor.repo_root).resolve(),
        descriptor.base_ref,
        descriptor.head_ref,
        descriptor.base_commit,
        descriptor.head_commit,
    )
    pack_scope = (
        review_pack.source_adapter,
        review_pack.source_access_status,
        Path(review_pack.repo_root).resolve(),
        review_pack.base_ref,
        review_pack.head_ref,
        review_pack.base_commit,
        review_pack.head_commit,
    )
    if descriptor_scope != pack_scope:
        raise ValueError("local PR review descriptor contradicts review pack source scope")
    if str(review_pack.source_access_status) != "resolved":
        raise ValueError("local PR review source must be resolved")


def _validate_local_pr_snapshot(
    review_pack: ReviewPack,
    snapshot: SourceSnapshot,
) -> None:
    descriptor = review_pack.diff_source
    source_kind = str(descriptor.source_kind)
    if source_kind != snapshot.source_kind:
        raise ValueError("local PR review pack and source snapshot source scope do not match")
    common_scope = (
        review_pack.base_ref,
        review_pack.head_ref,
        review_pack.head_commit,
    )
    if common_scope != (snapshot.base_ref, snapshot.head_ref, snapshot.head_commit):
        raise ValueError("local PR review pack and source snapshot source scope do not match")
    reviewed_files = review_pack.changed_files
    if source_kind == DiffSourceKind.PATCH.value:
        reviewed_files = [
            path for path in reviewed_files if not is_runtime_artifact_path(path)
        ]
    if sorted(reviewed_files) != sorted(snapshot.changed_files):
        raise ValueError("local PR review changed files do not match source snapshot")
    expected_diff_hash = snapshot.diff_hash.removeprefix("sha256:")
    if source_kind == DiffSourceKind.PATCH.value:
        expected_input_hash = snapshot.source_input_digest.removeprefix("sha256:")
        if not expected_input_hash:
            raise ValueError("local PR patch source input digest is unavailable")
        _validate_patch_snapshot(review_pack, snapshot, expected_input_hash)
        return
    if review_pack.base_commit != snapshot.base_commit:
        raise ValueError("local PR review pack and source snapshot source scope do not match")
    if descriptor.patch_hash and descriptor.patch_hash != expected_diff_hash:
        raise ValueError("local PR review diff hash does not match source snapshot")
    if source_kind in {
        DiffSourceKind.LOCAL_STAGED.value,
        DiffSourceKind.LOCAL_UNSTAGED.value,
    } and not descriptor.patch_hash:
        raise ValueError("local PR worktree review requires a diff hash")


def _validate_patch_snapshot(
    review_pack: ReviewPack,
    snapshot: SourceSnapshot,
    expected_diff_hash: str,
) -> None:
    descriptor = review_pack.diff_source
    root = Path(review_pack.repo_root).resolve()
    patch_path = (root / descriptor.patch_file).resolve()
    snapshot_path = (root / snapshot.patch_file).resolve()
    try:
        patch_path.relative_to(root)
        snapshot_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("local PR patch source must stay inside repository") from exc
    if patch_path != snapshot_path:
        raise ValueError("local PR patch descriptor does not match source snapshot")
    if descriptor.patch_hash != expected_diff_hash:
        raise ValueError("local PR review diff hash does not match source snapshot")
    if review_pack.base_commit != expected_diff_hash:
        raise ValueError("local PR patch identity does not match review pack")


def _validate_local_pr_work_item(facts: LocalPRAdapterFacts) -> None:
    work_item_id = facts.work_item_id.strip()
    if not work_item_id:
        raise ValueError("local PR adapter requires a work item identity")
    bound_ids = {
        value.strip()
        for value in (
            facts.review_run.lean_work_item_id,
            facts.review_pack.lean_work_item_id,
        )
        if value.strip()
    }
    if any(value != work_item_id for value in bound_ids):
        raise ValueError("local PR review work item does not match adapter context")
