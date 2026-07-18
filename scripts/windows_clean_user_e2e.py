"""在 Windows ConPTY 中重放 AI-SDLC 普通用户完整旅程。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path

from winpty import PtyProcess
from winpty.enums import Backend

_ANSI_ESCAPE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _plain_text(value: str) -> str:
    return _ANSI_ESCAPE.sub("", value).replace("\r", "")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_contains(text: str, *expected: str) -> None:
    missing = [item for item in expected if item not in text]
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


def _write_existing_project(project_root: Path) -> list[Path]:
    business_files = {
        "package.json": (
            '{\n  "name": "existing-customer-portal",\n'
            '  "private": true,\n  "scripts": {"build": "vite build"}\n}\n'
        ),
        "README.md": "# Existing Customer Portal\n\nProduction project fixture.\n",
        "TODO.md": "- [ ] Add the customer approval dashboard\n",
        "src/main.ts": "console.log('existing customer portal');\n",
    }
    paths: list[Path] = []
    for relative_path, content in business_files.items():
        path = project_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    return paths


def _write_refined_frontend_requirement(project_root: Path) -> None:
    requirement_path = project_root / "requirements" / "customer-approval-dashboard.md"
    requirement_path.parent.mkdir(parents=True, exist_ok=True)
    requirement_path.write_text(
        """# Customer Approval Dashboard Requirement

## Goal

Add a responsive enterprise approval dashboard to the existing portal.

## Scope

- Dashboard summary cards, searchable approval table, detail drawer and approval form.
- Desktop and mobile layouts, Chinese and English copy, light theme only.
- Frontend delivery only; existing backend APIs remain unchanged.

## Acceptance Criteria

- Users can filter, inspect and approve or reject a pending request.
- Loading, empty, validation, permission and network-error states are visible.
- Keyboard navigation and browser E2E coverage are required.
""",
        encoding="utf-8",
    )
    spec_root = project_root / "specs" / "001-customer-approval-dashboard"
    spec_root.mkdir(parents=True, exist_ok=True)
    (spec_root / "spec.md").write_text(
        requirement_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (project_root / "program-manifest.yaml").write_text(
        """schema_version: "1"
program:
  goal: "Deliver the customer approval dashboard without changing backend APIs."
capabilities:
  - id: "customer-approval-dashboard"
    title: "Customer Approval Dashboard"
    goal: "Provide a usable and verifiable approval workflow."
    spec_refs:
      - "001-customer-approval-dashboard"
specs:
  - id: "001-customer-approval-dashboard"
    path: "specs/001-customer-approval-dashboard"
    depends_on: []
    capability_refs:
      - "customer-approval-dashboard"
""",
        encoding="utf-8",
    )


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


def run_journey(cli_path: str, project_root: Path, evidence_root: Path) -> None:
    project_root.mkdir(parents=True, exist_ok=True)
    evidence_root.mkdir(parents=True, exist_ok=True)
    business_files = _write_existing_project(project_root)
    hashes_before = {str(path.relative_to(project_root)): _sha256(path) for path in business_files}
    (evidence_root / "business-hashes-before.json").write_text(
        json.dumps(hashes_before, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

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

    config_path = project_root / ".ai-sdlc" / "project" / "config" / "project-config.yaml"
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

    hashes_after_init = {
        str(path.relative_to(project_root)): _sha256(path) for path in business_files
    }
    if hashes_after_init != hashes_before:
        raise AssertionError("交互式 init 修改了已有业务文件")

    _write_refined_frontend_requirement(project_root)
    validate_output = _run_cli(
        cli_path,
        ["program", "validate"],
        cwd=project_root,
        evidence_path=evidence_root / "program-validate.txt",
    )
    _assert_contains(validate_output, "Program manifest is valid")

    simple_output = _run_cli(
        cli_path,
        ["program", "solution-confirm", "--dry-run"],
        cwd=project_root,
        evidence_path=evidence_root / "solution-simple.txt",
    )
    _assert_contains(
        simple_output,
        "Program Frontend Solution Confirm Simple",
        "Recommended Solution",
        "recommended_frontend_stack: vue3",
        "recommended_provider_id: public-primevue",
        "recommended_style_pack_id: modern-saas",
        "PrimeVue + @primeuix/themes + primeicons",
        "definePreset(Aura) + #1770e6 + darkModeSelector=false",
        "Vite + TypeScript + UnoCSS + CSS Variables",
        "Pinia + Vue Router + Axios + vee-validate + zod + vue-i18n",
        "Playwright + ESLint + Prettier + husky + lint-staged + commitlint",
        "Advanced Choice Entry",
        "ai-sdlc program solution-confirm --dry-run --mode advanced",
    )

    advanced_output = _run_cli(
        cli_path,
        ["program", "solution-confirm", "--dry-run", "--mode", "advanced"],
        cwd=project_root,
        evidence_path=evidence_root / "solution-advanced.txt",
    )
    _assert_contains(
        advanced_output,
        "Program Frontend Solution Confirm Advanced",
        "Structured Wizard",
        "Candidate Matrix",
        "enterprise-default",
        "data-console",
        "high-clarity",
        "macos-glass",
        "enterprise-vue2",
        "public-primevue",
    )

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
    _assert_contains(
        custom_output,
        "requested_frontend_stack: vue3",
        "requested_provider_id: public-primevue",
        "requested_style_pack_id: data-console",
        "effective_frontend_stack: vue3",
        "effective_provider_id: public-primevue",
        "effective_style_pack_id: data-console",
    )

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
        raise AssertionError("dry-run 用户路径不应物化方案或执行 managed delivery apply")

    hashes_after_all = {
        str(path.relative_to(project_root)): _sha256(path) for path in business_files
    }
    (evidence_root / "business-hashes-after.json").write_text(
        json.dumps(hashes_after_all, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if hashes_after_all != hashes_before:
        raise AssertionError("普通用户 E2E 修改了已有业务文件")

    summary = {
        "result": "passed",
        "install_source": "remote-main",
        "terminal_backend": "ConPTY",
        "init_command": "ai-sdlc init .",
        "selected_agent_target": "codex",
        "selected_shell": "powershell",
        "default_frontend_stack": "vue3",
        "default_provider": "public-primevue",
        "default_style_pack": "modern-saas",
        "custom_advanced_style_pack": "data-console",
        "managed_delivery_apply_executed": False,
        "business_files_unchanged": True,
    }
    (evidence_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


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
