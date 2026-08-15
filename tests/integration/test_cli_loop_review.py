"""Integration tests for read-only dynamic expert review inputs."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ai_sdlc.cli.loop_review_cmd import resolve_review_input
from ai_sdlc.cli.main import app

runner = CliRunner()
pytestmark = pytest.mark.usefixtures("isolated_cli_cwd")


@pytest.mark.parametrize(
    ("loop_type", "filenames", "excluded"),
    [
        (
            "requirement",
            [
                "requirement-intake.json",
                "requirement-brief.md",
                "clarification-questions.md",
                "acceptance-checklist.md",
            ],
            "requirement-freeze.json",
        ),
        (
            "design-contract",
            [
                "design-contract-input.json",
                "design-contract-report.json",
                "design-contract-report.md",
            ],
            "design-contract-close.json",
        ),
        (
            "implementation",
            [
                "implementation-report.json",
                "implementation-report.md",
                "verification-evidence.json",
                "implementation-input.json",
            ],
            "implementation-close.json",
        ),
        (
            "frontend-evidence",
            [
                "frontend-evidence-snapshot.json",
                "frontend-evidence-report.json",
                "frontend-evidence-report.md",
                "frontend-evidence-input.json",
            ],
            "frontend-evidence-close.json",
        ),
    ],
)
def test_loop_review_maps_only_substantive_stage_artifacts(
    tmp_path: Path,
    loop_type: str,
    filenames: list[str],
    excluded: str,
) -> None:
    loop_id = f"{loop_type}-001"
    loop_dir = tmp_path / ".ai-sdlc" / "loops" / loop_type / loop_id
    loop_dir.mkdir(parents=True)
    (loop_dir / "loop-run.json").write_text(
        json.dumps({"current_round": 2}),
        encoding="utf-8",
    )
    for filename in [*filenames, excluded]:
        content = "{}" if filename.endswith(".json") else f"{filename}\n"
        (loop_dir / filename).write_text(content, encoding="utf-8")
    expected_upstream = _write_predecessor_fixture(tmp_path, loop_type, loop_dir)

    with patch(
        "ai_sdlc.cli.loop_review_cmd.find_project_root", return_value=tmp_path
    ):
        result = runner.invoke(
            app,
            [
                "loop",
                "review",
                "--type",
                loop_type,
                "--loop-id",
                loop_id,
                "--json",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["loop_id"] == loop_id
    assert payload["loop_type"] == loop_type
    assert payload["round_number"] == 2
    assert {Path(path).name for path in payload["artifact_paths"]} == set(filenames)
    assert {Path(path).name for path in payload["upstream_context_paths"]} == (
        expected_upstream
    )
    assert excluded not in result.output


def test_local_pr_review_binds_pre_close_artifacts_and_git_state(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    review_dir = tmp_path / ".ai-sdlc" / "reviews" / "pr" / "review-001"
    review_dir.mkdir(parents=True)
    (review_dir / "review-run.json").write_text(
        json.dumps({"review_id": "review-001", "loop_id": "loop-pr-001"}),
        encoding="utf-8",
    )
    included = [
        "review-pack.json",
        "diff.patch",
        "findings.json",
        "resolution.yaml",
        "verification-evidence.json",
    ]
    diff = review_dir / "diff.patch"
    diff.write_text("diff --git a/tracked.txt b/tracked.txt\n", encoding="utf-8")
    (review_dir / "review-pack.json").write_text(
        json.dumps(
            {
                "diff_path": diff.relative_to(tmp_path).as_posix(),
                "diff_digest": f"sha256:{hashlib.sha256(diff.read_bytes()).hexdigest()}",
            }
        ),
        encoding="utf-8",
    )
    for filename in included:
        if filename not in {"review-pack.json", "diff.patch"}:
            (review_dir / filename).write_text(f"{filename}\n", encoding="utf-8")
    (review_dir / "final-report.md").write_text("must be excluded\n", encoding="utf-8")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("changed\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")

    with patch(
        "ai_sdlc.cli.loop_review_cmd.find_project_root", return_value=tmp_path
    ):
        result = runner.invoke(
            app,
            [
                "loop",
                "review",
                "--type",
                "local-pr-review",
                "--loop-id",
                "loop-pr-001",
                "--json",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert {Path(path).name for path in payload["artifact_paths"]} == set(included)
    assert "final-report.md" not in result.output
    assert any(item.startswith("git-head:") for item in payload["risk_signals"])
    assert any(item.startswith("git-index:") for item in payload["risk_signals"])
    assert any(item.startswith("git-staged-diff:") for item in payload["risk_signals"])

    diff.write_text("tampered diff\n", encoding="utf-8")
    with patch(
        "ai_sdlc.cli.loop_review_cmd.find_project_root", return_value=tmp_path
    ):
        tampered = runner.invoke(
            app,
            [
                "loop",
                "review",
                "--type",
                "local-pr-review",
                "--loop-id",
                "loop-pr-001",
                "--json",
            ],
        )
    assert tampered.exit_code == 1
    assert json.loads(tampered.output)["reason"] == "review-input-unavailable"
    diff.write_text("diff --git a/tracked.txt b/tracked.txt\n", encoding="utf-8")

    digest = payload["input_digest"]
    tracked.write_text("changed again\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    with patch(
        "ai_sdlc.cli.loop_review_cmd.find_project_root", return_value=tmp_path
    ):
        drift = runner.invoke(
            app,
            [
                "loop",
                "review",
                "--type",
                "local-pr-review",
                "--loop-id",
                "loop-pr-001",
                "--expect-digest",
                digest,
                "--json",
            ],
        )

    assert drift.exit_code == 1
    assert json.loads(drift.output)["reason"] == "review-input-drift"
    assert not (
        tmp_path / ".ai-sdlc" / "loops" / "local-pr-review" / "loop-pr-001"
    ).exists()


def test_local_pr_review_binds_live_unstaged_and_untracked_source(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("reviewed unstaged content\n", encoding="utf-8")
    untracked = tmp_path / "untracked.txt"
    untracked.write_text("reviewed untracked content\n", encoding="utf-8")
    review_dir = tmp_path / ".ai-sdlc" / "reviews" / "pr" / "review-unstaged"
    review_dir.mkdir(parents=True)
    (review_dir / "review-run.json").write_text(
        json.dumps({"loop_id": "loop-pr-unstaged", "current_round": 1}),
        encoding="utf-8",
    )
    diff = review_dir / "diff.patch"
    diff.write_text("reviewed local-unstaged diff\n", encoding="utf-8")
    (review_dir / "review-pack.json").write_text(
        json.dumps(
            {
                "diff_path": diff.relative_to(tmp_path).as_posix(),
                "diff_digest": f"sha256:{hashlib.sha256(diff.read_bytes()).hexdigest()}",
                "diff_source": {"source_kind": "local-unstaged"},
            }
        ),
        encoding="utf-8",
    )
    (review_dir / "findings.json").write_text("{}", encoding="utf-8")

    reviewed = resolve_review_input(
        tmp_path,
        loop_type="local-pr-review",
        loop_id="loop-pr-unstaged",
    )

    tracked.write_text("changed after review\n", encoding="utf-8")
    tracked_drift = resolve_review_input(
        tmp_path,
        loop_type="local-pr-review",
        loop_id="loop-pr-unstaged",
    )
    assert tracked_drift.input_digest != reviewed.input_digest

    tracked.write_text("reviewed unstaged content\n", encoding="utf-8")
    untracked.write_text("changed untracked content\n", encoding="utf-8")
    untracked_drift = resolve_review_input(
        tmp_path,
        loop_type="local-pr-review",
        loop_id="loop-pr-unstaged",
    )
    assert untracked_drift.input_digest != reviewed.input_digest


def test_stage_review_binds_recursive_predecessor_evidence(tmp_path: Path) -> None:
    requirement_dir = (
        tmp_path / ".ai-sdlc" / "loops" / "requirement" / "requirement-001"
    )
    design_dir = (
        tmp_path / ".ai-sdlc" / "loops" / "design-contract" / "design-001"
    )
    implementation_dir = (
        tmp_path / ".ai-sdlc" / "loops" / "implementation" / "implementation-001"
    )
    frontend_dir = (
        tmp_path / ".ai-sdlc" / "loops" / "frontend-evidence" / "frontend-001"
    )
    for loop_dir in (
        requirement_dir,
        design_dir,
        implementation_dir,
        frontend_dir,
    ):
        loop_dir.mkdir(parents=True)
        (loop_dir / "loop-run.json").write_text(
            json.dumps({"current_round": 1}),
            encoding="utf-8",
        )

    for filename in (
        "requirement-intake.json",
        "requirement-brief.md",
        "clarification-questions.md",
        "acceptance-checklist.md",
    ):
        content = "{}" if filename.endswith(".json") else filename
        (requirement_dir / filename).write_text(content, encoding="utf-8")
    (design_dir / "design-contract-input.json").write_text(
        json.dumps({"requirement_loop_id": "requirement-001"}),
        encoding="utf-8",
    )
    for filename in ("design-contract-report.json", "design-contract-report.md"):
        (design_dir / filename).write_text(filename, encoding="utf-8")
    (implementation_dir / "implementation-input.json").write_text(
        json.dumps({"design_contract_loop_id": "design-001"}),
        encoding="utf-8",
    )
    for filename in (
        "implementation-report.json",
        "implementation-report.md",
        "verification-evidence.json",
    ):
        content = "{}" if filename.endswith(".json") else filename
        (implementation_dir / filename).write_text(content, encoding="utf-8")
    (frontend_dir / "frontend-evidence-input.json").write_text(
        json.dumps({"implementation_loop_id": "implementation-001"}),
        encoding="utf-8",
    )
    for filename in (
        "frontend-evidence-snapshot.json",
        "frontend-evidence-report.json",
        "frontend-evidence-report.md",
    ):
        content = "{}" if filename.endswith(".json") else filename
        (frontend_dir / filename).write_text(content, encoding="utf-8")

    first = resolve_review_input(
        tmp_path,
        loop_type="frontend-evidence",
        loop_id="frontend-001",
    )

    assert {Path(path).name for path in first.upstream_context_paths} == {
        "requirement-brief.md",
        "requirement-intake.json",
        "clarification-questions.md",
        "acceptance-checklist.md",
        "design-contract-input.json",
        "design-contract-report.json",
        "design-contract-report.md",
        "implementation-input.json",
        "implementation-report.json",
        "implementation-report.md",
        "verification-evidence.json",
    }

    (requirement_dir / "requirement-brief.md").write_text(
        "changed requirement",
        encoding="utf-8",
    )
    changed = resolve_review_input(
        tmp_path,
        loop_type="frontend-evidence",
        loop_id="frontend-001",
    )
    assert changed.input_digest != first.input_digest


def test_stage_review_binds_each_stage_source_material(tmp_path: Path) -> None:
    work_item = tmp_path / "specs" / "demo"
    work_item.mkdir(parents=True)
    for filename in ("spec.md", "plan.md", "tasks.md"):
        (work_item / filename).write_text(filename, encoding="utf-8")
    source_dir = tmp_path / "src" / "runtime"
    source_dir.mkdir(parents=True)
    source = source_dir / "feature.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    gate_source = tmp_path / ".ai-sdlc" / "memory" / "frontend-browser-gate" / "latest.yaml"
    gate_source.parent.mkdir(parents=True)
    gate_source.write_text("gate_run_id: gate-1\n", encoding="utf-8")
    screenshot = (
        tmp_path
        / ".ai-sdlc"
        / "artifacts"
        / "frontend-browser-gate"
        / "gate-1"
        / "screenshot.png"
    )
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\n\x00screenshot-v1")

    loop_specs = {
        "requirement": ("requirement-001",),
        "design-contract": ("design-001",),
        "implementation": ("implementation-001",),
        "frontend-evidence": ("frontend-001",),
    }
    loop_dirs: dict[str, Path] = {}
    for loop_type, (loop_id,) in loop_specs.items():
        loop_dir = tmp_path / ".ai-sdlc" / "loops" / loop_type / loop_id
        loop_dir.mkdir(parents=True)
        (loop_dir / "loop-run.json").write_text(
            json.dumps({"current_round": 1}), encoding="utf-8"
        )
        loop_dirs[loop_type] = loop_dir

    requirement_dir = loop_dirs["requirement"]
    (requirement_dir / "requirement-intake.json").write_text(
        json.dumps(
            {
                "clarification_questions": ["Which users?"],
                "design_scope_families": ["implementation"],
            }
        ),
        encoding="utf-8",
    )
    for filename in (
        "requirement-brief.md",
        "clarification-questions.md",
        "acceptance-checklist.md",
    ):
        (requirement_dir / filename).write_text(filename, encoding="utf-8")

    design_dir = loop_dirs["design-contract"]
    (design_dir / "design-contract-input.json").write_text(
        json.dumps(
            {
                "requirement_loop_id": "requirement-001",
                "spec_path": "specs/demo/spec.md",
                "plan_path": "specs/demo/plan.md",
                "tasks_path": "specs/demo/tasks.md",
            }
        ),
        encoding="utf-8",
    )
    for filename in ("design-contract-report.json", "design-contract-report.md"):
        (design_dir / filename).write_text("{}", encoding="utf-8")

    implementation_dir = loop_dirs["implementation"]
    (implementation_dir / "implementation-input.json").write_text(
        json.dumps(
            {
                "design_contract_loop_id": "design-001",
                "declared_scope": ["src/runtime/*.py", "specs/demo/*.md"],
            }
        ),
        encoding="utf-8",
    )
    for filename in (
        "implementation-report.json",
        "implementation-report.md",
        "verification-evidence.json",
    ):
        (implementation_dir / filename).write_text("{}", encoding="utf-8")

    frontend_dir = loop_dirs["frontend-evidence"]
    (frontend_dir / "frontend-evidence-input.json").write_text(
        json.dumps(
            {
                "implementation_loop_id": "implementation-001",
                "source_artifact_path": ".ai-sdlc/memory/frontend-browser-gate/latest.yaml",
            }
        ),
        encoding="utf-8",
    )
    (frontend_dir / "frontend-evidence-snapshot.json").write_text(
        json.dumps(
            {
                "artifact_records": [
                    {
                        "capture_status": "captured",
                        "artifact_ref": screenshot.relative_to(tmp_path).as_posix(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    for filename in ("frontend-evidence-report.json", "frontend-evidence-report.md"):
        (frontend_dir / filename).write_text("{}", encoding="utf-8")

    cases = {
        "requirement": {
            "loop_id": "requirement-001",
            "expected": {"requirement-intake.json", "clarification-questions.md"},
            "mutate": requirement_dir / "requirement-intake.json",
        },
        "design-contract": {
            "loop_id": "design-001",
            "expected": {"design-contract-input.json", "spec.md", "plan.md", "tasks.md"},
            "mutate": work_item / "spec.md",
        },
        "implementation": {
            "loop_id": "implementation-001",
            "expected": {"implementation-input.json", "feature.py"},
            "mutate": source,
        },
        "frontend-evidence": {
            "loop_id": "frontend-001",
            "expected": {"frontend-evidence-input.json", "latest.yaml", "screenshot.png"},
            "mutate": screenshot,
        },
    }
    for loop_type, case in cases.items():
        first = resolve_review_input(
            tmp_path,
            loop_type=loop_type,
            loop_id=str(case["loop_id"]),
        )
        assert set(case["expected"]) <= {
            Path(path).name for path in first.artifact_paths
        }
        assert not set(first.artifact_paths) & set(first.upstream_context_paths)
        mutate = Path(case["mutate"])
        original = mutate.read_bytes()
        mutate.write_bytes(original + b" changed")
        changed = resolve_review_input(
            tmp_path,
            loop_type=loop_type,
            loop_id=str(case["loop_id"]),
        )
        assert changed.input_digest != first.input_digest
        mutate.write_bytes(original)


def test_implementation_review_represents_deleted_declared_scope(tmp_path: Path) -> None:
    design_dir = (
        tmp_path / ".ai-sdlc" / "loops" / "design-contract" / "design-delete-001"
    )
    design_dir.mkdir(parents=True)
    (design_dir / "design-contract-input.json").write_text(
        json.dumps({"requirement_loop_id": ""}), encoding="utf-8"
    )
    for filename in ("design-contract-report.json", "design-contract-report.md"):
        (design_dir / filename).write_text("{}", encoding="utf-8")
    loop_dir = (
        tmp_path
        / ".ai-sdlc"
        / "loops"
        / "implementation"
        / "implementation-delete-001"
    )
    loop_dir.mkdir(parents=True)
    (loop_dir / "loop-run.json").write_text(
        json.dumps({"current_round": 1}), encoding="utf-8"
    )
    (loop_dir / "implementation-input.json").write_text(
        json.dumps(
            {
                "design_contract_loop_id": "design-delete-001",
                "declared_scope": ["src/runtime/deleted.py"],
            }
        ),
        encoding="utf-8",
    )
    for filename in (
        "implementation-report.json",
        "implementation-report.md",
        "verification-evidence.json",
    ):
        (loop_dir / filename).write_text("{}", encoding="utf-8")

    deleted = resolve_review_input(
        tmp_path,
        loop_type="implementation",
        loop_id="implementation-delete-001",
    )

    restored_path = tmp_path / "src" / "runtime" / "deleted.py"
    restored_path.parent.mkdir(parents=True)
    restored_path.write_text("RESTORED = True\n", encoding="utf-8")
    restored = resolve_review_input(
        tmp_path,
        loop_type="implementation",
        loop_id="implementation-delete-001",
    )
    assert restored.input_digest != deleted.input_digest


def test_implementation_review_binds_repository_evidence_files(tmp_path: Path) -> None:
    design_dir = tmp_path / ".ai-sdlc" / "loops" / "design-contract" / "design-001"
    design_dir.mkdir(parents=True)
    (design_dir / "design-contract-input.json").write_text(
        json.dumps({"requirement_loop_id": ""}), encoding="utf-8"
    )
    for filename in ("design-contract-report.json", "design-contract-report.md"):
        (design_dir / filename).write_text("{}", encoding="utf-8")

    loop_dir = (
        tmp_path
        / ".ai-sdlc"
        / "loops"
        / "implementation"
        / "implementation-evidence-001"
    )
    loop_dir.mkdir(parents=True)
    (loop_dir / "loop-run.json").write_text(
        json.dumps({"current_round": 1}), encoding="utf-8"
    )
    (loop_dir / "implementation-input.json").write_text(
        json.dumps(
            {
                "design_contract_loop_id": "design-001",
                "declared_scope": [],
            }
        ),
        encoding="utf-8",
    )
    for filename in ("implementation-report.json", "implementation-report.md"):
        (loop_dir / filename).write_text("{}", encoding="utf-8")
    evidence_file = tmp_path / "artifacts" / "test-results.log"
    evidence_file.parent.mkdir(parents=True)
    evidence_file.write_text("3470 passed\n", encoding="utf-8")
    (loop_dir / "verification-evidence.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "evidence": [
                            evidence_file.relative_to(tmp_path).as_posix(),
                            "pytest completed successfully",
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    reviewed = resolve_review_input(
        tmp_path,
        loop_type="implementation",
        loop_id="implementation-evidence-001",
    )
    assert evidence_file.relative_to(tmp_path).as_posix() in reviewed.artifact_paths

    evidence_file.write_text("1 failed\n", encoding="utf-8")
    changed = resolve_review_input(
        tmp_path,
        loop_type="implementation",
        loop_id="implementation-evidence-001",
    )
    assert changed.input_digest != reviewed.input_digest


def test_risk_signals_ignore_substrings_in_structural_words(tmp_path: Path) -> None:
    loop_dir = tmp_path / ".ai-sdlc" / "loops" / "requirement" / "requirement-001"
    loop_dir.mkdir(parents=True)
    (loop_dir / "loop-run.json").write_text(
        json.dumps({"current_round": 1}),
        encoding="utf-8",
    )
    (loop_dir / "requirement-brief.md").write_text(
        '{"blocker_count": 0, "required": true, "build": "complete"}',
        encoding="utf-8",
    )
    (loop_dir / "acceptance-checklist.md").write_text("complete", encoding="utf-8")
    (loop_dir / "requirement-intake.json").write_text("{}", encoding="utf-8")
    (loop_dir / "clarification-questions.md").write_text("none", encoding="utf-8")

    review_input = resolve_review_input(
        tmp_path,
        loop_type="requirement",
        loop_id="requirement-001",
    )

    assert review_input.risk_signals == ["general-correctness"]


def test_risk_signals_detect_standalone_short_terms(tmp_path: Path) -> None:
    loop_dir = tmp_path / ".ai-sdlc" / "loops" / "requirement" / "requirement-001"
    loop_dir.mkdir(parents=True)
    (loop_dir / "loop-run.json").write_text(
        json.dumps({"current_round": 1}),
        encoding="utf-8",
    )
    (loop_dir / "requirement-brief.md").write_text(
        "UI state requires a lock",
        encoding="utf-8",
    )
    (loop_dir / "acceptance-checklist.md").write_text("complete", encoding="utf-8")
    (loop_dir / "requirement-intake.json").write_text("{}", encoding="utf-8")
    (loop_dir / "clarification-questions.md").write_text("none", encoding="utf-8")

    review_input = resolve_review_input(
        tmp_path,
        loop_type="requirement",
        loop_id="requirement-001",
    )

    assert review_input.risk_signals == ["concurrency", "frontend"]


def _init_git_repo(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "review@example.com")
    _git(root, "config", "user.name", "Review Test")
    (root / "tracked.txt").write_text("initial\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-m", "initial")


def _write_predecessor_fixture(
    root: Path,
    loop_type: str,
    loop_dir: Path,
) -> set[str]:
    if loop_type == "requirement":
        return set()
    if loop_type == "design-contract":
        (loop_dir / "design-contract-input.json").write_text(
            json.dumps({"requirement_loop_id": ""}),
            encoding="utf-8",
        )
        return set()

    design_dir = root / ".ai-sdlc" / "loops" / "design-contract" / "design-upstream"
    design_dir.mkdir(parents=True)
    (design_dir / "design-contract-input.json").write_text(
        json.dumps({"requirement_loop_id": ""}),
        encoding="utf-8",
    )
    design_files = {
        "design-contract-input.json",
        "design-contract-report.json",
        "design-contract-report.md",
    }
    for filename in design_files - {"design-contract-input.json"}:
        (design_dir / filename).write_text(filename, encoding="utf-8")
    if loop_type == "implementation":
        (loop_dir / "implementation-input.json").write_text(
            json.dumps({"design_contract_loop_id": "design-upstream"}),
            encoding="utf-8",
        )
        return design_files

    implementation_dir = (
        root / ".ai-sdlc" / "loops" / "implementation" / "implementation-upstream"
    )
    implementation_dir.mkdir(parents=True)
    (implementation_dir / "implementation-input.json").write_text(
        json.dumps({"design_contract_loop_id": "design-upstream"}),
        encoding="utf-8",
    )
    implementation_files = {
        "implementation-input.json",
        "implementation-report.json",
        "implementation-report.md",
        "verification-evidence.json",
    }
    for filename in implementation_files - {"implementation-input.json"}:
        content = "{}" if filename.endswith(".json") else filename
        (implementation_dir / filename).write_text(content, encoding="utf-8")
    (loop_dir / "frontend-evidence-input.json").write_text(
        json.dumps({"implementation_loop_id": "implementation-upstream"}),
        encoding="utf-8",
    )
    return design_files | implementation_files


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
