from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_sdlc.core.source_content_identity import (
    CANONICAL_DIGEST_KIND,
    CHANGE_IDENTITY_KIND,
)
from ai_sdlc.core.source_snapshot import (
    SourceSnapshot,
    SourceSnapshotOptions,
    build_source_snapshot,
)
from ai_sdlc.core.stage_review.candidate import (
    CandidateBuildContext,
    CandidateManifest,
    _build_candidate_manifest,
    _read_candidate_manifest,
    build_candidate_manifest,
    candidate_binding_digest,
    read_candidate_manifest,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
    canonical_payload,
    normalize_repo_path,
)
from ai_sdlc.core.stage_review.contracts import (
    RiskFact,
    SemanticRiskSuggestion,
    TaskRiskProfile,
    _risk_profile_digest,
    read_task_risk_profile,
    reconcile_risk_profile,
)
from ai_sdlc.core.stage_review.legacy import _legacy_digest

_REVIEW_ROOT = (
    ".ai-sdlc/state/stage-review/project-001/sessions/001-example/"
    "implementation-001/session-001"
)
_REVIEW_PATH = f"{_REVIEW_ROOT}/review-pass.json"


def _digest(seed: str) -> str:
    return f"sha256:{seed * 64}"


def _source_snapshot(
    *,
    source_digest: str = _digest("a"),
    review_digest: str = _digest("b"),
    source_kind: str = "local-unstaged",
    base_commit: str = "base-commit",
    head_commit: str = "head-commit",
) -> SourceSnapshot:
    paths = [
        "specs/001/spec.md",
        "src/example.py",
        "tests/test_example.py",
        _REVIEW_PATH,
    ]
    identities = {
        "specs/001/spec.md": _digest("e"),
        "src/example.py": source_digest,
        "tests/test_example.py": _digest("c"),
        _REVIEW_PATH: review_digest,
    }
    return SourceSnapshot(
        source_kind=source_kind,
        base_commit=base_commit,
        head_commit=head_commit,
        diff_hash=_digest("d"),
        changed_files=paths,
        file_digests=identities,
        canonical_digest_kind=CANONICAL_DIGEST_KIND,
        canonical_file_digests=identities,
        change_identity_kind=CHANGE_IDENTITY_KIND,
        raw_change_identities=identities,
        portable_change_identities=identities,
    )


def _build_from_snapshot(
    snapshot: SourceSnapshot,
    *,
    project_id: str = "project-001",
    review_session_id: str = "session-001",
    protected_source_set: list[str] | None = None,
    policy_digests: list[str] | None = None,
) -> CandidateManifest:
    return _build_candidate_manifest(
        Path("."),
        snapshot,
        _candidate_context(
            project_id=project_id,
            review_session_id=review_session_id,
            protected_source_set=protected_source_set,
            policy_digests=policy_digests,
        ),
    )


def _candidate_context(
    *,
    project_id: str = "project-001",
    review_session_id: str = "session-001",
    protected_source_set: list[str] | None = None,
    policy_digests: list[str] | None = None,
) -> CandidateBuildContext:
    return CandidateBuildContext(
        work_item_id="001-example",
        project_id=project_id,
        loop_id="implementation-001",
        loop_round_number=1,
        stage_key="implementation",
        stage_instance_id="implementation-001",
        review_session_id=review_session_id,
        adapter_id="stage-candidate.implementation",
        adapter_version="1.0.0",
        adapter_contract_digest="sha256:adapter:implementation",
        input_artifacts=("specs/001/spec.md",),
        output_artifacts=("src/example.py", "tests/test_example.py"),
        test_evidence_digests=(_digest("f"),),
        policy_digests=tuple(policy_digests or [_digest("1")]),
        toolchain_ids=("python:3.11", "pytest"),
        target_platform_ids=("linux", "windows"),
        protected_source_set=tuple(
            protected_source_set or ["specs", "src", "tests"]
        ),
    )


def _built_candidate() -> CandidateManifest:
    return _build_from_snapshot(_source_snapshot())


def _candidate(**updates: object) -> CandidateManifest:
    payload: dict[str, object] = _built_candidate().model_dump(mode="json")
    payload.update(updates)
    return CandidateManifest.model_validate(payload)


