# AI-SDLC 全新用户中文指引实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将比赛仓库中文用户指南改写为仅覆盖“全新空项目”和“已有项目”的可复制操作手册，并用 v1.0.1 正式资产验证文档与真实行为一致。

**Architecture:** `USER_GUIDE.zh-CN.md` 采用两个可独立阅读的场景章节，每章都包含三平台安装、适配器选择、初始化、成功输出和就近排错。静态文档合同测试锁定版本、资产、五个适配器、稳定输出锚点和禁止内容；运行时 E2E 使用正式 Release 资产在干净平台重放安装、`init` 与 `adopt`。

**Tech Stack:** Markdown、pytest、AI-SDLC v1.0.1 离线 Release 资产、PowerShell、zsh/bash、GitHub Actions。

## Global Constraints

- 只修改 `SinclairPan/Ai_AutoSDLC` 比赛仓库，不修改参考仓库。
- 用户范围仅为全新用户 + 全新空项目、全新用户 + 已有项目。
- 正式版本固定为 `v1.0.1`，CLI 输出固定为 `1.0.1`。
- 适配器必须按真实顺序覆盖 Claude Code、Codex、Cursor、VS Code、其他-通用，不设置 Codex 专属主流程。
- Windows 使用 PowerShell 命令；macOS 使用 zsh/bash 兼容命令；Linux 使用 bash 命令。
- 普通成功路径不要求 Python、venv、pip、uv、源码安装、`adapter status` 或 `run --dry-run`。
- 每个平台命令使用 v1.0.1 正式离线资产并保留解压后的长期安装目录。
- 只把真实稳定输出锚点写入手册，不固定动态路径、耗时、下载速度、扫描数量或环境相关默认项。

---

### Task 1: 建立用户指南文档合同

**Files:**
- Create: `tests/integration/test_user_guide_contract.py`
- Test: `tests/integration/test_user_guide_contract.py`

**Interfaces:**
- Consumes: `USER_GUIDE.zh-CN.md` 与 `ai_sdlc.integrations.agent_target.AGENT_TARGET_OPTIONS`。
- Produces: 对版本、场景、资产、适配器、成功输出、排错和禁止内容的可执行合同。

- [ ] **Step 1: 写入当前指南必然失败的合同测试**

```python
from pathlib import Path

from ai_sdlc.integrations.agent_target import AGENT_TARGET_OPTIONS, agent_target_label


ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "USER_GUIDE.zh-CN.md"


def guide_text() -> str:
    return GUIDE.read_text(encoding="utf-8")


def test_guide_is_scoped_to_two_new_user_scenarios() -> None:
    text = guide_text()
    assert "全新用户 + 全新空项目" in text
    assert "全新用户 + 已有项目" in text
    for forbidden in ("老版本升级", "从源码运行", "@main", "uv sync", "Lean Code"):
        assert forbidden not in text


def test_guide_lists_every_runtime_adapter_in_both_scenarios() -> None:
    text = guide_text()
    labels = [agent_target_label(option) for option in AGENT_TARGET_OPTIONS]
    assert labels == ["Claude Code", "Codex", "Cursor", "VS Code", "其他-通用"]
    for label in labels:
        assert text.count(label) >= 2
    assert "实际用于聊天开发的 AI 代理入口" in text
    assert "Codex + PowerShell 为默认组合" not in text


def test_guide_pins_published_assets_and_stable_output_contract() -> None:
    text = guide_text()
    for asset in (
        "ai-sdlc-offline-1.0.1-windows-amd64.zip",
        "ai-sdlc-offline-1.0.1-macos-arm64.tar.gz",
        "ai-sdlc-offline-1.0.1-linux-amd64.tar.gz",
    ):
        assert asset in text
        assert f"releases/download/v1.0.1/{asset}" in text
        assert f"{asset}.sha256" in text
    for anchor in (
        "Offline installation completed",
        "1.0.1",
        "Initialized AI-SDLC project",
        "当前结果 / Result",
        "下一步 / Next",
        "接入已有项目：已生成桥接结果",
        "原任务文件不会被修改",
        "推荐继续点",
    ):
        assert anchor in text


def test_guide_contains_copyable_recovery_paths() -> None:
    text = guide_text()
    for command in (
        "ai-sdlc init .",
        "ai-sdlc adopt .",
        "ai-sdlc adapter select",
        "ai-sdlc adapter shell-select",
    ):
        assert command in text
    for symptom in (
        "SHA256 verification failed",
        "No module named ai_sdlc",
        "open gates",
    ):
        assert symptom in text
```

- [ ] **Step 2: 运行合同测试并确认旧指南失败**

Run: `uv run pytest tests/integration/test_user_guide_contract.py -q`

Expected: FAIL；旧指南包含源码/开发内容，缺少两个独立场景和完整适配器选择合同。

- [ ] **Step 3: 提交失败合同**

```powershell
git add tests/integration/test_user_guide_contract.py
git commit -m "test: define new user guide contract"
```

### Task 2: 重写中文用户指南

**Files:**
- Modify: `USER_GUIDE.zh-CN.md`
- Test: `tests/integration/test_user_guide_contract.py`

**Interfaces:**
- Consumes: v1.0.1 正式资产、离线安装脚本、`init` 与 `adopt` 真实输出。
- Produces: 两个独立、三平台可复制、适配器中立的用户操作章节和异常速查表。

