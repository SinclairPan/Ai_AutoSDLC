# AI-SDLC 离线产品站前台正文 V2

本文只包含产品站前台可见正文和必要的内容标签，不包含 HTML、CSS、视觉稿或视频成片。

主导航：`AI-SDLC`、`Loop Engineering`、`Dynamic Expert Review`、`Platform Capabilities`、`Downloads & Docs`。

---

## AI-SDLC

### 让 AI 写代码，不等于让软件完成交付

AI Coding Agent 可以理解需求、修改文件和调用工具，但真实交付还需要回答一组更难的问题：目标是否已经确定？设计是否覆盖边界？测试和浏览器结果是否属于当前代码？审查是否读取了同一个版本？失败后从哪里继续？谁有权宣布完成？

AI-SDLC 是一个本地优先、可恢复、可验证的 AI 原生软件研发框架。它不替代 Codex、Claude Code、Cursor 等执行工具，而是在项目侧管理 WorkItem、Loop 状态、证据、失败、恢复与 Close，让可替换的 AI 执行能力进入一套稳定的软件交付过程。

主要入口：

- **理解完整闭环**：进入 `Loop Engineering`。
- **理解专家如何挑战结果**：进入 `Dynamic Expert Review`。
- **下载并自行验证**：进入 `Downloads & Docs`。

### 三个核心价值

#### 从一次生成，到持续完成

Loop 把目标、执行、验证、反馈与修复连成闭环。任务不会因为模型说“完成”而结束；只有当前证据满足既有 Close 条件，交付才能继续向前。

入口：`看 Loop 如何把任务做完`，进入 `Loop Engineering`。

#### 从模型自审，到专家对抗

AI-SDLC 按当前风险选择独立只读专家，让专家读取同一份冻结输入并返回可定位 Findings。问题回到原 Writer 修复；专家数量、职责和复审轮次都有边界。

入口：`看专家如何挑战关键结果`，进入 `Dynamic Expert Review`。

#### 从零散 Skills，到项目级工程系统

跨 AI 工具接入、断点续作、前端方案、组件规范与浏览器验收证据都留在项目中。更换执行工具时，项目规则、状态与工件不必重建。

入口：`查看完整平台能力`，进入 `Platform Capabilities`。

### 一条可恢复的交付链

```text
Init / Adopt
  → WorkItem
  → Requirement
  → Design Contract
  → Implementation
  → Frontend Evidence（前端任务条件启用）
  → Local PR Review（提交前跨阶段复核）
  → Close
```

每个阶段都要明确：当前输入、可执行动作、已生成工件、开放缺口、失败状态和下一步。AI-SDLC 可以允许结果关闭，也可以让结果停在 `needs_user`、`needs_fix` 或 `needs_review`；停止不是异常，而是交付系统拒绝伪完成的正常能力。

### 面向生产交付

#### 可恢复的项目事实

checkpoint、status、handoff 与 recover 共同保存当前阶段、开放门禁、分支和下一步。恢复依赖项目事实重算，不依赖模型记住上一段对话。

#### 受治理的前端交付

技术栈建议、组件提供方、Style Pack、Theme 约束、实现上下文和 Browser Gate 在同一交付上下文中传递。实现前保留人工确认，浏览器证据进入 Frontend Evidence 状态。

#### 跨 AI 工具、本地优先接入

AI-SDLC 将统一项目规则安装到不同 AI 工具的标准入口；WorkItem、Loop 状态和证据工件保留在项目侧。切换执行工具，不等于重新发明项目治理。

### AI-SDLC 产品实录

这里预留 AI-SDLC 正式产品实录播放器。视频完成并通过内容核验后，再依据成片实际覆盖的功能、运行环境和时间码生成介绍，不预先承诺尚未录制的环节。

视频发布后显示：

- 产品版本与运行环境。
- 完整章节与实际时间码。
- 字幕开关和全屏播放。
- 源视频 SHA256 与录制日期。

视频未放入离线包时，播放器显示“产品实录即将加入”，不影响全部产品正文、下载入口和文档阅读。

### 状态比总结更可靠

| 状态 | 含义 | 正确动作 |
|---|---|---|
| `needs_user` | 仍缺少用户决定、范围或高风险授权 | 停止实现，取得明确输入 |
| `needs_fix` | 当前结果或验证存在必须修复的问题 | 回到原 Writer 和当前候选修复 |
| `needs_review` | 实质结果尚未完成独立复核，或专家执行失败 | 保持未关闭，恢复复核 |
| `closed` | 当前 Loop 的身份、基础证据、digest 与既有关闭条件已经满足 | 进入后续阶段或最终交付 |

