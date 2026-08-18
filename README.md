# AI-SDLC 2.0.0

AI-SDLC 是一个本地优先、可恢复、可验证的 AI 原生软件研发框架。它把需求澄清、设计契约、任务执行、质量门禁、对抗审查和交付证据组织成一套可由 AI 代理与工程师共同执行的命令行工作流。

项目地址：<https://github.com/SinclairPan/Ai_AutoSDLC>

> 当前公开稳定版本为 `v2.0.0`。普通用户请按[中文用户指南](USER_GUIDE.zh-CN.md)安装；从 `v1.0.2` 升级前请先阅读 [v2 迁移说明](docs/v2-migration.zh-CN.md)。

## 核心特性

| 能力 | 说明 |
| --- | --- |
| 项目初始化与接入 | `init` 为新项目建立规则、状态与代理入口；`adopt` 在不修改业务文件的前提下识别已有项目事实。 |
| Codex 项目适配 | 以 `AGENTS.md` 作为项目级指令入口，可持久化 Codex 与 PowerShell、Bash、Zsh 或 Cmd 偏好。 |
| 可恢复流水线 | checkpoint 记录执行阶段、开放门禁和下一步动作，支持 `status`、`recover` 与 `run --dry-run`。 |
| Loop Engineering | 内置 requirement、design-contract、implementation、frontend-evidence、local-pr-review 五类闭环。 |
| 动态专家复核 | 每个 Loop 的实质结果由当前代理按内容选择最多两名只读专家；只允许一轮修复复审，不持久化专家权威。 |
| 精简建议 | 对代码体积和复杂度给出非阻断建议；建议不改变 Loop 状态，也不阻止 close。 |
| 质量与治理门禁 | 对规则、任务、约束、分支、文档契约、前端证据和关闭条件执行只读验证。 |
| 本地对抗审查 | `pr-review` 支持 Git 范围、暂存区、工作区和补丁输入，在提交前由独立只读代理检查当前变更。 |
| 前端交付治理 | 覆盖页面契约、生成约束、组件提供方、浏览器探针、视觉回归、可访问性和交付上下文。 |
| AgentOps 集成 | 可输出运行事件、保存 outbox、检查网关配置并重试投递，不在仓库内保存令牌值。 |
| 跨平台交付 | 支持 Windows、macOS、Linux 的源码安装、在线安装和带 Python 运行时的离线包。 |
| 本地优先 | 核心扫描、规则解析、门禁、Loop 和审查编排均可在本地执行；代码外发默认关闭。 |

## 安装