def test_canonical_digest_ignores_non_semantic_fields_and_set_order() -> None:
    policy = CanonicalizationPolicy(
        excluded_fields=frozenset({"request_id", "created_at"}),
        set_like_fields=frozenset({"capability_ids", "paths"}),
        path_fields=frozenset({"paths"}),
    )
    left = {
        "request_id": "first",
        "created_at": "2026-07-20T00:00:00Z",
        "capability_ids": ["security", "correctness", "security"],
        "paths": [r"src\b.py", "./src/a.py"],
        "ordered_steps": ["compile", "test"],
    }
    right = {
        "ordered_steps": ["compile", "test"],
        "paths": ["src/a.py", "src/b.py"],
        "capability_ids": ["correctness", "security"],
        "request_id": "second",
        "created_at": "2027-01-01T00:00:00Z",
    }

    assert canonical_digest(left, policy) == canonical_digest(right, policy)
    assert canonical_digest(
        {**right, "ordered_steps": ["test", "compile"]}, policy
    ) != canonical_digest(right, policy)


def test_canonical_policy_does_not_reinterpret_nested_extension_keys() -> None:
    policy = CanonicalizationPolicy(
        excluded_fields=frozenset({"created_at"}),
        set_like_fields=frozenset({"input_artifacts"}),
        path_fields=frozenset({"input_artifacts"}),
    )
    payload = canonical_payload(
        {
            "created_at": "top-level-excluded",
            "input_artifacts": [r"src\b.py", "src/a.py"],
            "extensions": {
                "created_at": "extension-preserved",
                "input_artifacts": ["ordered/z", "ordered/z", r"ordered\a"],
            },
        },
        policy,
    )

    assert payload == {
        "extensions": {
            "created_at": "extension-preserved",
            "input_artifacts": ["ordered/z", "ordered/z", r"ordered\a"],
        },
        "input_artifacts": ["src/a.py", "src/b.py"],
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"src\feature\file.py", "src/feature/file.py"),
        ("./src/./feature/file.py", "src/feature/file.py"),
        ("SRC/File.py", "src/file.py"),
    ],
)
def test_repo_path_normalization_is_explicit(raw: str, expected: str) -> None:
    assert normalize_repo_path(raw, case_policy="lower") == expected


@pytest.mark.parametrize("raw", ["../secret", "/tmp/file", "C:/Users/name/file"])
def test_repo_path_normalization_rejects_paths_outside_repo(raw: str) -> None:
    with pytest.raises(ValueError, match="repository-relative"):
        normalize_repo_path(raw)


def test_candidate_digest_binds_protected_semantics_only() -> None:
    baseline = _candidate()

    assert candidate_binding_digest(
        baseline.model_copy(update={"created_at": "2030-01-01T00:00:00Z"})
    ) == candidate_binding_digest(baseline)
    assert candidate_binding_digest(
        baseline.model_copy(update={"source_tree_digest": "sha256:changed"})
    ) != candidate_binding_digest(baseline)
    assert candidate_binding_digest(
        baseline.model_copy(
            update={
                "review_artifact_exclusion_set": [
                    _REVIEW_ROOT,
                    f"{_REVIEW_ROOT}/immutable-evidence",
                ]
            }
        )
    ) != candidate_binding_digest(baseline)


def test_candidate_normalizes_digest_paths_before_binding() -> None:
    posix = _candidate()
    windows_style = _candidate(
        input_artifacts=[r"specs\001\spec.md"],
        input_digests={r"specs\001\spec.md": _digest("e")},
        output_artifacts=[r"src\example.py", r"tests\test_example.py"],
        output_digests={
            r"src\example.py": _digest("a"),
            r"tests\test_example.py": _digest("c"),
        },
        change_surface=[
            r"specs\001\spec.md",
            r"src\example.py",
            r"tests\test_example.py",
        ],
    )

    assert windows_style.input_digests == posix.input_digests
    assert windows_style.output_digests == posix.output_digests
    assert candidate_binding_digest(windows_style) == candidate_binding_digest(posix)


def test_candidate_rejects_review_artifacts_in_protected_outputs() -> None:
    with pytest.raises(ValidationError, match="review artifact"):
        _candidate(
            output_artifacts=[
                "src/example.py",
                _REVIEW_PATH,
            ],
            output_digests={
                "src/example.py": _digest("a"),
                _REVIEW_PATH: _digest("b"),
            },
            change_surface=["src/example.py"],
        )


def test_candidate_rejects_empty_artifact_digest() -> None:
    with pytest.raises(ValidationError, match="artifact digest must not be empty"):
        _candidate(input_digests={"specs/001/spec.md": ""})


def test_candidate_factory_reuses_snapshot_and_excludes_only_review_artifacts() -> None:
    baseline = _built_candidate()
    review_only_change = _build_from_snapshot(
        _source_snapshot(review_digest=_digest("9"))
    )
    protected_change = _build_from_snapshot(
        _source_snapshot(source_digest=_digest("8"))
    )

    assert baseline.change_surface == [
        "specs/001/spec.md",
        "src/example.py",
        "tests/test_example.py",
    ]
    assert baseline.source_tree_digest == review_only_change.source_tree_digest
    assert baseline.change_surface_digest == review_only_change.change_surface_digest
    assert baseline.source_tree_digest != protected_change.source_tree_digest
    assert baseline.change_surface_digest != protected_change.change_surface_digest


