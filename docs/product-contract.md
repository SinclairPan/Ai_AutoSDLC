# AI-SDLC 1.0.5 产品能力契约

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
- 持久化 PowerShell、Bash、Zsh、Cmd 或自动选择。

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

五个 Loop 的实质结果均由当前代理按内容自动选择最多两名只读专家复核。专家只读取与 `input_digest` 同次获取的内联 `review_snapshot`，不得在复核期间重新打开可变工件路径。有发现时由原实现代理修复，并只允许一次复审；通过后调用既有 close。专家失败时保留 `needs_review`，不创建持久化专家身份、session、ledger、certificate、attestation 或 authority/store。

代码精简只提供非阻断建议。它不改变 Loop 状态，不产生强制修复、receipt、例外或 No-Go，也不阻止 close。

### 质量治理

- 项目规则与 Git 分支约束；
- 任务级验收与门禁一致性；
- 前端契约、交付上下文和浏览器探针；
- 本地独立对抗 PR 审查；
- 发布身份、文档、离线包和工作流一致性。

### 运行集成

- AgentOps outbox、状态诊断与重试；
- 企业端点、策略和凭据环境变量名配置；
- 结构化遥测、provenance 和 trace 命令；
- Windows、macOS、Linux 离线交付。

## 非目标

- 不替代源代码托管、CI 平台或制品仓库；
- 不在缺少证据时自动宣告项目完成；
- 不绕过组织权限执行合并、发布或生产变更；
- 不默认向远程模型发送代码；
- 不在项目文件中保存密钥或令牌值。
- 不提供 Shadow/Enforce 激活体系、close certificate、review session/ledger、离线优化、资源治理或阻断式 Lean governance；这些旧能力已删除，不是隐藏开关或后续默认路线。

## 当前源码与公开版本

- Python 源码版本：`1.0.5`；
- Git 仓库：`https://github.com/SinclairPan/Ai_AutoSDLC`；
- 当前公开稳定版本：`v1.0.2`，其安装与校验入口见 `USER_GUIDE.zh-CN.md`；
- `ai-sdlc-offline-1.0.5-windows-amd64.zip`、`ai-sdlc-offline-1.0.5-macos-arm64.tar.gz`、`ai-sdlc-offline-1.0.5-linux-amd64.tar.gz` 是当前源码的候选产物名；
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

交付关闭还必须通过测试、lint、构建、离线包完整性校验和目标平台 smoke。
