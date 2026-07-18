# AI-SDLC 1.0.0 中文用户指南

AI-SDLC 用一套本地命令把项目规则、AI 代理、工程闭环、质量门禁和交付证据连接起来。本指南以 Codex + PowerShell 为默认组合，同时给出 macOS、Linux 和 Windows 的安装方式。

项目地址：<https://github.com/SinclairPan/Ai_AutoSDLC>

## 1. 环境要求

- Python 3.11 或更高版本；
- Git；
- 源码开发推荐安装 `uv`；
- Windows 推荐 PowerShell 7，也兼容 Windows PowerShell 5.1；
- 离线环境使用同平台的 AI-SDLC 离线包。

先确认环境：

```powershell
python --version
git --version
```

## 2. 安装 AI-SDLC

### 2.1 从 Git 安装

Windows、macOS 和 Linux 都可以执行：

```powershell
python -m pip install "git+https://github.com/SinclairPan/Ai_AutoSDLC.git@main"
ai-sdlc --version
```

正确版本为：

```text
1.0.0
```

如果命令没有加入 PATH，可改用模块入口：

```powershell
python -m ai_sdlc --version
python -m ai_sdlc --help
```

### 2.2 从源码运行

```powershell
git clone https://github.com/SinclairPan/Ai_AutoSDLC.git
Set-Location Ai_AutoSDLC
uv sync
uv run ai-sdlc --version
```

在源码目录内，后续命令可把 `ai-sdlc` 替换为 `uv run ai-sdlc`。

### 2.3 使用离线包

离线包名称：

- Windows：`ai-sdlc-offline-1.0.0-windows-amd64.zip`
- macOS：`ai-sdlc-offline-1.0.0-macos-arm64.tar.gz`
- Linux：`ai-sdlc-offline-1.0.0-linux-amd64.tar.gz`

#### Windows

```powershell
$PackageName = "ai-sdlc-offline-1.0.0-windows-amd64.zip"
$BundleName = "ai-sdlc-offline-1.0.0-windows-amd64"
$InstallRoot = Join-Path (Get-Location) ".ai-sdlc-install"

New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
Expand-Archive -LiteralPath $PackageName -DestinationPath $InstallRoot -Force
Set-Location (Join-Path $InstallRoot $BundleName)
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_offline.ps1 -AddToPath
.\.venv\Scripts\ai-sdlc.exe --version
```

安装脚本会在包目录创建独立虚拟环境。重新打开终端后，可直接运行 `ai-sdlc`。

#### macOS

```bash
tar xzf ai-sdlc-offline-1.0.0-macos-arm64.tar.gz
cd ai-sdlc-offline-1.0.0-macos-arm64
./install_offline.sh --add-to-path
./.venv/bin/ai-sdlc --version
```

#### Linux

```bash
tar xzf ai-sdlc-offline-1.0.0-linux-amd64.tar.gz
cd ai-sdlc-offline-1.0.0-linux-amd64
./install_offline.sh --add-to-path
./.venv/bin/ai-sdlc --version
```

## 3. 初始化项目：Codex + PowerShell

进入目标项目目录：

```powershell
Set-Location D:\work\my-project
ai-sdlc init . --agent-target codex --shell powershell
```

命令会深度扫描项目，准备 `.ai-sdlc/` 目录、checkpoint、项目规则和 `AGENTS.md`，然后自动运行安全预演。

初始化完成后，按命令输出中的 `Result / Next` 进入 Codex 对话并提交需求。对新项目而言，安全预演显示开放门禁是正常结果；它表示下一步需要补充需求、设计、任务或测试证据，并不表示初始化失败。

只有在初始化异常或需要重新核对配置时，才运行 `ai-sdlc adapter status --details`、`ai-sdlc status` 或 `ai-sdlc run --dry-run`。

### 3.1 分别选择代理与 Shell

```powershell
ai-sdlc adapter select --agent-target codex
ai-sdlc adapter shell-select --shell powershell
ai-sdlc adapter status
```

Codex 会读取项目根目录的 `AGENTS.md`。如果规则更新，重新执行 `adapter select` 可刷新适配文件。

## 4. 接入已有项目