- [ ] **Step 1: 用两个独立场景替换现有指南**

每章必须依次包含：只修改一处项目路径、下载正式资产与 `.sha256`、校验、解压到长期安装目录、运行离线安装脚本、用包内 Direct CLI 验证 `1.0.1`、运行交互式 `init`、按真实五项适配器列表选择、按实际 Shell 选择、核对 `Result / Next`、在所选 AI 工具打开同一项目。已有项目章再包含 `git status`、`adopt`、推荐点正确/不正确/无任务资料三种继续方式。

- [ ] **Step 2: 加入就近排错和文末错误速查**

覆盖下载失败、SHA256 不一致、解压失败、PowerShell 执行策略、裸命令未刷新、安装目录被移动、项目路径错误、重新选择适配器/Shell、open gates、`No module named ai_sdlc`、`adopt` 推荐不准确。

- [ ] **Step 3: 运行合同测试并确认通过**

Run: `uv run pytest tests/integration/test_user_guide_contract.py -q`

Expected: `4 passed`。

- [ ] **Step 4: 运行指南相关现有测试**

Run: `uv run pytest tests/integration/test_cli_init.py tests/integration/test_cli_adopt.py tests/integration/test_github_workflows.py -q`

Expected: 全部通过。

- [ ] **Step 5: 提交指南重写**

```powershell
git add USER_GUIDE.zh-CN.md
git commit -m "docs: rewrite Chinese guide for new users"
```

### Task 3: 重放正式资产用户旅程

**Files:**
- Verify: `USER_GUIDE.zh-CN.md`
- Verify: `.github/workflows/windows-user-guide-e2e.yml`
- Verify: `.github/workflows/release-artifact-smoke.yml`

**Interfaces:**
- Consumes: v1.0.1 GitHub Release 三平台资产及新指南命令。
- Produces: macOS 本地证据、Windows 干净 runner 证据、Linux 干净 runner 证据、业务文件哈希不变证据。

- [ ] **Step 1: 在 macOS arm64 临时目录逐条执行手册空项目命令**

Expected: 下载和 SHA256 校验通过；安装输出包含 `Offline installation completed`；Direct CLI 输出 `1.0.1`；`init` 输出包含 `Initialized AI-SDLC project`、`当前结果 / Result`、`下一步 / Next`。

- [ ] **Step 2: 在 macOS arm64 临时目录逐条执行手册已有项目命令**

Expected: `adopt` 输出包含 `接入已有项目：已生成桥接结果`、`原任务文件不会被修改`、`推荐继续点`；`init`/`adopt` 前后业务文件 SHA256 相同。

- [ ] **Step 3: 推送分支并创建草稿 PR，触发 Windows 用户指南 E2E**

Expected: `existing-project-online-install` 与 `clean-online-interactive-user-journey` 通过，并上传交互式 init 与业务文件哈希证据。

- [ ] **Step 4: 在 v1.0.1 正式资产上运行三平台 Release Artifact Smoke**

Run: `gh workflow run release-artifact-smoke.yml --repo SinclairPan/Ai_AutoSDLC --ref main -f tag=v1.0.1`

Expected: Windows zip、macOS arm64 tar.gz、Linux amd64 tar.gz 三个作业全部通过。

- [ ] **Step 5: 核对 E2E 日志与手册稳定输出**

逐项确认手册声明的版本、安装完成、适配器菜单、Shell 菜单、`Result / Next`、`adopt` 和业务文件不变均能在本地或 CI evidence 中找到；任何不一致优先修正文档，除非真实 CLI 本身失败。

### Task 4: 完整回归与交付

**Files:**
- Verify: `USER_GUIDE.zh-CN.md`
- Verify: `tests/integration/test_user_guide_contract.py`
- Verify: `docs/superpowers/specs/2026-08-03-new-user-guide-design.md`
- Verify: `docs/superpowers/plans/2026-08-03-new-user-guide-implementation.md`

**Interfaces:**
- Consumes: 文档合同、运行时 E2E 和仓库约束。
- Produces: 可审阅 PR 与可追溯验证结果。

- [ ] **Step 1: 运行仓库级文档和约束检查**

```powershell
uv run ai-sdlc verify constraints
uv run ruff check .
uv run pytest tests/integration/test_user_guide_contract.py tests/integration/test_cli_init.py tests/integration/test_cli_adopt.py tests/integration/test_github_workflows.py -q
git diff --check origin/main...HEAD
```

Expected: 所有命令退出码为 0。

- [ ] **Step 2: 扫描禁止残留**

Run: `rg -n '老版本|升级|@main|从源码|uv sync|Codex \+ PowerShell 为默认组合|Lean Code' USER_GUIDE.zh-CN.md`

Expected: 无匹配。

- [ ] **Step 3: 更新连续工作交接**

Run: `uv run ai-sdlc handoff update --help`

Expected: 使用帮助页显示的参数记录目标、变更、测试、风险和下一步；工作树仅包含计划内文件。

- [ ] **Step 4: 请求 Codex Review 并等待 required checks**

Expected: review 无可操作问题，Windows 用户指南 E2E 与全部 required checks 通过后将 PR 标记 ready 并合并。