专家复核属于 Adapter 在当前对话中的工作合同，不是持久化 close credential。`closed` 工件本身不能被解释为独立证明专家执行过程；它证明的是既有 Loop 关闭条件和当前 digest 已经满足。

AI-SDLC 不承诺模型永不犯错。它提供的是：错误出现时能够被表示、被定位、被回流，并且在条件未满足时不把失败改写为成功。

---

## Loop Engineering

### 从“模型已经输出”到“工程结果可以关闭”

单轮问答擅长生成候选答案，却无法天然承担真实软件交付的连续责任。需求会变化，设计会遗漏，测试会失败，页面会出现运行时错误，代码在提交前还可能暴露跨阶段回归。Loop Engineering 将这些反馈转化为一组有身份、有状态、有证据和有退出条件的闭环。

### 一个 WorkItem 的生命周期

在正式绑定 WorkItem 的路径中，交付从目标和验收标准开始，而不是从改代码开始：

```text
目标与验收标准
  → 需求澄清与冻结
  → 设计覆盖与关闭
  → 任务执行与验证
  → 前端运行证据（按需）
  → 提交前跨阶段复核（按需）
  → WorkItem Close Check
```

每个 Loop 使用同一组最小协议：

1. 读取明确输入并建立 Loop 身份。
2. 从项目工件重算当前状态。
3. 输出缺口、停止原因和下一步动作。
4. 绑定 `input_digest` 与只读复核快照。
5. 将 Findings 回流原 Writer。
6. 修复后进行最多一次复审。
7. close 或 freeze 时重新构造输入；发现漂移则拒绝旧结论。

### Requirement

Requirement Loop 把模糊想法转化为目标、范围、验收标准、角色、风险和待澄清问题。信息不完整时进入 `needs_user`，而不是默认为模型熟悉的答案。

只有当前输入完成复核且未发生漂移，Requirement 才能 freeze。它为后续设计提供稳定输入，但不会把任何自然语言请求自动包装成已经完整的正式 WorkItem。

### Design Contract

Design Contract Loop 检查 `spec.md`、`plan.md` 和 `tasks.md` 是否覆盖接口、数据、边界、技术栈与验证策略。它关注需求是否已经被映射成可执行任务，而不是文档是否足够长。

前端任务在这里保留技术栈、组件提供方和 Style Pack 的显式确认。默认推荐服务于小白用户，高级矩阵和自定义入口服务于有明确约束的工程团队；未确认前不能进入实现。

### Implementation

Implementation Loop 管理 required tasks、进度、验证命令和 evidence。任务完成不等于 Loop 关闭：存在 blocked task 时进入 `needs_fix`；全部 required tasks 完成后进入 `needs_review`，由独立复核挑战实现结果。

测试、lint、build 和项目自己的验证命令可以进入 Implementation evidence。证据必须属于当前候选；更换输入、任务或代码后，旧结论不能直接复用。

代码体积、重复、复杂度和职责拆分只生成非阻断精简建议。正确性和交付证据保持严格，代码风格和精简取舍保留给实现者，不使用机械行数阈值阻止 Close。

### Frontend Evidence

Frontend Evidence 是前端任务的条件 Loop。它消费真实 browser entry、页面与交互结果、console/page error、截图、视觉结果和基础可访问性信息，并检查证据身份、结构和时效。

Playwright 是可选执行路径；其他浏览器工具或企业 E2E Runner 可以通过兼容 Browser Gate 工件接入。没有可用浏览器能力时可以显式 skip，但 skip 只表达“本次未执行”，不能冒充浏览器验收已经通过。

Frontend Evidence 不是完整 E2E 平台，也不提供 WCAG 认证。它的职责是让真实浏览器结果进入交付状态，而不是让截图看起来漂亮。

### Local PR Review

Local PR Review 是提交前的跨阶段终审，不是第五个永远必跑的串行阶段。它可以读取 Git range、暂存区、工作区或补丁，建立 Review Pack，由独立本地只读 Reviewer 检查当前变更、已有 Findings、修复结果和验证证据。

Reviewer 不拥有业务代码修改权。存在未解决 Finding，或 Reviewer 无法完成执行时，Review 保持未关闭。通过后生成普通最终报告；它不是远程 PR 平台、CI 证书或自动合并器。

