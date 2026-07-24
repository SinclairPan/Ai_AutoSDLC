from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from ai_sdlc.core.loop_models import LoopRound, LoopRun, LoopStatus, LoopType
from ai_sdlc.core.pr_review_models import (
    DiffSourceDescriptor,
    DiffSourceKind,
    ReviewPack,
    ReviewRun,
    SourceAdapterResolution,
)
from ai_sdlc.core.pr_review_pack import (
    ReviewPackBuildOptions,
    ReviewPackBuildStatus,
    build_review_pack,
)
from ai_sdlc.core.source_snapshot import (
    SourceSnapshot,
    SourceSnapshotOptions,
    build_source_snapshot,
)
from ai_sdlc.core.stage_review.adapters import (
    DesignContractStageAdapter,
    FrontendEvidenceStageAdapter,
    ImplementationStageAdapter,
    LocalPRAdapterFacts,
    LocalPRReviewStageAdapter,
    RequirementStageAdapter,
    StageAdapterFacts,
)
from ai_sdlc.core.stage_review.candidate import (
    CandidateBuildContext,
    CandidateManifest,
)
from ai_sdlc.core.stage_review.stage_adapter_registry import (
    StageCandidateAdapterRegistry,
    default_stage_candidate_adapter_registry,
)

_ADAPTER_CASES = [
    (RequirementStageAdapter, LoopType.REQUIREMENT),
    (DesignContractStageAdapter, LoopType.DESIGN_CONTRACT),
    (ImplementationStageAdapter, LoopType.IMPLEMENTATION),
    (FrontendEvidenceStageAdapter, LoopType.FRONTEND_EVIDENCE),
]


def test_default_stage_candidate_registry_covers_all_five_routes() -> None:
    registry = default_stage_candidate_adapter_registry()
    adapter_types = [item[0] for item in _ADAPTER_CASES] + [LocalPRReviewStageAdapter]

    registrations = [registry.resolve_instance(item()) for item in adapter_types]

    assert {item.contract.loop_type for item in registrations} == set(LoopType)
    assert len({item.contract.contract_digest for item in registrations}) == 5
    assert registry.registry_digest.startswith("sha256:")


def test_stage_candidate_registry_refuses_incomplete_coverage() -> None:
    registry = StageCandidateAdapterRegistry()
    registry.register(
        adapter_type=RequirementStageAdapter,
        adapter_id="stage-candidate.requirement",
        adapter_version="1.0.0",
        input_kind="loop-run",
    )

    with pytest.raises(ValueError, match="registry coverage is incomplete"):
        registry.freeze(tuple(LoopType))


@pytest.mark.parametrize(("adapter_type", "loop_type"), _ADAPTER_CASES)
def test_loop_stage_adapters_map_existing_loop_round_without_mutation(
    adapter_type: type,
    loop_type: LoopType,
) -> None:
    loop_run = _loop_run(loop_type)
    before = loop_run.model_dump(mode="json")

    context = adapter_type().candidate_context(_facts(loop_run))

    assert isinstance(context, CandidateBuildContext)
    assert context.loop_id == loop_run.loop_id
    assert context.loop_round_number == 1
    assert context.stage_key == loop_type.value
    assert context.stage_instance_id == loop_run.loop_id
    assert context.input_artifacts == ("specs/001/spec.md",)
    assert context.output_artifacts == ("src/example.py", "tests/test_example.py")
    assert loop_run.model_dump(mode="json") == before
    assert loop_run.current_round == 1


@pytest.mark.parametrize(("adapter_type", "loop_type"), _ADAPTER_CASES)
def test_loop_stage_adapters_build_one_unified_candidate(
    tmp_path: Path,
    adapter_type: type,
    loop_type: LoopType,
) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "specs/001/spec.md", "requirement\n")
    _write(tmp_path, "src/example.py", "VALUE = 1\n")
    _write(tmp_path, "tests/test_example.py", "def test_ok(): pass\n")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )
    adapter = adapter_type()
    facts = _facts(_loop_run(loop_type))

    candidate = adapter.build_candidate(
        root=tmp_path,
        source_snapshot=snapshot,
        facts=facts,
    )

    assert isinstance(candidate, CandidateManifest)
    assert candidate.stage_key == loop_type.value
    assert candidate.loop_round_number == 1
    assert candidate.output_artifacts == ["src/example.py", "tests/test_example.py"]


