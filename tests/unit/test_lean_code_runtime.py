"""Lean Code artifact、两轮状态与 Implementation close freshness 测试。"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

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
from ai_sdlc.core.lean_code_environment import resolve_execution_adapter
from ai_sdlc.core.lean_code_execution import LeanExecutionOptions, run_lean_command
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


def test_stale_verification_alone_cannot_satisfy_second_round(tmp_path: Path) -> None:
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

    assert second.status == "needs_fix"
    assert any(item.rule_id == "lean.targeted-verification" for item in report.findings)


def test_second_round_without_new_targeted_verification_needs_fix(
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

    assert second.status == "needs_fix"
    assert any(item.rule_id == "lean.targeted-verification" for item in report.findings)


def test_unexecuted_verification_command_does_not_advance_second_round(
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

    assert second.status == "needs_fix"


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
