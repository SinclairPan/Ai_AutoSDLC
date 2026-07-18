"""文件与进程辅助函数，供 Windows Lean Code 用户旅程使用。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_command(
    command: list[str],
    *,
    cwd: Path,
    evidence_path: Path,
    expect_json: bool = False,
) -> dict[str, object] | str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
        check=False,
    )
    output = completed.stdout + completed.stderr
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(output, encoding="utf-8")
    if completed.returncode != 0:
        raise AssertionError(
            f"普通用户命令失败 ({completed.returncode}): {command}\n{output}"
        )
    if not expect_json:
        return output
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"CLI 未返回有效 JSON: {command}\n{output}") from exc


def git_command(project_root: Path, evidence_root: Path, *args: str) -> None:
    safe_name = "-".join(part.replace("/", "-") for part in args[:2])
    run_command(
        ["git", *args],
        cwd=project_root,
        evidence_path=evidence_root / f"git-{safe_name}.txt",
    )


def write_initial_project(project_root: Path) -> None:
    files = {
        "README.md": "# 订单服务\n\nWindows Lean Code E2E 项目。\n",
        "src/订单.py": (
            'def format_order(order_id: str) -> str:\n    return f"订单 {order_id}"\n'
        ),
    }
    for relative, content in files.items():
        _write(project_root, relative, content)


def write_formal_docs(project_root: Path) -> None:
    spec_root = project_root / "specs" / "001-lean-e2e"
    spec_root.mkdir(parents=True, exist_ok=True)
    _write_spec(spec_root)
    _write_plan(spec_root)
    _write_tasks(spec_root)
    _write_work_item(project_root)


def _write_spec(spec_root: Path) -> None:
    (spec_root / "spec.md").write_text(
        """# PRD：订单编号规范化

**状态**：已冻结

## 需求

- **FR-LEAN-001**：格式化订单前必须去除编号两端空格。

## 成功标准

- **SC-LEAN-001**：保持既有输出格式并通过 Python 编译验证。
""",
        encoding="utf-8",
    )


def _write_plan(spec_root: Path) -> None:
    (spec_root / "plan.md").write_text(
        """# 实施计划

## 技术背景

现有项目是一个小型 Python 订单格式化模块。

## 阶段计划

在既有函数内部复用一个私有规范化函数。

## 验证策略

先新增失败回归测试，再完成实现并运行确定性 Lean Code 检查。

## 回退方式

回退本次业务文件与回归测试变更。
""",
        encoding="utf-8",
    )


def _write_tasks(spec_root: Path) -> None:
    (spec_root / "tasks.md").write_text(
        """# 任务分解

### Task 1.1 规范化订单编号

- **任务编号**：T11
- **优先级**：P0
- **文件**：src/订单.py, tests/order_regression.py
- **验收标准**：Cover FR-LEAN-001 and SC-LEAN-001.
- **验证**：python tests/order_regression.py
""",
        encoding="utf-8",
    )


def _write_work_item(project_root: Path) -> None:
    work_item = project_root / ".ai-sdlc" / "work-items" / "001-lean-e2e"
    work_item.mkdir(parents=True, exist_ok=True)
    (work_item / "work-item.yaml").write_text(
        """work_item_id: "001-lean-e2e"
work_type: "new_requirement"
severity: "medium"
source: "text"
status: "docs_baseline"
title: "订单编号规范化"
description: "验证公开 CLI 的 Lean Code 有界质量闭环。"
needs_human_confirmation: false
classification_confidence: "high"
""",
        encoding="utf-8",
    )


def write_regression_probe(project_root: Path) -> None:
    _write(
        project_root,
        "tests/order_regression.py",
        "from pathlib import Path\n"
        "source = Path('src/订单.py').read_text(encoding='utf-8')\n"
        "fixed = '_normalize_order_id' in source\n"
        "print('assertion:order-normalization' if not fixed else 'passed')\n"
        "raise SystemExit(0 if fixed else 1)\n",
    )


def apply_business_change(project_root: Path) -> Path:
    source = project_root / "src" / "订单.py"
    source.write_text(
        """def _normalize_order_id(order_id: str) -> str:
    return order_id.strip()


def format_order(order_id: str) -> str:
    return f"订单 {_normalize_order_id(order_id)}"
""",
        encoding="utf-8",
    )
    return source


def assert_value(payload: dict[str, object], key: str, expected: object) -> None:
    actual = payload.get(key)
    if actual != expected:
        raise AssertionError(f"{key}={actual!r}，预期 {expected!r}: {payload}")


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


__all__ = [
    "apply_business_change",
    "assert_value",
    "git_command",
    "run_command",
    "sha256_file",
    "write_formal_docs",
    "write_initial_project",
    "write_regression_probe",
]