def test_requirement_adapter_maps_inline_idea_to_persisted_intake(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "specs/001/spec.md", "requirement\n")
    intake_path = (
        ".ai-sdlc/loops/requirement/requirement-001/requirement-intake.json"
    )
    _write(tmp_path, intake_path, '{"raw_text":"inline requirement"}\n')
    loop_run = _loop_run(LoopType.REQUIREMENT)
    loop_run.rounds[0].input_artifacts = ["inline-idea"]
    loop_run.rounds[0].output_artifacts = [intake_path, "specs/001/spec.md"]
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )

    candidate = RequirementStageAdapter().build_candidate(
        root=tmp_path,
        source_snapshot=snapshot,
        facts=_facts(loop_run),
    )

    assert candidate.input_artifacts == [intake_path]
    assert "inline-idea" not in candidate.input_digests


def test_local_pr_adapter_reads_real_review_run_and_review_pack(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "specs/001/spec.md", "requirement\n")
    _write(tmp_path, "src/example.py", "VALUE = 1\n")
    _write(tmp_path, "tests/test_example.py", "def test_ok(): pass\n")
    review_pack_path = ".ai-sdlc/reviews/review-001/review-pack.json"
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )
    review_pack = _review_pack(tmp_path, snapshot)
    _write(tmp_path, review_pack_path, review_pack.model_dump_json())
    review_run = _review_run_for_pack(tmp_path, review_pack, review_pack_path)
    facts = _local_pr_facts(review_run, review_pack)

    context = LocalPRReviewStageAdapter().candidate_context(
        facts,
        source_snapshot=snapshot,
    )
    candidate = LocalPRReviewStageAdapter().build_candidate(
        root=tmp_path,
        source_snapshot=snapshot,
        facts=facts,
    )

    assert context.loop_id == review_run.loop_id
    assert context.loop_round_number == 1
    assert context.stage_instance_id == review_run.review_id
    assert context.input_artifacts == (
        review_pack.diff_path,
        review_pack_path,
        review_pack.source_resolution_path,
        "specs/001/spec.md",
        "tests/test_example.py",
    )
    assert context.output_artifacts == (
        "specs/001/spec.md",
        "src/example.py",
        "tests/test_example.py",
    )
    assert candidate.stage_key == LoopType.LOCAL_PR_REVIEW.value


def test_local_pr_adapter_accepts_default_real_git_range_pack(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    base_commit = _git(tmp_path, "rev-parse", "HEAD")
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    _git(tmp_path, "add", "src/app.py")
    _git(tmp_path, "commit", "-m", "add app")
    result = build_review_pack(
        ReviewPackBuildOptions(
            root=tmp_path,
            base_ref=base_commit,
            review_id="review-real-range",
            loop_id="local-pr-review-real-range",
            current_model="gpt-5",
        )
    )
    assert result.status == ReviewPackBuildStatus.READY
    assert result.review_pack is not None
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(
            root=tmp_path,
            source_kind="local-git-range",
            base_ref=base_commit,
        )
    )
    pack_path = Path(result.review_pack_path).relative_to(tmp_path).as_posix()
    run = _review_run_for_pack(tmp_path, result.review_pack, pack_path)

    candidate = LocalPRReviewStageAdapter().build_candidate(
        root=tmp_path,
        source_snapshot=snapshot,
        facts=_local_pr_facts(run, result.review_pack),
    )

    assert result.review_pack.diff_source.patch_hash == ""
    assert candidate.output_artifacts == ["src/app.py"]


def test_local_pr_adapter_accepts_real_patch_pack(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    _git(tmp_path, "add", "src/app.py")
    _git(tmp_path, "commit", "-m", "add app")
    _write(tmp_path, "src/app.py", "VALUE = 2\n")
    patch_path = tmp_path / "change.patch"
    patch_path.write_bytes(_git_bytes(tmp_path, "diff", "--binary"))
    _git(tmp_path, "restore", "src/app.py")
    result = build_review_pack(
        ReviewPackBuildOptions(
            root=tmp_path,
            base_ref="",
            diff_source="patch",
            patch_file="change.patch",
            review_id="review-real-patch",
            loop_id="local-pr-review-real-patch",
            current_model="gpt-5",
        )
    )
    assert result.status == ReviewPackBuildStatus.READY
    assert result.review_pack is not None
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(
            root=tmp_path,
            source_kind="patch",
            patch_file="change.patch",
        )
    )
    pack_path = Path(result.review_pack_path).relative_to(tmp_path).as_posix()
    run = _review_run_for_pack(tmp_path, result.review_pack, pack_path)

    candidate = LocalPRReviewStageAdapter().build_candidate(
        root=tmp_path,
        source_snapshot=snapshot,
        facts=_local_pr_facts(run, result.review_pack),
    )

    assert result.review_pack.base_commit != snapshot.base_commit
    assert candidate.output_artifacts == ["src/app.py"]