def test_candidate_source_tree_digest_binds_policy_content() -> None:
    baseline = _built_candidate()
    policy_change = _build_from_snapshot(
        _source_snapshot(), policy_digests=[_digest("2")]
    )

    assert baseline.source_tree_digest != policy_change.source_tree_digest


def test_git_range_review_only_commit_keeps_candidate_binding() -> None:
    baseline = _build_from_snapshot(
        _source_snapshot(source_kind="local-git-range", head_commit="head-one")
    )
    review_only_commit = _build_from_snapshot(
        _source_snapshot(
            source_kind="local-git-range",
            head_commit="head-two",
            review_digest=_digest("9"),
        )
    )

    assert baseline.source_tree_digest == review_only_commit.source_tree_digest
    assert candidate_binding_digest(baseline) == candidate_binding_digest(
        review_only_commit
    )


def test_candidate_factory_rejects_broad_exclusion_and_unprotected_change() -> None:
    with pytest.raises(ValidationError, match="current review identity"):
        _candidate(
            review_artifact_exclusion_set=[
                ".ai-sdlc/state/stage-review/other-project/sessions/"
                "other-work/other-stage/other-session"
            ]
        )

    snapshot = _source_snapshot().model_copy(
        update={
            "changed_files": ["outside.txt"],
            "file_digests": {"outside.txt": _digest("7")},
            "canonical_file_digests": {"outside.txt": _digest("7")},
            "raw_change_identities": {"outside.txt": _digest("7")},
            "portable_change_identities": {"outside.txt": _digest("7")},
        }
    )
    with pytest.raises(ValueError, match="outside protected_source_set"):
        _build_from_snapshot(snapshot)


def test_session_identity_segments_are_collision_safe() -> None:
    context = _candidate_context()
    for invalid in (
        ".",
        "..",
        "bad/value",
        "bad\nvalue",
        "Project",
        "alpha.",
        "con",
        "nul.txt",
        "com1",
    ):
        with pytest.raises(ValueError, match="safe path segment"):
            _build_candidate_manifest(
                Path("."),
                _source_snapshot(),
                replace(context, work_item_id=invalid),
            )


def test_candidate_change_surface_covers_rename_old_path() -> None:
    snapshot = _source_snapshot().model_copy(
        update={
            "changed_files": ["src/example.py"],
            "renamed_files": {"src/example.py": "outside/old.py"},
            "file_digests": {"src/example.py": _digest("a")},
            "canonical_file_digests": {"src/example.py": _digest("a")},
            "raw_change_identities": {
                "src/example.py": _digest("a"),
                "outside/old.py": _digest("7"),
            },
            "portable_change_identities": {
                "src/example.py": _digest("a"),
                "outside/old.py": _digest("7"),
            },
        }
    )
    context = replace(
        _candidate_context(),
        input_artifacts=("src/example.py",),
        output_artifacts=("src/example.py",),
    )

    with pytest.raises(ValueError, match="outside protected_source_set"):
        _build_candidate_manifest(Path("."), snapshot, context)


def test_rename_into_review_root_preserves_protected_old_path() -> None:
    snapshot = _source_snapshot().model_copy(
        update={
            "changed_files": [_REVIEW_PATH],
            "renamed_files": {_REVIEW_PATH: "src/auth.py"},
            "file_digests": {_REVIEW_PATH: _digest("b")},
            "canonical_file_digests": {_REVIEW_PATH: _digest("b")},
            "raw_change_identities": {
                _REVIEW_PATH: _digest("b"),
                "src/auth.py": _digest("7"),
            },
            "portable_change_identities": {
                _REVIEW_PATH: _digest("b"),
                "src/auth.py": _digest("7"),
            },
        }
    )
    context = replace(
        _candidate_context(), input_artifacts=(), output_artifacts=()
    )

    candidate = _build_candidate_manifest(Path("."), snapshot, context)

    assert candidate.change_surface == ["src/auth.py"]