运行要求：Python 3.11 或更高版本、Git。源码开发推荐使用 [uv](https://docs.astral.sh/uv/)。

### 从 Git 安装

```powershell
python -m pip install "git+https://github.com/SinclairPan/Ai_AutoSDLC.git@v2.0.0"
ai-sdlc --version
```

版本输出应为 `2.0.0`。

需要验证尚未发布的开发版时，可显式把安装地址末尾改为 `@main`；开发版不承诺输出稳定版版本号。

### 从源码运行

```powershell
git clone --branch v2.0.0 --depth 1 https://github.com/SinclairPan/Ai_AutoSDLC.git
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

初始化完成后，按命令输出中的 `Result / Next` 进入 Codex 对话并提交需求。`adapter status`、`status` 和 `run --dry-run` 用于异常排查，无需在正常初始化后重复执行。

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
ai-sdlc rules show
ai-sdlc verify constraints
```

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
ai-sdlc loop frontend-evidence start --wi specs/<work-item> --loop-id <loop-id>
ai-sdlc loop status --type frontend-evidence
ai-sdlc loop review --type frontend-evidence --loop-id <loop-id> --json
ai-sdlc loop frontend-evidence close --loop-id <loop-id> --expect-review-digest <input_digest> --yes
```

每个 Loop 都从本地工件计算状态，输出缺口、停止原因和下一步动作。`loop review` 会按内容返回最多两个 `expert_roles`；Codex、Claude Code、Cursor 或 VS Code 中的宿主 Agent 自动为每个角色启动独立只读上下文，并用 `loop review-record` 记录本轮结果，不要求用户手动触发专家。专家读取待审工件时使用同一 `loop review` 命令并追加 `--expect-digest <input_digest> --read-path <artifact_path>`，只检查返回的 `review_snapshot`，不重新打开可变路径。`review-record` 返回 `passed` 后，`input_digest` 必须原样传给紧随其后的 close/freeze；输入或目标身份发生变化时会拒绝关闭。Frontend Evidence 有告警时可显式追加 `--allow-warnings`；没有可用浏览器提供方时可使用 `ai-sdlc loop frontend-evidence skip`，需要重新采集时运行 `ai-sdlc program browser-gate-probe --execute`。

### 非阻断精简建议

Implementation Loop 可以根据变更内容生成代码体积、重复、复杂度或拆分建议。该报告只用于帮助实现者保持代码精简；它不产生 BLOCKER/REQUIRED，不改变 Loop 状态，不要求 receipt、例外、No-Go 或额外治理工件，也不阻止现有 close 命令。实现者可以基于行为正确性、维护成本和交付价值选择采纳或说明不采纳。

五个 Loop 的实质结果都采用同一个最小复核边界：CLI 按结果风险选择最多两种专家角色，宿主 Agent 自动执行并记录结果；有发现时由原实现代理修复，并只允许一次复审；通过后由当前代理调用既有 close。专家不可用时结果停留在 `needs_review`，不会把失败解释为通过。框架只在原 Loop 目录保留第一轮以及至多一个第二轮 outcome，不创建第三轮、session、ledger、certificate、attestation、authority/store 或优化历史。

### 3. 查看当前交付路由

```powershell
ai-sdlc run --dry-run
ai-sdlc run
```

两个入口都只读取五个 Loop 的当前 Result、Next 和 Blockers，不执行任务、不写 checkpoint，也不自动提交。旧 `--mode confirm` 与 `--acknowledge-execute-batch` 仅返回迁移提示，不再启动七阶段执行器。

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

- 页面/UI Schema 与生成约束绑定；
- 组件提供方和运行时适配器有明确边界；
- 浏览器探针输出结构化检查回执；
- 支持截图、视觉差异、可访问性和主题令牌治理；
- 交付上下文贯穿生成、验证、应用和关闭阶段；
- 管理式变更在写入前执行路径、范围和回滚保护。

## AgentOps

配置网关后，AI-SDLC 可将运行事实写入本地 outbox 并投递到 AgentOps：

```powershell
ai-sdlc agentops doctor
ai-sdlc agentops status
ai-sdlc agentops retry
```

企业配置只记录端点、策略和令牌环境变量名：

```powershell
ai-sdlc enterprise configure --help
```

完整配置见 [企业 AgentOps 接入说明](docs/enterprise-agentops-setup.zh-CN.md)。

## 离线打包

离线包会包含 AI-SDLC wheel、依赖 wheel、安装脚本、包内 `SHA256SUMS` 校验清单和可选的 Python 运行时。每个正式压缩包同时发布同名 `.sha256` 文件。`v2.0.0` 的正式产物名称为：

- `ai-sdlc-offline-2.0.0-windows-amd64.zip`
- `ai-sdlc-offline-2.0.0-macos-arm64.tar.gz`
- `ai-sdlc-offline-2.0.0-linux-amd64.tar.gz`

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
- [v2 迁移说明](docs/v2-migration.zh-CN.md)
- [产品能力契约](docs/product-contract.md)
- [Pull Request 检查清单](docs/pull-request-checklist.zh.md)
- [框架自迭代开发与发布约定](docs/框架自迭代开发与发布约定.md)
- [离线打包说明](packaging/offline/README.md)

## 许可证

本项目使用 MIT License，详见 [LICENSE](LICENSE)。