def test_local_pr_adapter_keeps_raw_patch_identity_for_filtered_runtime_view(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    _write(tmp_path, ".ai-sdlc/work-items/WI-1/runtime.py", "VALUE = 1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "add app and runtime")
    _write(tmp_path, "src/app.py", "VALUE = 2\n")
    _write(tmp_path, ".ai-sdlc/work-items/WI-1/runtime.py", "VALUE = 2\n")
    patch_path = tmp_path / "change.patch"
    patch_path.write_bytes(_git_bytes(tmp_path, "diff", "--binary"))
    _git(tmp_path, "restore", "src/app.py", ".ai-sdlc/work-items/WI-1/runtime.py")
    result = build_review_pack(
        ReviewPackBuildOptions(
            root=tmp_path,
            base_ref="",
            diff_source="patch",
            patch_file="change.patch",
            review_id="review-filtered-patch",
            loop_id="local-pr-review-filtered-patch",
            current_model="gpt-5",
        )
    )
    assert result.status == ReviewPackBuildStatus.READY
    assert result.review_pack is not None
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(
            root=tmp_path,
            source_kind="patch",
            patch_file="change.patch",
        )
    )
    pack_path = Path(result.review_pack_path).relative_to(tmp_path).as_posix()
    run = _review_run_for_pack(tmp_path, result.review_pack, pack_path)

    candidate = LocalPRReviewStageAdapter().build_candidate(
        root=tmp_path,
        source_snapshot=snapshot,
        facts=_local_pr_facts(run, result.review_pack),
    )

    assert snapshot.source_input_digest != snapshot.diff_hash
    assert result.review_pack.diff_source.patch_hash == (
        snapshot.source_input_digest.removeprefix("sha256:")
    )
    assert candidate.output_artifacts == ["src/app.py"]


def test_local_pr_adapter_rejects_modified_diff_artifact(tmp_path: Path) -> None:
    snapshot, pack, run = _real_git_range_review(tmp_path, "tampered-diff")
    (tmp_path / pack.diff_path).write_text("tampered\n", encoding="utf-8")

    with pytest.raises(ValueError, match="diff artifact digest"):
        LocalPRReviewStageAdapter().build_candidate(
            root=tmp_path,
            source_snapshot=snapshot,
            facts=_local_pr_facts(run, pack),
        )


def test_local_pr_adapter_requires_bound_source_resolution(tmp_path: Path) -> None:
    snapshot, pack, run = _real_git_range_review(tmp_path, "missing-resolution")
    (tmp_path / pack.source_resolution_path).unlink()

    with pytest.raises(ValueError, match="source resolution artifact"):
        LocalPRReviewStageAdapter().build_candidate(
            root=tmp_path,
            source_snapshot=snapshot,
            facts=_local_pr_facts(run, pack),
        )


def test_local_pr_adapter_rejects_mismatched_real_artifacts(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )
    pack = _review_pack(tmp_path, snapshot)
    run = ReviewRun(
        review_id="other-review",
        loop_id=pack.loop_id,
        review_pack_path=".ai-sdlc/reviews/review-001/review-pack.json",
    )

    with pytest.raises(ValueError, match="review identity"):
        LocalPRReviewStageAdapter().candidate_context(
            _local_pr_facts(run, pack),
            source_snapshot=snapshot,
        )


def test_local_pr_adapter_binds_persisted_pack_snapshot_and_work_item(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "src/a.py", "A = 1\n")
    _write(tmp_path, "src/b.py", "B = 1\n")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )
    pack_path = ".ai-sdlc/reviews/review-001/review-pack.json"
    persisted = _review_pack(tmp_path, snapshot).model_copy(
        update={"lean_work_item_id": "001-example"}
    )
    _write(tmp_path, pack_path, persisted.model_dump_json())
    run = _review_run_for_pack(tmp_path, persisted, pack_path).model_copy(
        update={"lean_work_item_id": "001-example"}
    )
    altered = persisted.model_copy(update={"changed_files": ["src/a.py"]})

    with pytest.raises(ValueError, match="persisted review pack"):
        LocalPRReviewStageAdapter().build_candidate(
            root=tmp_path,
            source_snapshot=snapshot,
            facts=_local_pr_facts(run, altered),
        )
    with pytest.raises(ValueError, match="source scope"):
        LocalPRReviewStageAdapter().build_candidate(
            root=tmp_path,
            source_snapshot=snapshot.model_copy(update={"head_commit": "other"}),
            facts=_local_pr_facts(run, persisted),
        )
    with pytest.raises(ValueError, match="work item"):
        LocalPRReviewStageAdapter().build_candidate(
            root=tmp_path,
            source_snapshot=snapshot,
            facts=_local_pr_facts(run, persisted, work_item_id="other-work"),
        )


