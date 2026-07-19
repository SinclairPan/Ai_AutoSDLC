"""Lean Code artifact、两轮状态与 Implementation close freshness 测试。"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import venv
import zipfile
from pathlib import Path

import pytest

import ai_sdlc.core.lean_code_environment as lean_code_environment
import ai_sdlc.core.lean_code_runner as lean_code_runner
import ai_sdlc.core.source_snapshot_view as source_snapshot_view
from ai_sdlc.core.implementation_loop import (
    close_implementation_loop,
    record_implementation_progress,
)
from ai_sdlc.core.implementation_models import (
    ImplementationCloseOptions,
    ImplementationCurrentPointer,
    ImplementationInput,
    ImplementationProgress,
    ImplementationRecordOptions,
    ImplementationTaskItem,
    ImplementationTaskProgress,
    ImplementationTasks,
    ImplementationTaskStatus,
)
from ai_sdlc.core.implementation_store import implementation_artifacts
from ai_sdlc.core.lean_code_environment import (
    controlled_execution_environment,
    execution_toolchain,
    resolve_execution_adapter,
)
from ai_sdlc.core.lean_code_execution import (
    LeanExecutionOptions,
    run_lean_command,
    validate_execution_receipt,
)
from ai_sdlc.core.lean_code_models import (
    LeanEvaluationInput,
    LeanEvaluationReport,
    LeanException,
    LeanNoGoDecision,
)
from ai_sdlc.core.lean_code_policy import stable_artifact_digest
from ai_sdlc.core.lean_code_regression import (
    LeanRegressionOptions,
    capture_regression_phase,
)
from ai_sdlc.core.lean_code_runtime import (
    LeanCheckOptions,
    LeanNoGoOptions,
    record_lean_no_go,
    run_lean_check,
    validate_lean_close,
)
from ai_sdlc.core.loop_artifacts import LoopArtifactStore
from ai_sdlc.core.loop_models import LoopRound, LoopRun, LoopStatus, LoopType
from ai_sdlc.models.work import WorkType


@pytest.mark.parametrize("receipt_id", ["..", "CON", "NUL", "abc."])
def test_execution_rejects_nonportable_receipt_id(
    tmp_path: Path,
    receipt_id: str,
) -> None:
    result = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id="impl-safe",
            purpose="targeted-verification",
            receipt_id=receipt_id,
            command_argv=(sys.executable, "tests/probe.py"),
            test_source_ref="tests/probe.py",
        )
    )

    assert result.status == "blocked"
    assert "receipt id" in result.blocker.lower()
    assert not (tmp_path / ".ai-sdlc").exists()


def test_lean_check_blocks_when_metric_blob_read_times_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_enabled_loop(tmp_path, "impl-blob-timeout")
    _write(tmp_path, "src/app.py", "def public_api():\n    return 1\n")
    _git(tmp_path, "add", "src/app.py")
    original = source_snapshot_view.subprocess.run

    def timed_run(*args: object, **kwargs: object):
        command = args[0] if args else kwargs.get("args")
        if isinstance(command, list) and "cat-file" in command:
            raise subprocess.TimeoutExpired(command, 30)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(source_snapshot_view.subprocess, "run", timed_run)

    result = run_lean_check(
        LeanCheckOptions(
            root=tmp_path,
            loop_id="impl-blob-timeout",
            source_kind="local-staged",
        )
    )

    assert result.status == "blocked"
    assert "timed out" in result.blocker


def test_first_required_then_targeted_fix_passes_second_round(tmp_path: Path) -> None:
    _seed_enabled_loop(tmp_path, "impl-fix")
    _commit_fixture(
        tmp_path,
        "tests/targeted_fix.py",
        "print('targeted verification passed')\n",
    )
    _write(tmp_path, "src/app.py", "def build_future():\n    return object()\n")

    first = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id="impl-fix"))
    assert first.status == "needs_fix", first
    assert first.loop_status == LoopStatus.NEEDS_FIX
    assert first.evaluation_round == 1

    _write(tmp_path, "src/app.py", "def _build_future():\n    return object()\n")
    verified = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id="impl-fix",
            purpose="targeted-verification",
            command_argv=(sys.executable, "tests/targeted_fix.py"),
            test_source_ref="tests/targeted_fix.py",
        )
    )
    assert verified.status == "ready"
    recorded = record_implementation_progress(
        ImplementationRecordOptions(
            root=tmp_path,
            loop_id="impl-fix",
            task_id="T11",
            status="done",
            evidence=(verified.receipt_path,),
            verification=("python -c targeted-verification",),
        )
    )
    assert recorded.status == "ready"
    second = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id="impl-fix"))

    assert second.status == "ready", second
    assert second.loop_status == LoopStatus.PASSED
    assert second.evaluation_round == 2
    assert validate_lean_close(tmp_path, "impl-fix") == ""
    report = _lean_dir(tmp_path, "impl-fix") / "round-002" / "report.json"
    assert report.is_file()
    assert (_lean_dir(tmp_path, "impl-fix") / "current.json").is_file()
    loop = _read_loop(tmp_path, "impl-fix")
    assert [item.round_kind for item in loop.rounds] == [
        "execution",
        "lean-evaluation",
        "lean-evaluation",
    ]


def test_fresh_verification_supersedes_previous_diff_receipt(tmp_path: Path) -> None:
    loop_id = "impl-superseded-verification"
    test_source = "tests/superseded_verification.py"
    _seed_enabled_loop(tmp_path, loop_id)
    _commit_fixture(tmp_path, test_source, "print('verified')\n")
    _write(tmp_path, "src/app.py", "def build_future():\n    return object()\n")
    stale_ref = _record_targeted_verification(tmp_path, loop_id, test_source)

    first = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))
    assert first.status == "needs_fix"
    _write(tmp_path, "src/app.py", "def _build_future():\n    return object()\n")
    fresh_ref = _record_targeted_verification(tmp_path, loop_id, test_source)

    second = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))

    assert stale_ref != fresh_ref
    assert second.status == "ready", second
    assert second.evaluation_round == 2


def test_stale_verification_alone_forces_user_decision_at_second_round(
    tmp_path: Path,
) -> None:
    loop_id = "impl-stale-only-verification"
    test_source = "tests/stale_only_verification.py"
    _seed_enabled_loop(tmp_path, loop_id)
    _commit_fixture(tmp_path, test_source, "print('verified')\n")
    _write(tmp_path, "src/app.py", "def build_future():\n    return object()\n")
    _record_targeted_verification(tmp_path, loop_id, test_source)

    first = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))
    assert first.status == "needs_fix"
    _write(tmp_path, "src/app.py", "def _build_future():\n    return object()\n")

    second = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))
    report = LeanEvaluationReport.model_validate_json(
        (_lean_dir(tmp_path, loop_id) / "round-002" / "report.json").read_text("utf-8")
    )

    assert second.status == "needs_user"
    assert second.stop_reason.startswith("max_rounds_reached:")
    assert any(item.rule_id == "lean.targeted-verification" for item in report.findings)


def test_second_round_without_new_targeted_verification_needs_user(
    tmp_path: Path,
) -> None:
    loop_id = "impl-no-targeted-verification"
    _seed_enabled_loop(tmp_path, loop_id)
    _write(tmp_path, "src/app.py", "def build_future():\n    return object()\n")
    first = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))
    assert first.status == "needs_fix"
    _write(tmp_path, "src/app.py", "def _build_future():\n    return object()\n")
    second = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))
    report = LeanEvaluationReport.model_validate_json(
        (_lean_dir(tmp_path, loop_id) / "round-002" / "report.json").read_text("utf-8")
    )

    assert second.status == "needs_user"
    assert second.stop_reason.startswith("max_rounds_reached:")
    assert any(item.rule_id == "lean.targeted-verification" for item in report.findings)


def test_second_round_rejects_pointer_consistent_previous_report_tamper(
    tmp_path: Path,
) -> None:
    loop_id = "impl-previous-report-tamper"
    _seed_enabled_loop(tmp_path, loop_id)
    _write(tmp_path, "src/app.py", "def build_future():\n    return object()\n")
    first = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))
    assert first.status == "needs_fix"
    lean_dir = _lean_dir(tmp_path, loop_id)
    report_path = lean_dir / "round-001" / "report.json"
    report = LeanEvaluationReport.model_validate_json(report_path.read_text("utf-8"))
    forged = report.model_copy(update={"status": "passed", "findings": []})
    report_path.write_text(forged.model_dump_json(), encoding="utf-8")
    current_path = lean_dir / "current.json"
    current = json.loads(current_path.read_text("utf-8"))
    current["report_digest"] = stable_artifact_digest(forged)
    current_path.write_text(json.dumps(current), encoding="utf-8")
    _write(tmp_path, "src/app.py", "def _build_future():\n    return object()\n")

    second = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))

    assert second.status == "blocked"
    assert "previous lean" in second.blocker.lower()
    assert not (lean_dir / "round-002").exists()


def test_close_rejects_previous_report_changed_after_second_round(
    tmp_path: Path,
) -> None:
    loop_id = "impl-previous-history-close"
    test_source = "tests/previous_history.py"
    _seed_enabled_loop(tmp_path, loop_id)
    _commit_fixture(tmp_path, test_source, "print('verified')\n")
    _write(tmp_path, "src/app.py", "def build_future():\n    return object()\n")
    first = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))
    assert first.status == "needs_fix"
    _write(tmp_path, "src/app.py", "def _build_future():\n    return object()\n")
    _record_targeted_verification(tmp_path, loop_id, test_source)
    second = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))
    assert second.status == "ready"
    lean_dir = _lean_dir(tmp_path, loop_id)
    second_input = LeanEvaluationInput.model_validate_json(
        (lean_dir / "round-002" / "evaluation-input.json").read_text("utf-8")
    )
    assert second_input.previous_report_path.endswith("round-001/report.json")
    assert second_input.previous_report_digest
    assert second_input.previous_actionable_signatures
    report_path = lean_dir / "round-001" / "report.json"
    previous = LeanEvaluationReport.model_validate_json(report_path.read_text("utf-8"))
    report_path.write_text(
        previous.model_copy(update={"stop_reason": "tampered"}).model_dump_json(),
        encoding="utf-8",
    )

    blocker = validate_lean_close(tmp_path, loop_id)

    assert "previous report" in blocker.lower()
    assert "digest" in blocker.lower()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("previous_report_path", "forged/report.json"),
        ("previous_report_digest", "sha256:forged"),
        ("previous_verification_digest", "sha256:forged"),
        ("previous_actionable_signatures", []),
    ],
)
def test_close_rejects_previous_history_input_rebinding(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    loop_id = f"impl-history-{field.replace('_', '-')}"
    test_source = "tests/history_binding.py"
    _seed_enabled_loop(tmp_path, loop_id)
    _commit_fixture(tmp_path, test_source, "print('verified')\n")
    _write(tmp_path, "src/app.py", "def build_future():\n    return object()\n")
    assert run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id)).status == (
        "needs_fix"
    )
    _write(tmp_path, "src/app.py", "def _build_future():\n    return object()\n")
    _record_targeted_verification(tmp_path, loop_id, test_source)
    assert run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id)).status == (
        "ready"
    )
    current_path = _lean_dir(tmp_path, loop_id) / "current.json"
    current = json.loads(current_path.read_text("utf-8"))
    input_path = tmp_path / current["input_path"]
    evaluation_input = json.loads(input_path.read_text("utf-8"))
    evaluation_input[field] = replacement
    rebound = LeanEvaluationInput.model_validate(evaluation_input)
    input_path.write_text(rebound.model_dump_json(), encoding="utf-8")
    current["input_digest"] = stable_artifact_digest(rebound)
    current_path.write_text(json.dumps(current), encoding="utf-8")

    blocker = validate_lean_close(tmp_path, loop_id)

    assert "previous" in blocker.lower()


def test_close_rejects_current_pointer_rebound_from_canonical_report(
    tmp_path: Path,
) -> None:
    loop_id = "impl-rebound-current-report"
    _seed_enabled_loop(tmp_path, loop_id)
    _write(tmp_path, "src/app.py", "def _small():\n    return 1\n")
    result = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))
    assert result.status == "ready"
    lean_dir = _lean_dir(tmp_path, loop_id)
    current_path = lean_dir / "current.json"
    current = json.loads(current_path.read_text("utf-8"))
    rebound_path = lean_dir / "rebound-report.json"
    rebound_path.write_bytes((tmp_path / current["report_path"]).read_bytes())
    current["report_path"] = rebound_path.relative_to(tmp_path).as_posix()
    current_path.write_text(json.dumps(current), encoding="utf-8")

    blocker = validate_lean_close(tmp_path, loop_id)

    assert "canonical artifact paths" in blocker.lower()


def test_unexecuted_verification_command_forces_user_decision_at_second_round(
    tmp_path: Path,
) -> None:
    loop_id = "impl-unexecuted-verification"
    _seed_enabled_loop(tmp_path, loop_id)
    _write(tmp_path, "src/app.py", "def build_future():\n    return object()\n")
    first = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))
    assert first.status == "needs_fix"
    _write(tmp_path, "src/app.py", "def _build_future():\n    return object()\n")
    recorded = record_implementation_progress(
        ImplementationRecordOptions(
            root=tmp_path,
            loop_id=loop_id,
            task_id="T11",
            status="done",
            verification=("THIS COMMAND WAS NEVER RUN",),
        )
    )
    assert recorded.status == "ready"

    second = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))

    assert second.status == "needs_user"
    assert second.stop_reason.startswith("max_rounds_reached:")


def test_targeted_verification_requires_a_bound_test_source(tmp_path: Path) -> None:
    loop_id = "impl-verification-subject"
    _seed_enabled_loop(tmp_path, loop_id)
    _write(tmp_path, "src/app.py", "def _small():\n    return 1\n")
    _write(tmp_path, "tests/subject.py", "print('subject passed')\n")

    missing = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(sys.executable, "-c", "print('unrelated')"),
        )
    )
    unrelated = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(sys.executable, "-c", "print('unrelated')"),
            test_source_ref="tests/subject.py",
        )
    )
    mentioned_only = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(
                sys.executable,
                "-c",
                "print('tests/subject.py')",
            ),
            test_source_ref="tests/subject.py",
        )
    )
    argument_only = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(
                sys.executable,
                "-c",
                "print('no test executed')",
                "tests/subject.py",
            ),
            test_source_ref="tests/subject.py",
        )
    )
    _write(tmp_path, "tests/other.py", "def test_other():\n    assert True\n")
    ignored = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--ignore",
                "tests/subject.py",
                "tests/other.py",
            ),
            test_source_ref="tests/subject.py",
        )
    )
    _write(tmp_path, "tests/subject.py", "def test_subject():\n    assert False\n")
    filtered = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/subject.py",
                "tests/other.py",
                "-k",
                "other",
            ),
            test_source_ref="tests/subject.py",
        )
    )
    deselected = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/subject.py",
                "tests/other.py",
                "--deselect",
                "tests/subject.py::test_subject",
            ),
            test_source_ref="tests/subject.py",
        )
    )
    _write(tmp_path, "pytest.ini", "[pytest]\naddopts = --collect-only\n")
    collect_only = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/subject.py::test_subject",
            ),
            test_source_ref="tests/subject.py",
        )
    )
    _write(tmp_path, "tests/subject.py", "def test_subject():\n    assert True\n")
    pytest_node = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/subject.py::test_subject",
            ),
            test_source_ref="tests/subject.py",
        )
    )

    assert missing.status == "blocked"
    assert "test source" in missing.blocker.lower()
    assert unrelated.status == "blocked"
    assert "runner" in unrelated.blocker.lower()
    assert mentioned_only.status == "blocked"
    assert "runner" in mentioned_only.blocker.lower()
    assert argument_only.status == "blocked"
    assert "runner" in argument_only.blocker.lower()
    assert ignored.status == "blocked"
    assert "runner" in ignored.blocker.lower()
    assert filtered.status == "blocked"
    assert "runner" in filtered.blocker.lower()
    assert deselected.status == "blocked"
    assert "runner" in deselected.blocker.lower()
    assert collect_only.status == "blocked"
    assert pytest_node.status == "ready"


def test_documented_whole_file_pytest_targets_execute(tmp_path: Path) -> None:
    loop_id = "impl-whole-file-pytest"
    _seed_enabled_loop(tmp_path, loop_id)
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    test_source = "tests/whole_file_probe.py"
    _write(tmp_path, test_source, "def test_value():\n    assert True\n")
    pytest_executable = shutil.which("pytest")
    assert pytest_executable is not None

    commands = (
        (sys.executable, "-m", "pytest", test_source, "-q"),
        (sys.executable, "-S", "-m", "pytest", test_source, "-q"),
        (sys.executable, "-S", test_source),
        (pytest_executable, test_source, "-q"),
    )
    for command in commands:
        result = run_lean_command(
            LeanExecutionOptions(
                root=tmp_path,
                loop_id=loop_id,
                purpose="targeted-verification",
                command_argv=command,
                test_source_ref=test_source,
            )
        )
        assert result.status == "ready", result
        assert result.command_argv.count("-S") == 1
        if Path(command[0]).name.lower().startswith(("pytest", "py.test")):
            assert result.command_argv[1:4] == ["-S", "-m", "pytest"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX runner shim regression")
def test_python_named_script_cannot_forge_execution_receipt(tmp_path: Path) -> None:
    root = tmp_path / "project"
    runner = tmp_path / "bin" / "python-fake"
    root.mkdir()
    _seed_enabled_loop(root, "impl-fake-python")
    test_source = "tests/failing_probe.py"
    _write(root, test_source, "raise SystemExit(9)\n")
    _write(runner.parent, runner.name, "#!/bin/sh\nprintf 'sha256:%064d\\n' 0\n")
    runner.chmod(0o755)

    result = run_lean_command(
        LeanExecutionOptions(
            root=root,
            loop_id="impl-fake-python",
            purpose="targeted-verification",
            command_argv=(str(runner), test_source),
            test_source_ref=test_source,
        )
    )

    assert result.status == "blocked"


@pytest.mark.skipif(os.name == "nt", reason="POSIX console-script regression")
def test_fake_pytest_entrypoint_cannot_ignore_failing_source(tmp_path: Path) -> None:
    root = tmp_path / "project"
    runner = tmp_path / "bin" / "pytest"
    root.mkdir()
    _seed_enabled_loop(root, "impl-fake-pytest")
    test_source = "tests/failing_test.py"
    _write(root, test_source, "def test_failure():\n    assert False\n")
    _write(runner.parent, runner.name, f"#!{sys.executable}\nraise SystemExit(0)\n")
    runner.chmod(0o755)

    result = run_lean_command(
        LeanExecutionOptions(
            root=root,
            loop_id="impl-fake-pytest",
            purpose="targeted-verification",
            command_argv=(str(runner), test_source, "-q"),
            test_source_ref=test_source,
        )
    )

    assert result.status == "blocked"


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell shim regression")
def test_shell_pytest_shim_resolves_selected_python(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "project"
    bin_dir = tmp_path / "bin"
    python_shim = bin_dir / "python"
    pytest_shim = bin_dir / "pytest"
    root.mkdir()
    _seed_enabled_loop(root, "impl-shell-pytest")
    test_source = "tests/passing_test.py"
    _write(root, test_source, "def test_value():\n    assert True\n")
    _write(
        bin_dir,
        python_shim.name,
        f'#!/bin/sh\nexec {str(sys.executable)!r} "$@"\n',
    )
    _write(
        bin_dir,
        pytest_shim.name,
        '#!/bin/sh\nexec "$(dirname "$0")/python" -m pytest "$@"\n',
    )
    python_shim.chmod(0o755)
    pytest_shim.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join((str(bin_dir), os.environ["PATH"])))

    result = run_lean_command(
        LeanExecutionOptions(
            root=root,
            loop_id="impl-shell-pytest",
            purpose="targeted-verification",
            command_argv=(str(pytest_shim), test_source, "-q"),
            test_source_ref=test_source,
        )
    )

    assert result.status == "ready", result


def test_controlled_execution_ignores_external_python_import_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "project"
    external = tmp_path / "external"
    loop_id = "impl-isolated-python-path"
    root.mkdir()
    _seed_enabled_loop(root, loop_id)
    _write(root, "subject.py", "VALUE = 'selected-source-view'\n")
    _write(external, "subject.py", "VALUE = 'external-checkout'\n")
    test_source = "tests/import_path_probe.py"
    _write(
        root,
        test_source,
        "from subject import VALUE\nassert VALUE == 'selected-source-view', VALUE\n",
    )
    monkeypatch.setenv("PYTHONPATH", str(external))
    monkeypatch.setenv("PYTHONHOME", str(external))

    result = run_lean_command(
        LeanExecutionOptions(
            root=root,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(sys.executable, test_source),
            test_source_ref=test_source,
        )
    )

    assert result.status == "ready", result


def test_patch_execution_precedes_editable_src_mapping(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    loop_id = "impl-editable-src-isolation"
    _seed_enabled_loop(tmp_path, loop_id)
    package = "src/example_subject/__init__.py"
    test_source = "tests/editable_import_probe.py"
    _write(tmp_path, package, "VALUE = 'base'\n")
    _write(
        tmp_path,
        test_source,
        "from example_subject import VALUE\nassert VALUE == 'selected-patch', VALUE\n",
    )
    _git(tmp_path, "add", package, test_source)
    _git(tmp_path, "commit", "-m", "add editable import probe")
    _write(tmp_path, package, "VALUE = 'selected-patch'\n")
    patch_file = "selected-source.patch"
    patch = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", "--no-textconv"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    ).stdout
    (tmp_path / patch_file).write_bytes(patch)
    _write(tmp_path, package, "VALUE = 'live-worktree'\n")
    runner = _editable_runner(tmp_path)

    result = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(str(runner), test_source),
            test_source_ref=test_source,
            source_kind="patch",
            patch_file=patch_file,
        )
    )

    assert result.status == "ready", result
    assert result.exit_code == 0, result


def test_patch_execution_does_not_fallback_after_selected_module_deletion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    loop_id = "impl-editable-deletion-isolation"
    _seed_enabled_loop(tmp_path, loop_id)
    package = tmp_path / "src/example_subject/__init__.py"
    test_source = "tests/editable_deletion_probe.py"
    _write(tmp_path, str(package.relative_to(tmp_path)), "VALUE = 'base'\n")
    _write(
        tmp_path,
        test_source,
        "try:\n"
        "    from example_subject import VALUE\n"
        "except ModuleNotFoundError:\n"
        "    print('SELECTED_DELETION_RESPECTED')\n"
        "else:\n"
        "    raise AssertionError(VALUE)\n",
    )
    _git(tmp_path, "add", "src/example_subject/__init__.py", test_source)
    _git(tmp_path, "commit", "-m", "add editable deletion probe")
    package.unlink()
    patch_file = "selected-deletion.patch"
    patch = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", "--no-textconv"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    ).stdout
    (tmp_path / patch_file).write_bytes(patch)
    _write(tmp_path, str(package.relative_to(tmp_path)), "VALUE = 'live-worktree'\n")
    runner = _editable_runner(tmp_path)

    result = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(str(runner), test_source),
            test_source_ref=test_source,
            source_kind="patch",
            patch_file=patch_file,
        )
    )

    assert result.status == "ready", result
    assert "SELECTED_DELETION_RESPECTED" in (tmp_path / result.output_path).read_text(
        "utf-8"
    )


def test_patch_deletion_blocks_runner_site_installed_project_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    loop_id = "impl-installed-copy-isolation"
    _seed_enabled_loop(tmp_path, loop_id)
    package = tmp_path / "src/example_subject/__init__.py"
    test_source = "tests/installed_copy_deletion_probe.py"
    _write(tmp_path, str(package.relative_to(tmp_path)), "VALUE = 'base'\n")
    _write(
        tmp_path,
        test_source,
        "try:\n"
        "    from example_subject import VALUE\n"
        "except ModuleNotFoundError:\n"
        "    print('INSTALLED_COPY_BLOCKED')\n"
        "else:\n"
        "    raise AssertionError(VALUE)\n",
    )
    _git(tmp_path, "add", "src/example_subject/__init__.py", test_source)
    _git(tmp_path, "commit", "-m", "add installed copy deletion probe")
    package.unlink()
    patch_file = "selected-installed-deletion.patch"
    patch = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", "--no-textconv"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    ).stdout
    (tmp_path / patch_file).write_bytes(patch)
    runner, purelib = _runner_venv(tmp_path / "installed-runner")
    installed = purelib / "example_subject/__init__.py"
    installed.parent.mkdir()
    installed.write_text("VALUE = 'installed-runner-copy'\n", encoding="utf-8")

    result = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(str(runner), test_source),
            test_source_ref=test_source,
            source_kind="patch",
            patch_file=patch_file,
        )
    )

    assert result.status == "ready", result
    assert "INSTALLED_COPY_BLOCKED" in (tmp_path / result.output_path).read_text(
        "utf-8"
    )


def test_patch_deletion_blocks_runner_namespace_package_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    loop_id = "impl-installed-namespace-isolation"
    _seed_enabled_loop(tmp_path, loop_id)
    kept = "src/example_subject/kept.py"
    removed = "src/example_subject/removed.py"
    test_source = "tests/installed_namespace_deletion_probe.py"
    _write(tmp_path, kept, "VALUE = 'selected-namespace'\n")
    _write(tmp_path, removed, "VALUE = 'base'\n")
    _write(
        tmp_path,
        test_source,
        "from example_subject.kept import VALUE\n"
        "assert VALUE == 'selected-namespace', VALUE\n"
        "try:\n"
        "    from example_subject.removed import VALUE as removed_value\n"
        "except ModuleNotFoundError:\n"
        "    print('SELECTED_NAMESPACE_DELETION_RESPECTED')\n"
        "else:\n"
        "    raise AssertionError(removed_value)\n",
    )
    _git(tmp_path, "add", kept, removed, test_source)
    _git(tmp_path, "commit", "-m", "add namespace deletion probe")
    (tmp_path / removed).unlink()
    patch_file = "selected-namespace-deletion.patch"
    patch = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", "--no-textconv"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    ).stdout
    (tmp_path / patch_file).write_bytes(patch)
    runner, purelib = _runner_venv(tmp_path / "namespace-runner")
    installed = purelib / "example_subject/removed.py"
    installed.parent.mkdir()
    installed.write_text("VALUE = 'installed-runner-copy'\n", encoding="utf-8")

    result = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(str(runner), test_source),
            test_source_ref=test_source,
            source_kind="patch",
            patch_file=patch_file,
        )
    )

    assert result.status == "ready", result
    assert "SELECTED_NAMESPACE_DELETION_RESPECTED" in (
        tmp_path / result.output_path
    ).read_text("utf-8")


def test_namespace_dependency_sibling_survives_while_removed_member_is_blocked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    loop_id = "impl-namespace-dependency"
    _seed_enabled_loop(tmp_path, loop_id)
    project_part = "src/shared_namespace/project_part.py"
    removed = "src/shared_namespace/removed.py"
    test_source = "tests/namespace_dependency_probe.py"
    _write(tmp_path, project_part, "VALUE = 'project-part'\n")
    _write(tmp_path, removed, "VALUE = 'base'\n")
    _write(
        tmp_path,
        test_source,
        "from shared_namespace.project_part import VALUE as project_value\n"
        "from shared_namespace.dependency_part import VALUE as dependency_value\n"
        "assert project_value == 'project-part', project_value\n"
        "assert dependency_value == 'dependency-part', dependency_value\n"
        "try:\n"
        "    from shared_namespace.removed import VALUE as removed_value\n"
        "except ModuleNotFoundError:\n"
        "    print('NAMESPACE_DEPENDENCY_AND_DELETION_VALID')\n"
        "else:\n"
        "    raise AssertionError(removed_value)\n",
    )
    _git(tmp_path, "add", project_part, removed, test_source)
    _git(tmp_path, "commit", "-m", "add namespace dependency probe")
    (tmp_path / removed).unlink()
    patch_file = "selected-namespace-dependency.patch"
    patch = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", "--no-textconv"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    ).stdout
    (tmp_path / patch_file).write_bytes(patch)
    runner, purelib = _runner_venv(tmp_path / "namespace-dependency-runner")
    installed = purelib / "shared_namespace"
    installed.mkdir()
    (installed / "dependency_part.py").write_text(
        "VALUE = 'dependency-part'\n", encoding="utf-8"
    )
    (installed / "removed.py").write_text(
        "VALUE = 'installed-fallback'\n", encoding="utf-8"
    )

    result = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(str(runner), test_source),
            test_source_ref=test_source,
            source_kind="patch",
            patch_file=patch_file,
        )
    )

    assert result.status == "ready", result
    assert "NAMESPACE_DEPENDENCY_AND_DELETION_VALID" in (
        tmp_path / result.output_path
    ).read_text("utf-8")


def test_patch_deletion_blocks_runner_copy_of_native_extension(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    loop_id = "impl-native-extension-isolation"
    _seed_enabled_loop(tmp_path, loop_id)
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    native_module = f"src/example_native{suffix}"
    test_source = "tests/native_extension_deletion_probe.py"
    _write(tmp_path, native_module, "base-native-placeholder\n")
    _write(
        tmp_path,
        test_source,
        "try:\n"
        "    from example_native import VALUE\n"
        "except ModuleNotFoundError:\n"
        "    print('NATIVE_EXTENSION_DELETION_RESPECTED')\n"
        "else:\n"
        "    raise AssertionError(VALUE)\n",
    )
    _git(tmp_path, "add", native_module, test_source)
    _git(tmp_path, "commit", "-m", "add native extension deletion probe")
    (tmp_path / native_module).unlink()
    patch_file = "selected-native-deletion.patch"
    patch = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", "--no-textconv"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    ).stdout
    (tmp_path / patch_file).write_bytes(patch)
    runner, purelib = _runner_venv(tmp_path / "native-runner")
    (purelib / "example_native.py").write_text(
        "VALUE = 'installed-fallback'\n",
        encoding="utf-8",
    )

    result = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(str(runner), test_source),
            test_source_ref=test_source,
            source_kind="patch",
            patch_file=patch_file,
        )
    )

    assert result.status == "ready", result
    assert "NATIVE_EXTENSION_DELETION_RESPECTED" in (
        tmp_path / result.output_path
    ).read_text("utf-8")


def test_path_pth_dependency_is_preserved_by_controlled_execution(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    dependency_root = tmp_path / "path-dependency"
    root.mkdir()
    _write(dependency_root, "review_path_dep/__init__.py", "VALUE = 'path-pth'\n")
    loop_id = "impl-path-pth-dependency"
    _seed_enabled_loop(root, loop_id)
    test_source = "tests/path_pth_probe.py"
    _write(root, test_source, "from review_path_dep import VALUE\nprint(VALUE)\n")
    runner, purelib = _runner_venv(tmp_path / "path-pth-runner")
    (purelib / "review-path-dep.pth").write_text(
        str(dependency_root) + "\n",
        encoding="utf-8",
    )
    normal = subprocess.run(
        [str(runner), "-c", "from review_path_dep import VALUE; print(VALUE)"],
        capture_output=True,
        check=True,
        text=True,
    )

    result = run_lean_command(
        LeanExecutionOptions(
            root=root,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(str(runner), test_source),
            test_source_ref=test_source,
        )
    )

    assert normal.stdout.strip() == "path-pth"
    assert result.status == "ready", result


def test_pep660_editable_dependency_is_preserved_by_controlled_execution(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    dependency_root = tmp_path / "editable-dependency"
    root.mkdir()
    _write(dependency_root, "review_editable_dep/__init__.py", "VALUE = 'pep660'\n")
    loop_id = "impl-pep660-dependency"
    _seed_enabled_loop(root, loop_id)
    test_source = "tests/pep660_probe.py"
    _write(root, test_source, "from review_editable_dep import VALUE\nprint(VALUE)\n")
    runner, purelib = _runner_venv(tmp_path / "pep660-runner")
    _install_pep660_fixture(purelib, dependency_root)
    normal = subprocess.run(
        [str(runner), "-c", "from review_editable_dep import VALUE; print(VALUE)"],
        capture_output=True,
        check=True,
        text=True,
    )

    result = run_lean_command(
        LeanExecutionOptions(
            root=root,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(str(runner), test_source),
            test_source_ref=test_source,
        )
    )

    assert normal.stdout.strip() == "pep660"
    assert result.status == "ready", result


@pytest.mark.parametrize("binding", ["path-pth", "pep660"])
def test_project_local_venv_editable_dependency_is_preserved(
    tmp_path: Path,
    binding: str,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    loop_id = f"impl-local-venv-{binding}"
    _seed_enabled_loop(root, loop_id)
    _write(root, ".gitignore", ".venv/\n")
    _git(root, "add", ".gitignore")
    _git(root, "commit", "-m", "ignore local environment")
    test_source = "tests/local_venv_dependency_probe.py"
    _write(root, test_source, "from review_editable_dep import VALUE\nprint(VALUE)\n")
    runner, purelib = _runner_venv(root / ".venv")
    dependency_root = root / ".venv/src/editable-dependency"
    _write(dependency_root, "review_editable_dep/__init__.py", "VALUE = 'local'\n")
    if binding == "path-pth":
        (purelib / "review-local-dep.pth").write_text(
            str(dependency_root) + "\n", encoding="utf-8"
        )
    else:
        _install_pep660_fixture(purelib, dependency_root)
    normal = subprocess.run(
        [str(runner), "-c", "from review_editable_dep import VALUE; print(VALUE)"],
        capture_output=True,
        check=True,
        text=True,
    )

    result = run_lean_command(
        LeanExecutionOptions(
            root=root,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(str(runner), test_source),
            test_source_ref=test_source,
        )
    )

    assert normal.stdout.strip() == "local"
    assert result.status == "ready", result


def test_system_site_venv_preserves_base_dependencies(tmp_path: Path) -> None:
    root = tmp_path / "project"
    runner_root = tmp_path / "system-site-runner"
    root.mkdir()
    loop_id = "impl-system-site-runner"
    _seed_enabled_loop(root, loop_id)
    test_source = "tests/system_site_probe.py"
    _write(root, test_source, "import pip\nprint(pip.__version__)\n")
    runner, _ = _runner_venv(runner_root, system_site_packages=True)
    normal = subprocess.run(
        [str(runner), "-c", "import pip; print(pip.__version__)"],
        capture_output=True,
        check=True,
        text=True,
    )

    result = run_lean_command(
        LeanExecutionOptions(
            root=root,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(str(runner), test_source),
            test_source_ref=test_source,
        )
    )

    assert normal.stdout.strip()
    assert result.status == "ready", result
    assert len(lean_code_runner._runner_site_packages(runner)) >= 2


def test_controlled_execution_captures_utf8_stdout(tmp_path: Path) -> None:
    loop_id = "impl-unicode-output"
    _seed_enabled_loop(tmp_path, loop_id)
    test_source = "tests/unicode_output.py"
    _write(tmp_path, test_source, "print('中文😀')\n")

    result = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(sys.executable, test_source),
            test_source_ref=test_source,
        )
    )

    assert result.status == "ready", result
    assert "中文😀" in (tmp_path / result.output_path).read_text("utf-8")


def test_controlled_environment_rebuilds_python_startup_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    for name in (
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
    ):
        monkeypatch.setenv(name, "external-value")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path.parent / "external-path"))

    environment = controlled_execution_environment("python-script", tmp_path)

    assert environment["PYTHONPATH"] == str(tmp_path.resolve())
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTHONUTF8"] == "1"
    assert environment["PYTHONIOENCODING"] == "utf-8"
    assert all(
        name not in environment
        for name in ("PYTHONHOME", "PYTHONINSPECT", "PYTHONSTARTUP", "PYTHONUSERBASE")
    )


def test_controlled_environment_keeps_legacy_single_argument_api(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k external")

    environment = controlled_execution_environment("python-module:pytest")

    assert environment["PYTEST_ADDOPTS"] == ""
    assert environment["PYTHONPATH"]


def test_controlled_environment_maps_project_pythonpath_to_selected_view(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    execution_root = tmp_path / "selected-view"
    external = tmp_path / "external"
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join((str(project_root / "src"), str(external))),
    )

    environment = controlled_execution_environment(
        "python-script",
        execution_root,
        project_root,
    )

    assert environment["PYTHONPATH"].split(os.pathsep) == [
        str(execution_root / "src"),
        str(execution_root),
    ]


def test_controlled_environment_rejects_external_src_symlink(
    tmp_path: Path,
) -> None:
    execution_root = tmp_path / "selected-view"
    external = tmp_path / "external-src"
    execution_root.mkdir()
    external.mkdir()
    try:
        (execution_root / "src").symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(ValueError, match="escapes the selected source view"):
        controlled_execution_environment(
            "python-script",
            execution_root,
            tmp_path / "project",
        )


def test_controlled_environment_rejects_external_module_symlink(
    tmp_path: Path,
) -> None:
    execution_root = tmp_path / "selected-view"
    source_root = execution_root / "src"
    external = tmp_path / "external-module.py"
    source_root.mkdir(parents=True)
    external.write_text("VALUE = 'external'\n", encoding="utf-8")
    try:
        (source_root / "subject.py").symlink_to(external)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(ValueError, match="escapes the selected source view"):
        controlled_execution_environment(
            "python-script",
            execution_root,
            tmp_path / "project",
        )


def test_execution_rejects_external_data_symlink_without_receipt(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    external = tmp_path / "external-config.json"
    root.mkdir()
    external.write_text('{"source": "outside"}\n', encoding="utf-8")
    loop_id = "impl-external-data-link"
    _seed_enabled_loop(root, loop_id)
    test_source = "tests/data_link_probe.py"
    _write(
        root,
        test_source,
        "from pathlib import Path\n"
        "print(Path('data/config.json').read_text(encoding='utf-8'))\n",
    )
    _git(root, "add", test_source)
    _git(root, "commit", "-m", "add data link probe")
    (root / "data").mkdir()
    try:
        (root / "data/config.json").symlink_to(external)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    result = run_lean_command(
        LeanExecutionOptions(
            root=root,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(sys.executable, test_source),
            test_source_ref=test_source,
        )
    )

    assert result.status == "blocked"
    assert "selected source view" in result.blocker
    assert result.receipt_path == ""


def test_controlled_environment_converts_symlink_loop_to_blocker(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "selected-view/src"
    source_root.mkdir(parents=True)
    try:
        (source_root / "a.py").symlink_to("b.py")
        (source_root / "b.py").symlink_to("a.py")
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(ValueError, match="selected source view"):
        controlled_execution_environment(
            "python-script",
            tmp_path / "selected-view",
            tmp_path / "project",
        )


def test_execution_blocks_symlink_loop_without_receipt(tmp_path: Path) -> None:
    loop_id = "impl-symlink-loop"
    _seed_enabled_loop(tmp_path, loop_id)
    test_source = "tests/symlink_loop_probe.py"
    _write(tmp_path, test_source, "print('must not execute')\n")
    _git(tmp_path, "add", test_source)
    _git(tmp_path, "commit", "-m", "add symlink loop probe")
    source_root = tmp_path / "src"
    source_root.mkdir()
    try:
        (source_root / "a.py").symlink_to("b.py")
        (source_root / "b.py").symlink_to("a.py")
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    result = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(sys.executable, test_source),
            test_source_ref=test_source,
        )
    )

    assert result.status == "blocked"
    assert "selected source view" in result.blocker
    assert result.receipt_path == ""


def test_controlled_environment_rejects_external_root_module_symlink(
    tmp_path: Path,
) -> None:
    execution_root = tmp_path / "selected-view"
    external = tmp_path / "external-root-module.py"
    execution_root.mkdir()
    external.write_text("VALUE = 'external'\n", encoding="utf-8")
    try:
        (execution_root / "subject.py").symlink_to(external)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(ValueError, match="selected source view"):
        controlled_execution_environment(
            "python-script",
            execution_root,
            tmp_path / "project",
        )


def test_controlled_environment_rejects_external_custom_import_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    execution_root = tmp_path / "selected-view"
    custom_root = execution_root / "lib"
    external = tmp_path / "external-custom-module.py"
    custom_root.mkdir(parents=True)
    external.write_text("VALUE = 'external'\n", encoding="utf-8")
    try:
        (custom_root / "subject.py").symlink_to(external)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    monkeypatch.setenv("PYTHONPATH", str(project_root / "lib"))

    with pytest.raises(ValueError, match="selected source view"):
        controlled_execution_environment(
            "python-script",
            execution_root,
            project_root,
        )


def test_controlled_environment_rejects_uppercase_python_symlink(
    tmp_path: Path,
) -> None:
    execution_root = tmp_path / "selected-view"
    external = tmp_path / "external-uppercase.py"
    execution_root.mkdir()
    external.write_text("VALUE = 'external'\n", encoding="utf-8")
    try:
        (execution_root / "subject.PY").symlink_to(external)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(ValueError, match="selected source view"):
        controlled_execution_environment(
            "python-script",
            execution_root,
            tmp_path / "project",
        )


def test_controlled_environment_allows_dangling_internal_python_symlink(
    tmp_path: Path,
) -> None:
    execution_root = tmp_path / "selected-view"
    source_root = execution_root / "src"
    source_root.mkdir(parents=True)
    try:
        (source_root / "generated_binding.py").symlink_to("generated_binding_impl.py")
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    environment = controlled_execution_environment(
        "python-script",
        execution_root,
        tmp_path / "project",
    )

    assert environment["PYTHONPATH"].split(os.pathsep)[0] == str(source_root)


def test_controlled_environment_rejects_dangling_external_python_symlink(
    tmp_path: Path,
) -> None:
    execution_root = tmp_path / "selected-view"
    source_root = execution_root / "src"
    source_root.mkdir(parents=True)
    external = tmp_path / "missing-external.py"
    try:
        (source_root / "external_binding.py").symlink_to(external)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(ValueError, match="selected source view"):
        controlled_execution_environment(
            "python-script",
            execution_root,
            tmp_path / "project",
        )


def test_controlled_environment_allows_dangling_internal_package_symlink(
    tmp_path: Path,
) -> None:
    execution_root = tmp_path / "selected-view"
    source_root = execution_root / "src"
    source_root.mkdir(parents=True)
    package_link = source_root / "generated_pkg"
    try:
        package_link.symlink_to("generated_pkg_impl", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    environment = controlled_execution_environment(
        "python-script",
        execution_root,
        tmp_path / "project",
    )
    package_target = source_root / "generated_pkg_impl"
    package_target.mkdir()
    (package_target / "__init__.py").write_text(
        "VALUE = 'selected-view'\n",
        encoding="utf-8",
    )
    imported = subprocess.run(
        [sys.executable, "-c", "import generated_pkg; print(generated_pkg.VALUE)"],
        capture_output=True,
        check=True,
        cwd=execution_root,
        env=environment,
        text=True,
    )

    assert imported.stdout.strip() == "selected-view"


def test_controlled_environment_rejects_dangling_external_package_symlink(
    tmp_path: Path,
) -> None:
    execution_root = tmp_path / "selected-view"
    source_root = execution_root / "src"
    source_root.mkdir(parents=True)
    external = tmp_path / "missing-external-package"
    try:
        (source_root / "escaped_pkg").symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(ValueError, match="selected source view"):
        controlled_execution_environment(
            "python-script",
            execution_root,
            tmp_path / "project",
        )


def test_execution_blocks_dangling_external_package_without_receipt(
    tmp_path: Path,
) -> None:
    loop_id = "impl-dangling-package"
    _seed_enabled_loop(tmp_path, loop_id)
    test_source = "tests/dangling_package_probe.py"
    _write(tmp_path, test_source, "print('must not execute')\n")
    _git(tmp_path, "add", test_source)
    _git(tmp_path, "commit", "-m", "add dangling package probe")
    source_root = tmp_path / "src"
    source_root.mkdir()
    try:
        (source_root / "escaped_pkg").symlink_to(
            tmp_path.parent / "missing-external-package",
            target_is_directory=True,
        )
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    result = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(sys.executable, test_source),
            test_source_ref=test_source,
        )
    )

    assert result.status == "blocked"
    assert "selected source view" in result.blocker
    assert result.receipt_path == ""


def test_controlled_environment_rejects_external_file_import_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    execution_root = tmp_path / "selected-view"
    external_archive = tmp_path / "external-modules.zip"
    project_root.mkdir()
    execution_root.mkdir()
    (project_root / "vendor-import").write_bytes(b"live project import root")
    with zipfile.ZipFile(external_archive, "w") as archive:
        archive.writestr("outside_probe.py", "VALUE = 'outside'\n")
    try:
        (execution_root / "vendor-import").symlink_to(external_archive)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    monkeypatch.setenv("PYTHONPATH", str(project_root / "vendor-import"))

    with pytest.raises(ValueError, match="selected source view"):
        controlled_execution_environment(
            "python-script",
            execution_root,
            project_root,
        )


def test_controlled_environment_allows_internal_file_import_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    execution_root = tmp_path / "selected-view"
    archive_path = execution_root / "archives/modules.zip"
    project_root.mkdir()
    archive_path.parent.mkdir(parents=True)
    (project_root / "vendor-import").write_bytes(b"live project import root")
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("inside_probe.py", "VALUE = 'selected-view'\n")
    try:
        (execution_root / "vendor-import").symlink_to(archive_path)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    monkeypatch.setenv("PYTHONPATH", str(project_root / "vendor-import"))

    environment = controlled_execution_environment(
        "python-script",
        execution_root,
        project_root,
    )
    imported = subprocess.run(
        [sys.executable, "-c", "import inside_probe; print(inside_probe.VALUE)"],
        capture_output=True,
        check=True,
        cwd=execution_root,
        env=environment,
        text=True,
    )

    assert imported.stdout.strip() == "selected-view"


def test_import_tree_prunes_resolved_directory_aliases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    execution_root = (tmp_path / "selected-view").resolve()
    inside = execution_root / "inside"
    regular = execution_root / "regular"
    inside.mkdir(parents=True)
    regular.mkdir()
    try:
        (execution_root / "alias").symlink_to(inside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    observed: list[tuple[str, ...]] = []

    def _walk(current, *, followlinks):
        assert followlinks is False
        current = Path(current).resolve()
        if current == execution_root:
            directories = ["inside", "alias", "regular"]
            yield str(current), directories, []
            observed.append(tuple(directories))
        else:
            yield str(current), [], []

    monkeypatch.setattr(lean_code_environment.os, "walk", _walk)

    controlled_execution_environment(
        "python-script",
        execution_root,
        tmp_path / "project",
    )

    assert observed == [("inside", "regular")]


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_controlled_environment_rejects_external_windows_junction(
    tmp_path: Path,
) -> None:
    execution_root = tmp_path / "selected-view"
    external = tmp_path / "external-src"
    execution_root.mkdir()
    external.mkdir()
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(execution_root / "src"), str(external)],
        capture_output=True,
        check=True,
        text=True,
    )

    with pytest.raises(ValueError, match="selected source view"):
        controlled_execution_environment(
            "python-script",
            execution_root,
            tmp_path / "project",
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_controlled_environment_bounds_internal_windows_junction_loop(
    tmp_path: Path,
) -> None:
    execution_root = tmp_path / "selected-view"
    source_root = execution_root / "src"
    package_root = source_root / "package"
    package_root.mkdir(parents=True)
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(package_root / "back"), str(source_root)],
        capture_output=True,
        check=True,
        text=True,
    )

    environment = controlled_execution_environment(
        "python-script",
        execution_root,
        tmp_path / "project",
    )

    assert environment["PYTHONPATH"].split(os.pathsep)[0] == str(source_root)


def test_toolchain_fingerprint_changes_with_installed_distribution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Distribution:
        def __init__(self, version: str) -> None:
            self.version = version
            self.metadata = {"Name": "example-runner"}

        def read_text(self, filename: str) -> str | None:
            return (
                "example-runner.py,sha256=bound,1\n" if filename == "RECORD" else None
            )

    monkeypatch.setattr(
        importlib.metadata,
        "distributions",
        lambda: [Distribution("1.0")],
    )
    first = execution_toolchain(tmp_path, sys.executable)
    monkeypatch.setattr(
        importlib.metadata,
        "distributions",
        lambda: [Distribution("2.0")],
    )

    second = execution_toolchain(tmp_path, sys.executable)

    assert first[0] != second[0]
    assert first[3] != second[3]


def test_receipt_fingerprint_tracks_actual_runner_environment(tmp_path: Path) -> None:
    root = tmp_path / "project"
    runner_root = tmp_path / "runner"
    root.mkdir()
    loop_id = "impl-runner-environment"
    _seed_enabled_loop(root, loop_id)
    test_source = "tests/runner_environment_probe.py"
    _write(root, test_source, "print('runner environment verified')\n")
    venv.EnvBuilder(
        with_pip=False,
        symlinks=sys.platform != "win32",
    ).create(runner_root)
    runner = (
        runner_root / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else runner_root / "bin" / "python"
    )
    purelib_result = subprocess.run(
        [
            str(runner),
            "-I",
            "-c",
            "import sysconfig; print(sysconfig.get_paths()['purelib'])",
        ],
        capture_output=True,
        check=True,
        text=True,
    )
    purelib = Path(purelib_result.stdout.strip())
    first_dist = purelib / "probe_dep-1.0.dist-info"
    _write(first_dist, "METADATA", "Name: probe-dep\nVersion: 1.0\n")
    _write(first_dist, "RECORD", "probe_dep.py,sha256=first,1\n")

    result = run_lean_command(
        LeanExecutionOptions(
            root=root,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(str(runner), test_source),
            test_source_ref=test_source,
        )
    )
    assert result.status == "ready", result
    shutil.rmtree(first_dist)
    second_dist = purelib / "probe_dep-2.0.dist-info"
    _write(second_dist, "METADATA", "Name: probe-dep\nVersion: 2.0\n")
    _write(second_dist, "RECORD", "probe_dep.py,sha256=second,1\n")

    validated, issue = validate_execution_receipt(
        root,
        result.receipt_path,
        expected_digest=result.receipt_digest,
    )

    assert validated is None
    assert "dependency environment" in issue


def test_receipt_fingerprint_tracks_project_pythonpath_selection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    loop_id = "impl-project-pythonpath"
    _seed_enabled_loop(tmp_path, loop_id)
    _write(tmp_path, "src_a/subject.py", "VALUE = 'selected'\n")
    _write(tmp_path, "src_b/subject.py", "VALUE = 'other'\n")
    test_source = "tests/project_pythonpath_probe.py"
    _write(
        tmp_path,
        test_source,
        "from subject import VALUE\nassert VALUE == 'selected', VALUE\n",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "src_a"))
    result = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(sys.executable, test_source),
            test_source_ref=test_source,
        )
    )
    assert result.status == "ready", result
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "src_b"))

    validated, issue = validate_execution_receipt(
        tmp_path,
        result.receipt_path,
        expected_digest=result.receipt_digest,
    )

    assert validated is None
    assert "dependency environment" in issue


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink root regression")
def test_receipt_validation_canonicalizes_equivalent_project_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "project"
    alias = tmp_path / "project-alias"
    root.mkdir()
    _seed_enabled_loop(root, "impl-root-alias")
    _write(root, "src/subject.py", "VALUE = 1\n")
    test_source = "tests/root_alias_probe.py"
    _write(root, test_source, "from subject import VALUE\nassert VALUE == 1\n")
    alias.symlink_to(root, target_is_directory=True)
    monkeypatch.setenv("PYTHONPATH", str(alias / "src"))
    result = run_lean_command(
        LeanExecutionOptions(
            root=alias,
            loop_id="impl-root-alias",
            purpose="targeted-verification",
            command_argv=(sys.executable, test_source),
            test_source_ref=test_source,
        )
    )
    assert result.status == "ready", result

    validated, issue = validate_execution_receipt(
        alias,
        result.receipt_path,
        expected_digest=result.receipt_digest,
    )

    assert validated is not None, issue
    assert issue == ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX alternate runner regression")
def test_distribution_probe_timeout_fails_closed_without_escaping(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = tmp_path / "python-alternate"
    runner.symlink_to(sys.executable)
    _seed_enabled_loop(tmp_path, "impl-probe-timeout")
    test_source = "tests/probe_timeout.py"
    _write(tmp_path, test_source, "print('verified')\n")
    result = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id="impl-probe-timeout",
            purpose="targeted-verification",
            command_argv=(str(runner), test_source),
            test_source_ref=test_source,
        )
    )
    assert result.status == "ready", result

    def _timeout(*args, **kwargs):
        del args, kwargs
        raise subprocess.TimeoutExpired(str(runner), timeout=30)

    monkeypatch.setattr(lean_code_runner.subprocess, "run", _timeout)

    validated, issue = validate_execution_receipt(
        tmp_path,
        result.receipt_path,
        expected_digest=result.receipt_digest,
    )

    assert validated is None
    assert "dependency environment is unavailable" in issue


@pytest.mark.skipif(os.name == "nt", reason="POSIX relative runner regression")
def test_relative_runner_link_keeps_stable_receipt_identity(tmp_path: Path) -> None:
    runner = tmp_path / "python-alternate"
    runner.symlink_to(sys.executable)
    loop_id = "impl-relative-runner"
    _seed_enabled_loop(tmp_path, loop_id)
    test_source = "tests/relative_runner.py"
    _write(tmp_path, test_source, "print('relative-runner-verified')\n")

    result = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=("./python-alternate", test_source),
            test_source_ref=test_source,
        )
    )

    assert result.status == "ready", result
    validated, issue = validate_execution_receipt(
        tmp_path,
        result.receipt_path,
        expected_digest=result.receipt_digest,
    )
    assert validated is not None, issue
    assert issue == ""
    assert validated.command_argv[0] == str(runner)


def test_pytest_option_shaped_path_cannot_impersonate_test_source(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "tests/subject.py", "def test_subject():\n    assert False\n")
    (tmp_path / "-kpassing").mkdir()
    option_path = "-kpassing/../tests/subject.py"

    adapter = resolve_execution_adapter(
        tmp_path,
        ("pytest", option_path, "-q"),
        "tests/subject.py",
    )

    assert adapter == ""


def test_direct_red_execution_rejects_non_assertion_signature(
    tmp_path: Path,
) -> None:
    loop_id = "impl-direct-invalid-signature"
    _seed_enabled_loop(tmp_path, loop_id)
    _write(tmp_path, "src/app.py", "VALUE = 0\n")
    test_source = "tests/direct_invalid_signature.py"
    signature = "plain-non-assertion-signature"
    _write(tmp_path, test_source, f"print({signature!r})\nraise SystemExit(1)\n")

    result = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="regression-red",
            command_argv=(sys.executable, test_source),
            test_source_ref=test_source,
            failure_signature=signature,
        )
    )

    assert result.status == "blocked"
    assert "assertion:" in result.blocker
    execution_root = _lean_dir(tmp_path, loop_id) / "executions"
    assert not list(execution_root.glob("*/receipt.json"))


def test_lean_execution_blocks_when_unstaged_source_is_empty(tmp_path: Path) -> None:
    loop_id = "impl-empty-unstaged-source"
    _seed_enabled_loop(tmp_path, loop_id)
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    test_source = "tests/empty_unstaged_probe.py"
    signature = "assertion:empty-unstaged-source"
    _write(
        tmp_path,
        test_source,
        f"print({signature!r})\nraise SystemExit(1)\n",
    )
    _git(tmp_path, "add", "src/app.py", test_source)

    for purpose, failure_signature in (
        ("targeted-verification", ""),
        ("regression-red", signature),
    ):
        result = run_lean_command(
            LeanExecutionOptions(
                root=tmp_path,
                loop_id=loop_id,
                purpose=purpose,
                command_argv=(sys.executable, test_source),
                test_source_ref=test_source,
                failure_signature=failure_signature,
            )
        )
        assert result.status == "blocked"
        assert "source snapshot" in result.blocker.lower()

    execution_root = _lean_dir(tmp_path, loop_id) / "executions"
    assert not list(execution_root.glob("*/receipt.json"))


def test_verification_receipt_from_another_loop_cannot_advance_round(
    tmp_path: Path,
) -> None:
    _seed_enabled_loop(tmp_path, "impl-receipt-source", write_spec=False)
    _seed_enabled_loop(
        tmp_path,
        "impl-receipt-target",
        initialize_repo=False,
    )
    _commit_fixture(
        tmp_path,
        "tests/cross_loop.py",
        "print('verified elsewhere')\n",
    )
    _write(tmp_path, "src/app.py", "def build_future():\n    return object()\n")
    first = run_lean_check(
        LeanCheckOptions(root=tmp_path, loop_id="impl-receipt-target")
    )
    assert first.status == "needs_fix"
    _write(tmp_path, "src/app.py", "def _build_future():\n    return object()\n")
    verified = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id="impl-receipt-source",
            purpose="targeted-verification",
            command_argv=(sys.executable, "tests/cross_loop.py"),
            test_source_ref="tests/cross_loop.py",
        )
    )
    assert verified.status == "ready"
    recorded = record_implementation_progress(
        ImplementationRecordOptions(
            root=tmp_path,
            loop_id="impl-receipt-target",
            task_id="T11",
            status="done",
            evidence=(verified.receipt_path,),
        )
    )
    assert recorded.status == "ready"

    second = run_lean_check(
        LeanCheckOptions(root=tmp_path, loop_id="impl-receipt-target")
    )

    assert second.status == "blocked"
    assert "loop" in second.blocker.lower()


def test_tampered_targeted_verification_receipt_blocks_second_round(
    tmp_path: Path,
) -> None:
    loop_id = "impl-tampered-verification"
    _seed_enabled_loop(tmp_path, loop_id)
    _commit_fixture(tmp_path, "tests/tamper_probe.py", "print('verified')\n")
    _write(tmp_path, "src/app.py", "def build_future():\n    return object()\n")
    first = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))
    assert first.status == "needs_fix"
    _write(tmp_path, "src/app.py", "def _build_future():\n    return object()\n")
    verified = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(sys.executable, "tests/tamper_probe.py"),
            test_source_ref="tests/tamper_probe.py",
        )
    )
    assert verified.status == "ready"
    recorded = record_implementation_progress(
        ImplementationRecordOptions(
            root=tmp_path,
            loop_id=loop_id,
            task_id="T11",
            status="done",
            evidence=(verified.receipt_path,),
        )
    )
    assert recorded.status == "ready"
    (tmp_path / verified.output_path).write_text("forged\n", encoding="utf-8")

    second = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))

    assert second.status == "blocked"
    assert "receipt" in second.blocker.lower()


def test_same_required_finding_twice_enters_needs_user(tmp_path: Path) -> None:
    _seed_enabled_loop(tmp_path, "impl-stuck")
    _write(tmp_path, "src/app.py", "def build_future():\n    return object()\n")

    run_lean_check(LeanCheckOptions(root=tmp_path, loop_id="impl-stuck"))
    second = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id="impl-stuck"))

    assert second.status == "needs_user"
    assert second.loop_status == LoopStatus.NEEDS_USER
    assert second.stop_reason.startswith("max_rounds_reached:")
    third = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id="impl-stuck"))
    assert third.status == "blocked"
    assert "maximum of 2" in third.blocker


def test_new_actionable_finding_at_final_round_enters_needs_user(
    tmp_path: Path,
) -> None:
    loop_id = "impl-final-round-new-finding"
    _seed_enabled_loop(tmp_path, loop_id)
    _commit_fixture(tmp_path, "tests/final_round_probe.py", "print('verified')\n")
    _write(tmp_path, "src/app.py", "def build_future():\n    return object()\n")

    first = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))
    assert first.status == "needs_fix"
    _write(tmp_path, "src/app.py", "def _build_future():\n    return object()\n")
    _write(tmp_path, "src/unrelated.py", "VALUE = 1\n")
    _record_targeted_verification(
        tmp_path,
        loop_id,
        "tests/final_round_probe.py",
    )

    second = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))
    second_report = LeanEvaluationReport.model_validate_json(
        (_lean_dir(tmp_path, loop_id) / "round-002" / "report.json").read_text("utf-8")
    )

    assert second.status == "needs_user"
    assert second.loop_status == LoopStatus.NEEDS_USER
    current_signatures = sorted(
        item.stable_signature
        for item in second_report.findings
        if item.rule_id == "lean.scope-drift"
    )
    assert current_signatures
    assert second.stop_reason == "max_rounds_reached:" + ",".join(current_signatures)
    assert "run lean-check again" not in second.next_action.lower()
    third = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))
    assert third.status == "blocked"
    assert "maximum of 2" in third.blocker


def test_advisory_only_can_close_but_source_change_makes_report_stale(
    tmp_path: Path,
) -> None:
    _seed_enabled_loop(tmp_path, "impl-advisory")
    body = "\n".join("    value += 1" for _ in range(51))
    _write(
        tmp_path,
        "src/app.py",
        f"def _large():\n    value = 0\n{body}\n    return value\n",
    )
    result = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id="impl-advisory"))

    assert result.status == "ready"
    assert result.advisory_count == 1
    assert validate_lean_close(tmp_path, "impl-advisory") == ""
    with (tmp_path / "src" / "app.py").open("a", encoding="utf-8") as stream:
        stream.write("\n")
    assert "stale" in validate_lean_close(tmp_path, "impl-advisory").lower()


def test_report_mode_required_finding_is_visible_but_does_not_block_close(
    tmp_path: Path,
) -> None:
    loop_id = "impl-report-mode"
    _seed_enabled_loop(tmp_path, loop_id)
    _write(
        tmp_path,
        ".ai-sdlc/project/config/loop-policy.yaml",
        "lean_enforcement_mode: report\n",
    )
    _git(tmp_path, "add", ".ai-sdlc/project/config/loop-policy.yaml")
    _git(tmp_path, "commit", "-m", "configure report mode")
    _write(tmp_path, "src/app.py", "def build_future():\n    return object()\n")

    result = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))

    assert result.status == "ready"
    assert result.required_count == 1
    assert validate_lean_close(tmp_path, loop_id) == ""


def test_report_mode_required_findings_remain_non_blocking_at_final_round(
    tmp_path: Path,
) -> None:
    loop_id = "impl-report-mode-final-round"
    _seed_enabled_loop(tmp_path, loop_id)
    _write(
        tmp_path,
        ".ai-sdlc/project/config/loop-policy.yaml",
        "lean_enforcement_mode: report\n",
    )
    _git(tmp_path, "add", ".ai-sdlc/project/config/loop-policy.yaml")
    _git(tmp_path, "commit", "-m", "configure report mode")
    _write(tmp_path, "src/app.py", "def build_future():\n    return object()\n")

    first = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))
    second = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))

    assert first.status == "ready"
    assert second.status == "ready"
    assert second.loop_status == LoopStatus.PASSED
    assert second.stop_reason == ""
    assert second.required_count >= 1


def test_controlled_red_green_receipts_satisfy_bugfix_gate(tmp_path: Path) -> None:
    loop_id = "impl-regression-receipt"
    _seed_enabled_loop(
        tmp_path,
        loop_id,
        work_type=WorkType.PRODUCTION_ISSUE,
        declared_scope=["src/app.py", "tests/regression_probe.py"],
    )
    signature = "assertion:expected-1-got-0"
    _write(tmp_path, "src/app.py", "def _value():\n    return 0\n")
    _write(
        tmp_path,
        "tests/regression_probe.py",
        "\n".join(
            (
                "from pathlib import Path",
                "source = Path('src/app.py').read_text(encoding='utf-8')",
                "if 'return 1' not in source:",
                f"    print({signature!r})",
                "    raise SystemExit(1)",
                "print('regression passed')",
                "",
            )
        ),
    )
    common = {
        "root": tmp_path,
        "loop_id": loop_id,
        "test_id": "value-regression",
        "test_symbol": "tests.regression_probe",
        "command_argv": (sys.executable, "tests/regression_probe.py"),
        "test_source_ref": "tests/regression_probe.py",
        "failure_signature": signature,
    }
    red = capture_regression_phase(LeanRegressionOptions(phase="red", **common))
    assert red.status == "ready", red
    _write(tmp_path, "src/app.py", "def _value():\n    return 1\n")
    green = capture_regression_phase(LeanRegressionOptions(phase="green", **common))
    assert green.status == "ready", green

    result = run_lean_check(
        LeanCheckOptions(
            root=tmp_path,
            loop_id=loop_id,
            regression_evidence_paths=(green.evidence_path,),
        )
    )

    assert result.status == "ready", result
    assert validate_lean_close(tmp_path, loop_id) == ""
    evidence = json.loads((tmp_path / green.evidence_path).read_text("utf-8"))
    (tmp_path / evidence["red_output_ref"]).write_text("forged\n", encoding="utf-8")
    assert "regression" in validate_lean_close(tmp_path, loop_id).lower()


def test_malformed_lean_report_fails_closed(tmp_path: Path) -> None:
    _seed_enabled_loop(tmp_path, "impl-malformed")
    _write(tmp_path, "src/app.py", "def _small():\n    return 1\n")
    run_lean_check(LeanCheckOptions(root=tmp_path, loop_id="impl-malformed"))
    current = json.loads(
        (_lean_dir(tmp_path, "impl-malformed") / "current.json").read_text("utf-8")
    )
    (tmp_path / current["report_path"]).write_text("{broken", encoding="utf-8")

    blocker = validate_lean_close(tmp_path, "impl-malformed")

    assert "malformed" in blocker.lower()


def test_legacy_loop_without_capability_marker_keeps_old_close_contract(
    tmp_path: Path,
) -> None:
    _seed_enabled_loop(tmp_path, "impl-legacy", enabled=False)

    assert validate_lean_close(tmp_path, "impl-legacy") == ""


def test_current_pointer_resolves_lean_check_without_explicit_loop_id(
    tmp_path: Path,
) -> None:
    _seed_enabled_loop(tmp_path, "impl-current")
    _write(tmp_path, "src/app.py", "def _small():\n    return 1\n")

    result = run_lean_check(LeanCheckOptions(root=tmp_path))

    assert result.loop_id == "impl-current"
    assert result.report_path.endswith("lean/round-001/report.json")
    assert result.requires_model is False
    assert result.writes_code is False


def test_recorded_enabled_implementation_waits_for_lean_review(tmp_path: Path) -> None:
    _seed_enabled_loop(tmp_path, "impl-record", progress_done=False)

    result = record_implementation_progress(
        ImplementationRecordOptions(
            root=tmp_path,
            loop_id="impl-record",
            task_id="T11",
            status="done",
            evidence=("src/app.py",),
        )
    )

    assert result.loop_status == LoopStatus.NEEDS_REVIEW
    assert result.next_action == "Run ai-sdlc loop implementation lean-check."


def test_implementation_close_blocks_stale_lean_then_accepts_fresh_report(
    tmp_path: Path,
) -> None:
    _seed_enabled_loop(tmp_path, "impl-close")
    _write(tmp_path, "src/app.py", "def _small():\n    return 1\n")
    run_lean_check(LeanCheckOptions(root=tmp_path, loop_id="impl-close"))
    _write(tmp_path, "src/app.py", "def _small():\n    return 2\n")

    stale = close_implementation_loop(
        ImplementationCloseOptions(root=tmp_path, loop_id="impl-close", yes=True)
    )
    assert stale.closed is False
    assert stale.loop_status == LoopStatus.NEEDS_REVIEW
    assert "stale" in stale.blocker.lower()

    refreshed = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id="impl-close"))
    assert refreshed.loop_status == LoopStatus.PASSED
    closed = close_implementation_loop(
        ImplementationCloseOptions(root=tmp_path, loop_id="impl-close", yes=True)
    )
    assert closed.closed is True
    assert closed.loop_status == LoopStatus.CLOSED
    report = json.loads(
        implementation_artifacts(tmp_path, "impl-close").report_json_path.read_text(
            encoding="utf-8"
        )
    )
    assert report["status"] == LoopStatus.PASSED
    assert report["next_action"] == "Run ai-sdlc pr-review start."
    artifacts = implementation_artifacts(tmp_path, "impl-close")
    markdown = artifacts.report_md_path.read_text(encoding="utf-8")
    persisted_loop = json.loads(artifacts.loop_run_path.read_text(encoding="utf-8"))
    assert "- Status: `passed`" in markdown
    assert persisted_loop["status"] == LoopStatus.CLOSED
    execution_round = next(
        item for item in persisted_loop["rounds"] if item["round_kind"] == "execution"
    )
    assert execution_round["status"] == LoopStatus.CLOSED
    assert persisted_loop["rounds"][-1]["status"] == LoopStatus.PASSED


def test_runtime_persists_valid_exception_and_allows_risk_accepted_close(
    tmp_path: Path,
) -> None:
    _seed_enabled_loop(
        tmp_path,
        "impl-exception",
        work_type=WorkType.PRODUCTION_ISSUE,
    )
    _commit_fixture(
        tmp_path,
        "tests/exception_probe.py",
        "print('exception path verified')\n",
    )
    _write(tmp_path, "src/app.py", "def _value():\n    return 1\n")
    first = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id="impl-exception"))
    first_report = LeanEvaluationReport.model_validate_json(
        (tmp_path / first.report_path).read_text(encoding="utf-8")
    )
    first_snapshot = json.loads(
        (
            _lean_dir(tmp_path, "impl-exception") / "round-001" / "source-snapshot.json"
        ).read_text(encoding="utf-8")
    )
    finding = next(
        item
        for item in first_report.findings
        if item.rule_id == "lean.bugfix-regression"
    )
    evidence_ref = (
        ".ai-sdlc/loops/implementation/impl-exception/lean/exception-proof.txt"
    )
    _write(tmp_path, evidence_ref, "approved risk\n")
    evidence_digest = (
        "sha256:" + hashlib.sha256((tmp_path / evidence_ref).read_bytes()).hexdigest()
    )
    exception = LeanException(
        exception_id="EX-RUNTIME",
        rule_id=finding.rule_id,
        path="src/app.py",
        stable_signature=finding.stable_signature,
        reason="Reproduction environment is unavailable for this bounded delivery.",
        owner="release-owner",
        approver="quality-owner",
        evidence_refs=[evidence_ref],
        evidence_digests={evidence_ref: evidence_digest},
        scope=["src/app.py"],
        policy_digest=first_report.policy_digest,
        base_commit=first_snapshot["base_commit"],
        head_commit=first_snapshot["head_commit"],
        diff_hash=first_report.diff_hash,
        evaluation_digest=stable_artifact_digest(first_report),
        expires_at="2099-01-01T00:00:00Z",
    )
    exception_ref = ".ai-sdlc/loops/implementation/impl-exception/lean/exception.json"
    (tmp_path / exception_ref).write_text(
        exception.model_dump_json(),
        encoding="utf-8",
    )
    verified = run_lean_command(
        LeanExecutionOptions(
            root=tmp_path,
            loop_id="impl-exception",
            purpose="targeted-verification",
            command_argv=(sys.executable, "tests/exception_probe.py"),
            test_source_ref="tests/exception_probe.py",
        )
    )
    assert verified.status == "ready"
    recorded = record_implementation_progress(
        ImplementationRecordOptions(
            root=tmp_path,
            loop_id="impl-exception",
            task_id="T11",
            status="done",
            evidence=(verified.receipt_path,),
            verification=("uv run pytest tests/test_bug.py -q",),
        )
    )
    assert recorded.status == "ready"

    second = run_lean_check(
        LeanCheckOptions(
            root=tmp_path,
            loop_id="impl-exception",
            exception_paths=(exception_ref,),
        )
    )
    second_report = LeanEvaluationReport.model_validate_json(
        (_lean_dir(tmp_path, "impl-exception") / "round-002" / "report.json").read_text(
            encoding="utf-8"
        )
    )

    assert second.status == "ready", [
        (item.rule_id, item.resolution, item.claim) for item in second_report.findings
    ]
    assert second.risk_accepted is True
    evaluation_input = json.loads(
        (
            _lean_dir(tmp_path, "impl-exception")
            / "round-002"
            / "evaluation-input.json"
        ).read_text(encoding="utf-8")
    )
    assert evaluation_input["exception_refs"] == [exception_ref]
    assert evaluation_input["tasks_refs"] == ["T11"]
    assert validate_lean_close(tmp_path, "impl-exception") == ""

    (tmp_path / exception_ref).unlink()
    assert "exception" in validate_lean_close(tmp_path, "impl-exception").lower()
    (tmp_path / exception_ref).write_text(exception.model_dump_json(), encoding="utf-8")
    (tmp_path / evidence_ref).unlink()
    assert "evidence" in validate_lean_close(tmp_path, "impl-exception").lower()


def test_close_rejects_cross_bound_input_identity_tamper(tmp_path: Path) -> None:
    loop_id = "impl-input-identity"
    _seed_enabled_loop(tmp_path, loop_id)
    _write(tmp_path, "src/app.py", "def _small():\n    return 1\n")
    run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))
    current_path = _lean_dir(tmp_path, loop_id) / "current.json"
    current = json.loads(current_path.read_text("utf-8"))
    input_path = tmp_path / current["input_path"]
    evaluation_input = LeanEvaluationInput.model_validate_json(
        input_path.read_text("utf-8")
    ).model_copy(update={"work_item_id": "WI-OTHER"})
    input_path.write_text(evaluation_input.model_dump_json(), encoding="utf-8")
    current["input_digest"] = stable_artifact_digest(evaluation_input)
    current_path.write_text(json.dumps(current), encoding="utf-8")

    blocker = validate_lean_close(tmp_path, loop_id)

    assert "work item" in blocker.lower()


def test_close_rejects_task_and_acceptance_reference_rebinding(tmp_path: Path) -> None:
    mutations = {
        "tasks_refs": ["T-OTHER"],
        "acceptance_refs": ["specs/WI-OTHER/spec.md"],
    }
    for field, replacement in mutations.items():
        root = tmp_path / field
        root.mkdir()
        loop_id = f"impl-{field.replace('_', '-')}"
        _seed_enabled_loop(root, loop_id)
        _write(root, "src/app.py", "def _small():\n    return 1\n")
        run_lean_check(LeanCheckOptions(root=root, loop_id=loop_id))
        current_path = _lean_dir(root, loop_id) / "current.json"
        current = json.loads(current_path.read_text("utf-8"))
        input_path = root / current["input_path"]
        evaluation_input = LeanEvaluationInput.model_validate_json(
            input_path.read_text("utf-8")
        ).model_copy(update={field: replacement})
        input_path.write_text(evaluation_input.model_dump_json(), encoding="utf-8")
        current["input_digest"] = stable_artifact_digest(evaluation_input)
        current_path.write_text(json.dumps(current), encoding="utf-8")

        blocker = validate_lean_close(root, loop_id)

        assert field.split("_")[0] in blocker.lower(), (field, blocker)


def test_close_rejects_task_and_acceptance_byte_changes(tmp_path: Path) -> None:
    loop_id = "impl-input-bytes"
    _seed_enabled_loop(tmp_path, loop_id)
    _write(tmp_path, "src/app.py", "def _small():\n    return 1\n")
    run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))
    artifacts = implementation_artifacts(tmp_path, loop_id)
    tasks_before = artifacts.tasks_path.read_text("utf-8")
    tasks = json.loads(tasks_before)
    tasks["items"][0]["title"] = "changed after evaluation"
    artifacts.tasks_path.write_text(json.dumps(tasks), encoding="utf-8")

    tasks_blocker = validate_lean_close(tmp_path, loop_id)

    assert "tasks" in tasks_blocker.lower()
    artifacts.tasks_path.write_text(tasks_before, encoding="utf-8")
    spec_path = tmp_path / "specs/WI-LEAN/spec.md"
    spec_path.write_text("# Changed acceptance\n", encoding="utf-8")

    acceptance_blocker = validate_lean_close(tmp_path, loop_id)

    assert "acceptance" in acceptance_blocker.lower()


def test_lean_evaluation_rejects_tasks_changed_after_implementation_start(
    tmp_path: Path,
) -> None:
    loop_id = "impl-frozen-tasks"
    _seed_enabled_loop(tmp_path, loop_id)
    artifacts = implementation_artifacts(tmp_path, loop_id)
    tasks = json.loads(artifacts.tasks_path.read_text("utf-8"))
    tasks["items"][0]["required"] = False
    artifacts.tasks_path.write_text(json.dumps(tasks), encoding="utf-8")
    _write(tmp_path, "src/app.py", "def _small():\n    return 1\n")

    result = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))

    assert result.status == "blocked"
    assert "frozen" in result.blocker.lower()
    assert not (_lean_dir(tmp_path, loop_id) / "current.json").exists()


def test_lean_evaluation_allows_task_artifact_reformat_before_evaluation(
    tmp_path: Path,
) -> None:
    loop_id = "impl-reformatted-tasks"
    _seed_enabled_loop(tmp_path, loop_id)
    artifacts = implementation_artifacts(tmp_path, loop_id)
    tasks = json.loads(artifacts.tasks_path.read_text("utf-8"))
    artifacts.tasks_path.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=4, sort_keys=True),
        encoding="utf-8",
    )
    _write(tmp_path, "src/app.py", "def _small():\n    return 1\n")

    result = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))

    assert result.status == "ready"
    assert validate_lean_close(tmp_path, loop_id) == ""


def test_close_returns_blocker_for_malformed_task_artifact(tmp_path: Path) -> None:
    loop_id = "impl-malformed-tasks"
    _seed_enabled_loop(tmp_path, loop_id)
    _write(tmp_path, "src/app.py", "def _small():\n    return 1\n")
    run_lean_check(LeanCheckOptions(root=tmp_path, loop_id=loop_id))
    artifacts = implementation_artifacts(tmp_path, loop_id)
    artifacts.tasks_path.write_text("{broken", encoding="utf-8")

    blocker = validate_lean_close(tmp_path, loop_id)

    assert "tasks" in blocker.lower()
    assert "malformed" in blocker.lower()


def test_repeated_red_capture_does_not_leave_orphan_receipt(tmp_path: Path) -> None:
    loop_id = "impl-repeat-red"
    test_source = "tests/repeat_red.py"
    signature = "assertion:repeat-red"
    _seed_enabled_loop(tmp_path, loop_id)
    _write(tmp_path, "src/app.py", "VALUE = 0\n")
    _write(
        tmp_path,
        test_source,
        f"print({signature!r})\nraise SystemExit(1)\n",
    )
    options = LeanRegressionOptions(
        root=tmp_path,
        loop_id=loop_id,
        phase="red",
        test_id="repeat-red",
        command_argv=(sys.executable, test_source),
        test_source_ref=test_source,
        failure_signature=signature,
    )
    first = capture_regression_phase(options)
    execution_root = _lean_dir(tmp_path, loop_id) / "executions"
    receipts_before = list(execution_root.glob("*/receipt.json"))

    second = capture_regression_phase(options)
    receipts_after = list(execution_root.glob("*/receipt.json"))

    assert first.status == "ready"
    assert second.status == "blocked"
    assert receipts_after == receipts_before


def test_explicit_no_go_records_decision_and_enters_needs_user(tmp_path: Path) -> None:
    _seed_enabled_loop(tmp_path, "impl-no-go")
    _write(tmp_path, "src/app.py", "def build_future():\n    return object()\n")
    checked = run_lean_check(LeanCheckOptions(root=tmp_path, loop_id="impl-no-go"))
    evidence_ref = ".ai-sdlc/loops/implementation/impl-no-go/lean/no-go-proof.txt"
    _write(tmp_path, evidence_ref, "behavior would regress\n")

    result = record_lean_no_go(
        LeanNoGoOptions(
            root=tmp_path,
            loop_id="impl-no-go",
            reason="The only metric-reducing change would break public behavior.",
            owner="implementation-owner",
            repair_cost="behavioral regression and added indirection",
            expected_benefit="one advisory metric reduction",
            evidence_refs=(evidence_ref,),
        )
    )

    assert checked.status == "needs_fix"
    assert result.status == "needs_user"
    assert result.stop_reason.startswith("no_go:")
    decision_path = _lean_dir(tmp_path, "impl-no-go") / "no-go.json"
    decision = LeanNoGoDecision.model_validate_json(decision_path.read_text("utf-8"))
    assert decision.diff_hash == checked.diff_hash
    assert decision.reason.startswith("The only metric-reducing")
    assert decision.evidence_digests
    loop = _read_loop(tmp_path, "impl-no-go")
    assert loop.status == LoopStatus.NEEDS_USER
    assert loop.rounds[-1].round_kind == "lean-decision"


def test_no_go_rejects_stale_source_snapshot(tmp_path: Path) -> None:
    _seed_enabled_loop(tmp_path, "impl-stale-no-go")
    _write(tmp_path, "src/app.py", "def build_future():\n    return object()\n")
    run_lean_check(LeanCheckOptions(root=tmp_path, loop_id="impl-stale-no-go"))
    evidence_ref = ".ai-sdlc/loops/implementation/impl-stale-no-go/lean/no-go-proof.txt"
    _write(tmp_path, evidence_ref, "behavior would regress\n")
    _write(tmp_path, "src/app.py", "def build_future():\n    return 2\n")

    result = record_lean_no_go(
        LeanNoGoOptions(
            root=tmp_path,
            loop_id="impl-stale-no-go",
            reason="Metric-only change would regress behavior.",
            owner="implementation-owner",
            repair_cost="behavioral regression",
            expected_benefit="one metric reduction",
            evidence_refs=(evidence_ref,),
        )
    )

    assert result.status == "blocked"
    assert "stale" in result.blocker.lower()
    assert not (_lean_dir(tmp_path, "impl-stale-no-go") / "no-go.json").exists()


def test_source_snapshot_tamper_blocks_lean_close(tmp_path: Path) -> None:
    _seed_enabled_loop(tmp_path, "impl-snapshot-tamper")
    _write(tmp_path, "src/app.py", "def _small():\n    return 1\n")
    run_lean_check(LeanCheckOptions(root=tmp_path, loop_id="impl-snapshot-tamper"))
    snapshot_path = (
        _lean_dir(tmp_path, "impl-snapshot-tamper")
        / "round-001"
        / "source-snapshot.json"
    )
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload["changed_files"] = []
    payload["file_digests"] = {}
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    blocker = validate_lean_close(tmp_path, "impl-snapshot-tamper")

    assert "snapshot" in blocker.lower()
    assert "digest" in blocker.lower()


def test_implementation_input_profile_tamper_cannot_downgrade_lean(
    tmp_path: Path,
) -> None:
    _seed_enabled_loop(tmp_path, "impl-input-tamper")
    _write(tmp_path, "src/app.py", "def _small():\n    return 1\n")
    run_lean_check(LeanCheckOptions(root=tmp_path, loop_id="impl-input-tamper"))
    input_path = implementation_artifacts(tmp_path, "impl-input-tamper").input_path
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    payload["quality_profiles"] = []
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    result = close_implementation_loop(
        ImplementationCloseOptions(root=tmp_path, loop_id="impl-input-tamper", yes=True)
    )

    assert result.closed is False
    assert "input" in result.blocker.lower()
    assert "digest" in result.blocker.lower()


def test_complete_lean_artifact_chain_tamper_blocks_close(tmp_path: Path) -> None:
    for artifact_name in ("input", "findings", "policy"):
        root = tmp_path / artifact_name
        loop_id = f"impl-{artifact_name}-tamper"
        root.mkdir()
        _seed_enabled_loop(root, loop_id)
        _write(root, "src/app.py", "def _small():\n    return 1\n")
        run_lean_check(LeanCheckOptions(root=root, loop_id=loop_id))
        pointer = json.loads(
            (_lean_dir(root, loop_id) / "current.json").read_text("utf-8")
        )
        path = root / pointer[f"{artifact_name}_path"]
        payload = json.loads(path.read_text("utf-8"))
        if artifact_name == "policy":
            payload["file_line_budget"] += 1
        else:
            payload["loop_id"] += "-tampered"
        path.write_text(json.dumps(payload), encoding="utf-8")

        blocker = validate_lean_close(root, loop_id)

        assert "digest" in blocker.lower(), (artifact_name, blocker)


def _editable_runner(root: Path) -> Path:
    runner, purelib = _runner_venv(root / "editable-runner")
    Path(purelib, "example-subject-editable.pth").write_text(
        str(root / "src") + "\n",
        encoding="utf-8",
    )
    return runner


def _install_pep660_fixture(purelib: Path, dependency_root: Path) -> None:
    package = "review_editable_dep"
    finder = purelib / "__editable___review_editable_dep_finder.py"
    finder.write_text(
        "from importlib.util import spec_from_file_location\n"
        "from pathlib import Path\n"
        "import sys\n"
        f"MAPPING = {{{package!r}: {str(dependency_root / package)!r}}}\n"
        "class Finder:\n"
        "    @classmethod\n"
        "    def find_spec(cls, fullname, path=None, target=None):\n"
        "        location = MAPPING.get(fullname)\n"
        "        if location is None:\n"
        "            return None\n"
        "        package_path = Path(location)\n"
        "        return spec_from_file_location(\n"
        "            fullname,\n"
        "            package_path / '__init__.py',\n"
        "            submodule_search_locations=[str(package_path)],\n"
        "        )\n"
        "def install():\n"
        "    if Finder not in sys.meta_path:\n"
        "        sys.meta_path.append(Finder)\n",
        encoding="utf-8",
    )
    (purelib / "review-editable-dep.pth").write_text(
        "import __editable___review_editable_dep_finder; "
        "__editable___review_editable_dep_finder.install()\n",
        encoding="utf-8",
    )
    dist_info = purelib / "review_editable_dep-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: review-editable-dep\nVersion: 1.0\n",
        encoding="utf-8",
    )
    (dist_info / "top_level.txt").write_text(package + "\n", encoding="utf-8")


def _runner_venv(
    runner_root: Path,
    *,
    system_site_packages: bool = False,
) -> tuple[Path, Path]:
    venv.EnvBuilder(
        with_pip=False,
        symlinks=os.name != "nt",
        system_site_packages=system_site_packages,
    ).create(runner_root)
    runner = (
        runner_root / "Scripts/python.exe"
        if os.name == "nt"
        else runner_root / "bin/python"
    )
    purelib = subprocess.run(
        [
            str(runner),
            "-I",
            "-c",
            "import sysconfig; print(sysconfig.get_paths()['purelib'])",
        ],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    return runner, Path(purelib)


def _seed_enabled_loop(
    root: Path,
    loop_id: str,
    *,
    enabled: bool = True,
    progress_done: bool = True,
    work_type: WorkType = WorkType.NEW_REQUIREMENT,
    declared_scope: list[str] | None = None,
    initialize_repo: bool = True,
    write_spec: bool = True,
) -> None:
    if initialize_repo:
        _init_repo(root)
    artifacts = implementation_artifacts(root, loop_id)
    store = LoopArtifactStore(root)
    store.create_loop_run_dir(loop_id, loop_type=LoopType.IMPLEMENTATION.value)
    task = ImplementationTaskItem(
        task_id="T11",
        required=True,
        files=["src/app.py"],
        acceptance=["AC-1"],
    )
    impl_input = ImplementationInput(
        loop_id=loop_id,
        work_item_id="WI-LEAN",
        work_item_path="specs/WI-LEAN",
        spec_path="specs/WI-LEAN/spec.md",
        plan_path="specs/WI-LEAN/plan.md",
        tasks_path="specs/WI-LEAN/tasks.md",
        design_contract_loop_id="design-lean",
        work_type=work_type,
        quality_profiles=["lean-code"] if enabled else [],
        declared_scope=declared_scope or ["src/app.py"],
        tasks_digest=_content_digest([task.model_dump(mode="json")]),
        acceptance_digest=_content_digest(task.acceptance),
    )
    loop = LoopRun(
        loop_id=loop_id,
        loop_type=LoopType.IMPLEMENTATION,
        status=LoopStatus.NEEDS_REVIEW,
        work_item_id="WI-LEAN",
        input_digest=stable_artifact_digest(impl_input),
        current_round=1,
        rounds=[LoopRound(round_number=1, status=LoopStatus.NEEDS_REVIEW)],
    )
    store.write_json_artifact(artifacts.input_path, impl_input)
    store.write_json_artifact(
        artifacts.tasks_path,
        ImplementationTasks(
            loop_id=loop_id,
            work_item_id="WI-LEAN",
            items=[task],
        ),
    )
    store.write_json_artifact(
        artifacts.progress_path,
        ImplementationProgress(
            loop_id=loop_id,
            work_item_id="WI-LEAN",
            tasks=[
                ImplementationTaskProgress(
                    task_id="T11",
                    status=(
                        ImplementationTaskStatus.DONE
                        if progress_done
                        else ImplementationTaskStatus.PENDING
                    ),
                    evidence=["src/app.py"] if progress_done else [],
                )
            ],
        ),
    )
    if write_spec:
        _write(root, impl_input.spec_path, "# Acceptance\n\n- AC-1\n")
        _git(root, "add", impl_input.spec_path)
        _git(root, "commit", "-m", "acceptance fixture")
    store.write_json_artifact(artifacts.loop_run_path, loop)
    store.write_json_artifact(
        artifacts.pointer_path,
        ImplementationCurrentPointer(
            loop_id=loop_id,
            loop_run_path=artifacts.loop_run_path.relative_to(root).as_posix(),
        ),
    )


def _record_targeted_verification(
    root: Path,
    loop_id: str,
    test_source: str,
) -> str:
    verified = run_lean_command(
        LeanExecutionOptions(
            root=root,
            loop_id=loop_id,
            purpose="targeted-verification",
            command_argv=(sys.executable, test_source),
            test_source_ref=test_source,
        )
    )
    assert verified.status == "ready", verified
    recorded = record_implementation_progress(
        ImplementationRecordOptions(
            root=root,
            loop_id=loop_id,
            task_id="T11",
            status="done",
            evidence=(verified.receipt_path,),
        )
    )
    assert recorded.status == "ready", recorded
    return verified.receipt_path


def _lean_dir(root: Path, loop_id: str) -> Path:
    return root / ".ai-sdlc" / "loops" / "implementation" / loop_id / "lean"


def _read_loop(root: Path, loop_id: str) -> LoopRun:
    path = implementation_artifacts(root, loop_id).loop_run_path
    return LoopRun.model_validate_json(path.read_text("utf-8"))


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


def _content_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _commit_fixture(root: Path, relative: str, content: str) -> None:
    _write(root, relative, content)
    _git(root, "add", relative)
    _git(root, "commit", "-m", f"add {Path(relative).name} fixture")


def _git(root: Path, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    if result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", errors="replace"))