def test_public_candidate_boundaries_reject_stale_snapshot_and_wrong_context() -> None:
    snapshot = _source_snapshot()
    with pytest.raises(ValueError, match="source snapshot"):
        build_candidate_manifest(
            root=Path("."),
            source_snapshot=snapshot,
            context=_candidate_context(),
        )
    with pytest.raises(ValueError, match="source snapshot"):
        read_candidate_manifest(
            _built_candidate().model_dump(mode="json"),
            root=Path("."),
            source_snapshot=snapshot,
            context=_candidate_context(),
        )

    candidate = _built_candidate()
    with pytest.raises(ValueError, match="source snapshot binding"):
        _read_candidate_manifest(
            candidate.model_dump(mode="json"),
            root=Path("."),
            source_snapshot=snapshot,
            context=replace(_candidate_context(), loop_id="other-loop"),
        )


def test_public_candidate_boundaries_accept_fresh_and_review_only_changes(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _write_repo_file(tmp_path, "specs/001/spec.md", "requirement\n")
    _write_repo_file(tmp_path, "src/example.py", "VALUE = 1\n")
    _write_repo_file(tmp_path, "tests/test_example.py", "def test_ok(): pass\n")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )
    context = _candidate_context()

    candidate = build_candidate_manifest(
        root=tmp_path,
        source_snapshot=snapshot,
        context=context,
    )
    assert (
        read_candidate_manifest(
            candidate.model_dump(mode="json"),
            root=tmp_path,
            source_snapshot=snapshot,
            context=context,
        )
        == candidate
    )

    _write_repo_file(tmp_path, _REVIEW_PATH, "review-only\n")
    assert (
        read_candidate_manifest(
            candidate.model_dump(mode="json"),
            root=tmp_path,
            source_snapshot=snapshot,
            context=context,
        )
        == candidate
    )

    _write_repo_file(tmp_path, "src/example.py", "VALUE = 2\n")
    with pytest.raises(ValueError, match="source snapshot is stale"):
        read_candidate_manifest(
            candidate.model_dump(mode="json"),
            root=tmp_path,
            source_snapshot=snapshot,
            context=context,
        )


def test_public_git_range_candidate_accepts_review_only_head_commit(
    tmp_path: Path,
) -> None:
    base = _init_git_repo(tmp_path)
    _write_repo_file(tmp_path, "specs/001/spec.md", "requirement\n")
    _write_repo_file(tmp_path, "src/example.py", "VALUE = 1\n")
    _write_repo_file(tmp_path, "tests/test_example.py", "def test_ok(): pass\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "protected change")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(
            root=tmp_path,
            source_kind="local-git-range",
            base_ref=base,
            head_ref="HEAD",
        )
    )
    context = _candidate_context()
    candidate = build_candidate_manifest(
        root=tmp_path,
        source_snapshot=snapshot,
        context=context,
    )

    _write_repo_file(tmp_path, _REVIEW_PATH, "review-only\n")
    _git(tmp_path, "add", _REVIEW_PATH)
    _git(tmp_path, "commit", "-m", "review evidence")

    assert (
        read_candidate_manifest(
            candidate.model_dump(mode="json"),
            root=tmp_path,
            source_snapshot=snapshot,
            context=context,
        )
        == candidate
    )


def test_public_candidate_rejects_case_alias_of_review_artifact(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    alias = _REVIEW_PATH.upper()
    _write_repo_file(tmp_path, alias, "review output\n")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )
    context = replace(
        _candidate_context(),
        input_artifacts=(),
        output_artifacts=(alias,),
    )

    with pytest.raises(ValueError, match="review artifact"):
        build_candidate_manifest(
            root=tmp_path,
            source_snapshot=snapshot,
            context=context,
        )


def test_public_candidate_rejects_windows_tail_alias_of_review_artifact(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    alias = _REVIEW_PATH.replace(".ai-sdlc/", ".ai-sdlc./", 1)
    _write_repo_file(tmp_path, alias, "review output\n")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )
    context = replace(
        _candidate_context(),
        input_artifacts=(),
        output_artifacts=(alias,),
    )

    with pytest.raises(ValueError, match="review artifact"):
        build_candidate_manifest(
            root=tmp_path,
            source_snapshot=snapshot,
            context=context,
        )


@pytest.mark.parametrize(
    "alias",
    [
        _REVIEW_PATH.upper(),
        _REVIEW_PATH.replace(".ai-sdlc/", ".ai-sdlc./", 1),
        _REVIEW_PATH.replace(".ai-sdlc/", ".ai-sdlc/. /", 1),
        _REVIEW_PATH.replace(".ai-sdlc/", ".ai-sdlc/pivot/.. /", 1),
        f" {_REVIEW_PATH}",
        f"{_REVIEW_PATH} ",
    ],
)
def test_public_candidate_rejects_snapshot_only_review_alias(
    tmp_path: Path,
    alias: str,
) -> None:
    _init_git_repo(tmp_path)
    _write_repo_file(tmp_path, "specs/001/spec.md", "requirement\n")
    _write_repo_file(tmp_path, "src/example.py", "VALUE = 1\n")
    _write_repo_file(tmp_path, "tests/test_example.py", "def test_ok(): pass\n")
    _write_repo_file(tmp_path, alias, "hidden alias\n")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )

    with pytest.raises(ValueError, match="non-canonical review artifact alias"):
        build_candidate_manifest(
            root=tmp_path,
            source_snapshot=snapshot,
            context=_candidate_context(),
        )


