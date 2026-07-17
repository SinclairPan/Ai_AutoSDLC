# AI-SDLC 1.0.0

AI-SDLC 是一个本地优先、可恢复、可验证的 AI 原生软件研发框架。它把需求澄清、设计契约、任务执行、质量门禁、对抗审查和交付证据组织成一套可由 AI 代理与工程师共同执行的命令行工作流。

项目地址：<https://github.com/SinclairPan/Ai_AutoSDLC>

## 核心特性

| 能力 | 说明 |
| --- | --- |
| 项目初始化与接入 | `init` 为新项目建立规则、状态与代理入口；`adopt` 在不修改业务文件的前提下识别已有项目事实。 |
| Codex 项目适配 | 以 `AGENTS.md` 作为项目级指令入口，可持久化 Codex 与 PowerShell、Bash、Zsh 或 Cmd 偏好。 |
| 可恢复流水线 | checkpoint 记录执行阶段、开放门禁和下一步动作，支持 `status`、`recover` 与 `run --dry-run`。 |
| Loop Engineering | 内置 requirement、design-contract、implementation、frontend-evidence 四类确定性闭环。 |
| 质量与治理门禁 | 对规则、任务、约束、分支、文档契约、前端证据和关闭条件执行只读验证。 |
| 本地对抗审查 | `pr-review` 支持 Git 范围、暂存区、工作区和补丁输入，可生成修复计划、复审结果与 CI 证明。 |
| 前端交付治理 | 覆盖页面契约、生成约束、组件提供方、浏览器探针、视觉回归、可访问性和交付上下文。 |
| AgentOps 集成 | 可输出运行事件、保存 outbox、检查网关配置并重试投递，不在仓库内保存令牌值。 |
| 跨平台交付 | 支持 Windows、macOS、Linux 的源码安装、在线安装和带 Python 运行时的离线包。 |
| 本地优先 | 核心扫描、规则解析、门禁、Loop 和审查编排均可在本地执行；代码外发默认关闭。 |

## 安装

运行要求：Python 3.11 或更高版本、Git。源码开发推荐使用 [uv](https://docs.astral.sh/uv/)。

### 从 Git 安装

```powershell
python -m pip install "git+https://github.com/SinclairPan/Ai_AutoSDLC.git@main"
ai-sdlc --version
```

版本输出应为 `1.0.0`。

### 从源码运行

```powershell
git clone https://github.com/SinclairPan/Ai_AutoSDLC.git
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
ai-sdlc loop requirement
ai-sdlc loop design-contract
ai-sdlc loop implementation
ai-sdlc loop frontend-evidence
ai-sdlc loop status
```

每个 Loop 都从本地工件计算状态，输出缺口、停止原因和下一步动作。只有证据满足关闭条件时，闭环才会进入完成状态。

### 3. 预演与执行

```powershell
ai-sdlc run --dry-run
ai-sdlc run --mode confirm
```

`--dry-run` 只运行门禁，不执行任务；`--mode confirm` 会在需要人工判断的位置停下确认。

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
ai-sdlc pr-review close
ai-sdlc pr-review attest
```

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

离线包会包含 AI-SDLC wheel、依赖 wheel、安装脚本、校验清单和可选的 Python 运行时。默认产物名称：

- `ai-sdlc-offline-1.0.0-windows-amd64.zip`
- `ai-sdlc-offline-1.0.0-macos-arm64.tar.gz`
- `ai-sdlc-offline-1.0.0-linux-amd64.tar.gz`

构建入口：

```powershell
bash packaging/offline/build_offline_bundle.sh
```

产物写入 `dist-offline/`。发布前应运行安装 smoke 和完整性校验，具体命令见 [离线打包说明](packaging/offline/README.md)。

## 质量验证

```powershell
uv run pytest -q
uv run ruff check src tests scripts
uv run ai-sdlc verify constraints
uv build
```

公开交付树还提供发行身份门禁：

```powershell
uv run python scripts/validate_public_release_identity.py .
```

成功时输出 `PUBLIC_RELEASE_IDENTITY_VALID`。

## 文档

- [中文用户指南](USER_GUIDE.zh-CN.md)
- [产品能力契约](docs/product-contract.md)
- [1.0.0 交付说明](docs/releases/v1.0.0.md)
- [Pull Request 检查清单](docs/pull-request-checklist.zh.md)
- [框架自迭代开发与发布约定](docs/框架自迭代开发与发布约定.md)
- [离线打包说明](packaging/offline/README.md)

## 许可证

本项目使用 MIT License，详见 [LICENSE](LICENSE)。
