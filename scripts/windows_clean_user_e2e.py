"""在 Windows ConPTY 中重放 AI-SDLC 普通用户完整旅程。"""

from __future__ import annotations

import argparse
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path

from windows_clean_user_e2e_support import (
    ADVANCED_SOLUTION_TOKENS,
    CUSTOM_SOLUTION_TOKENS,
    DEFAULT_SOLUTION_TOKENS,
    _business_hashes,
    _commit_current_state,
    _initialize_existing_repo,
    _write_existing_project,
    _write_hashes,
    _write_refined_frontend_requirement,
    _write_summary,
)
from winpty import PtyProcess
from winpty.enums import Backend

_ANSI_ESCAPE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _plain_text(value: str) -> str:
    return _ANSI_ESCAPE.sub("", value).replace("\r", "")


def _assert_contains(text: str, *expected: str) -> None:
    searchable = " ".join(text.split())
    missing = [item for item in expected if " ".join(item.split()) not in searchable]
    if missing:
        raise AssertionError(f"输出缺少预期内容: {missing}")


class _ConPtyTranscript:
    """持续读取 ConPTY，确保只有看到真实提示后才发送用户输入。"""

    def __init__(self, process: PtyProcess) -> None:
        self.process = process
        self.text = ""
        self._chunks: queue.Queue[str] = queue.Queue()
        self._reader = threading.Thread(target=self._read_forever, daemon=True)
        self._reader.start()

    def _read_forever(self) -> None:
        while True:
            try:
                chunk = self.process.read(4096)
            except EOFError:
                return
            if chunk:
                self._chunks.put(chunk)

    def _drain_once(self, timeout: float = 0.1) -> None:
        try:
            self.text += self._chunks.get(timeout=timeout)
        except queue.Empty:
            return
        while True:
            try:
                self.text += self._chunks.get_nowait()
            except queue.Empty:
                return

    def wait_for(self, expected: str, *, timeout: float = 90.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._drain_once()
            if expected in _plain_text(self.text):
                return
            if not self.process.isalive() and self._chunks.empty():
                break
        raise AssertionError(
            f"ConPTY 未在 {timeout:.0f} 秒内显示提示: {expected}\n"
            f"--- transcript ---\n{_plain_text(self.text)}"
        )

    def collect_to_exit(self, *, timeout: float = 300.0) -> tuple[int, str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._drain_once()
            if not self.process.isalive() and self._chunks.empty():
                self._reader.join(timeout=2)
                self._drain_once(timeout=0)
                return self.process.exitstatus, _plain_text(self.text)
        self.process.terminate(force=True)
        raise AssertionError(
            f"ConPTY 命令超过 {timeout:.0f} 秒未退出。\n"
            f"--- transcript ---\n{_plain_text(self.text)}"
        )


def _run_cli(
    cli_path: str,
    args: list[str],
    *,
    cwd: Path,
    evidence_path: Path,
) -> str:
    completed = subprocess.run(
        [cli_path, *args],
        cwd=cwd,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    output = _plain_text(completed.stdout + completed.stderr)
    evidence_path.write_text(output, encoding="utf-8")
    if completed.returncode != 0:
        raise AssertionError(
            f"公开 CLI 命令失败 ({completed.returncode}): {[cli_path, *args]}\n{output}"
        )
    return output


def _run_interactive_init(
    cli_path: str,
    project_root: Path,
    evidence_root: Path,
) -> str:
    clean_env = os.environ.copy()
    for name in (
        "OPENAI_CODEX",
        "CODEX_CLI_READY",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDECODE",
        "CURSOR_TRACE_ID",
        "CURSOR_AGENT",
        "VSCODE_IPC_HOOK_CLI",
        "TERM_PROGRAM",
    ):
        clean_env.pop(name, None)
    clean_env.update(
        {
            "CI": "1",
            "CONPTY_CI": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )

    process = PtyProcess.spawn(
        [cli_path, "init", "."],
        cwd=str(project_root),
        env=clean_env,
        dimensions=(45, 180),
        backend=Backend.ConPTY,
    )
    transcript = _ConPtyTranscript(process)
    transcript.wait_for("请选择当前实际用于聊天开发的 AI 代理入口")
    process.write("2\r\n")
    transcript.wait_for("请选择当前项目默认使用的命令 Shell")
    process.write("1\r\n")
    exit_code, output = transcript.collect_to_exit()
    (evidence_root / "interactive-init.txt").write_text(output, encoding="utf-8")
    if exit_code != 0:
        raise AssertionError(f"交互式 init 失败 ({exit_code})\n{output}")
    return output


def _verify_interactive_init(
    cli_path: str,
    project_root: Path,
    evidence_root: Path,
) -> None:
    init_output = _run_interactive_init(cli_path, project_root, evidence_root)
    _assert_contains(
        init_output,
        "请选择当前实际用于聊天开发的 AI 代理入口",
        "请选择当前项目默认使用的命令 Shell",
        "AI 代理入口: Codex",
        "Project shell: PowerShell",
        "当前结果 / Result",
        "下一步 / Next",
    )
    if "non-interactive fallback" in init_output or "explicit override" in init_output:
        raise AssertionError("init 未走真实交互选择路径")
    config_path = (
        project_root / ".ai-sdlc" / "project" / "config" / "project-config.yaml"
    )
    agents_path = project_root / "AGENTS.md"
    if not config_path.is_file() or not agents_path.is_file():
        raise AssertionError("init 未生成项目配置或 Codex canonical AGENTS.md")
    config_text = config_path.read_text(encoding="utf-8")
    _assert_contains(config_text, "agent_target: codex", "preferred_shell: powershell")
    agents_text = agents_path.read_text(encoding="utf-8")
    _assert_contains(
        agents_text,
        "若需求涉及前端需求、UI、页面、组件、浏览器交互或前端工程",
        "进入实现前必须先给出技术栈 / 组件库建议",
        "program solution-confirm --dry-run --mode advanced",
    )


def _run_requirement_and_workitem_flow(
    cli_path: str,
    project_root: Path,
    evidence_root: Path,
) -> None:
    requirement = _write_refined_frontend_requirement(project_root)
    relative_requirement = requirement.relative_to(project_root).as_posix()
    start_output = _run_cli(
        cli_path,
        [
            "loop",
            "requirement",
            "start",
            "--input-file",
            relative_requirement,
            "--acceptance",
            "The approval flow is responsive and browser-tested.",
            "--work-item-id",
            "001-customer-approval-dashboard",
            "--json",
        ],
        cwd=project_root,
        evidence_path=evidence_root / "requirement-start.json",
    )
    _assert_contains(
        start_output,
        '"result": "Requirement loop started."',
        '"loop_status": "needs_review"',
    )
    status_output = _run_cli(
        cli_path,
        ["loop", "requirement", "status", "--json"],
        cwd=project_root,
        evidence_path=evidence_root / "requirement-status.json",
    )
    _assert_contains(
        status_output,
        '"result": "Current requirement loop found."',
        '"status": "needs_review"',
    )
    freeze_output = _run_cli(
        cli_path,
        ["loop", "requirement", "freeze", "--yes", "--json"],
        cwd=project_root,
        evidence_path=evidence_root / "requirement-freeze.json",
    )
    _assert_contains(freeze_output, '"frozen": true')
    _commit_current_state(project_root, "freeze frontend requirement")
    _initialize_workitem(cli_path, project_root, evidence_root, requirement)


def _initialize_workitem(
    cli_path: str,
    project_root: Path,
    evidence_root: Path,
    requirement: Path,
) -> None:
    _run_cli(
        cli_path,
        [
            "workitem",
            "init",
            "--title",
            "Customer Approval Dashboard",
            "--wi-id",
            "001-customer-approval-dashboard",
            "--input",
            requirement.read_text(encoding="utf-8"),
            "--related-doc",
            requirement.relative_to(project_root).as_posix(),
        ],
        cwd=project_root,
        evidence_path=evidence_root / "workitem-init.txt",
    )
    spec = project_root / "specs" / "001-customer-approval-dashboard" / "spec.md"
    if not spec.is_file():
        raise AssertionError("公开 workitem init 未生成规范目录")
    validate_output = _run_cli(
        cli_path,
        ["program", "validate"],
        cwd=project_root,
        evidence_path=evidence_root / "program-validate.txt",
    )
    _assert_contains(validate_output, "program validate: PASS")


def _run_default_solution(
    cli_path: str,
    project_root: Path,
    evidence_root: Path,
) -> None:
    simple_output = _run_cli(
        cli_path,
        ["program", "solution-confirm", "--dry-run"],
        cwd=project_root,
        evidence_path=evidence_root / "solution-simple.txt",
    )
    _assert_contains(simple_output, *DEFAULT_SOLUTION_TOKENS)


def _run_advanced_solutions(
    cli_path: str,
    project_root: Path,
    evidence_root: Path,
) -> None:
    advanced_output = _run_cli(
        cli_path,
        ["program", "solution-confirm", "--dry-run", "--mode", "advanced"],
        cwd=project_root,
        evidence_path=evidence_root / "solution-advanced.txt",
    )
    _assert_contains(advanced_output, *ADVANCED_SOLUTION_TOKENS)

    custom_output = _run_cli(
        cli_path,
        [
            "program",
            "solution-confirm",
            "--dry-run",
            "--mode",
            "advanced",
            "--frontend-stack",
            "vue3",
            "--provider-id",
            "public-primevue",
            "--style-pack-id",
            "data-console",
        ],
        cwd=project_root,
        evidence_path=evidence_root / "solution-advanced-custom.txt",
    )
    _assert_contains(custom_output, *CUSTOM_SOLUTION_TOKENS)


def _verify_no_delivery_apply(project_root: Path) -> None:
    solution_artifact = (
        project_root
        / ".ai-sdlc"
        / "memory"
        / "frontend-solution-confirmation"
        / "latest.yaml"
    )
    managed_apply_root = (
        project_root / ".ai-sdlc" / "memory" / "frontend-managed-delivery-apply"
    )
    if solution_artifact.exists() or managed_apply_root.exists():
        raise AssertionError(
            "dry-run 用户路径不应物化方案或执行 managed delivery apply"
        )


def run_journey(cli_path: str, project_root: Path, evidence_root: Path) -> None:
    project_root.mkdir(parents=True, exist_ok=True)
    evidence_root.mkdir(parents=True, exist_ok=True)
    business_files = _write_existing_project(project_root)
    _initialize_existing_repo(project_root)
    hashes_before = _business_hashes(project_root, business_files)
    _write_hashes(evidence_root / "business-hashes-before.json", hashes_before)
    _verify_interactive_init(cli_path, project_root, evidence_root)
    if _business_hashes(project_root, business_files) != hashes_before:
        raise AssertionError("交互式 init 修改了已有业务文件")
    _commit_current_state(project_root, "initialize AI-SDLC")
    _run_requirement_and_workitem_flow(cli_path, project_root, evidence_root)
    _run_default_solution(cli_path, project_root, evidence_root)
    _run_advanced_solutions(cli_path, project_root, evidence_root)
    _verify_no_delivery_apply(project_root)
    hashes_after_all = _business_hashes(project_root, business_files)
    _write_hashes(evidence_root / "business-hashes-after.json", hashes_after_all)
    if hashes_after_all != hashes_before:
        raise AssertionError("普通用户 E2E 修改了已有业务文件")
    _write_summary(evidence_root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    run_journey(args.cli, args.project_root.resolve(), args.evidence_root.resolve())
    print("WINDOWS_CLEAN_USER_E2E_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