def test_public_candidate_rejects_parent_escape_reentry_review_alias(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _write_repo_file(tmp_path, "specs/001/spec.md", "requirement\n")
    _write_repo_file(tmp_path, "src/example.py", "VALUE = 1\n")
    _write_repo_file(tmp_path, "tests/test_example.py", "def test_ok(): pass\n")
    alias = f".. /{tmp_path.name}/{_REVIEW_PATH}"
    _write_repo_file(tmp_path, alias, "hidden alias\n")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )

    with pytest.raises(ValueError, match="non-canonical review artifact alias"):
        build_candidate_manifest(
            root=tmp_path,
            source_snapshot=snapshot,
            context=_candidate_context(),
        )


def test_public_candidate_keeps_protected_roots_case_sensitive(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _write_repo_file(tmp_path, "SRC/outside.py", "VALUE = 1\n")
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=tmp_path, source_kind="local-unstaged")
    )
    context = replace(
        _candidate_context(),
        input_artifacts=(),
        output_artifacts=("SRC/outside.py",),
    )

    with pytest.raises(ValueError, match="outside protected_source_set"):
        build_candidate_manifest(
            root=tmp_path,
            source_snapshot=snapshot,
            context=context,
        )


def test_public_candidate_reader_routes_unknown_schema_before_repository_io() -> None:
    payload = _built_candidate().model_dump(mode="json")
    payload["schema_version"] = "999"

    with pytest.raises(ValueError, match="unknown stage-review schema"):
        read_candidate_manifest(
            payload,
            root=Path("missing-repository"),
            source_snapshot=_source_snapshot(),
            context=_candidate_context(),
        )


def test_candidate_extensions_are_bound_to_trusted_context() -> None:
    extension = {
        "future-capability": {
            "created_at": "one",
            "enabled": True,
            "input_artifacts": ["ordered/z", "ordered/z", r"ordered\a"],
        }
    }
    context = replace(
        _candidate_context(),
        extensions=extension,
    )
    candidate = _build_candidate_manifest(Path("."), _source_snapshot(), context)
    baseline_digest = candidate_binding_digest(candidate)

    extension["future-capability"]["enabled"] = False
    assert candidate.extensions["future-capability"] == {
        "created_at": "one",
        "enabled": True,
        "input_artifacts": ["ordered/z", "ordered/z", r"ordered\a"],
    }
    assert candidate_binding_digest(candidate) == baseline_digest
    trusted_context = replace(
        context,
        extensions={
            "future-capability": {
                "created_at": "one",
                "enabled": True,
                "input_artifacts": ["ordered/z", "ordered/z", r"ordered\a"],
            }
        },
    )
    assert (
        _read_candidate_manifest(
            candidate.model_dump(mode="json"),
            root=Path("."),
            source_snapshot=_source_snapshot(),
            context=trusted_context,
        )
        == candidate
    )
    with pytest.raises(ValueError, match="source snapshot binding"):
        _read_candidate_manifest(
            candidate.model_dump(mode="json"),
            root=Path("."),
            source_snapshot=_source_snapshot(),
            context=context,
        )
    with pytest.raises(ValueError, match="source snapshot binding"):
        _read_candidate_manifest(
            candidate.model_dump(mode="json"),
            root=Path("."),
            source_snapshot=_source_snapshot(),
            context=_candidate_context(),
        )
    different_context = replace(
        context,
        extensions={
            "future-capability": {
                "created_at": "two",
                "enabled": True,
                "input_artifacts": ["ordered/z", "ordered/z", r"ordered\a"],
            }
        },
    )
    with pytest.raises(ValueError, match="source snapshot binding"):
        _read_candidate_manifest(
            candidate.model_dump(mode="json"),
            root=Path("."),
            source_snapshot=_source_snapshot(),
            context=different_context,
        )
    with pytest.raises(ValidationError, match="unsupported canonical value"):
        CandidateManifest.model_validate(
            {
                **_built_candidate().model_dump(mode="json"),
                "extensions": {"future-capability": object()},
            }
        )
    cycle: dict[str, object] = {}
    cycle["self"] = cycle
    with pytest.raises(ValidationError, match="maximum recursion depth"):
        CandidateManifest.model_validate(
            {
                **_built_candidate().model_dump(mode="json"),
                "extensions": cycle,
            }
        )