### 失败、修复与恢复

Loop 不追求无限自动重试。稳定的恢复路径是：

```text
发现缺口
  → 明确 needs_user / needs_fix / needs_review
  → 保留当前 Loop 与候选身份
  → 用户补充、原 Writer 修复或恢复独立复核
  → 重新计算输入与状态
  → 关闭或继续保持未关闭
```

当 checkpoint、分支或项目工件不一致时，AI-SDLC 先停止当前运行，再提示 recover 或显式 reconcile。恢复的对象是项目事实，不是模型没有保存下来的思维过程。

---

## Dynamic Expert Review

### 让专家挑战结果，而不是接管结果

同一上下文中的自审容易重复原有盲点；固定堆叠多个 Reviewer 会增加成本和无关意见；无限 Agent 讨论则模糊了谁负责修复、何时可以退出。

AI-SDLC 使用有界动态专家复核合同：根据当前结果的主要风险选择一名 Primary Expert；只有出现明确第二风险面时，才增加一名 Cross-risk Expert。两者都在全新只读上下文中工作，不继承 Writer 的推理历史。

### Bounded Dynamic Expert Review Graph

```text
Writer
  │ 产出当前 Loop 的实质结果
  ▼
AI-SDLC
  │ 绑定 input_digest 与 review_snapshot
  ├──────────────► Primary Expert
  │                 主风险面，必选
  └──── 条件满足 ─► Cross-risk Expert
                    第二风险面，最多一名
                          │
                          ▼
                  Located Findings
                          │
                          ▼
                 原 Writer 修复
                          │
                          ▼
                   最多一次复审
                          │
             ┌────────────┴────────────┐
             ▼                         ▼
        原 Loop Close              needs_review
```

这里的 Graph 用于解释职责和信息流，不代表新增了持久图数据库、专家调度运行时或第二套状态机。Loop 仍是唯一状态源，Writer 仍是唯一修改者，既有 close/freeze 仍拥有完成权。

### 三种职责

#### Writer

Writer 是当前 Requirement、Design Contract、Implementation、Frontend Evidence 或 Local PR Review 结果的原编写者。它负责解释目标、修复 Findings、重新运行验证，并调用既有状态动作。

#### Primary Expert

Primary Expert 面向当前结果的主要风险。例如需求结果优先检查范围和验收标准，设计结果优先检查接口和边界，实现结果优先检查行为、回归和错误处理，前端结果优先检查交互与证据身份。

#### Cross-risk Expert

Cross-risk Expert 只有在结果同时暴露第二种独立风险时加入。例如一个权限配置界面既涉及前端状态一致性，也涉及授权边界；此时可以在主前端专家之外增加一名安全专家。没有明确交叉风险时，不为“看起来更强”而增加角色。

### 输入必须是同一个结果

AI-SDLC 为当前复核输入计算 `input_digest`，并在同一次读取中形成内联 `review_snapshot`。专家只能读取这份快照，不得在复核期间重新打开可变工件路径。

close 或 freeze 会重新构造输入并校验 digest。Writer 在复核后修改了候选、分支发生变化，或目标身份已经漂移时，旧 Findings 和旧通过结论不能关闭新结果。

### Findings 回到原 Writer

专家只返回带位置、风险和修复方向的 Findings，不直接修改候选，也不投票决定“总体通过”。多个专家的输出合并的是问题，不是 verdict。

原 Writer 根据 Findings 修复同一个结果并重跑必要验证。最多一次复审后，结果要么满足既有 Close 条件，要么明确停在 `needs_review`；专家超时、执行失败或输出无效同样不能折算成零问题。

### 有界意味着可以退出

| 机制问题 | AI-SDLC 的处理 |
|---|---|
| 需要多少专家 | 一名 Primary Expert；存在明确第二风险面时最多增加一名 Cross-risk Expert |
| 专家能否改代码 | 不能，只读同一 snapshot 并返回 Findings |
| 谁负责修复 | 原 Writer |
| 可以复审多少次 | 最多一次修复后复审 |
| 专家失败怎么办 | 保持 `needs_review`，不解释为通过 |
| 谁决定完成 | 原 Loop 的 close/freeze 条件，而不是专家身份 |

该机制不承诺发现所有缺陷，也没有在缺少同任务、同模型、同预算实验时声称缺陷发现率、效率或成本领先。它解决的是复核职责、输入身份、失败语义和退出条件不清楚的问题。

---