def test_local_pr_adapter_rejects_descriptor_that_contradicts_pack_scope(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "src/a.py", "A = 1\n")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )
    pack = _review_pack(tmp_path, snapshot)
    foreign_descriptor = pack.diff_source.model_copy(
        update={
            "source_kind": DiffSourceKind.PATCH,
            "adapter_id": "patch",
            "repo_root": str((tmp_path / "foreign").resolve()),
            "base_ref": "foreign-base",
            "head_ref": "foreign-head",
            "base_commit": "foreign-base-commit",
            "head_commit": "foreign-head-commit",
        }
    )
    altered = pack.model_copy(update={"diff_source": foreign_descriptor})
    run = ReviewRun(
        review_id=altered.review_id,
        loop_id=altered.loop_id,
        review_pack_path=".ai-sdlc/reviews/review-001/review-pack.json",
        source_adapter=altered.source_adapter,
        source_access_status=altered.source_access_status,
        source_resolution_path=altered.source_resolution_path,
        diff_source=altered.diff_source,
        base_ref=altered.base_ref,
        head_ref=altered.head_ref,
        base_commit=altered.base_commit,
        head_commit=altered.head_commit,
    )

    with pytest.raises(ValueError, match="descriptor"):
        LocalPRReviewStageAdapter().candidate_context(
            _local_pr_facts(run, altered),
            source_snapshot=snapshot,
        )


def test_local_pr_adapter_keeps_deleted_files_only_in_change_surface(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "src/deleted.py", "VALUE = 1\n")
    _git(tmp_path, "add", "src/deleted.py")
    _git(tmp_path, "commit", "-m", "add deleted source")
    (tmp_path / "src/deleted.py").unlink()
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )
    pack_path = ".ai-sdlc/reviews/review-001/review-pack.json"
    pack = _review_pack(tmp_path, snapshot)
    _write(tmp_path, pack_path, pack.model_dump_json())
    run = _review_run_for_pack(tmp_path, pack, pack_path)

    candidate = LocalPRReviewStageAdapter().build_candidate(
        root=tmp_path,
        source_snapshot=snapshot,
        facts=_local_pr_facts(run, pack),
    )

    assert candidate.output_artifacts == []
    assert candidate.change_surface == ["src/deleted.py"]


def test_stage_adapter_rejects_wrong_loop_type_and_missing_current_round() -> None:
    with pytest.raises(ValueError, match="loop type"):
        RequirementStageAdapter().candidate_context(
            _facts(_loop_run(LoopType.IMPLEMENTATION))
        )

    no_round = LoopRun(
        loop_id="requirement-001",
        loop_type=LoopType.REQUIREMENT,
        work_item_id="001-example",
    )
    with pytest.raises(ValueError, match="current round"):
        RequirementStageAdapter().candidate_context(_facts(no_round))


def test_local_pr_adapter_does_not_expose_nested_panel_or_close_logic() -> None:
    public_names = {
        name
        for name in dir(LocalPRReviewStageAdapter)
        if not name.startswith("_")
    }

    assert public_names == {"build_candidate", "candidate_context", "loop_type", "stage_key"}
    assert {item.value for item in LoopType} == {
        "requirement",
        "design-contract",
        "implementation",
        "frontend-evidence",
        "local-pr-review",
    }


def _loop_run(loop_type: LoopType) -> LoopRun:
    loop_id = f"{loop_type.value}-001"
    return LoopRun(
        loop_id=loop_id,
        loop_type=loop_type,
        status=LoopStatus.PASSED,
        work_item_id="001-example",
        current_round=1,
        rounds=[
            LoopRound(
                round_number=1,
                status=LoopStatus.PASSED,
                input_artifacts=["specs/001/spec.md"],
                output_artifacts=["src/example.py", "tests/test_example.py"],
            )
        ],
    )


def _facts(loop_run: LoopRun) -> StageAdapterFacts:
    return StageAdapterFacts(
        loop_run=loop_run,
        project_id="project-001",
        review_session_id="session-001",
        adapter_id=f"stage-candidate.{loop_run.loop_type}",
        adapter_version="1.0.0",
        adapter_contract_digest=f"sha256:adapter:{loop_run.loop_type}",
        test_evidence_digests=("sha256:test",),
        policy_digests=("sha256:policy",),
        toolchain_ids=("python:3.11", "pytest"),
        target_platform_ids=("linux", "windows"),
        protected_source_set=("specs", "src", "tests"),
    )