def test_verified_candidate_reader_rejects_forged_snapshot_binding() -> None:
    payload = _built_candidate().model_dump(mode="json")
    payload["source_tree_digest"] = _digest("0")

    with pytest.raises(ValueError, match="source snapshot binding"):
        _read_candidate_manifest(
            payload,
            root=Path("."),
            source_snapshot=_source_snapshot(),
            context=_candidate_context(),
        )


def test_verified_candidate_reader_rejects_forged_output_digest() -> None:
    payload = _built_candidate().model_dump(mode="json")
    payload["output_digests"]["src/example.py"] = _digest("0")

    with pytest.raises(ValueError, match="source snapshot binding"):
        _read_candidate_manifest(
            payload,
            root=Path("."),
            source_snapshot=_source_snapshot(),
            context=_candidate_context(),
        )


def test_candidate_reader_migrates_previous_major_as_read_only() -> None:
    current = _built_candidate().model_dump(mode="json")
    excluded = {
        "created_by",
        "created_at",
        "ai_sdlc_version",
        "extensions",
        "compatibility_mode",
        "project_id",
        "review_session_id",
            "source_snapshot_digest",
            "adapter_id",
            "adapter_version",
            "adapter_contract_digest",
        }
    legacy = {key: value for key, value in current.items() if key not in excluded}
    legacy["schema_version"] = "0"
    legacy["canonicalization_version"] = "stage-review-canonical/v0"
    legacy["legacy_digest"] = "pending"
    legacy["legacy_digest"] = _legacy_digest(legacy)

    migrated = _read_candidate_manifest(
        legacy,
        root=Path("."),
        source_snapshot=_source_snapshot(),
        context=_candidate_context(),
        expected_legacy_digest=str(legacy["legacy_digest"]),
    )

    assert migrated.compatibility_mode == "read-only-legacy"
    assert migrated.extensions["migrated_from_schema_version"] == "0"
    assert migrated.extensions["migrated_from_digest"] == legacy["legacy_digest"]

    tampered = {**legacy, "stage_key": "tampered"}
    with pytest.raises(ValidationError, match="legacy artifact digest"):
        _read_candidate_manifest(
            tampered,
            root=Path("."),
            source_snapshot=_source_snapshot(),
            context=_candidate_context(),
            expected_legacy_digest=str(legacy["legacy_digest"]),
        )
    with pytest.raises(ValueError, match="current source truth"):
        _read_candidate_manifest(
            legacy,
            root=Path("."),
            source_snapshot=_source_snapshot(source_digest=_digest("9")),
            context=_candidate_context(),
            expected_legacy_digest=str(legacy["legacy_digest"]),
        )
    with pytest.raises(ValueError, match="expected lineage digest"):
        _read_candidate_manifest(
            legacy,
            root=Path("."),
            source_snapshot=_source_snapshot(),
            context=_candidate_context(),
        )


def test_candidate_schema_is_strict_and_versioned() -> None:
    with pytest.raises(ValidationError, match="unsupported schema_version"):
        _candidate().model_copy(update={"schema_version": "999"}).model_validate(
            {
                **_candidate().model_dump(mode="json"),
                "schema_version": "999",
            }
        )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CandidateManifest.model_validate(
            {**_candidate().model_dump(mode="json"), "unknown_field": True}
        )

    with pytest.raises(ValidationError, match="candidate-manifest"):
        CandidateManifest.model_validate(
            {
                **_candidate().model_dump(mode="json"),
                "artifact_kind": "task-risk-profile",
            }
        )

    candidate = _candidate()
    assert candidate.extensions == {}
    assert candidate.compatibility_mode == "strict"
    assert candidate.identity_fields
    assert candidate.digest_covered_fields
    assert candidate.migration_policy == "current-and-previous-major"
    assert candidate.artifact_classification == "immutable-fact"
    assert set(candidate.digest_covered_fields) == {
        field
        for field in CandidateManifest.model_fields
        if field not in {"created_at", "created_by", "ai_sdlc_version"}
    }
    assert set(candidate.identity_fields).issubset(candidate.digest_covered_fields)


def test_candidate_declared_digest_fields_each_change_binding() -> None:
    candidate = _built_candidate()
    baseline = candidate_binding_digest(candidate)

    for field in candidate.digest_covered_fields:
        value = getattr(candidate, field)
        if isinstance(value, str):
            changed: object = f"{value}-changed"
        elif isinstance(value, int):
            changed = value + 1
        elif isinstance(value, list):
            changed = [*value, "changed/value"]
        elif isinstance(value, dict):
            changed = {**value, "changed/value": "changed"}
        else:  # pragma: no cover - Schema 新增未知类型时显式暴露。
            raise AssertionError(f"unsupported digest field type: {field}")
        mutated = candidate.model_copy(update={field: changed})
        assert candidate_binding_digest(mutated) != baseline, field


