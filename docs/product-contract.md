# AI-SDLC 1.0.0 产品能力契约

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
- Frontend Evidence Loop：页面契约、浏览器证据、视觉与可访问性。

### 质量治理

- 项目规则与 Git 分支约束；
- 任务级验收与门禁一致性；
- 前端契约、交付上下文和浏览器探针；
- 本地对抗 PR 审查与 CI attestation；
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

## 1.0.0 发布真值

- Python 包版本：`1.0.0`；
- Git 仓库：`https://github.com/SinclairPan/Ai_AutoSDLC`；
- 当前发行说明：`docs/releases/v1.0.0.md`；
- Windows 包：`ai-sdlc-offline-1.0.0-windows-amd64.zip`；
- macOS 包：`ai-sdlc-offline-1.0.0-macos-arm64.tar.gz`；
- Linux 包：`ai-sdlc-offline-1.0.0-linux-amd64.tar.gz`。

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
