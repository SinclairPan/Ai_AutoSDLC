"""Lean Code CLI plain/JSON integration tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from click import unstyle
from typer.testing import CliRunner

from ai_sdlc.cli.main import app
from ai_sdlc.core.implementation_models import (
    ImplementationCurrentPointer,
    ImplementationInput,
)
from ai_sdlc.core.implementation_store import implementation_artifacts
from ai_sdlc.core.loop_artifacts import LoopArtifactStore
from ai_sdlc.core.loop_models import LoopRound, LoopRun, LoopStatus, LoopType
from ai_sdlc.models.work import WorkType

runner = CliRunner()


def test_lean_check_json_is_deterministic_and_model_free(tmp_path: Path) -> None:
    _seed_loop(tmp_path, "impl-cli-json")
    _write(tmp_path, "src/app.py", "def _small():\n    return 1\n")

    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=tmp_path):
        result = runner.invoke(
            app,
            [
                "loop",
                "implementation",
                "lean-check",
                "--loop-id",
                "impl-cli-json",
                "--json",
            ],
        )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "ready"
    assert payload["loop_status"] == "passed"
    assert payload["requires_model"] is False
    assert payload["writes_code"] is False
    assert payload["writes_artifacts"] is True
    assert (tmp_path / payload["report_path"]).is_file()


def test_lean_check_plain_outputs_result_and_next(tmp_path: Path) -> None:
    _seed_loop(tmp_path, "impl-cli-plain")
    _write(tmp_path, "src/app.py", "def _small():\n    return 1\n")

    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=tmp_path):
        result = runner.invoke(
            app,
            [
                "loop",
                "implementation",
                "lean-check",
                "--loop-id",
                "impl-cli-plain",
            ],
        )

    assert result.exit_code == 0
    assert "Result: ready" in result.output
    assert "Next: Run ai-sdlc loop implementation close --yes." in result.output
    assert "Model call: no" in result.output
    assert "Writes code: no" in result.output


def test_lean_verify_cli_executes_argv_and_writes_receipt(tmp_path: Path) -> None:
    _seed_loop(tmp_path, "impl-cli-verify")
    _write(tmp_path, "src/app.py", "def _small():\n    return 1\n")
    _write(tmp_path, "tests/verify_probe.py", "print('verified')\n")

    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=tmp_path):
        result = runner.invoke(
            app,
            [
                "loop",
                "implementation",
                "lean-verify",
                "--loop-id",
                "impl-cli-verify",
                "--test-source",
                "tests/verify_probe.py",
                "--json",
                "--",
                sys.executable,
                "tests/verify_probe.py",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ready"
    assert (tmp_path / payload["receipt_path"]).is_file()


def test_controlled_execution_cli_requires_explicit_loop_id(tmp_path: Path) -> None:
    _seed_loop(tmp_path, "impl-cli-required-loop")

    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=tmp_path):
        verify = runner.invoke(
            app,
            [
                "loop",
                "implementation",
                "lean-verify",
                "--",
                sys.executable,
                "-c",
                "print('verified')",
            ],
        )
        regression = runner.invoke(
            app,
            [
                "loop",
                "implementation",
                "lean-regression",
                "--phase",
                "red",
                "--test-id",
                "missing-loop",
                "--test-source",
                "tests/missing.py",
                "--failure-signature",
                "missing-loop",
                "--",
                sys.executable,
                "-c",
                "raise SystemExit(1)",
            ],
        )

    for result in (verify, regression):
        assert result.exit_code == 2
        assert "Missing option '--loop-id'" in unstyle(result.output)


def test_lean_regression_cli_captures_real_red_then_green(tmp_path: Path) -> None:
    loop_id = "impl-cli-regression"
    _seed_loop(tmp_path, loop_id)
    signature = "assertion:cli-regression"
    _write(tmp_path, "src/app.py", "VALUE = 0\n")
    _write(
        tmp_path,
        "tests/regression_probe.py",
        "from pathlib import Path\n"
        "source = Path('src/app.py').read_text(encoding='utf-8')\n"
        f"print({signature!r}) if 'VALUE = 1' not in source else print('passed')\n"
        "raise SystemExit(0 if 'VALUE = 1' in source else 1)\n",
    )
    prefix = [
        "loop",
        "implementation",
        "lean-regression",
        "--loop-id",
        loop_id,
        "--test-id",
        "cli-regression",
        "--test-source",
        "tests/regression_probe.py",
        "--failure-signature",
        signature,
        "--json",
    ]
    command = ["--", sys.executable, "tests/regression_probe.py"]
    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=tmp_path):
        red = runner.invoke(app, [*prefix, "--phase", "red", *command])
        _write(tmp_path, "src/app.py", "VALUE = 1\n")
        green = runner.invoke(app, [*prefix, "--phase", "green", *command])

    assert red.exit_code == 0, red.output
    assert green.exit_code == 0, green.output
    payload = json.loads(green.output)
    assert payload["status"] == "ready"
    assert (tmp_path / payload["evidence_path"]).is_file()


def test_lean_regression_cli_rejects_invalid_failure_signature(tmp_path: Path) -> None:
    loop_id = "impl-cli-invalid-signature"
    _seed_loop(tmp_path, loop_id)
    _write(tmp_path, "src/app.py", "VALUE = 0\n")
    test_source = "tests/invalid_signature_probe.py"
    signature = "cli-invalid-signature"
    _write(tmp_path, test_source, f"print({signature!r})\nraise SystemExit(1)\n")

    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=tmp_path):
        result = runner.invoke(
            app,
            [
                "loop",
                "implementation",
                "lean-regression",
                "--loop-id",
                loop_id,
                "--phase",
                "red",
                "--test-id",
                "invalid-signature",
                "--test-source",
                test_source,
                "--failure-signature",
                signature,
                "--json",
                "--",
                sys.executable,
                test_source,
            ],
        )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert "assertion:" in payload["blocker"]
    regression_dir = implementation_artifacts(tmp_path, loop_id).loop_dir / "lean"
    assert not (regression_dir / "regressions" / "invalid-signature").exists()


def test_lean_check_exception_option_fails_closed_for_missing_artifact(
    tmp_path: Path,
) -> None:
    _seed_loop(tmp_path, "impl-cli-exception")
    _write(tmp_path, "src/app.py", "def _small():\n    return 1\n")

    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=tmp_path):
        result = runner.invoke(
            app,
            [
                "loop",
                "implementation",
                "lean-check",
                "--loop-id",
                "impl-cli-exception",
                "--exception",
                "evidence/missing-exception.json",
                "--json",
            ],
        )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert "malformed" in payload["blocker"].lower()


def test_lean_no_go_cli_records_needs_user_without_writing_code(tmp_path: Path) -> None:
    _seed_loop(tmp_path, "impl-cli-no-go")
    source = "def build_future():\n    return object()\n"
    _write(tmp_path, "src/app.py", source)
    evidence_ref = ".ai-sdlc/loops/implementation/impl-cli-no-go/lean/no-go-proof.txt"
    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=tmp_path):
        checked = runner.invoke(
            app,
            [
                "loop",
                "implementation",
                "lean-check",
                "--loop-id",
                "impl-cli-no-go",
                "--json",
            ],
        )
        _write(tmp_path, evidence_ref, "public behavior would regress\n")
        result = runner.invoke(
            app,
            [
                "loop",
                "implementation",
                "lean-no-go",
                "--loop-id",
                "impl-cli-no-go",
                "--reason",
                "Metric-only change would break behavior.",
                "--owner",
                "implementation-owner",
                "--repair-cost",
                "behavioral regression",
                "--expected-benefit",
                "one metric reduction",
                "--evidence",
                evidence_ref,
                "--json",
            ],
        )

    assert checked.exit_code == 1
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "needs_user"
    assert payload["stop_reason"].startswith("no_go:")
    assert payload["writes_code"] is False
    assert (tmp_path / "src/app.py").read_text(encoding="utf-8") == source


def _seed_loop(root: Path, loop_id: str) -> None:
    _init_repo(root)
    artifacts = implementation_artifacts(root, loop_id)
    store = LoopArtifactStore(root)
    store.create_loop_run_dir(loop_id, loop_type=LoopType.IMPLEMENTATION.value)
    store.write_json_artifact(
        artifacts.input_path,
        ImplementationInput(
            loop_id=loop_id,
            work_item_id="WI-CLI",
            work_item_path="specs/WI-CLI",
            spec_path="specs/WI-CLI/spec.md",
            plan_path="specs/WI-CLI/plan.md",
            tasks_path="specs/WI-CLI/tasks.md",
            design_contract_loop_id="design-cli",
            work_type=WorkType.NEW_REQUIREMENT,
            quality_profiles=["lean-code"],
            declared_scope=["src/app.py"],
        ),
    )
    store.write_json_artifact(
        artifacts.loop_run_path,
        LoopRun(
            loop_id=loop_id,
            loop_type=LoopType.IMPLEMENTATION,
            status=LoopStatus.NEEDS_REVIEW,
            current_round=1,
            rounds=[LoopRound(round_number=1, status=LoopStatus.NEEDS_REVIEW)],
        ),
    )
    store.write_json_artifact(
        artifacts.pointer_path,
        ImplementationCurrentPointer(
            loop_id=loop_id,
            loop_run_path=artifacts.loop_run_path.relative_to(root).as_posix(),
        ),
    )


def _init_repo(root: Path) -> None:
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


def _git(root: Path, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    if result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", errors="replace"))