def test_semantic_suggestions_cannot_lower_deterministic_risk_floor() -> None:
    deterministic = RiskFact(
        risk_fact_id="rf-security",
        source_ref="src/auth.py",
        extractor_version="paths/v1",
        confidence=1.0,
        severity="critical",
        required_capability_ids=["security-review"],
        evidence_digest="sha256:evidence",
    )
    suggestion = SemanticRiskSuggestion(
        suggestion_id="suggest-security",
        evidence_ref="src/auth.py",
        confidence=0.9,
        severity="low",
        suggested_capability_ids=[],
        targets_risk_fact_id="rf-security",
    )

    profile = reconcile_risk_profile(
        work_item_id="001-example",
        stage_key="implementation",
        deterministic_facts=[deterministic],
        semantic_suggestions=[suggestion],
    )

    assert isinstance(profile, TaskRiskProfile)
    assert profile.required_capability_ids == ["security-review"]
    assert profile.risk_facts == [deterministic]
    assert profile.unconfirmed_risk_suggestions == [suggestion]
    assert profile.risk_level == "critical"


def test_semantic_suggestion_requires_identity_and_evidence() -> None:
    for field in ("suggestion_id", "evidence_ref"):
        payload = {
            "suggestion_id": "suggest-security",
            "evidence_ref": "src/auth.py",
            "confidence": 0.9,
            "severity": "high",
        }
        payload[field] = "   "
        with pytest.raises(ValidationError, match="must not be empty"):
            SemanticRiskSuggestion.model_validate(payload)


def test_risk_profile_digest_is_stable_for_input_order() -> None:
    facts = [
        RiskFact(
            risk_fact_id="rf-b",
            source_ref="src/b.py",
            extractor_version="paths/v1",
            confidence=1.0,
            severity="medium",
            required_capability_ids=["compatibility", "correctness"],
            evidence_digest="sha256:b",
        ),
        RiskFact(
            risk_fact_id="rf-a",
            source_ref="src/a.py",
            extractor_version="paths/v1",
            confidence=1.0,
            severity="high",
            required_capability_ids=["security"],
            evidence_digest="sha256:a",
        ),
    ]

    left = reconcile_risk_profile(
        work_item_id="001-example",
        stage_key="implementation",
        deterministic_facts=facts,
        semantic_suggestions=[],
    )
    right = reconcile_risk_profile(
        work_item_id="001-example",
        stage_key="implementation",
        deterministic_facts=list(reversed(facts)),
        semantic_suggestions=[],
    )

    assert left.profile_digest == right.profile_digest
    assert [fact.risk_fact_id for fact in left.risk_facts] == ["rf-a", "rf-b"]


def test_risk_profile_extensions_are_deep_frozen_and_digest_covered() -> None:
    profile = reconcile_risk_profile(
        work_item_id="001-example",
        stage_key="implementation",
        deterministic_facts=[],
        semantic_suggestions=[],
    )
    extension = {"future": {"created_at": "one", "enabled": True}}
    payload = {**profile.model_dump(mode="json"), "extensions": extension}
    payload["profile_digest"] = _risk_profile_digest(payload)
    frozen = TaskRiskProfile.model_validate(payload)
    first_digest = frozen.profile_digest

    extension["future"]["enabled"] = False
    changed = {
        **profile.model_dump(mode="json"),
        "extensions": {"future": {"created_at": "two", "enabled": True}},
    }
    changed["profile_digest"] = _risk_profile_digest(changed)

    assert frozen.extensions == {"future": {"created_at": "one", "enabled": True}}
    assert frozen.profile_digest == first_digest
    assert changed["profile_digest"] != first_digest


def test_risk_profile_rejects_conflicting_facts_with_same_identity() -> None:
    baseline = RiskFact(
        risk_fact_id="rf-security",
        source_ref="src/auth.py",
        extractor_version="paths/v1",
        confidence=1.0,
        severity="high",
        required_capability_ids=["security"],
        evidence_digest="sha256:first",
    )
    conflict = baseline.model_copy(
        update={"severity": "low", "evidence_digest": "sha256:second"}
    )

    with pytest.raises(ValueError, match="conflicting deterministic risk fact"):
        reconcile_risk_profile(
            work_item_id="001-example",
            stage_key="implementation",
            deterministic_facts=[baseline, conflict],
            semantic_suggestions=[],
        )