def _local_pr_facts(
    review_run: ReviewRun,
    review_pack: ReviewPack,
    *,
    work_item_id: str = "001-example",
) -> LocalPRAdapterFacts:
    return LocalPRAdapterFacts(
        review_run=review_run,
        review_pack=review_pack,
        work_item_id=work_item_id,
        project_id="project-001",
        review_session_id="session-001",
        adapter_id="stage-candidate.local-pr-review",
        adapter_version="1.0.0",
        adapter_contract_digest="sha256:adapter:local-pr-review",
        test_evidence_digests=("sha256:test",),
        policy_digests=("sha256:policy",),
        toolchain_ids=("git", "pytest"),
        target_platform_ids=("linux", "windows"),
        protected_source_set=("specs", "src", "tests"),
    )


def _review_pack(root: Path, snapshot: SourceSnapshot) -> ReviewPack:
    descriptor = DiffSourceDescriptor(
        source_kind=DiffSourceKind(snapshot.source_kind),
        adapter_id=snapshot.source_kind,
        source_id=snapshot.source_kind,
        repo_root=str(root.resolve()),
        base_ref=snapshot.base_ref,
        head_ref=snapshot.head_ref,
        base_commit=snapshot.base_commit,
        head_commit=snapshot.head_commit,
        patch_hash=snapshot.diff_hash.removeprefix("sha256:"),
    )
    resolution_path = ".ai-sdlc/reviews/review-001/source-resolution.json"
    diff_path = ".ai-sdlc/reviews/review-001/diff.patch"
    resolution = SourceAdapterResolution.model_validate(
        {
            **descriptor.model_dump(mode="json"),
            "artifact_kind": "source-resolution",
        }
    )
    _write(root, resolution_path, resolution.model_dump_json())
    _write(root, diff_path, "diff\n")
    return ReviewPack(
        review_id="review-001",
        loop_id="local-pr-review-001",
        repo_root=str(root.resolve()),
        source_adapter=snapshot.source_kind,
        source_resolution_path=resolution_path,
        source_resolution_digest=_artifact_digest(root / resolution_path),
        diff_source=descriptor,
        base_ref=snapshot.base_ref,
        head_ref=snapshot.head_ref,
        base_commit=snapshot.base_commit,
        head_commit=snapshot.head_commit,
        changed_files=snapshot.changed_files,
        diff_path=diff_path,
        diff_digest=_artifact_digest(root / diff_path),
        work_item_refs=[
            path for path in snapshot.changed_files if path.startswith("specs/")
        ],
        test_results_refs=[
            path for path in snapshot.changed_files if path.startswith("tests/")
        ],
    )


def _review_run_for_pack(root: Path, pack: ReviewPack, pack_path: str) -> ReviewRun:
    return ReviewRun(
        review_id=pack.review_id,
        loop_id=pack.loop_id,
        review_pack_path=pack_path,
        review_pack_digest=_sha256(root / pack_path),
        source_adapter=pack.source_adapter,
        source_access_status=pack.source_access_status,
        source_resolution_path=pack.source_resolution_path,
        diff_source=pack.diff_source,
        base_ref=pack.base_ref,
        head_ref=pack.head_ref,
        base_commit=pack.base_commit,
        head_commit=pack.head_commit,
    )


def _real_git_range_review(
    root: Path,
    suffix: str,
) -> tuple[SourceSnapshot, ReviewPack, ReviewRun]:
    _init_git_repo(root)
    base_commit = _git(root, "rev-parse", "HEAD")
    _write(root, "src/app.py", "VALUE = 1\n")
    _git(root, "add", "src/app.py")
    _git(root, "commit", "-m", "add app")
    result = build_review_pack(
        ReviewPackBuildOptions(
            root=root,
            base_ref=base_commit,
            review_id=f"review-{suffix}",
            loop_id=f"local-pr-review-{suffix}",
            current_model="gpt-5",
        )
    )
    if result.status != ReviewPackBuildStatus.READY or result.review_pack is None:
        raise AssertionError(result.blocker)
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(
            root=root,
            source_kind="local-git-range",
            base_ref=base_commit,
        )
    )
    pack_path = Path(result.review_pack_path).relative_to(root).as_posix()
    return (
        snapshot,
        result.review_pack,
        _review_run_for_pack(root, result.review_pack, pack_path),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_digest(path: Path) -> str:
    return f"sha256:{_sha256(path)}"


def _init_git_repo(root: Path) -> None:
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    _write(root, "README.md", "# Test\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initial")


def _write(root: Path, relative: str, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", errors="replace"))
    return result.stdout.decode("utf-8", errors="strict").strip()


def _git_bytes(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", errors="replace"))
    return result.stdout
