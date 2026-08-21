# AI-SDLC 3.0.1

AI-SDLC 是一个本地优先、可恢复、可验证的 AI 原生软件研发框架。它把需求澄清、设计契约、任务执行、质量门禁、对抗审查和交付证据组织成一套可由 AI 代理与工程师共同执行的命令行工作流。

项目地址：<https://github.com/SinclairPan/Ai_AutoSDLC>

> 当前公开稳定版本与比赛最终版本均为 `v3.0.1`。第一次使用时，请在下面的 12 条路线中只选择一条完整执行；从 `v2.0.0` 升级前请阅读 [v3 迁移说明](docs/v3-migration.zh-CN.md)，从 `v1.0.2` 跨大版本升级还应先阅读 [v2 迁移说明](docs/v2-migration.zh-CN.md)。

`v3.0.1` 是比赛最终补丁版本：运行时能力与 `v3.0.0` 保持一致，正式启用面向全新用户的 12 条安装、初始化与恢复路线，并修正跨机器离线安装和已有项目核对说明。

## 核心特性

| 能力 | 说明 |
| --- | --- |
| 项目初始化与接入 | `init` 为新项目建立规则、状态与代理入口；`adopt` 在不修改业务文件的前提下识别已有项目事实。 |
| 命令前升级提示 | 安装版在业务命令执行前检查缓存；stable shim / `python -m ai_sdlc` 支持 TTY 确认后升级重放，Agent/非 TTY 通过 stderr 获得稳定单行提示，JSON stdout 不受污染。 |
| Codex 项目适配 | 以 `AGENTS.md` 作为项目级指令入口，可持久化 Codex 与 PowerShell、Bash、Zsh 或 Cmd 偏好。 |
| 可恢复流水线 | checkpoint 记录执行阶段、开放门禁和下一步动作；`status` 默认只显示 Result、Next、Blockers，详细诊断进入 `--details`。 |
| Loop Engineering | 内置 requirement、design-contract、implementation、frontend-evidence、local-pr-review 五类闭环。 |
| 动态专家复核 | 每个 Loop 的实质结果由当前代理按内容选择最多两名只读专家；只允许一轮修复复审，不持久化专家权威。 |
| 精简建议 | 对代码体积和复杂度给出非阻断建议；建议不改变 Loop 状态，也不阻止 close。 |
| 质量与治理门禁 | 对规则、任务、约束、分支、文档契约、前端证据和关闭条件执行只读验证。 |
| 本地对抗审查 | `pr-review` 支持 Git 范围、暂存区、工作区和补丁输入，在提交前由独立只读代理检查当前变更。 |
| 前端交付闭环 | 基于项目事实确认方案，受控应用后采集浏览器、视觉回归与可访问性证据，并进入 Frontend Evidence Loop。 |
| 跨平台交付 | 支持 Windows、macOS、Linux 的源码安装、在线安装和带 Python 运行时的离线包。 |
| 本地优先 | 核心扫描、规则解析、门禁、Loop 和审查编排均可在本地执行；代码外发默认关闭。 |

## 安装

### 新用户路线选择器

无需理解 Python、venv 或仓库结构。先判断项目目录是空的还是已有内容，再选择在线或离线渠道和操作系统。每个链接都包含准备、下载、校验、安装、初始化或接入、成功证据和就地恢复，不需要跳转到其他路线补步骤。