`adopt` 会读取项目结构、候选任务文件和 Git 事实，不修改业务源文件：

```powershell
ai-sdlc adopt .
```

需要机器可读结果时：

```powershell
ai-sdlc adopt . --json
```

项目较大时可限制扫描预算：

```powershell
ai-sdlc adopt . --max-candidate-files 60 --max-file-bytes 65536 --max-recent-commits 20
```

完成接入后运行：

```powershell
ai-sdlc scan .
ai-sdlc index .
ai-sdlc verify constraints
```

## 5. 日常研发工作流

### 5.1 查看当前状态

```powershell
ai-sdlc status
ai-sdlc loop status
ai-sdlc handoff status
```

重点关注：当前阶段、开放门禁、checkpoint、当前分支、下一步动作和待补证据。

### 5.2 需求闭环

```powershell
ai-sdlc loop requirement
```

需求闭环检查目标、范围、验收标准、风险和未决问题。输入不足时会给出停止原因，不会伪造完成状态。

### 5.3 设计契约闭环

```powershell
ai-sdlc loop design-contract
```

设计契约闭环检查接口、数据模型、边界条件、迁移策略和验证计划是否能够支撑实现。

### 5.4 实现闭环

```powershell
ai-sdlc loop implementation
```

实现闭环比较任务、代码变更、测试与质量门禁，输出可执行的缺口列表。

### 5.5 Lean Code 有界质量闭环

Lean Code 是 Implementation Loop 的质量 Profile。它用确定性证据限制新功能和 Bug 修复的范围、风险与关闭条件，不会创建新的顶层 Loop，也不会自动修改业务代码。

运行评估：

```powershell
ai-sdlc loop implementation lean-check --loop-id <implementation-loop-id>
```

也可以省略 `--loop-id`，使用当前 Implementation pointer。命令支持 `--json`，并始终说明 `Result / Next`、finding 数量、artifact 路径、是否调用模型和是否写应用代码。

评估源支持 `local-unstaged`、`local-staged`、`local-git-range` 和项目内 patch。若 `lean-check` 选择了非默认源，`lean-verify` 以及 `lean-regression` 的 RED/GREEN 阶段必须重复使用同一组 `--diff-source / --base / --head / --patch-file` 参数。CLI 会把完整 source tuple 传入受控执行器；receipt 与评估按精确 diff hash 绑定，不会自动改用其他工作区视图。

判定方式：

- `BLOCKER`：artifact/policy/input 损坏或过期、未批准的 scope drift、验证失败、行为或安全合同破坏、无效例外；不能关闭。
- `REQUIRED`：文件/函数超出初始预算并伴随复杂度、重复、耦合或范围风险，Bug 修复缺少 RED/GREEN 证据，或新增公共抽象少于 3 个真实调用者；需要定向处理或显式风险决策。
- `ADVISORY`：只有 400/50 数值超限、历史债务或不影响关闭的可读性机会；可以保留并进入最终报告。

新功能只允许覆盖冻结的 acceptance/tasks。Bug 修复还需要同一目标断言先失败、后通过的结构化证据。用 CLI 执行同一 argv；`--` 后的参数不会交给 shell：

```powershell
ai-sdlc loop implementation lean-regression --loop-id <implementation-loop-id> `
  --phase red --test-id <test-id> --test-source tests/test_bug.py `
  --failure-signature "assertion:<目标错误>" -- python -m pytest tests/test_bug.py -q
# 完成最小修复后，使用完全相同的 test-id、test-source、signature 和 argv
ai-sdlc loop implementation lean-regression --loop-id <implementation-loop-id> `
  --phase green --test-id <test-id> --test-source tests/test_bug.py `
  --failure-signature "assertion:<目标错误>" -- python -m pytest tests/test_bug.py -q
ai-sdlc loop implementation lean-check --loop-id <implementation-loop-id> `
  --regression-evidence <GREEN 返回的 evidence_path>
