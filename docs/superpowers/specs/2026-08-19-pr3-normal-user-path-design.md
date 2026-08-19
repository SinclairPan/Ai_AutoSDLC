# PR3 正常用户路径设计

## 基线与目标

本设计落实《AI-SDLC 2.0 内部优化执行合同（修订冻结版）》中的逻辑 PR3。

- 实施基线：`f9fe2894475afd76aa04c617acc21369c71c0483`
- 基线 tree：`43e9bdba3c8103cf52bb6f0ddcbef183825bb927`
- PR #29 已完成逻辑 PR1：恢复用户项目真值。
- PR #31 已完成逻辑 PR2：五个 Loop 成为唯一交付与评审闭环。
- PR #30 只修复了升级提示的部分旧缺口；本 PR 补全正常用户路径，但不进入 PR4 物理删除。

目标只有三项：

1. 用户或 AI 每次执行真实业务命令时，都能在不破坏原命令的前提下看到可靠升级提示。
2. `run` 按当前五 Loop 真值直接给出少量适用规则正文，用户不再手动执行 `rules show`。
3. 默认 `status` 与顶层帮助只展示正常用户需要的信息，旧命令保持可调用但不再制造入口噪声。

## 当前事实与根因

### 升级提示只完成了一半

现有 `update_advisor.py` 已具备安装身份识别、24 小时缓存、短超时刷新、失败退避、
源码/editable 运行保护和 GitHub latest Release 校验，应继续复用。缺口位于 CLI 接线：

- 根回调遇到任意 `--json` 就完全跳过提示，AI 常用的机器路径看不到新版本。
- TTY 用户确认后安装并直接退出，原始业务命令没有重新执行。
- 非 TTY 使用多行 Rich Panel，不是 Agent 可稳定解析的单行协议。
- PR #30 的设计明确接受了这些限制，所以不能把它视为 PR3 完成。

### 规则仍停留在旧七阶段与“大段规则文件”模型

`RulesLoader` 仍按 `init/refine/design/execute/verify/close` 选择规则，和当前五 Loop 不一致；
内置规则文件与 Agent adapter 又包含大量固定 PrimeVue/Vue2、自研发布和历史 Program 细节。
把完整规则库注入每次对话会增加 token 和误导风险，而要求用户手动 `rules show` 又不会形成
真实正常路径。

### 默认状态与帮助仍暴露内部历史结构

`run` 已能只读输出五 Loop 的 Result/Next/Blockers，但 `status` 默认仍构造巨大的 checkpoint、
Program、Telemetry 和治理表。顶层帮助同时展示二十余个历史/高级命令，用户难以判断真正入口。

## 产品边界

必须保留：

1. 本地独立提交前 Reviewer。
2. 严格 advisory 的代码精简建议。
3. 五个 Loop 的动态专家评审：最多两名专家、最多一次修复复审。
4. PR2 已形成的 review outcome、质量命令和 reviewed tree 合同。
5. 普通 release、离线升级与显式 `self-update` 能力。

明确不做：

- 不新增规则 store、digest、版本锁、远程策略、注入平台或运行时学习。
- 不新增后台更新服务、常驻进程、Telemetry、AgentOps 或 ROI 平台。
- 不新增 Graph 编排、authority、CAS、proof、certificate 或 quorum。
- 不管理多人分支、WorkItem 分布式编号或共享 checkpoint。
- 不物理删除 Program、Telemetry、studio、host-runtime 等模块；物理删除属于 PR4。
- 不新增固定技术栈作为通用企业规范。

## 升级提示合同

### 命令分类

根回调在解析到真实业务子命令后、执行该子命令前调用既有 update advisor。

绕过：`self-update`、帮助、补全、裸命令、`--version`、源码/editable/`uv run` 开发运行。

覆盖：其余普通命令，包括 `loop`、`run`、`status` 以及带 `--json` 的机器路径。

### TTY 人类路径