def test_risk_profile_rejects_direct_risk_downgrade_and_stale_digest() -> None:
    fact = RiskFact(
        risk_fact_id="rf-security",
        source_ref="src/auth.py",
        extractor_version="paths/v1",
        confidence=1.0,
        severity="critical",
        required_capability_ids=["security"],
        evidence_digest=_digest("a"),
    )
    profile = reconcile_risk_profile(
        work_item_id="001-example",
        stage_key="implementation",
        deterministic_facts=[fact],
        semantic_suggestions=[],
    )

    with pytest.raises(ValidationError, match="risk level"):
        TaskRiskProfile.model_validate(
            {**profile.model_dump(mode="json"), "risk_level": "low"}
        )
    with pytest.raises(ValidationError, match="profile digest"):
        TaskRiskProfile.model_validate(
            {**profile.model_dump(mode="json"), "stage_key": "design-contract"}
        )


def test_risk_profile_rejects_semantic_upward_authority_and_empty_identity() -> None:
    profile = reconcile_risk_profile(
        work_item_id="001-example",
        stage_key="implementation",
        deterministic_facts=[],
        semantic_suggestions=[],
    )
    inflated = {
        **profile.model_dump(mode="json"),
        "risk_level": "critical",
        "required_capability_ids": ["security-review"],
    }
    inflated["profile_digest"] = _risk_profile_digest(inflated)

    with pytest.raises(ValidationError, match="deterministic facts"):
        read_task_risk_profile(inflated)
    with pytest.raises(ValidationError, match="identity values must not be empty"):
        reconcile_risk_profile(
            work_item_id=" ",
            stage_key=" ",
            deterministic_facts=[],
            semantic_suggestions=[],
        )


def test_risk_profile_rejects_conflicting_suggestion_identity() -> None:
    suggestion = SemanticRiskSuggestion(
        suggestion_id="suggest-security",
        evidence_ref="src/auth.py",
        confidence=0.9,
        severity="high",
    )
    conflict = suggestion.model_copy(update={"severity": "low"})

    with pytest.raises(ValueError, match="conflicting semantic risk suggestion"):
        reconcile_risk_profile(
            work_item_id="001-example",
            stage_key="implementation",
            deterministic_facts=[],
            semantic_suggestions=[suggestion, conflict],
        )


def test_stage_review_reader_routes_schema_and_migrates_previous_major() -> None:
    profile = reconcile_risk_profile(
        work_item_id="001-example",
        stage_key="implementation",
        deterministic_facts=[],
        semantic_suggestions=[],
    )
    assert read_task_risk_profile(profile.model_dump(mode="json")) == profile
    assert profile.identity_fields == ("work_item_id", "stage_key")
    assert profile.digest_covered_fields
    assert profile.artifact_classification == "immutable-fact"
    assert set(profile.digest_covered_fields) == {
        field
        for field in TaskRiskProfile.model_fields
        if field
        not in {"created_at", "created_by", "ai_sdlc_version", "profile_digest"}
    }

    legacy = {
        "schema_version": "0",
        "artifact_kind": "task-risk-profile",
        "canonicalization_version": "stage-review-canonical/v0",
        "work_item_id": profile.work_item_id,
        "stage_key": profile.stage_key,
        "risk_level": profile.risk_level,
        "risk_facts": [],
        "unconfirmed_risk_suggestions": [],
        "required_capability_ids": [],
        "legacy_digest": "pending",
    }
    legacy["legacy_digest"] = _legacy_digest(legacy)
    migrated = read_task_risk_profile(
        legacy, expected_legacy_digest=str(legacy["legacy_digest"])
    )
    assert migrated.schema_version == "1"
    assert migrated.compatibility_mode == "read-only-legacy"
    assert migrated.extensions["migrated_from_schema_version"] == "0"
    assert migrated.extensions["migrated_from_digest"] == legacy["legacy_digest"]

    with pytest.raises(ValueError, match="unknown stage-review schema"):
        read_task_risk_profile({**legacy, "schema_version": "999"})

    with pytest.raises(ValidationError, match="legacy artifact digest"):
        read_task_risk_profile(
            {**legacy, "stage_key": "tampered"},
            expected_legacy_digest=str(legacy["legacy_digest"]),
        )
    with pytest.raises(ValueError, match="expected lineage digest"):
        read_task_risk_profile(legacy)


def _init_git_repo(root: Path) -> str:
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    _write_repo_file(root, "README.md", "# Test\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initial")
    return _git(root, "rev-parse", "HEAD")


def _write_repo_file(root: Path, relative: str, content: str) -> None:
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