```

运行时 receipt 会绑定 source snapshot、退出码、stdout/stderr、测试源码、受控 runner adapter、可执行文件字节和依赖锁环境。当前接受 `python <path>`、`python -m pytest <path>::<node>`、`python -m py_compile <path>`、直接 `pytest <path>` 等可解释形态；只把路径放进 `python -c` 的普通参数、ignore/config 参数或输出文本不会通过。

例外通过项目内 JSON artifact 传入：

```powershell
ai-sdlc loop implementation lean-check `
  --loop-id <implementation-loop-id> `
  --exception "evidence/lean-exception.json"
```

例外必须绑定 rule/finding、path 或 symbol、scope、policy、base/head、diff、有效期、负责人、审批人和证据 digest。有效例外保留原 finding，最终状态为 `risk_accepted`；缺字段、证据不存在、digest 不匹配或已过期时直接 BLOCKER。

第一次评估产生 BLOCKER/REQUIRED 后，Implementation Agent 只能按 fix plan 做定向修改，并真实执行定向验证：

```powershell
ai-sdlc loop implementation lean-verify --loop-id <implementation-loop-id> `
  --test-source tests/test_target.py -- python -m pytest tests/test_target.py -q
ai-sdlc loop implementation record --loop-id <implementation-loop-id> `
  --task-id <task-id> --status done --evidence <返回的 receipt_path>
```

然后再运行第二次评估。只填写 `--verification` 文本不算执行证据。Implementation start 时冻结的任务内容不能在评估前或关闭前改写；当前任务的语义摘要必须持续匹配冻结值。最多两轮；在当前 enforcement mode 下，第二轮只要仍有未解决的 BLOCKER/REQUIRED，无论 finding 是否与上一轮相同，都会进入 `needs_user`。如果修复只能破坏行为，或成本明显高于收益，记录结构化 No-Go：

```powershell
ai-sdlc loop implementation lean-no-go `
  --loop-id <implementation-loop-id> `
  --reason "Metric-only change would break behavior." `
  --owner "implementation-owner" `
  --repair-cost "behavioral regression" `
  --expected-benefit "one metric reduction" `
  --evidence "evidence/no-go-proof.txt"
