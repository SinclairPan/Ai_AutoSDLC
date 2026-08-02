"""Lean identity 对抗用例的 Python 真值与静态结果夹具。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ai_sdlc.core.lean_code_evaluator import (
    LeanEvaluationOptions,
    evaluate_lean_code,
)
from ai_sdlc.core.lean_code_models import LeanPolicy
from ai_sdlc.core.source_snapshot import SourceSnapshotOptions, build_source_snapshot
from ai_sdlc.models.work import WorkType

_API_SOURCE = """_CALL_COUNT = 0

def build_value():
    global _CALL_COUNT
    _CALL_COUNT += 1
    return 1

def _call_count():
    return _CALL_COUNT
"""


def assert_python_truth_and_lineage(
    root: Path,
    *,
    caller_source: str,
    expected_calls: int,
    supporting_files: dict[str, str] | None = None,
) -> None:
    """先执行普通 Python，再要求 Lean lineage 与真实调用结果一致。"""

    _init_repo(root)
    _write(root, "src/api.py", _API_SOURCE)
    for path, content in (supporting_files or {}).items():
        _write(root, path, content)
    _write(
        root,
        "src/callers.py",
        caller_source
        + "\nfrom src.api import _call_count\n"
        + "print(f'CALL_COUNT={_call_count()}')\n",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "src.callers"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines()[-1] == f"CALL_COUNT={expected_calls}"

    snapshot = build_source_snapshot(
        SourceSnapshotOptions(root=root, source_kind="local-unstaged")
    )
    report = evaluate_lean_code(
        LeanEvaluationOptions(
            root=root,
            loop_id="identity-adversarial",
            work_item_id="WI-IDENTITY",
            work_type=WorkType.NEW_REQUIREMENT,
            source_snapshot=snapshot,
            policy=LeanPolicy(),
            declared_scope=("src/*.py",),
            task_refs=("specs/WI-IDENTITY/tasks.md",),
            acceptance_refs=("AC-IDENTITY",),
        )
    )
    target = next(
        function
        for metric in report.metrics.files
        for function in metric.functions
        if metric.path == "src/api.py" and function.symbol == "build_value"
    )
    if expected_calls:
        assert target.invocation_boundary == "dynamic-reference", (
            target.invocation_boundary,
            "dynamic-reference",
            target.invocation_evidence,
        )
    else:
        assert target.invocation_boundary == "", (
            target.invocation_boundary,
            "not-executed",
            target.invocation_evidence,
        )


def _init_repo(root: Path) -> None:
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    _write(root, "README.md", "# Test\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")


def _write(root: Path, relative: str, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