## Platform Capabilities

### 从智能体技能到受治理的工程交付

专业 Skills、Spec Workflow、角色分工、TDD、Review、长期上下文和前端工程知识已经成为 AI Coding 生态的共同基础。AI-SDLC 不以最大的 Skills 目录、最强 IDE 或最丰富角色库为目标。

它提供的是项目侧控制层：把这些工程方法接入同一个状态、证据、失败、恢复和 Close 过程。本页只展开四组支撑两条核心主线的通用机制。

### 可恢复的项目事实

长任务的最大风险不是“模型忘记了一句话”，而是团队无法确认当前分支、阶段、开放门禁和下一步是否仍与已有工件一致。

```text
checkpoint
  → status
  → human-readable handoff
  → compare branch and project artifacts
  → recover
  → reconcile when explicitly required
```

checkpoint 保存阶段、分支、开放门禁和下一步；status 读取当前本地事实；handoff 为跨会话和跨人员交接提供简洁摘要；recover 对照当前分支和项目工件给出续作路径。

如果工件比 checkpoint 更新，AI-SDLC 会停止当前 run，避免把过期状态当作事实。显式 reconcile 用于安全条件下重建 checkpoint；它不是事务回滚，也不会恢复模型的内部思维或进程栈。

### 跨 AI 工具的一致治理与本地优先接入

AI-SDLC 为 Claude Code、Codex、Cursor、VS Code/Copilot 四个专用 Adapter 提供各自的 canonical 项目规则入口。generic 只提供兼容提示，要求使用者人工引用项目宪章和 CLI，不提供与专用 Adapter 等价的 canonical proof carrier。项目可以显式选择或切换当前入口，状态和工件仍保留在项目目录中。

| AI 工具入口 | 项目侧标准载体 |
|---|---|
| Claude Code | `.claude/CLAUDE.md` |
| Codex | `AGENTS.md` |
| Cursor | `.cursor/rules/ai-sdlc.mdc` |
| VS Code / Copilot | `.github/copilot-instructions.md` |
| Generic | `.ai-sdlc/memory/ide-adapter-hint.md` 兼容提示；不是 canonical 规则载体 |

Adapter 解决的是规则入口差异，不是多个 AI 工具的并行编排。切换工具不代表实时共享会话、无损迁移隐式上下文或拥有完全相同的工具能力。

核心扫描、状态、Loop、复核输入和证据工件本地优先；代码外发默认关闭。如果用户选择远程 AI Provider，数据边界由该 Provider 单独决定。“离线产品站”“离线安装包”“本地优先治理”和“完全离线 AI 推理”不是同一个概念。

### 从前端意图到验收证据

前端治理不只是内置组件选择，而是一条从意图到浏览器证据的约束链：

```text
前端需求
  → 默认方案与高级候选
  → 用户显式确认
  → Provider / Style Pack / Theme 合同
  → 页面与生成约束
  → 实现上下文
  → Browser Gate
  → Frontend Evidence
```

| 路径 | 定位 | AI-SDLC 提供的治理 |
|---|---|---|
| `vue3 / public-primevue / modern-saas` | 内置默认推荐路径 | PrimeVue Provider profile、语义组件映射、白名单、Style Pack、Theme Token、`theme.ts` 唯一入口和交付约束 |
| `vue2 / enterprise-vue2` | 内置企业兼容路径 | 私有 Provider profile、组件映射、白名单、安装策略和兼容约束；组件包、网络与授权由企业环境提供 |
| 自定义或不使用组件库 | 兼容执行与证据路径 | 项目提供可加载 browser entry 与兼容 Browser Gate 工件后，由 Frontend Evidence 消费 |

普通新前端需求默认推荐 Vue 3、PrimeVue、modern-saas；资深用户可以查看高级 Style Pack 与自定义入口。推荐不等于已经安装，企业兼容 profile 也不等于产品站附送私有组件包。

Browser Gate 可以携带页面、交互、console/page error、截图、视觉和基础可访问性结果。项目自己的 E2E Suite 负责测试策略，Frontend Evidence 负责工件身份、时效、结构和 Loop 状态，两者互补而不是互相替代。

### 与风险匹配的工程控制

AI-SDLC 不对所有问题使用同一种强度：