```

No-Go 会写入绑定当前 report/diff 的决策 artifact，并将现有 Loop 置为 `needs_user`；它不会新增状态枚举或修改应用代码。

generated/vendored 文件只有带生成头或可核验上游 provenance 时才享受独立分类；目录名、后缀或 `vendor/` 路径本身不是豁免证据。`report` 模式只报告非完整性 REQUIRED，`warning` 要求定向修复，`blocking` 阻断未解决 REQUIRED；完整性、scope drift、验证失败与无效例外始终 fail-closed。close 与 PR 会重新读取 receipt、例外和证据；删除、替换、跨 work item 重绑或过期都会使旧结论失效。

能力边界：当前精确语义 adapter 使用 Python AST。TypeScript、Java、Go 等语言仍计算可重复的 diff、文件分类和行数指标；没有可靠 parser 时语义能力标记为 `unsupported` 并进入 `needs_user`，不把缺失测量写成零风险。Local PR Reviewer 必须与 Implementation Agent 独立，消费 fresh Lean report；report、snapshot、policy、findings、evaluation input、review-pack、final-report 和 attestation 通过 digest 链相互绑定。内置审计证明独立进程和独立输入上下文，不冒充不同人类身份；需要职责分离时应配置不同 reviewer 账号/provider，并在治理系统中保留 actor/session 记录。

### 5.6 前端证据闭环

```powershell
ai-sdlc loop frontend-evidence
```

前端闭环可组合浏览器探针、视觉证据、可访问性结果、页面契约和交付上下文。

### 5.7 安全预演与确认执行

```powershell
ai-sdlc run --dry-run
ai-sdlc run --mode confirm
```

- `--dry-run`：只计算门禁，不执行任务；
- `--mode confirm`：在需要人工判断的动作前停下确认；
- 默认 `auto`：只在规则允许时自动推进。

## 6. 恢复与连续工作

```powershell
ai-sdlc recover
ai-sdlc recover --reconcile
```

当 checkpoint 与当前分支或项目工件不一致时，使用 `--reconcile` 重新对齐事实。恢复完成后再次执行：

```powershell
ai-sdlc status
ai-sdlc verify constraints
ai-sdlc run --dry-run
```

Codex 连续工作可使用 handoff 命令：

```powershell
ai-sdlc handoff --help
```

## 7. 质量门禁

### 7.1 约束验证

```powershell
ai-sdlc verify constraints
```

该命令只读检查规则文件、checkpoint、任务验收、前端契约与发布面。存在 BLOCKER 时退出码为 1。

### 7.2 Gate 命令

```powershell
ai-sdlc gate --help
```

Gate 输出应被当作交付证据，而不是普通提示。任何被标记为 BLOCKER 的问题都必须在关闭前解决。

### 7.3 项目自检

```powershell
ai-sdlc doctor
ai-sdlc adapter status --details
```

`doctor` 检查解释器、命令路径和常见 shim 位置；`adapter status` 检查代理入口与项目规则是否一致。

## 8. 本地对抗 PR 审查

检查审查运行条件：

```powershell
ai-sdlc pr-review doctor
```

预览工作区输入：

```powershell
ai-sdlc pr-review start --diff-source local-unstaged --dry-run
```

审查分支差异：

```powershell
ai-sdlc pr-review start --base main --head HEAD --diff-source local-git-range --provider local-agent
```

处理审查结果：

```powershell
ai-sdlc pr-review status
ai-sdlc pr-review fix
ai-sdlc pr-review rerun
ai-sdlc pr-review close
ai-sdlc pr-review attest
```

默认策略禁止代码外发。只有在组织策略允许且用户明确确认时，才可启用远程模型代码输入。

## 9. AgentOps 接入

AI-SDLC 使用环境变量读取 AgentOps 网关与令牌，不把令牌值写入仓库。

配置完成后检查：

```powershell
ai-sdlc agentops doctor
ai-sdlc agentops status
```

本地 outbox 投递失败时：

```powershell
ai-sdlc agentops retry
```

企业配置入口：

```powershell
ai-sdlc enterprise configure --help
```

详细字段见 [企业 AgentOps 接入说明](docs/enterprise-agentops-setup.zh-CN.md)。

## 10. 离线包构建

在 AI-SDLC 源码根目录执行：

```powershell
bash packaging/offline/build_offline_bundle.sh
```

产物位于 `dist-offline/`。打包脚本读取 `pyproject.toml` 中的 `1.0.0` 版本，并生成 manifest、SHA256 校验和、wheelhouse 与安装脚本。

验证包内容：

```powershell
python packaging/offline/verify_offline_bundle.py dist-offline/<解压后的包目录> --require-bundled-runtime
```

正式交付前，应在目标操作系统上完成：解压、离线安装、`--version`、`--help`、Codex 初始化、adapter status 和 `run --dry-run`。

## 11. 常见问题

### `ai-sdlc` 命令找不到

```powershell
python -m ai_sdlc doctor
python -m ai_sdlc --help
```

如果模块入口可用，请把 Python Scripts 目录加入 PATH，或继续使用 `python -m ai_sdlc`。

### 初始化后仍有开放门禁

先运行：

```powershell
ai-sdlc status
ai-sdlc verify constraints
ai-sdlc loop status
```

根据输出补齐需求、设计、任务或测试证据，再执行 `run --dry-run`。

### Codex 没有读取项目规则

```powershell
ai-sdlc adapter select --agent-target codex
ai-sdlc adapter status --details
```

确认项目根目录存在 `AGENTS.md`，并从该项目目录启动 Codex。

### PowerShell 执行策略阻止离线安装

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_offline.ps1 -AddToPath
```

### 需要查看机器可读结果

支持 `--json` 的命令可直接输出 JSON；其他命令可通过退出码和保存的 YAML/JSON 工件接入 CI。

## 12. 验收清单

- `ai-sdlc --version` 输出 `1.0.0`；
- `ai-sdlc adapter status` 显示 Codex 项目规则已准备；
- 项目 Shell 偏好为 PowerShell；
- `ai-sdlc status` 能读取 checkpoint；
- `ai-sdlc run --dry-run` 能展示明确的 Result 与 Next；
- `ai-sdlc verify constraints` 没有未处理的 BLOCKER；
- 测试、lint、离线包校验和目标平台 smoke 均通过。