发现更新时先在 stderr 询问是否升级：

- 拒绝：记录本次选择并继续执行原命令。
- 离线、超时、解析失败或缓存不可写：继续执行原命令。
- 确认：调用现有安全安装流程；安装成功后用 `shell=False` 启动更新后的同一 CLI
  可执行文件，并传入原始参数；父进程传播重跑命令的退出码。
- 重跑通过一次性进程环境标记跳过第二次提示，禁止递归；标记不写入项目或长期配置。
- 安装成功但无法重跑时明确返回失败，不能谎报业务命令已完成。

不得把原始 argv 写入缓存、日志或升级提示，避免泄漏业务参数与凭证。

Windows console launcher 是同一 update→replay 状态机的一部分。现有 `ai-sdlc.exe` 会在安装前
通过 `os.execve` 把进程替换成 `python -m ai_sdlc self-update install`，因此不能假设安装函数
会返回原根回调。初始进程必须先冻结原 executable 与 argv，并通过一次性、仅进程链可见的
环境 handoff 交给 module updater；不得写临时文件。updater 启动后立即严格解析并从
`os.environ` 删除 handoff，再执行安装；安装成功后由 updater 调用更新后的原 executable 与
exact argv。业务子进程只接收一次性 bypass 标记，根回调必须在调用业务 handler 前消费并删除
该标记，因此 handoff/bypass 都不会出现在实际业务命令环境中。

显式 `self-update` 命令不创建 replay handoff。自动升级 handoff 缺失/损坏、安装失败或重跑
启动失败都必须非零退出；安装失败时不得执行原业务命令，任何路径都不得把“升级完成”当作
“业务命令完成”。

### 非 TTY 与 JSON 路径

非 TTY 或任意带 `--json` 的命令不得询问、不得自动安装，只在 stderr 输出一行：

```text
AI_SDLC_UPDATE_NOTICE {"action":"ask_then_self_update_and_retry","current_version":"1.0.0","latest_version":"2.0.0","schema_version":1,"upgrade_command":"ai-sdlc self-update check"}
```

JSON 使用紧凑、稳定键名和固定前缀；不包含原始 argv。stdout 完全属于原业务命令，必须保持
可解析。Agent adapter 明确要求：向用户确认；确认后执行 `ai-sdlc self-update check`；成功后
重新执行自己刚才的原命令。用户拒绝或暂不确认时继续处理原命令结果。

### 缓存与网络

继续使用现有每安装身份缓存、24 小时成功刷新周期、1.5 秒自动检查超时与失败退避。
同一缓存周期不得重复联网；自动检查不增加重试。显式 `self-update check` 保留其现有更长超时
与失败恢复。不得下载 Release 页面正文或在普通命令中执行安装探测。

## 五 Loop 的有界规则上下文

### 唯一入口

`ai-sdlc run` 在现有五 Loop 路由结果后追加 `Applicable Rules`；机器路径新增
`ai-sdlc run --json`，在 JSON 中返回同一有界规则上下文。用户和 Agent 不需要再调用
`rules show`。`status` 仍只负责 Result/Next/Blockers，不承担规则注入。

### 静态选择而非新平台

在既有 rules 包内增加一个小型、纯本地选择函数。它只读取：

- 当前 Loop 类型；
- 当前 review-aware Loop 状态；
- 已存在的结构化 task 标志，例如 Implementation 是否要求 Frontend Evidence。

不得扫描需求关键词、调用模型、联网、写状态或支持远程/项目自定义策略。每次最多返回两个
规则片段，总 UTF-8 字节数设置硬上限；超限或 marker 损坏时 fail closed，并提示维护内置规则，
不得退化为加载完整文件。

默认映射：

| 当前 Loop | 规则片段 |
| --- | --- |
| requirement | `prd-guidance`、`scenario-routing` |
| design-contract | `prd-guidance`、`quality-gate` |
| implementation | `tdd`、`verification`；needs-fix/blocked 时 `debugging` 替换 `tdd` |
| frontend-evidence | `verification`、`quality-gate` |
| local-pr-review | `code-review`、`verification` |

