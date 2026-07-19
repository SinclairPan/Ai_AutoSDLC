"""Lean Code CLI plain/JSON integration tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click import unstyle
from typer.testing import CliRunner

from ai_sdlc.cli.main import app
from ai_sdlc.core.implementation_models import (
    ImplementationCurrentPointer,
    ImplementationInput,
)
from ai_sdlc.core.implementation_store import implementation_artifacts
from ai_sdlc.core.lean_code_evidence import regression_evidence_issue
from ai_sdlc.core.lean_code_execution import validate_execution_receipt
from ai_sdlc.core.lean_code_models import RegressionEvidence
from ai_sdlc.core.loop_artifacts import LoopArtifactStore
from ai_sdlc.core.loop_models import LoopRound, LoopRun, LoopStatus, LoopType
from ai_sdlc.models.work import WorkType

runner = CliRunner()


def test_lean_source_bound_commands_expose_the_same_source_options() -> None:
    for command in ("lean-check", "lean-verify", "lean-regression"):
        result = runner.invoke(app, ["loop", "implementation", command, "--help"])

        assert result.exit_code == 0, (command, result.output)
        plain = unstyle(result.output)
        for option in ("--diff-source", "--base", "--head", "--patch-file"):
            assert option in plain, (command, option, plain)


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


def test_lean_verify_cli_binds_receipt_to_selected_diff_source(
    tmp_path: Path,
) -> None:
    loop_id = "impl-cli-verify-staged"
    _seed_loop(tmp_path, loop_id)
    _write(tmp_path, "src/app.py", "def _small():\n    return 1\n")
    _write(
        tmp_path,
        "tests/verify_staged.py",
        "from pathlib import Path\n"
        "source = Path('src/app.py').read_text(encoding='utf-8')\n"
        "raise SystemExit(0 if 'return 1' in source else 1)\n",
    )
    _git(tmp_path, "add", "src/app.py", "tests/verify_staged.py")
    _write(tmp_path, "src/app.py", "def _small():\n    return 0\n")
    (tmp_path / "tests/verify_staged.py").unlink()

    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=tmp_path):
        result = runner.invoke(
            app,
            [
                "loop",
                "implementation",
                "lean-verify",
                "--loop-id",
                loop_id,
                "--test-source",
                "tests/verify_staged.py",
                "--diff-source",
                "local-staged",
                "--json",
                "--",
                sys.executable,
                "tests/verify_staged.py",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    receipt = _receipt_payload(tmp_path, payload)
    snapshot = _receipt_snapshot(tmp_path, payload)
    assert snapshot["source_kind"] == "local-staged"
    assert receipt["test_source_digest"] == _payload_digest(
        "from pathlib import Path\n"
        "source = Path('src/app.py').read_text(encoding='utf-8')\n"
        "raise SystemExit(0 if 'return 1' in source else 1)\n"
    )
    validated, issue = validate_execution_receipt(
        tmp_path, str(payload["receipt_path"])
    )
    assert validated is not None
    assert issue == ""


def test_lean_verify_staged_ignores_external_worktree_symlink_overlay(
    tmp_path: Path,
) -> None:
    loop_id = "impl-cli-verify-staged-symlink"
    test_source = "tests/verify_staged_symlink.py"
    test_content = "print('selected staged test')\n"
    _seed_loop(tmp_path, loop_id)
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    _write(tmp_path, test_source, test_content)
    _git(tmp_path, "add", "src/app.py", test_source)
    selected_path = tmp_path / test_source
    selected_path.unlink()
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("raise SystemExit(1)\n", encoding="utf-8")
    try:
        selected_path.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=tmp_path):
        result = runner.invoke(
            app,
            [
                "loop",
                "implementation",
                "lean-verify",
                "--loop-id",
                loop_id,
                "--test-source",
                test_source,
                "--diff-source",
                "local-staged",
                "--json",
                "--",
                sys.executable,
                test_source,
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    receipt = _receipt_payload(tmp_path, payload)
    assert receipt["test_source_digest"] == _payload_digest(test_content)
    validated, issue = validate_execution_receipt(
        tmp_path, str(payload["receipt_path"])
    )
    assert validated is not None
    assert issue == ""


def test_lean_verify_cli_forwards_git_range_and_patch_source_parameters(
    tmp_path: Path,
) -> None:
    range_root = tmp_path / "range"
    range_root.mkdir()
    range_loop = "impl-cli-verify-range"
    _seed_loop(range_root, range_loop)
    _write(range_root, "src/app.py", "VALUE = 1\n")
    _write(
        range_root,
        "tests/verify_range.py",
        "from pathlib import Path\n"
        "source = Path('src/app.py').read_text(encoding='utf-8')\n"
        "raise SystemExit(0 if 'VALUE = 1' in source else 1)\n",
    )
    _git(range_root, "add", "src/app.py", "tests/verify_range.py")
    _git(range_root, "commit", "-m", "add range fixture")
    _write(range_root, "src/app.py", "VALUE = 0\n")
    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=range_root):
        range_result = runner.invoke(
            app,
            [
                "loop",
                "implementation",
                "lean-verify",
                "--loop-id",
                range_loop,
                "--test-source",
                "tests/verify_range.py",
                "--diff-source",
                "local-git-range",
                "--base",
                "HEAD~1",
                "--head",
                "HEAD",
                "--json",
                "--",
                sys.executable,
                "tests/verify_range.py",
            ],
        )
    assert range_result.exit_code == 0, range_result.output
    range_snapshot = _receipt_snapshot(range_root, json.loads(range_result.output))
    assert range_snapshot["source_kind"] == "local-git-range"
    assert range_snapshot["base_ref"] == "HEAD~1"
    assert range_snapshot["head_ref"] == "HEAD"

    patch_root = tmp_path / "patch"
    patch_root.mkdir()
    patch_loop = "impl-cli-verify-patch"
    _seed_loop(patch_root, patch_loop)
    _write(patch_root, "src/app.py", "VALUE = 0\n")
    _write(
        patch_root,
        "tests/verify_patch.py",
        "from pathlib import Path\n"
        "source = Path('src/app.py').read_text(encoding='utf-8')\n"
        "raise SystemExit(0 if 'VALUE = 1' in source else 1)\n",
    )
    _git(patch_root, "add", "src/app.py", "tests/verify_patch.py")
    _git(patch_root, "commit", "-m", "add patch fixture")
    _write(patch_root, "src/app.py", "VALUE = 1\n")
    _write_patch(patch_root, "evidence/change.patch")
    _write(patch_root, "src/app.py", "VALUE = 2\n")
    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=patch_root):
        patch_result = runner.invoke(
            app,
            [
                "loop",
                "implementation",
                "lean-verify",
                "--loop-id",
                patch_loop,
                "--test-source",
                "tests/verify_patch.py",
                "--diff-source",
                "patch",
                "--patch-file",
                "evidence/change.patch",
                "--json",
                "--",
                sys.executable,
                "tests/verify_patch.py",
            ],
        )
    assert patch_result.exit_code == 0, patch_result.output
    patch_snapshot = _receipt_snapshot(patch_root, json.loads(patch_result.output))
    assert patch_snapshot["source_kind"] == "patch"
    assert patch_snapshot["patch_file"] == "evidence/change.patch"


def test_lean_verify_patch_executes_test_source_added_by_selected_patch(
    tmp_path: Path,
) -> None:
    loop_id = "impl-cli-verify-patch-added-test"
    test_source = "tests/patch_added_verify.py"
    test_content = (
        "from pathlib import Path\n"
        "source = Path('src/app.py').read_text(encoding='utf-8')\n"
        "raise SystemExit(0 if 'VALUE = 1' in source else 1)\n"
    )
    _seed_loop(tmp_path, loop_id)
    _write(tmp_path, "src/app.py", "VALUE = 0\n")
    _git(tmp_path, "add", "src/app.py")
    _git(tmp_path, "commit", "-m", "add patch baseline")
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    _write(tmp_path, test_source, test_content)
    _git(tmp_path, "add", "--intent-to-add", test_source)
    _write_patch(tmp_path, "evidence/add-test.patch")
    (tmp_path / test_source).unlink()

    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=tmp_path):
        result = runner.invoke(
            app,
            [
                "loop",
                "implementation",
                "lean-verify",
                "--loop-id",
                loop_id,
                "--test-source",
                test_source,
                "--diff-source",
                "patch",
                "--patch-file",
                "evidence/add-test.patch",
                "--json",
                "--",
                sys.executable,
                test_source,
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    receipt = _receipt_payload(tmp_path, payload)
    snapshot = _receipt_snapshot(tmp_path, payload)
    assert snapshot["source_kind"] == "patch"
    assert receipt["test_source_digest"] == _payload_digest(test_content)
    validated, issue = validate_execution_receipt(
        tmp_path, str(payload["receipt_path"])
    )
    assert validated is not None
    assert issue == ""


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


@pytest.mark.parametrize("phase", ["red", "green"])
@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("unsafe_loop_id", ["../bad", "CON", "NUL"])
def test_lean_regression_cli_blocks_unsafe_loop_id_without_traceback(
    tmp_path: Path,
    phase: str,
    json_output: bool,
    unsafe_loop_id: str,
) -> None:
    command = [
        "loop",
        "implementation",
        "lean-regression",
        "--loop-id",
        unsafe_loop_id,
        "--phase",
        phase,
        "--test-id",
        "unsafe-loop",
        "--test-source",
        "tests/regression_probe.py",
        "--failure-signature",
        "assertion:unsafe-loop",
    ]
    if json_output:
        command.append("--json")
    command.extend(["--", sys.executable, "tests/regression_probe.py"])

    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=tmp_path):
        result = runner.invoke(app, command)

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    if json_output:
        payload = json.loads(result.output)
        assert payload["status"] == "blocked"
        assert "loop id" in payload["blocker"].lower()
    else:
        assert "Result: blocked" in result.output
        assert "loop id" in result.output.lower()


@pytest.mark.parametrize("phase", ["red", "green"])
@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("unsafe_test_id", ["..", "CON", "NUL", "abc."])
def test_lean_regression_cli_blocks_unsafe_test_id_without_path_escape(
    tmp_path: Path,
    phase: str,
    json_output: bool,
    unsafe_test_id: str,
) -> None:
    command = [
        "loop",
        "implementation",
        "lean-regression",
        "--loop-id",
        "impl-safe",
        "--phase",
        phase,
        "--test-id",
        unsafe_test_id,
        "--test-source",
        "tests/regression_probe.py",
        "--failure-signature",
        "assertion:unsafe-test-id",
    ]
    if json_output:
        command.append("--json")
    command.extend(["--", sys.executable, "tests/regression_probe.py"])

    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=tmp_path):
        result = runner.invoke(app, command)

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert not (tmp_path / ".ai-sdlc" / "loops" / "implementation").exists()
    if json_output:
        payload = json.loads(result.output)
        assert payload["status"] == "blocked"
        assert "test id" in payload["blocker"].lower()
    else:
        assert "Result: blocked" in result.output
        assert "test id" in result.output.lower()


def test_lean_regression_cli_validates_selected_snapshot_test_source(
    tmp_path: Path,
) -> None:
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
    _git(tmp_path, "add", "src/app.py", "tests/regression_probe.py")
    (tmp_path / "tests/regression_probe.py").unlink()
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
        "--diff-source",
        "local-staged",
        "--json",
    ]
    command = ["--", sys.executable, "tests/regression_probe.py"]
    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=tmp_path):
        _write(tmp_path, "src/app.py", "VALUE = 1\n")
        red = runner.invoke(app, [*prefix, "--phase", "red", *command])
        _write(tmp_path, "src/app.py", "VALUE = 1\n")
        _git(tmp_path, "add", "src/app.py")
        _write(tmp_path, "src/app.py", "VALUE = 0\n")
        green = runner.invoke(app, [*prefix, "--phase", "green", *command])

    assert red.exit_code == 0, red.output
    assert green.exit_code == 0, green.output
    payload = json.loads(green.output)
    assert payload["status"] == "ready"
    assert (tmp_path / payload["evidence_path"]).is_file()
    evidence = RegressionEvidence.model_validate_json(
        (tmp_path / payload["evidence_path"]).read_text("utf-8")
    )
    assert regression_evidence_issue(tmp_path, evidence, loop_id) == ""


def test_lean_regression_cli_rejects_red_green_source_selector_mismatch(
    tmp_path: Path,
) -> None:
    loop_id = "impl-cli-regression-source-mismatch"
    _seed_loop(tmp_path, loop_id)
    signature = "assertion:cli-source-mismatch"
    _write(tmp_path, "src/app.py", "VALUE = 0\n")
    _write(
        tmp_path,
        "tests/regression_source.py",
        "from pathlib import Path\n"
        "source = Path('src/app.py').read_text(encoding='utf-8')\n"
        f"print({signature!r}) if 'VALUE = 1' not in source else print('passed')\n"
        "raise SystemExit(0 if 'VALUE = 1' in source else 1)\n",
    )
    _git(tmp_path, "add", "src/app.py", "tests/regression_source.py")
    common = [
        "loop",
        "implementation",
        "lean-regression",
        "--loop-id",
        loop_id,
        "--test-id",
        "source-mismatch",
        "--test-source",
        "tests/regression_source.py",
        "--failure-signature",
        signature,
        "--json",
    ]
    command = ["--", sys.executable, "tests/regression_source.py"]
    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=tmp_path):
        red = runner.invoke(
            app,
            [*common, "--phase", "red", "--diff-source", "local-staged", *command],
        )
        _write(tmp_path, "src/app.py", "VALUE = 1\n")
        green = runner.invoke(app, [*common, "--phase", "green", *command])

    assert red.exit_code == 0, red.output
    assert green.exit_code == 1
    payload = json.loads(green.output)
    assert payload["status"] == "blocked"
    assert "source selection" in payload["blocker"].lower()


def test_lean_regression_cli_captures_patch_red_then_green(tmp_path: Path) -> None:
    loop_id = "impl-cli-regression-patch"
    _seed_loop(tmp_path, loop_id)
    signature = "assertion:cli-regression-patch"
    _write(tmp_path, "src/app.py", "VALUE = -1\n")
    _write(
        tmp_path,
        "tests/regression_patch.py",
        "from pathlib import Path\n"
        "source = Path('src/app.py').read_text(encoding='utf-8')\n"
        f"print({signature!r}) if 'VALUE = 1' not in source else print('passed')\n"
        "raise SystemExit(0 if 'VALUE = 1' in source else 1)\n",
    )
    _git(tmp_path, "add", "src/app.py", "tests/regression_patch.py")
    _git(tmp_path, "commit", "-m", "add patch regression fixture")
    common = [
        "loop",
        "implementation",
        "lean-regression",
        "--loop-id",
        loop_id,
        "--test-id",
        "cli-regression-patch",
        "--test-source",
        "tests/regression_patch.py",
        "--failure-signature",
        signature,
        "--diff-source",
        "patch",
        "--patch-file",
        "evidence/regression.patch",
        "--json",
    ]
    command = ["--", sys.executable, "tests/regression_patch.py"]
    with patch("ai_sdlc.cli.loop_cmd.find_project_root", return_value=tmp_path):
        _write(tmp_path, "src/app.py", "VALUE = 0\n")
        _write_patch(tmp_path, "evidence/regression.patch")
        _write(tmp_path, "src/app.py", "VALUE = 1\n")
        red = runner.invoke(app, [*common, "--phase", "red", *command])
        _write(tmp_path, "src/app.py", "VALUE = 1\n")
        _write_patch(tmp_path, "evidence/regression.patch")
        _write(tmp_path, "src/app.py", "VALUE = 0\n")
        green = runner.invoke(app, [*common, "--phase", "green", *command])

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


def _write_patch(root: Path, relative: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", "--no-textconv"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", errors="replace"))
    target.write_bytes(result.stdout)


def _receipt_snapshot(root: Path, payload: dict[str, object]) -> dict[str, object]:
    receipt = _receipt_payload(root, payload)
    return json.loads((root / receipt["source_snapshot_ref"]).read_text("utf-8"))


def _receipt_payload(root: Path, payload: dict[str, object]) -> dict[str, object]:
    return json.loads((root / str(payload["receipt_path"])).read_text("utf-8"))


def _payload_digest(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"


def _git(root: Path, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    if result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", errors="replace"))
