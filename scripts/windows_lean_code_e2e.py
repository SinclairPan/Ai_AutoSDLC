"""使用远端安装后的公开 CLI 重放 Windows Lean Code 用户闭环。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from windows_lean_code_e2e_support import (
    apply_business_change,
    assert_value,
    git_command,
    run_command,
    sha256_file,
    write_formal_docs,
    write_initial_project,
    write_regression_probe,
)


@dataclass(frozen=True)
class _Journey:
    cli: str
    project_root: Path
    evidence_root: Path

    def run(
        self,
        command: list[str],
        evidence_name: str,
        *,
        expect_json: bool = True,
    ) -> dict[str, object] | str:
        return run_command(
            command,
            cwd=self.project_root,
            evidence_path=self.evidence_root / evidence_name,
            expect_json=expect_json,
        )


def _prepare(context: _Journey) -> None:
    root = context.project_root
    if any(root.iterdir()) if root.exists() else False:
        raise AssertionError(f"Lean E2E 项目目录必须为空: {root}")
    root.mkdir(parents=True, exist_ok=True)
    context.evidence_root.mkdir(parents=True, exist_ok=True)
    write_initial_project(root)
    git_command(root, context.evidence_root, "init", "--initial-branch=main")
    git_command(root, context.evidence_root, "config", "user.email", "e2e@example.com")
    git_command(root, context.evidence_root, "config", "user.name", "AI-SDLC E2E")
    git_command(root, context.evidence_root, "add", "-A")
    git_command(root, context.evidence_root, "commit", "-m", "initial project")
    output = context.run(
        [context.cli, "init", ".", "--agent-target", "codex", "--shell", "powershell"],
        "lean-init.txt",
        expect_json=False,
    )
    if "当前结果 / Result" not in str(output) or "下一步 / Next" not in str(output):
        raise AssertionError("init 未输出普通用户可见的 Result / Next")
    write_formal_docs(root)
    git_command(root, context.evidence_root, "add", "-A")
    git_command(root, context.evidence_root, "commit", "-m", "freeze lean e2e inputs")


def _run_requirement(context: _Journey) -> None:
    started = context.run(
        [
            context.cli,
            "loop",
            "requirement",
            "start",
            "--idea",
            "订单格式化前去除编号两端空格。",
            "--acceptance",
            "保持既有输出格式并通过编译验证。",
            "--work-item-id",
            "001-lean-e2e",
            "--loop-id",
            "req-windows-lean",
            "--json",
        ],
        "lean-requirement-start.json",
    )
    assert_value(started, "status", "ready")
    frozen = context.run(
        [
            context.cli,
            "loop",
            "requirement",
            "freeze",
            "--loop-id",
            "req-windows-lean",
            "--yes",
            "--json",
        ],
        "lean-requirement-freeze.json",
    )
    assert_value(frozen, "loop_status", "closed")


def _run_design(context: _Journey) -> None:
    checked = context.run(
        [
            context.cli,
            "loop",
            "design-contract",
            "check",
            "--wi",
            "specs/001-lean-e2e",
            "--requirement-loop-id",
            "req-windows-lean",
            "--loop-id",
            "dc-windows-lean",
            "--json",
        ],
        "lean-design-check.json",
    )
    assert_value(checked, "loop_status", "passed")
    closed = context.run(
        [
            context.cli,
            "loop",
            "design-contract",
            "close",
            "--loop-id",
            "dc-windows-lean",
            "--yes",
            "--json",
        ],
        "lean-design-close.json",
    )
    assert_value(closed, "loop_status", "closed")


def _start_implementation(context: _Journey) -> None:
    started = context.run(
        [
            context.cli,
            "loop",
            "implementation",
            "start",
            "--wi",
            "specs/001-lean-e2e",
            "--design-contract-loop-id",
            "dc-windows-lean",
            "--loop-id",
            "impl-windows-lean",
            "--json",
        ],
        "lean-implementation-start.json",
    )
    assert_value(started, "loop_status", "running")


def _run_regression(context: _Journey) -> tuple[Path, dict[str, object]]:
    write_regression_probe(context.project_root)
    prefix = [
        context.cli,
        "loop",
        "implementation",
        "lean-regression",
        "--loop-id",
        "impl-windows-lean",
        "--test-id",
        "order-normalization",
        "--test-source",
        "tests/order_regression.py",
        "--failure-signature",
        "assertion:order-normalization",
        "--json",
    ]
    command = ["--", sys.executable, "tests/order_regression.py"]
    red = context.run(
        [*prefix, "--phase", "red", *command],
        "lean-regression-red.json",
    )
    assert_value(red, "status", "ready")
    source = apply_business_change(context.project_root)
    green = context.run(
        [*prefix, "--phase", "green", *command],
        "lean-regression-green.json",
    )
    assert_value(green, "status", "ready")
    return source, green


def _controlled_verification(context: _Journey, source: Path) -> dict[str, object]:
    context.run(
        [sys.executable, "-m", "py_compile", str(source)],
        "lean-source-verification.txt",
        expect_json=False,
    )
    receipt = context.run(
        [
            context.cli,
            "loop",
            "implementation",
            "lean-verify",
            "--loop-id",
            "impl-windows-lean",
            "--test-source",
            "src/订单.py",
            "--json",
            "--",
            sys.executable,
            "-m",
            "py_compile",
            "src/订单.py",
        ],
        "lean-controlled-verification.json",
    )
    assert_value(receipt, "status", "ready")
    return receipt


def _record_progress(context: _Journey, receipt: dict[str, object]) -> None:
    recorded = context.run(
        [
            context.cli,
            "loop",
            "implementation",
            "record",
            "--loop-id",
            "impl-windows-lean",
            "--task-id",
            "T11",
            "--status",
            "done",
            "--evidence",
            "src/订单.py",
            "--evidence",
            str(receipt.get("receipt_path", "")),
            "--verification",
            "python -m py_compile src/订单.py",
            "--json",
        ],
        "lean-implementation-record.json",
    )
    assert_value(recorded, "loop_status", "needs_review")


def _evaluate(context: _Journey, source: Path) -> dict[str, object]:
    before = sha256_file(source)
    checked = context.run(
        [
            context.cli,
            "loop",
            "implementation",
            "lean-check",
            "--loop-id",
            "impl-windows-lean",
            "--json",
        ],
        "lean-check.json",
    )
    assert_value(checked, "status", "ready")
    assert_value(checked, "loop_status", "passed")
    assert_value(checked, "requires_model", False)
    assert_value(checked, "writes_code", False)
    if sha256_file(source) != before:
        raise AssertionError("lean-check 修改了业务源文件")
    report_path = context.project_root / str(checked.get("report_path", ""))
    if not report_path.is_file():
        raise AssertionError("lean-check 未生成公开结果中声明的报告")
    return checked


def _close_and_check_rule(context: _Journey) -> None:
    closed = context.run(
        [
            context.cli,
            "loop",
            "implementation",
            "close",
            "--loop-id",
            "impl-windows-lean",
            "--yes",
            "--json",
        ],
        "lean-implementation-close.json",
    )
    assert_value(closed, "loop_status", "closed")
    assert_value(closed, "closed", True)
    rule = context.run(
        [context.cli, "rules", "show", "lean-code"],
        "lean-built-in-rule.txt",
        expect_json=False,
    )
    if "风险预算" not in str(rule) or "最多两轮" not in str(rule):
        raise AssertionError("安装包中的 Lean Code 内置规则不完整")


def _write_summary(context: _Journey, report, regression) -> None:
    summary = {
        "result": "passed",
        "source_revision": os.environ.get("AI_SDLC_E2E_SOURCE_REVISION", ""),
        "cli": "remote-installed ai-sdlc",
        "work_item_id": "001-lean-e2e",
        "work_type": "new_requirement",
        "loop_path": [
            "requirement",
            "design-contract",
            "implementation",
            "lean-verify",
            "lean-regression",
            "lean-check",
            "close",
        ],
        "requires_model": False,
        "writes_code": False,
        "business_source_unchanged_by_lean_check": True,
        "report_path": str(report["report_path"]),
        "regression_evidence_path": str(regression["evidence_path"]),
    }
    path = context.evidence_root / "lean-code-summary.json"
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def run_journey(cli: str, project_root: Path, evidence_root: Path) -> None:
    context = _Journey(cli, project_root, evidence_root)
    _prepare(context)
    _run_requirement(context)
    _run_design(context)
    _start_implementation(context)
    source, regression = _run_regression(context)
    receipt = _controlled_verification(context, source)
    _record_progress(context, receipt)
    report = _evaluate(context, source)
    _close_and_check_rule(context)
    _write_summary(context, report, regression)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    run_journey(args.cli, args.project_root.resolve(), args.evidence_root.resolve())
    print("WINDOWS_LEAN_CODE_E2E_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