每个相关 Markdown 只新增一个显式 `normal-path` 片段，选择器只提取该片段。旧完整规则仍可由
隐藏的兼容命令读取，但不进入正常 Agent 上下文。

### 通用规则清理

Codex、Cursor、Claude Code、VS Code adapter 与通用 pipeline 文本只保留：入口、当前 Loop、
适用规则、动态专家、advisory slimming 和必要的失败边界。固定 PrimeVue/Vue2、竞赛、
AI-SDLC 自研发布和历史 WorkItem 内容从通用模板移除。

前端需求仍必须先给出一个推荐方案与可选方案，并等待用户确认；具体框架、组件库和 style pack
由项目事实与用户选择决定，不再由通用规则硬编码。仓库自身的 self-development 约定只能保留
在明确标记的本仓 Local Repository 区域，不得复制到用户项目。

## `status` 与顶层帮助

### `status`

- 默认：复用 review-aware 五 Loop 路由，只显示 `Result`、`Next`、`Blockers`，不触发写入。
- `--details`：保留当前详细人类诊断表，供排障使用。
- `--json`：为兼容保留当前详细机器合同；升级提示只能出现在 stderr。
- `--details` 与 `--json` 同时使用时返回明确参数错误。

### 顶层帮助

默认只展示正常用户入口：

`init`、`adopt`、`doctor`、`status`、`recover`、`run`、`adapter`、`workitem`、`verify`、
`loop`、`pr-review`、`self-update`。

下列历史/高级命令改为 hidden，但保持原命令名和直接调用兼容：

`index`、`scan`、`refresh`、`agentops`、`enterprise`、`gate`、`rules`、`studio`、`stage`、
`program`、`host-runtime`、`handoff`、`telemetry`、`provenance`、`trace`。

本 PR 不删除模块、不移动数据、不改变这些命令的业务语义。

## 验收

升级提示：

- 模拟 installed `1.0.0` 与 latest `2.0.0`：普通命令和 `loop` 均提示。
- TTY 拒绝、离线、刷新失败时原命令继续；确认后安装并精确重跑原命令一次。
- Windows 安装入口必须真实覆盖 `.exe`→module updater→更新后 `.exe` 的进程链；安装和业务
  handler 各执行一次，argv 与业务退出码保持不变，handoff/bypass 被消费且不落盘。
- 非 TTY 与 JSON 只产生一行 stderr；JSON stdout 始终可解析。
- 同一成功缓存周期只联网一次；源码/editable/`uv run` 不安装、不重跑。
- 安装或重跑失败不产生假成功。

规则、状态与帮助：

- 五 Loop 各返回正确的最多两个片段；错误 marker、超限和未知 Loop 不加载完整规则库。
- Agent 正常 `run` 路径直接取得规则正文，不依赖 `rules show`。
- Node、Java、Python 企业项目只看到当前 Loop 的通用规则，不出现 AI-SDLC 发布、竞赛、
  固定 PrimeVue/Vue2 或历史 WorkItem 内容。
- `status` 默认只有 Result/Next/Blockers；`--details` 和 `--json` 保持诊断能力。
- 默认 help 只显示冻结入口；hidden 命令仍能按原名调用。
- HEAD、index、worktree、Loop outcome 与 checkpoint 不因提示、规则读取或默认 status 改变。

完成目标测试后运行完整 pytest、Ruff、constraints、构建和 fresh wheel/sdist/离线安装 smoke。
由两名独立本地专家对同一冻结 candidate 做产品边界与交付回归评审，最多允许一轮聚焦修复。

## 终止条件

以上验收通过即结束逻辑 PR3。不得顺便删除隐藏模块、重构 Program/Telemetry、改变发布流程、
新增规则平台或开始 PR4。
