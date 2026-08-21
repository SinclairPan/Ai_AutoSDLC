# AI-SDLC 3.0.1 产品能力契约

## 产品定位

AI-SDLC 是面向 AI 代理与工程团队的本地研发治理框架。它负责读取项目事实、固化工程规则、组织可恢复流水线、运行质量门禁，并把每次推进转化为可验证的本地证据。

项目地址：<https://github.com/SinclairPan/Ai_AutoSDLC>

## 核心原则

1. 本地项目事实优先于会话记忆。
2. 需求、设计、任务、实现和测试必须可追踪。
3. 门禁失败必须保留为明确状态，不得伪造完成。
4. 高影响动作必须支持预演、确认或恢复。
5. 代码外发默认关闭，凭据不得写入仓库。
6. 自动化结论必须能由命令、工件或测试复核。

## 能力边界

### 项目入口

- 初始化新项目并生成项目配置；
- 只读接入已有项目；
- 扫描语言、依赖、测试、入口和风险；
- 为 Codex 生成 `AGENTS.md` 项目入口；
- 持久化 PowerShell、Bash、Zsh、Cmd 或自动选择；
- 安装版 CLI 在业务命令前读取更新缓存：外部 stable shim 与 `python -m ai_sdlc` 仅在 TTY 用户明确确认后升级并重放原命令；Windows runtime-local direct `Scripts\ai-sdlc.exe` 只给出迁移提示、零安装并继续当前业务，显式 direct self-update 非零退出；Agent、非 TTY 和 JSON 路径只在 stderr 输出稳定单行提示，不污染业务 stdout。

### 流水线与恢复

- 以 checkpoint 表达阶段、分支、开放门禁和执行模式；
- 支持 dry-run、确认执行、状态查看与事实对齐；
- 支持 Codex handoff，避免跨会话丢失关键上下文；
- 对过期分支、工件缺失和状态漂移给出阻断或修复指引。

### Loop Engineering

- Requirement Loop：目标、范围、验收标准和风险；
- Design Contract Loop：接口、数据、边界和验证策略；
- Implementation Loop：任务、代码、测试和关闭证据；
- Frontend Evidence Loop：页面契约、浏览器证据、视觉与可访问性；
- Local PR Review：提交前由独立本地只读代理执行跨阶段审查。

五个 Loop 的实质结果均由 CLI 按内容选择最多两种专家角色，再由 Codex、Claude Code、Cursor 或 VS Code 中的当前宿主 Agent 自动为每个角色启动一个全新只读上下文。专家只读取与 `input_digest` 同次获取的内联 `review_snapshot`，不得在复核期间重新打开可变工件路径。宿主 Agent 用 `loop review-record` 汇总当前轮结果；有发现时由原实现代理修复，并只允许一次复审；通过后调用既有 close。专家失败时保留 `needs_review`，不得要求用户手动触发专家。

框架只在原 Loop 目录保存固定的 `review-outcome-round-1.json`，修复后至多再保存 `review-outcome-round-2.json`，用于防止缺失结果、角色不完整和摘要漂移。它不保存专家上下文或长期身份，也不创建 session、ledger、certificate、attestation、authority/store 或第三轮结果。

代码精简只提供非阻断建议。它不改变 Loop 状态，不产生强制修复、receipt、例外或 No-Go，也不阻止 close。

正常用户入口 `ai-sdlc run` 只读取当前五 Loop 真值，并返回 Result、Next、Blockers 和当前 Loop 最多两个内置规则片段。选择只依赖结构化 Loop 类型与状态，不扫描需求关键词、不联网、不写项目状态，也不建立规则平台。通用规则不得硬编码具体前端框架、组件库、provider 或 style pack；前端实现前只要求根据项目事实给出推荐方案、可选方案并等待用户确认。

`ai-sdlc status` 默认只显示 Result、Next、Blockers；详细人类诊断保留在 `--details`，详细机器合同保留在 `--json`。顶层帮助只展示正常用户入口；已经退役的平行 Program、Telemetry、Provenance、AgentOps、Studio 和 Host Runtime 命令不再注册，显式调用返回未知命令。

### 质量治理

- 项目规则与 Git 分支约束；
- 任务级验收与门禁一致性；
- 前端契约、交付上下文和浏览器探针；
- 本地独立对抗 PR 审查；
- 发布身份、文档、离线包和工作流一致性。

### 运行集成

- 本地 Continuity handoff 与五 Loop 状态恢复；
- 受支持安装入口的命令前升级提示、离线升级与失败恢复；
- Windows、macOS、Linux 离线交付。

## 非目标

- 不替代源代码托管、CI 平台或制品仓库；
- 不在缺少证据时自动宣告项目完成；
- 不绕过组织权限执行合并、发布或生产变更；
- 不默认向远程模型发送代码；
- 不在项目文件中保存密钥或令牌值。
- 不提供 Shadow/Enforce 激活体系、close certificate、review session/ledger、离线优化、资源治理或阻断式 Lean governance；这些旧能力已删除，不是隐藏开关或后续默认路线。

## 当前源码与公开版本

- Python 源码版本：`3.0.1`；
- Git 仓库：`https://github.com/SinclairPan/Ai_AutoSDLC`；
- 当前公开稳定版本：`v3.0.1`，其安装与校验入口见 `USER_GUIDE.zh-CN.md`；
- `ai-sdlc-offline-3.0.1-windows-amd64.zip`、`ai-sdlc-offline-3.0.1-macos-arm64.tar.gz`、`ai-sdlc-offline-3.0.1-linux-amd64.tar.gz` 是正式离线产物名；
- `v3.0.1` 仅启用面向全新用户的 12 条安装、初始化与恢复路线并修正文档合同，运行时能力与 `v3.0.0` 保持一致；
- `v2.0.0` 相对 `v1.0.2` 删除了旧审查治理入口，历史升级说明见 `docs/v2-migration.zh-CN.md`；
- `v3.0.0` 删除了 `v2.0.0` 中公开但已退出产品边界的旧顶层命令；升级说明见 `docs/v3-migration.zh-CN.md`；
- 新版本只通过普通 GitHub Release、tag、跨平台 smoke 和分支保护发布，不建立 Release Proof、Certificate、attestation、generation burn 或额外 authority/store。

## 验收接口

```powershell
ai-sdlc --version
ai-sdlc adapter status
ai-sdlc status
ai-sdlc run --dry-run
ai-sdlc verify constraints
python scripts/validate_public_release_identity.py .
```

上述命令默认验证普通用户项目；仅 AI-SDLC 仓库自身维护使用
`ai-sdlc verify constraints --profile self-development`。

交付关闭还必须通过测试、lint、构建、离线包完整性校验和目标平台 smoke。
