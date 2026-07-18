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
| Lean Code 有界质量闭环 | 对新功能与 Bug 修复执行确定性风险评估、定向修复计划、最多两轮复评和结构化 No-Go。 |
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

### Lean Code 风险预算

Lean Code 是 Implementation Loop 的质量 Profile，不是新的顶层 Loop，也不是机械 LOC 门禁。手写产品文件 400 行、函数 50 行只是初始风险预算：单独超限为 ADVISORY；同时出现复杂度、重复、耦合或范围蔓延时才成为 REQUIRED；行为、安全、授权、兼容、验证或 artifact 完整性被破坏时为 BLOCKER。

新功能必须限制在已确认的 acceptance/tasks 范围内。Bug 修复还必须提供绑定当前 diff、RED/GREEN 输出 artifact 和测试源码 digest 的结构化回归证据；只填写退出码或任意字符串不能通过。generated/vendored 豁免必须有生成头或上游 provenance，不能只靠目录名。运行确定性评估：

```powershell
ai-sdlc loop implementation lean-check --loop-id <implementation-loop-id>
```

评估源可以选择 `local-unstaged`、`local-staged`、`local-git-range` 或项目内 patch。若使用非默认源，后续 `lean-verify` 以及 `lean-regression` 的 RED/GREEN 阶段必须重复传入同一组 `--diff-source / --base / --head / --patch-file` 参数；receipt 与评估会按精确 diff hash 交叉绑定，不会静默回退到工作区未暂存变更。

Bug 修复先由公开 CLI 真实执行同一回归 argv；`--` 后的参数不会经过 shell：

```powershell
ai-sdlc loop implementation lean-regression --loop-id <id> --phase red `
  --test-id <test-id> --test-source tests/test_bug.py `
  --failure-signature "assertion:<目标错误>" -- python -m pytest tests/test_bug.py -q
# 完成最小修复后运行同一命令
ai-sdlc loop implementation lean-regression --loop-id <id> --phase green `
  --test-id <test-id> --test-source tests/test_bug.py `
  --failure-signature "assertion:<目标错误>" -- python -m pytest tests/test_bug.py -q
```

如果第一轮产生可操作 finding，修复后用 `lean-verify --loop-id <id> --test-source <path> -- <argv>` 生成当前 diff 的执行 receipt。声明的 test source 必须由受控 runner 形态实际执行，例如 `python <path>`、`python -m pytest <path>::<node>`、`python -m py_compile <path>` 或直接 `pytest <path>`；只把路径放进 `python -c` 的普通参数、ignore/config 参数或输出文本不会被接受。把返回的 `receipt_path` 记录为 Implementation evidence；只记录命令文本不会推进第二轮。Implementation start 时冻结的任务内容不可事后修改；评估与 close 都会对照冻结摘要并 fail-closed。

评估只读取项目事实并写入 JSON/Markdown artifact，不调用模型，也不修改应用代码。BLOCKER/REQUIRED 会生成定向 fix plan，由 Implementation Agent 修改后再运行第二轮评估。最多两轮；在当前 enforcement mode 下，第二轮只要仍有未解决的 BLOCKER/REQUIRED，无论是否与上一轮相同，都会进入 `needs_user`，不会留下无法继续评估的悬空状态。close 与 PR 会重新验证 receipt、例外及其证据，删除或替换后旧结论立即失效。

确有边界风险时，可通过 `--exception <project-local-json>` 提交绑定 finding、scope、policy、commit、diff、有效期与证据 digest 的结构化例外。例外不会隐藏 finding，结论只能是 `risk_accepted`。如果降低指标只能破坏行为，或修复成本大于收益，可以记录 source-bound No-Go：

```powershell
ai-sdlc loop implementation lean-no-go --loop-id <implementation-loop-id> `
  --reason "Metric-only change would break behavior." `
  --owner "implementation-owner" `
  --repair-cost "behavioral regression" `
  --expected-benefit "one metric reduction" `
  --evidence ".ai-sdlc/evidence/no-go-proof.txt"
```

`report`、`warning`、`blocking` 分别用于只报告非完整性 REQUIRED、要求定向修复、以及阻断未解决 REQUIRED；artifact 完整性、scope drift、验证失败和无效例外始终 fail-closed。当前语义指标的精确 adapter 为 Python AST。TypeScript、Java、Go 等语言仍保留确定性 diff/分类指标；缺少可靠语义 adapter 时会明确标记 `unsupported` 并进入 `needs_user`，不会用零复杂度或零调用者制造假结论。Local PR Reviewer 与 Implementation Agent 保持独立，并将 report、snapshot、policy、findings 和 evaluation input 纳入 review-pack、final-report 与 attestation digest 链；内置审计证明独立进程与独立输入上下文，不宣称不同人类身份。需要职责分离时应另配 reviewer 账号/provider 并保留 actor/session 记录。CI 只验证确定性 artifact，不调用模型自动修复。

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
- [Pull Request 检查清单](docs/pull-request-checklist.zh.md)
- [框架自迭代开发与发布约定](docs/框架自迭代开发与发布约定.md)
- [离线打包说明](packaging/offline/README.md)

## 许可证

本项目使用 MIT License，详见 [LICENSE](LICENSE)。