| 项目状态 | 渠道 | Windows AMD64 | macOS Apple Silicon | Linux AMD64 |
| --- | --- | --- | --- | --- |
| 全新空项目 | 在线 | [执行路线](USER_GUIDE.zh-CN.md#route-new-online-windows-amd64) | [执行路线](USER_GUIDE.zh-CN.md#route-new-online-macos-arm64) | [执行路线](USER_GUIDE.zh-CN.md#route-new-online-linux-amd64) |
| 全新空项目 | 离线 | [执行路线](USER_GUIDE.zh-CN.md#route-new-offline-windows-amd64) | [执行路线](USER_GUIDE.zh-CN.md#route-new-offline-macos-arm64) | [执行路线](USER_GUIDE.zh-CN.md#route-new-offline-linux-amd64) |
| 已有项目 | 在线 | [执行路线](USER_GUIDE.zh-CN.md#route-existing-online-windows-amd64) | [执行路线](USER_GUIDE.zh-CN.md#route-existing-online-macos-arm64) | [执行路线](USER_GUIDE.zh-CN.md#route-existing-online-linux-amd64) |
| 已有项目 | 离线 | [执行路线](USER_GUIDE.zh-CN.md#route-existing-offline-windows-amd64) | [执行路线](USER_GUIDE.zh-CN.md#route-existing-offline-macos-arm64) | [执行路线](USER_GUIDE.zh-CN.md#route-existing-offline-linux-amd64) |

Linux 选择边界：已存在 Python 3.11+ 的 Linux 主机保持发行版无关的在线兼容路径。缺少 Python 时，在线自动 bootstrap 仅认证 Debian GNU/Linux 12 (bookworm) + amd64/x86_64 + glibc；其他缺少 Python 的 amd64/x86_64 + glibc Linux 主机使用路线 6/12 的 ai-sdlc-offline-3.0.1-linux-amd64.tar.gz。非 AMD64 或非 glibc 的 Linux 主机没有兼容的 v3.0.1 Linux 发行资产，不得使用路线 6/12 的 AMD64 离线包。

普通用户优先使用安装器创建的 `ai-sdlc`。若当前终端还没有刷新 PATH，就使用该路线给出的 `python -m ai_sdlc ...` 命令；不要自行创建 Python 环境或手工补依赖。

运行要求：Python 3.11 或更高版本、Git。源码开发推荐使用 [uv](https://docs.astral.sh/uv/)。

### 高级安装：从 Git 安装

```powershell
python -m pip install "git+https://github.com/SinclairPan/Ai_AutoSDLC.git@v3.0.1"
ai-sdlc --version
```

版本输出应为 `3.0.1`。

需要验证尚未发布的开发版时，可显式把安装地址末尾改为 `@main`；开发版不承诺输出稳定版版本号。

### 开发者入口：从源码运行

```powershell
git clone --branch v3.0.1 --depth 1 https://github.com/SinclairPan/Ai_AutoSDLC.git
Set-Location Ai_AutoSDLC
uv sync
uv run ai-sdlc --version
```

## 快速开始

在目标项目目录执行：

```powershell
ai-sdlc init . --agent-target codex --shell powershell
```

初始化会完成以下工作：

- 扫描项目语言、依赖、测试、入口文件和风险区域；
- 生成 `.ai-sdlc/` 项目配置与 checkpoint；
- 为 Codex 准备项目级 `AGENTS.md`；
- 写入 PowerShell 作为项目命令偏好；
- 自动执行一次安全预演，并明确展示仍需处理的门禁。

初始化完成后，按命令输出中的 `Result / Next` 进入 Codex 对话并提交需求。正常推进使用 `ai-sdlc run`：它返回当前五 Loop 路由以及最多两个 `Applicable Rules` 片段。`adapter status`、`status --details` 和 `run --dry-run` 仅用于需要时的排查。

已有项目可先运行只读接入：

```powershell
ai-sdlc adopt .
ai-sdlc scan .
ai-sdlc index .
```

## 标准工作流

### 1. 读取项目事实

```powershell
ai-sdlc status
ai-sdlc run
ai-sdlc run --json
ai-sdlc verify constraints
```

`status` 默认只显示 Result、Next 和 Blockers；`status --details` 保留完整人类诊断表，`status --json` 保留详细机器合同。Agent 直接使用 `run` 返回的有界规则正文，不需要手动加载完整规则库。

### 2. 运行工程闭环

```powershell
# Requirement
ai-sdlc loop requirement start --loop-id <loop-id> --idea "<requirement>" --acceptance "<acceptance criterion>"
ai-sdlc loop status --type requirement
ai-sdlc loop review --type requirement --loop-id <loop-id> --json
ai-sdlc loop requirement freeze --loop-id <loop-id> --expect-review-digest <input_digest> --yes

# Design Contract
ai-sdlc loop design-contract check --wi specs/<work-item> --loop-id <loop-id>
ai-sdlc loop status --type design-contract
ai-sdlc loop review --type design-contract --loop-id <loop-id> --json
ai-sdlc loop design-contract close --loop-id <loop-id> --expect-review-digest <input_digest> --yes

# Implementation
ai-sdlc loop implementation start --wi specs/<work-item> --loop-id <loop-id>
ai-sdlc loop implementation record --loop-id <loop-id> --task-id <task-id> --status done --verification "<command>" --evidence <path>
ai-sdlc loop status --type implementation
ai-sdlc loop review --type implementation --loop-id <loop-id> --json
ai-sdlc loop implementation close --loop-id <loop-id> --expect-review-digest <input_digest> --yes

# Frontend Evidence（仅前端工作）
ai-sdlc loop frontend-evidence doctor --provider auto
ai-sdlc loop frontend-evidence solution-confirm --wi specs/<work-item> --dry-run --json
ai-sdlc loop frontend-evidence solution-confirm --wi specs/<work-item> --execute --yes
ai-sdlc loop frontend-evidence apply --dry-run --json
ai-sdlc loop frontend-evidence apply --execute --yes
ai-sdlc loop frontend-evidence capture --execute
# 首次采集要求视觉基线时：
ai-sdlc loop frontend-evidence baseline --execute --yes
ai-sdlc loop frontend-evidence capture --execute
ai-sdlc loop frontend-evidence start --wi specs/<work-item> --loop-id <loop-id>
ai-sdlc loop status --type frontend-evidence
ai-sdlc loop review --type frontend-evidence --loop-id <loop-id> --json
ai-sdlc loop frontend-evidence close --loop-id <loop-id> --expect-review-digest <input_digest> --yes
```

每个 Loop 都从本地工件计算状态，输出缺口、停止原因和下一步动作。`loop review` 会按内容返回最多两个 `expert_roles`；Codex、Claude Code、Cursor 或 VS Code 中的宿主 Agent 自动为每个角色启动独立只读上下文，并用 `loop review-record` 记录本轮结果，不要求用户手动触发专家。专家读取待审工件时使用同一 `loop review` 命令并追加 `--expect-digest <input_digest> --read-path <artifact_path>`，只检查返回的 `review_snapshot`，不重新打开可变路径。`review-record` 返回 `passed` 后，`input_digest` 必须原样传给紧随其后的 close/freeze；输入或目标身份发生变化时会拒绝关闭。Frontend Evidence 有告警时可显式追加 `--allow-warnings`；没有可用浏览器提供方时可使用 `ai-sdlc loop frontend-evidence skip`，需要重新采集时运行 `ai-sdlc loop frontend-evidence capture --execute`。

### 非阻断精简建议

Implementation Loop 可以根据变更内容生成代码体积、重复、复杂度或拆分建议。该报告只用于帮助实现者保持代码精简；它不产生 BLOCKER/REQUIRED，不改变 Loop 状态，不要求 receipt、例外、No-Go 或额外治理工件，也不阻止现有 close 命令。实现者可以基于行为正确性、维护成本和交付价值选择采纳或说明不采纳。

五个 Loop 的实质结果都采用同一个最小复核边界：CLI 按结果风险选择最多两种专家角色，宿主 Agent 自动执行并记录结果；有发现时由原实现代理修复，并只允许一次复审；通过后由当前代理调用既有 close。专家不可用时结果停留在 `needs_review`，不会把失败解释为通过。框架只在原 Loop 目录保留第一轮以及至多一个第二轮 outcome，不创建第三轮、session、ledger、certificate、attestation、authority/store 或优化历史。

### 3. 查看当前交付路由

```powershell
ai-sdlc run --dry-run
ai-sdlc run
```

两个入口都只读取五个 Loop 的当前 Result、Next 和 Blockers，不执行任务、不写 checkpoint，也不自动提交。旧 `--mode confirm` 与 `--acknowledge-execute-batch` 仅返回迁移提示，不再启动七阶段执行器。

### 命令前升级提示

安装版 CLI 在业务命令执行前使用本地缓存判断是否有新版本。经验证的外部 stable shim 与 `python -m ai_sdlc` 入口支持 TTY 用户确认后先升级，再精确重放原命令。Windows 运行时目录内的 direct `Scripts\ai-sdlc.exe` 在活动期间无法安全替换：自动检查只输出可执行迁移提示、零安装并让当前业务命令继续一次，显式 direct self-update 则不修改安装且非零退出。非 TTY、Codex、Cursor 和 Claude Code 获得 stderr 中的一行 `AI_SDLC_UPDATE_NOTICE` 结构化提示，原命令继续执行。`--json` 的 stdout 始终保持可解析。拒绝升级、离线、超时或检查失败不会阻断原命令；`self-update`、帮助、补全以及源码/`uv run` 开发环境不会触发自动覆盖安装。

### 4. 恢复工作

```powershell
ai-sdlc recover
ai-sdlc recover --reconcile
ai-sdlc handoff status
```

恢复逻辑会比较 checkpoint、当前分支和项目工件，避免把过期状态直接当作当前事实。

## Codex 与 Shell 配置

初始化时可一次完成选择：

```powershell
ai-sdlc init . --agent-target codex --shell powershell
```

也可以分别调整：

```powershell
ai-sdlc adapter select --agent-target codex
ai-sdlc adapter shell-select --shell powershell
ai-sdlc adapter status --details
```

项目规则的公开真值位于：

- `AGENTS.md`：Codex 项目入口与执行约束；
- `src/ai_sdlc/rules/pipeline.md`：流水线阶段和门禁语义；
- `src/ai_sdlc/rules/git-branch-rule.md`：分支与提交规则。

## 本地对抗 PR 审查

先检查本地审查条件：

```powershell
ai-sdlc pr-review doctor
```

预览当前工作区审查输入：

```powershell
ai-sdlc pr-review start --diff-source local-unstaged --dry-run
```

正式审查可使用本地代理提供方；代码外发默认关闭：

```powershell
ai-sdlc pr-review start --diff-source local-git-range --base main --head HEAD --provider local-agent
ai-sdlc pr-review status
ai-sdlc pr-review fix
ai-sdlc pr-review rerun
ai-sdlc loop review --type local-pr-review --loop-id <loop-id> --json
ai-sdlc pr-review close --review-id <review-id> --loop-id <loop-id> --expect-review-digest <input_digest>
```

Local PR Review 在 close 前由独立的本地只读代理检查 Review Pack、findings、修复结果和当前 Git 变更。若代理执行失败或发现仍未解决，审查保持未关闭；通过后直接生成普通最终报告。该流程不生成 CI 证书、attestation、authority pointer 或持久化专家会话。

## 前端工程能力

AI-SDLC 将前端质量作为可验证交付的一部分：

- 方案来自当前项目事实或用户明确选择，不固定框架、组件库或 style pack；
- 确认前不写业务文件，应用时执行路径、范围和回滚保护；
- 浏览器探针输出结构化检查回执并绑定当前源码摘要；
- 支持截图、视觉差异、可访问性和主题令牌治理；
- 视觉/可访问性结果进入同一个 Frontend Evidence review snapshot；
- 源码或证据摘要漂移时拒绝 Close。

## 离线打包

离线包会包含 AI-SDLC wheel、依赖 wheel、安装脚本、包内 `SHA256SUMS` 校验清单和可选的 Python 运行时。每个正式压缩包同时发布同名 `.sha256` 文件。`v3.0.1` 的正式产物名称为：

- `ai-sdlc-offline-3.0.1-windows-amd64.zip`
- `ai-sdlc-offline-3.0.1-macos-arm64.tar.gz`
- `ai-sdlc-offline-3.0.1-linux-amd64.tar.gz`

具体下载与校验命令见[中文用户指南](USER_GUIDE.zh-CN.md)。

构建入口：

```powershell
bash packaging/offline/build_offline_bundle.sh
```

产物写入 `dist-offline/`。发布前应运行安装 smoke 和完整性校验，具体命令见 [离线打包说明](packaging/offline/README.md)。

## 质量验证

```powershell
uv run pytest -q
uv run ruff check src tests scripts
uv run ai-sdlc verify constraints --profile self-development
uv build
```

公开交付树还提供发行身份门禁：

```powershell
uv run python scripts/validate_public_release_identity.py .
```

成功时输出 `PUBLIC_RELEASE_IDENTITY_VALID`。

## 文档

- [中文用户指南](USER_GUIDE.zh-CN.md)
- [v3 迁移说明](docs/v3-migration.zh-CN.md)
- [v2 迁移说明](docs/v2-migration.zh-CN.md)
- [产品能力契约](docs/product-contract.md)
- [Pull Request 检查清单](docs/pull-request-checklist.zh.md)
- [框架自迭代开发与发布约定](docs/框架自迭代开发与发布约定.md)
- [离线打包说明](packaging/offline/README.md)

## 许可证

本项目使用 MIT License，详见 [LICENSE](LICENSE)。