- 对 WorkItem 身份、复核输入、验证证据、漂移和 Close 条件使用明确状态与拒绝语义。
- 对技术栈和前端方案保留实现前人工确认。
- 对秘密、遗漏文件和高风险授权采用 fail-closed 或 `needs_user`。
- 对代码体积、重复、复杂度和职责拆分只给非阻断建议。
- 对修复和复审设置明确上限，不无限自动生成直到看似成功。

这种取舍让“必须证明的事情”保持严格，让“需要工程判断的事情”保留空间。AI-SDLC 不以硬性极简、自动合并、自动发布、通用 Agent Runtime 或长期语义记忆作为产品目标。

---

## Downloads & Docs

### 获取正确版本和权威文档

本页集中提供 AI-SDLC v3.0.1 的正式仓库、Release、README、中文新用户指南和安装资产。安装步骤不在本页重复；请打开中文新用户指南，并在手册内部选择适合自己的完整路径。

### 版本身份

| 项目 | 值 |
|---|---|
| 正式版本 | `v3.0.1` |
| Tag object | `408086505718fbd26824373bb72ed98c27c3b652` |
| Tag commit | `9a59a3edd483b0e6526b67b03fbfcac3ba48d2e4` |
| Tag tree | `fd5c2dac0a216f0eb17855d03cc7900d872d3c61` |
| GitHub | [SinclairPan/Ai_AutoSDLC](https://github.com/SinclairPan/Ai_AutoSDLC) |
| Release | [v3.0.1](https://github.com/SinclairPan/Ai_AutoSDLC/releases/tag/v3.0.1) |

### 公共文档

#### 中文新用户指南

面向第一次安装和初始化 AI-SDLC 的用户。离线站点优先打开与 v3.0.1 发布源文件逐字绑定的本地只读副本；联网时还可以访问 GitHub 中随 v3.0.1 发布的同一份指南。

- 本地入口：`docs/USER_GUIDE.zh-CN.html`
- v3.0.1 发布基线：[USER_GUIDE.zh-CN.md](https://github.com/SinclairPan/Ai_AutoSDLC/blob/v3.0.1/USER_GUIDE.zh-CN.md)

#### README

了解产品定位、核心能力、标准工作流和主要 CLI：

- [README v3.0.1](https://github.com/SinclairPan/Ai_AutoSDLC/blob/v3.0.1/README.md)

### 离线安装资产

产品站只提供正式 Release 下载链接，不把安装包二进制放入站点交付目录。下载后必须同时取得同名 `.sha256` 文件并按中文用户指南完成校验。

| 平台 | 正式资产 | 下载 |
|---|---|---|
| Windows AMD64 | `ai-sdlc-offline-3.0.1-windows-amd64.zip` · SHA256 `61a0a8bbe2f2c77b1e60ac2e15fb46a09efcd1180c000853ef04a8cafd6bef85` | [安装包](https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v3.0.1/ai-sdlc-offline-3.0.1-windows-amd64.zip) · [SHA256](https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v3.0.1/ai-sdlc-offline-3.0.1-windows-amd64.zip.sha256) |
| macOS Apple Silicon | `ai-sdlc-offline-3.0.1-macos-arm64.tar.gz` · SHA256 `5a5a4067389c2ae56e2600560aba6c18899cfe8395f7198892447f76df510260` | [安装包](https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v3.0.1/ai-sdlc-offline-3.0.1-macos-arm64.tar.gz) · [SHA256](https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v3.0.1/ai-sdlc-offline-3.0.1-macos-arm64.tar.gz.sha256) |
| Linux AMD64 | `ai-sdlc-offline-3.0.1-linux-amd64.tar.gz` · SHA256 `864d0b311f702cde9751ddcc0f9faa82967c8e04d02286adf3e032fba1e055f4` | [安装包](https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v3.0.1/ai-sdlc-offline-3.0.1-linux-amd64.tar.gz) · [SHA256](https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v3.0.1/ai-sdlc-offline-3.0.1-linux-amd64.tar.gz.sha256) |

Windows ARM、macOS Intel 和 Linux ARM 不属于 v3.0.1 正式离线资产范围。离线站点可以在无网络时阅读产品正文和本地用户指南，但下载安装包仍需要网络或由可信渠道提前取得正式资产。

### 开始验证

1. 确认版本和操作系统。
2. 打开中文新用户指南。
3. 在中文新用户指南中按“已有项目 / 全新项目”“离线包 / 在线安装”和操作系统选择唯一完整路径。
4. 按每一步的命令、预期结果、异常处理和下一步执行。
5. 回到 AI-SDLC 首页继续理解 Loop 与专家复核机制。
