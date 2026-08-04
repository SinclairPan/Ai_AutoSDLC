# AI-SDLC 下一阶段可信交付与价值激活顶层设计 PRD

## 1. 文档信息

| 字段 | 内容 |
|---|---|
| 文档状态 | Final Review Candidate R7 |
| 文档版本 | 0.7.0 |
| 冻结日期 | 2026-08-03 |
| 线上事实基线 | `origin/main=067d51d146d46f6d83958384270e76072733b85d` |
| 线上 Tree | `d5851b1b2a7cbad2b5fc99807fbb160d711e44cb` |
| 当前正式版本 | `v1.0.2`，与线上事实基线为同一提交 |
| 文档用途 | 统一下一阶段背景、目标、总体架构、实现边界、优先级与验收原则 |
| 后续产物 | 各专项独立拆分 `spec.md`、`research.md`、`data-model.md`、`plan.md`、`tasks.md` |

本 PRD 是顶层产品与架构约束，不是任何一个专项的详细设计，也不授权直接跳过专项评审进入开发。后续专项必须继承本 PRD 的单一事实源、作用域隔离、质量不可压缩、自动优化边界和真实终态规则。

### 1.1 输入材料及事实等级

| 输入 | 标识 | 事实等级 | 使用边界 |
|---|---|---|---|
| 线上 `main` | commit `067d51d...` / tree `d5851b...` | 已发布运行时事实 | 判断当前已经实现和默认启用的能力 |
| 动态专家组 PRD | `AI-SDLC 1.0.0 动态专家组阶段关闭门禁 PRD` | 已实现能力的原始需求输入 | 必须用线上代码和默认 Policy 反向核验，不以原 PRD 宣称代替上线事实 |
| Loop 收敛成本治理规格 | blob `4128cfdfdeb797eaee1cfd7aef50a2ff9850567b` | 已通过历史双专家评审、尚未进入线上 `main` 的规划输入 | 只继承已冻结的控制面边界和 Slice DAG，不描述为已开发 |
| 框架 CI 治理 PRD | blob `3eb147af23612f0e3d46ce2eb41ee84b7e19cc7d` | 尚未进入线上 `main` 且此前评审未通过的规划输入 | 只吸收已被事实证明的问题和安全边界，必须重新拆分精简后再开发 |
| 三专家顶层现状评审 | 2026-08-03 同一线上快照 | 决策输入 | 用于校正四模块成熟度、优先级和不可虚假承诺边界 |

## 2. 执行摘要

AI-SDLC 当前不是“没有功能”，而是出现了明显的闭环失衡：内部控制面、证据协议和测试数量快速扩张，但发布真值、CI 反馈、用户可见入口、正式激活和真实数据闭环没有同步成熟。

下一阶段不得再通过一个超大专项继续横向增加协议。顶层目标是先恢复可信交付基础，再把已经存在的能力转化为可见、可用、可验证的用户价值，最后基于真实事实做自动优化。

整体开发顺序固定为：

1. 立即冻结不满足精确候选证据的正式发布，不等待任何根因修复；
2. 并行修复 Windows 并发根因、永久 Release Truth 和静态 CI 分层；
3. 暴露动态专家真实状态，并用已发布保守 Policy 打通普通用户价值路径；
4. 建立 Framework Qualification 与 Project-local Adaptation 双作用域的数据驱动晋升；
5. 按既有 Slice DAG 完成 Loop 收敛成本治理；
6. 在 Shadow 证明后启用自适应 CI 和 Phase 4 离线参数优化；
7. 最后治理 Lean Code 自身碎片化；跨语言能力只保留独立 Roadmap Guardrail。

## 3. 背景与当前基线

### 3.1 已发布能力

线上 `main` 已具备：

- Requirement、Design Contract、Implementation、Frontend Evidence、Local PR Review 五类 Loop 的基础编排、持久化、状态和关闭入口；
- 动态专家的 Stage Adapter、风险提取、角色规划、运行时绑定、Finding Ledger、阶段关闭证书、隔离、预算、恢复和离线优化基础代码；
- Lean Code 的 Python AST、调用关系、复杂度、重复、耦合、范围与分析覆盖判断；
- `PR Checks`、`Cross Platform Core`、`Compatibility Gate`、安装包 Smoke、用户指南 E2E、Release Build 和 Artifact Smoke 等工作流；
- Windows、macOS、Linux 正式安装制品和在线安装路径。

### 3.2 当前默认启用状态

存在代码不等于已经形成产品价值。当前默认状态是：

- Loop：五阶段基础能力可用，但未具备 002 所要求的跨阶段 Graph-lite、Historical Replay、Delivery Feasibility、Aggregate Closure、RepairAttempt、CostReceipt 和 Evidence Scope；
- Lean Code：默认以 warning/advisory 为主，400 行文件和 50 行函数只是风险信号；
- CI：每个 PR 与 `main` push 都可能执行 3 OS × Python 3.11/3.12/3.13/3.14 的近全量测试；
- 动态专家：`active_phase=1`、`enabled_risk_levels=[]`、`offline_optimization_enabled=false`，即 Shadow、非阻断、未启用本地自动优化；
- Activation CLI：以恢复和回滚能力为主，缺少普通用户可理解的状态、样本水位、晋升原因和下一步入口。

## 4. 已确认现象

### OBS-001：同一 Tree 的绿色结论不稳定

PR 候选曾在完整矩阵中通过，但相同 Tree 合并到 `main` 后，Windows Python 3.12 在并发阶段关闭测试中等待 activation safety mutation lease 超时，Windows Python 3.13 又达到三小时上限。说明当前绿色证据没有稳定证明相同执行合同可重复得到相同结果。

### OBS-002：发布发生在精确主线证据完成之前

`v1.0.2` 已发布并完成制品 Smoke，但相同提交的主线 Compatibility Gate 随后失败。制品存在不等于精确发布候选已经获得完整兼容性证书。

### OBS-003：普通修复触发近全量矩阵重复执行

Compatibility Gate 同时绑定 PR 与 `main`，每个组合几乎运行整个测试集。新推送会取消旧运行并从零开始，导致同一问题的多次定向修复重复消耗长尾 Windows 任务、Runner 时间和等待时间。

### OBS-004：测试规模和重型行为快速增长

当前测试不仅包含轻量单元断言，还包含真实 CLI、Git 子进程、多进程、文件锁、并发竞争、崩溃恢复、故障注入和超时场景。把这些测试不加区分地复制到全部 OS/Python 组合，造成超线性成本增长。

### OBS-005：Loop 具备阶段骨架但缺少跨阶段收敛合同

现有 Loop 可以分别启动、记录和关闭，但不能完整证明大需求已经被合理切片、父级验收全部覆盖、修复真正产生语义进展、预算耗尽后真实停止，以及局部变更只失效相关证据。

### OBS-006：动态专家实现面积大但用户价值仍处于 Shadow

动态专家已经接入五阶段关闭路径，但默认 Policy 不阻断任何风险等级。普通用户缺少一个稳定入口理解当前处于 Shadow 还是 Enforce、已经收集多少独立有效样本、为什么不能晋升、Finding 是否影响关闭，以及发生错误时如何回滚。

### OBS-007：固定 14 天不能证明数据充分

当前 Activation 同时要求总样本量、每阶段样本量和固定 14 天观察窗口。实现只检查最早 Session 到评估时间是否满 14 天，因此不能证明样本在时间上分布合理：样本可以短时集中产生后空等，也可能已有足够独立、成熟、覆盖全面的样本却被日历硬阻断。

### OBS-008：原始样本量也不能单独证明可以晋升

大量相同项目、相同候选、相同 Finding、相同专家组合的重复运行不构成独立证据。关闭后仍可能出现迟到 P0/P1、回滚、验证失败或线上逃逸；未完成归因和成熟观察的 Session 不能被当作成功结果。

### OBS-009：Lean Code 治理产生了新的维护面积

400/50 已被修正为风险信号，但 Lean Code 自身形成大量分析模块。继续依据大小阈值机械拆分会增加单调用模块、跨文件跳转、调用解析和测试组合，反向增加维护及上下文成本。

### OBS-010：线上问题登记与真实运行状态脱节

线上存在主线红灯、动态专家未激活和规划需求未上线，但框架问题 Backlog 和 GitHub Issue 仍可能显示为零。问题没有进入权威 Backlog，不代表问题不存在。本 PRD 编写过程中还实测到：在缺少 `checkpoint.yml` 的干净分支执行 `uv run ai-sdlc handoff update`，命令先写入 handoff，随后以未捕获的 `CheckpointLoadError` 退出；这种“部分写入但命令失败”的恢复缺口也必须进入同一问题真值，而不能只留在一次终端日志中。

## 5. 共同根因

### RC-001：成熟度状态被压缩成“已完成”

现有表达没有稳定区分以下状态：

1. `implemented`：代码存在；
2. `integrated`：进入真实执行链路；
3. `shadow`：产生观察结果但不拥有阻断权；
4. `enforced`：在明确风险范围内拥有关闭阻断权；
5. `optimized`：策略已通过事实数据自动改进并可回滚。

### RC-002：功能扩张超过验证和运营闭环带宽

大型专项一次引入大量文件、协议、恢复路径和测试。每个新增治理边界都用更宽的全量测试补偿不确定性，却没有同步建设分层执行、稳定测试身份、证据等价、增量验证和用户入口。

### RC-003：控制面建设优先于最小用户纵向价值

动态专家优先完成了复杂的隔离、证书和优化能力，但普通用户还不能自然理解、启用和验证它。Loop 也存在多个独立阶段，却缺少父级聚合和收敛摘要。

### RC-004：固定阈值代替数据质量判断

测试时长、Runner 成本、Activation 天数和样本数量存在静态默认值，但缺少独立性、覆盖度、成熟度、置信度与延迟分布的联合判断，导致固定值既可能放行低质量数据，也可能阻断高质量数据。

### RC-005：发布、CI、Loop 和专家优化没有共享同一证据语义

候选身份、执行等价、关闭授权、发布满足证明和优化数据集容易相互混用。若继续分别建立状态机、Ledger、指针或优化器，会形成多个互相不一致的治理真值。

## 6. 产品目标

### G-001：恢复可重复的发布真值

任何正式 Release 必须绑定精确 commit、Tree、执行合同、完整 Required Gate、制品摘要和用户路径证据。Pending、Failed、Cancelled 或证据不完整时不得发布。

### G-002：恢复快速且不压缩质量的开发反馈

普通推送先获得分钟级 Fast Gate；完整矩阵继续保留在 Merge Assurance、Release Candidate、Nightly 或显式高风险路径。不得通过删除测试、永久跳过、放宽超时或 retry-to-green 达成时延目标。

### G-003：让已实现能力形成可见用户价值

状态可见不是价值完成。Phase 1 正式安装包必须让普通用户无需内部 JSON、统计参数或维护者介入，完成“初始化 → 产生有效 Shadow Finding → 理解唯一下一步 → 接受/修复/忽略 → 看见明确非阻断结果”的纵向路径。Phase 2 的独立价值出口再验证 Low Risk Finding 对 Final Gate 的真实关闭影响、坏 Policy 回滚和历史影响补偿；不得把 Phase 2 权限伪装成 Phase 1 MVP。

低数据项目从已经通过 Framework Qualification 的保守稳定 Policy 起步，不必先独立收集覆盖所有项目类型的样本才能获得基础价值。本地自动优化是数据充足后的附加价值，不是使用动态专家基本能力的前置条件。

### G-004：完成长周期 Loop 的有界收敛和聚合关闭

通过 002 的 A 至 E 强制 Slice，实现历史重放、交付可行性、父级验收覆盖、语义进展、真实终态、Evidence Scope 和分层验证；可选 Slice F 不阻断 A 至 E。

### G-005：以独立、覆盖、成熟、可信的数据驱动晋升

废除固定 14 天作为永久硬门槛。晋升必须同时满足有效样本覆盖、Outcome 成熟、质量置信区间、证据完整性和回滚就绪；满足后可以提前晋升，不满足时即使等待更久也不能晋升。

### G-006：实现用户无感的本地自动迭代

普通用户不负责判断调哪些参数或调整多少。系统在项目本地基于版本化数据集生成候选，在 Shadow/Holdout 中独立评价，通过后自动晋升，异常时自动回滚；不得上传用户代码、Finding 或优化数据，也不得跨项目共享策略。

### G-007：控制 Lean Code 自身复杂度

停止无收益的机械拆分。只有能证明职责、重复、耦合、可测试性或维护成本改善的调整才能进入减重候选。

### G-008：建立线上问题和规划差距的权威真值

任何主线红灯、发布证据缺口、P0/P1 escaped finding 和质量回退，都必须进入现有 WorkItem 生命周期并绑定关闭证据。尚未承诺上线的规划能力必须标记为 `accepted_gap/roadmap`，不得与线上 defect 混为一个缺陷队列，也不得因为 GitHub Issue 为零就隐去真实问题。

## 7. 非目标

- 不在本顶层 PRD 中定义全部数据字段、CLI 参数和存储文件名；这些属于专项 data-model 和 contracts；
- 不重新实现第二套 Loop 状态机、Finding Ledger、ResourceGovernor、Close Authority、Telemetry Store 或 Offline Optimization Controller；
- 不把 CI 自适应治理默认下发到普通用户项目；
- 不将普通用户本地数据上传到 AI-SDLC 仓库或中央服务；
- 不要求用户人工判断 Budget、Activation 或角色参数；
- 不用固定文件行数或函数行数触发机械重构；
- 不把 PRD、代码合入或单次绿色描述成生产成熟；
- 不用尚未完成真实证据窗口的结果宣称 Phase 2/3/4 已经生效。

## 8. 作用域与隔离

### 8.1 框架自开发作用域

以下能力只作用于 AI-SDLC 框架自身仓库和受信任的参赛仓：

- CI 测试分层、Runner 成本、取消成本和测试时长遥测；
- 测试稳定身份、风险映射、Selection Manifest、动态分片和跨运行 Evidence 复用；
- Release Certificate 与框架发布决策；
- 框架级 Lean Code 内部减重。

框架还可以使用框架自有、合成或明确受信且不含普通用户数据的测试项目执行 `Framework Activation Qualification`。它先产生内容寻址的 Qualification Bundle；受保护 Release Build/Publish Job 是唯一 Envelope 发布权威，负责把 Bundle digest、前序 Envelope digest 和 Draft Release identity 绑定成版本化 `Release Activation Envelope` 与保守默认 Policy，并确保实际安装制品嵌入精确 Envelope digest。该过程不能消费普通用户项目数据，也不能直接写任一用户项目 Pointer。`release_phase_ceiling` 只是 Envelope 的人类可读最大 Phase 摘要，不能单独授权任何组合。

普通用户安装 AI-SDLC 后，不得因为这些能力而自动增加 CI Workflow、上传遥测、改变分支保护、形成强制远程可用性依赖或承担额外 Runner 成本。只允许在版本化缓存与最大传播延迟合同内读取不含项目数据的最小签名撤销元数据；读取失败必须回退 Phase 1 Advisory，不能阻断原有基础流程。

### 8.2 普通用户运行时作用域

以下能力会进入正式安装包：

- 五阶段 Loop 的收敛、状态、Evidence Scope 和父级关闭；
- 动态专家状态、Shadow/Enforce、Finding、自动晋升与回滚；
- 项目本地 Budget 和本地估算成本；
- Lean Code 的已支持语言语义检查。

普通项目执行 `Project-local Adaptation`：只消费当前项目事实，只能在已发布 Release Activation Envelope 和自动化包络内调整当前项目 Policy。身份主键至少为 `data_domain + project_lineage_id + policy_family`，其中框架 CI、框架 Activation Qualification、项目 Activation、项目 Loop 和项目 Budget 属于不同 `data_domain/policy_family`。

复用 Controller、Store 和协议只表示复用代码与状态迁移合同，不共享 Dataset、Pointer、预算池、Holdout、Alpha Ledger 或授权实例。复制 Policy、修改远程地址、Fork 仓库、伪造 domain 或使用同名目录不得获得其他项目的数据或授权；同一框架仓同时运行 CI 与动态专家时，也必须通过域身份保持物理或密码学可验证的隔离。

### 8.3 决策权边界

- 人工可以冻结不可优化的安全底线、关闭权威、P0/P1 定义和数据外发边界；
- 人工不负责日常选择参数、调整阈值或批准每次候选；
- `ActivationAssessment` 扩展现有 Phase 评估合同，现有 `ActivationPolicyStore` 继续作为 Phase 和项目 Policy Pointer 的唯一 CAS 写入者；
- 对 `dynamic_expert` policy family，Offline Optimization Controller 只在 Phase 4 后对冻结自动化包络内的角色、Provider 和 Budget 等参数生成及评价候选，不能直接迁移 Phase 或写 Pointer；CI/Loop 只能在各自 Shadow 前置通过后复用同一协议的隔离 domain/family 实例，并由各自既有 Store/Authority 完成写入；
- 任何超出包络的变更必须形成新 PRD，而不是在运行时静默扩大权限。

## 9. 总体架构原则

### AP-001：单一事实源

LoopRun、LoopRound、WorkItem、Finding Ledger、ResourceGovernor、StageCloseAuthorizer 和现有 Policy Pointer 继续承担唯一权威。新增 Manifest、Graph、Receipt、Dataset、Assessment 和 Certificate 只能是可重建投影或授权证据。

### AP-002：先静态确定性，后数据自适应

立即收益先通过静态分层、显式完整矩阵和确定性状态实现。自动选择、复用、晋升和优化必须先在 Shadow 中与现有基线对照。

### AP-003：质量 Floor 与效率 SLO 分离

质量完整性、P0/P1 检出、证据血缘、关闭授权和发布最低标准不可被成本优化。时长、Runner、测试顺序、分片数量、专家数量和软预算只有列入版本化 Automation Envelope 白名单后才可优化。Envelope 必须冻结不可变字段、最低 Stage/Risk/角色/能力覆盖、最大单步变化和紧急回退；未知字段一律不可优化，默认一个候选只改变一个可归因维度。

### AP-004：最宽触发和未知 Fail-Closed

同一变更命中多个验证或专家规则时，采用要求最宽的确定性结果。分析未知、映射缺失、证据损坏或结果不成熟时回退宽门禁、Shadow、`needs_user` 或真实阻断，不得当作安全通过。

### AP-005：候选与当前任务隔离

任何自动优化候选只能影响后续新 Session、后续 CI 候选或后续 Loop。当前正在执行的任务继续绑定创建时的 Policy digest，不允许热更新。撤销和信任验证不是 Policy 热更新，而是只作用于动态专家 Enforce 权的安全栅栏：权威 Final Gate、Stage Close Attestation 和框架 Release Certificate 的最终提交都必须绑定同一权威撤销 revision 并条件写入。仅撤销状态不可达或缓存过期时，系统撤回 Enforce 权并回到真正 non-blocking 的 Phase 1 基础关闭路径；已知撤销、已确认篡改或真实 P0/P1 才使用既有 `needs_user/blocked` 事故事实阻断。

### AP-006：事件驱动而非轮询驱动

CI 未变化状态、等待人工和等待外部结果不消耗 LLM Token。只有新日志、新 Finding、新 Evidence、新候选或状态迁移才触发 Agent 决策。

### AP-007：真实停止不伪装成完成

预算耗尽、证据不足、依赖不可用、样本不成熟和专项 No-Go 都必须形成真实终态与下一步；不得写成 `passed`、`completed` 或“全部成熟”。

## 10. 目标能力结构

顶层只保留一个共享控制面，四个能力域通过现有合同接入：

```text
Framework / Project Facts
        |
        +--> Release & CI Reliability
        |       +--> Fast PR Result
        |       +--> Merge Assurance
        |       +--> Release Certificate
        |
        +--> Loop Convergence
        |       +--> Replay / Baseline
        |       +--> SlicePlan / Aggregate Close
        |       +--> Evidence Scope / Budget / Terminal State
        |
        +--> Dynamic Expert Activation
        |       +--> Framework Qualification / Release Activation Envelope
        |       +--> Project-local Status / Shadow / Enforce
        |       +--> Mature Outcome Dataset / Assessment
        |       +--> ActivationPolicyStore CAS / Rollback
        |
        +--> Lean Code Sustainability
                +--> Risk Signal
                +--> Hotspot & Split Benefit
                +--> Evidence-backed Consolidation

Phase 1-4 decisions
        --> existing ActivationAssessment contract
        --> existing ActivationPolicyStore as the only Pointer writer

Phase 4 parameter candidates only
        --> existing OfflineOptimizationController
        --> independent Shadow / Holdout evaluation
        --> ActivationPolicyStore CAS for a versioned future Policy
```

## 11. 专项一：发布与 CI 可靠性治理

### FR-001：修复 Windows Activation Lease 并发不确定性

系统必须定位并修复租约所有权、获取、续期、清理、异常退出和重放过程中的真实竞争。测试必须使用事件或可观测状态同步，不得仅增加等待时间。

修复前必须冻结能够暴露原问题的复现/压力 Profile，包括执行合同、seed、重复次数、进程模型、前序资源压力和停止规则；不得在看到绿色后缩短 Profile。无法稳定复现或完成归因时保持 `unresolved/blocked`，不能以“疑似基础设施”关闭。

验收至少覆盖：

- 同一关闭操作的并发调用只形成一个权威 Attestation；
- 竞争失败不会留下永久 lease 或 marker；
- 线程、进程、异常和崩溃恢复后状态可重建；
- Windows Python 3.11 至 3.14 的定向重复验证不出现随机超时；
- 清洁 Runner 上的真实长链、前序异常/残留 marker、持有进程终止、Runner Cancel 和恢复重放通过，并由精确候选完整 Merge Assurance 复核；
- 失败测试不得被标记为 flaky、skip 或 retry-to-green。

### FR-002：Release Truth

正式发布顺序固定为：

```text
Freeze Candidate
  -> Draft Release
  -> Exact Commit/Tree Required Gates
  -> Build and Hash Artifacts
  -> Artifact and User Journey Smoke
  -> Release Satisfaction Proof
  -> Publish as GitHub Immutable Release
  -> Verify GitHub Release Attestation
  -> Release Certificate and Installation Recommendation
```

Pre-publish Release Satisfaction Proof 必须绑定 commit、tree、trigger、run attempt、执行合同、测试集合、制品 digest、安装证据和用户指南证据；若 Release 携带 Activation 能力，还必须绑定 Qualification Decision/Evidence、TargetActivationDelta、Applicability Profile、Release Activation Envelope、默认 Policy、Policy Schema 及实际 wheel/安装制品的全部 digest。任一输入 Pending、Failed、Cancelled、Incomplete 或摘要不一致时不得 Publish。

GitHub Release 的 Draft/Published 状态和受保护 Publish Job 是唯一发布迁移权威；Satisfaction Proof、Release Certificate 和 Revocation Receipt 只是不可变投影，不拥有第二个 Release Pointer 或状态机。Satisfaction Proof 还必须绑定不可变 Release ID、Draft 状态版本、tag object/commit/tree、资产名称与 digest 全集、Required Policy digest、workflow/run attempt，并由幂等 Publish Job 以 CAS 方式一次性消费。

正式发布前必须验证仓库或组织已经启用 [GitHub Immutable Releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)；未启用时只能生成显式不受信任 prerelease。Satisfaction Proof 必须绑定不可变发布设置状态。Publish 瞬间原子重验 Proof 仍为最新、未消费、未撤销，且所有绑定未发生签发后变化；tag 移动、资产覆盖/增删、run attempt 替换或 Draft 版本变化都使旧 Proof 失效。Main、Merge Assurance 和 Release Candidate 的完整验证不得被后续无关提交取消。

Publish 后必须先把 AI-SDLC 推荐投影标记为 `published_unverified`，文档、installer 和推荐器不得选择该版本；随后重读 GitHub 权威状态，验证平台自动生成且框架不能阻止签发的 GitHub Release Attestation，并把 attestation identity/digest、Satisfaction Proof、Activation Bundle/Envelope 与制品 digest 写入现有 Evidence Store 的签名 Release Certificate。GitHub Release Attestation、框架内部 Stage Close Attestation 和 Release Certificate 是三个不同工件；后两者可被安全栅栏阻止提交，不得实现第二套 GitHub attestation authority。该 Certificate 是派生证明，不作为发布后新增/修改的 Release asset；完成后推荐投影才可进入 `trusted`。Publish API 响应丢失、进程崩溃或重复投递时必须通过重读 GitHub Release、tag、资产和 attestation 幂等对账，收敛到唯一 Published/Failed 事实，禁止盲目重发或新建重复 Release。

发布后出现同执行合同红灯、Gate Proof/Execution Equivalence 后验失效、资产/attestation 不一致或迟到 P0/P1 时，不改写历史 Certificate，而是生成绑定 WorkItem 的不可变 Revocation Receipt，将证明投影标记为 `revoked/compromised/withdrawn`，停止后续发布与安装推荐，撤销进行中 Session 的动态专家正式关闭权和待提交 Stage Close Attestation/Release Certificate，并按平台能力隔离或撤回 Release。进行中 Session 保留原 Policy digest 并可继续非权威分析；已知撤销、已确认篡改或真实 P0/P1 时不得形成正式 Final Gate 关闭或新内部证明，并进入既有事故终态。平台已经生成的 GitHub Release Attestation 不可被框架撤回，只能由 Revocation Receipt 与推荐状态表达后验失信。Break-glass 不能生成正式 Certificate 或正式 Release；紧急分发只能是显式不受信任的 prerelease。

专项合同必须冻结 Revocation 到在线 installer/recommendation 停止传播的最大延迟，并定义离线安装时如何验证最后可信 Certificate/Receipt；无法获得新鲜撤销状态时不得把缓存结果表述为“当前仍受信任”。

Certificate、Manifest、资产 digest、Publish/Revocation Receipt 和绑定执行证据的最短保留期必须覆盖该 Release 的支持周期及其后续审计窗口；到期清理只能删除可重建副本，不能破坏已发布版本的验证链。

### FR-003：静态双层门禁

在智能选择器开发前，先复用现有能力形成两个明确层级：

1. Fast PR Result：每次 PR 更新运行 `PR Checks`、`Cross Platform Core`、确定性的核心 Smoke 和变更直接相关的显式专项包；
2. Merge Assurance Result：PR Ready、合并候选、Release Candidate 或显式高风险触发时运行完整 3 OS × 4 Python 基线及 Windows Shell。

静态阶段必须先冻结当前每个 OS/Python 格的 Baseline Collection Manifest。P0 分层只允许改变触发时机、执行顺序和集合等价的分片，不得改变任一格的测试成员；Merge Assurance 必须绑定精确 head/tree/merge candidate，候选变化立即失效。任何减少某格测试成员的行为都属于 FR-007，只有版本化平台元数据、未知回退和对冻结基线的 Shadow 证明通过后才能生效。

P0-3 同时交付三类彼此分离的最小身份与绑定，不提前包含 Selector/Telemetry 的完整元数据：

- `bootstrap_test_case_id`：由版本化 collection namespace、规范化 pytest node identity 和参数实例形成；普通代码/测试 tree 变化、Baseline 更新和 OS/Python cell 变化不得改变该 ID；collection namespace 升级属于显式身份迁移，必须提供完整血缘映射，否则 fail-closed，不得静默重建全体 ID；
- `bootstrap_execution_member_id`：由 `bootstrap_test_case_id + OS/Python cell` 形成，是冻结矩阵成员、集合等价和成员 delta 的比较键；
- `baseline_snapshot_binding`：绑定 workflow/collection command、代码/测试 tree、前序 Baseline digest、候选 identity 和独立 Gate Satisfaction，只承担快照来源与授权证明，不得进入上述成员身份键。

唯一 Baseline 更新权威是受保护的 Baseline Update Job；Manifest 只是投影。更新必须用稳定 `bootstrap_execution_member_id` 计算成员 delta，并通过 `baseline_snapshot_binding` 绑定前序 digest、代码/测试 tree、候选与独立 Gate Satisfaction；候选不能用自己生成且未独立验证的新 Baseline 证明自身完整性。P0/P1 允许经独立 Gate 验证的单调新增；删除成员、缩小 cell、无显式血缘映射的重命名或无法解析血缘时继续 fail-closed，并进入 FR-007 的 Shadow/独立审批合同。tree 或 Baseline digest 的正常变化只形成新快照证明，不得隐式重建全部成员身份。

完整测试不得删除。普通开发阶段只避免重复启动完整矩阵；进入合并和发布时仍由冻结完整基线授权。

### FR-004：重型测试分层

真实 CLI、Git 子进程、多进程、文件锁、并发竞争、崩溃恢复、故障注入、长超时和真实安装测试必须被识别为可审计 Test Tier。Tier 包含 owner、版本、原因、复核期限和降级审计。

目标状态是只有具有操作系统行为差异的用例跨三系统重复，纯语义单元测试不因惯性复制到全部平台和版本；但该成员变化在稳定 Test ID、版本化 platform metadata、未知回退和 Shadow 基线对照完成前不得用于缩小 P0 静态矩阵。

### FR-005：CI 原始证据和指标采集

框架 CI 必须生成可重建的 Collection Manifest、JUnit、Job/Shard 时间、Active Execution Time、Queue Time、取消原因、Runner Minutes、失败签名、平台、Python、命令和工具链摘要。

取消原因至少区分 superseded、人工取消、timeout、infra、fail-fast 和 Policy cancel，并分别记录取消前是否已经形成可复用终态 Evidence；不得把已产生有效证据和纯浪费合并成一个 Cancelled 指标。

集合不变量固定为：`expected collection = executed terminal outcomes ∪ contractual_not_applicable`。Shard 交集必须为空、并集必须等于冻结 Collection，JUnit 与 Execution Manifest 必须逐测试双向对应。

只有在 Test ID、cell 和预声明 OS/Python 适用性合同证明该测试对当前 cell 结构性不适用时，`contractual_not_applicable` 才可计入完备性；它必须绑定 Policy digest、WorkItem、独立审批者、创建/到期时间和补偿证据，候选不得自批。Flaky、失败、timeout、资源不足和临时 quarantine 都不能满足正式 Merge/Release Floor，只能保持阻断、恢复宽门禁或进入显式不受信任 prerelease。`not_run`、unexpected deselection/skip、cancelled、timeout、early-exit、空/损坏 JUnit、无终态、过期/未知/自签排除默认使 Gate 失败。

这些数据只用于框架自开发，不进入普通用户项目。指标先用于观察和决策，不直接缩窄门禁。

### FR-006：版本化 CI Budget Policy

Fast Gate 时长和 Runner 预算是版本化 Policy，不是固定 Schema。初始目标只作为观察目标；系统采集足够运行后，依据风险 Cohort 的滚动 P50/P95、取消成本、晚红率和发布 Lead Time 自动生成候选。

Baseline 与 Candidate 必须在冻结的同一输入人口、风险定义、窗口和质量 Floor 上做配对比较；Candidate 不能在同一次评估中重定义 Cohort。Failed、Timeout、Cancelled 和 Incomplete 必须进入明确的删失或下界统计，不能从耗时人口静默排除。后移工作同时计入 push-to-safe-to-merge、总 Active Execution、总 Runner、late-red、Coverage Leak 和 Release Lead Time。

Budget 超限触发诊断、分片或范围回退，不允许删除质量 Floor 中的测试。任一人口、必要字段或质量反指标缺失时，Budget Candidate fail-closed；效率改善不能只由 Fast P95 单指标成立。

### FR-007：自适应 CI 的后续能力

只有静态双层门禁稳定后，才能依次引入：

1. 将每个仍存活的 `bootstrap_test_case_id` 一一迁移为稳定 `test_case_id`，补齐跨运行重命名、合并和拆分的显式血缘；`bootstrap_execution_member_id` 继续只表达 Test Case 在 OS/Python cell 中的执行成员，不得与 Case Identity 混为一谈；
2. 版本化 tier、platform、resource、determinism、component 和 owner 元数据；
3. Selection Manifest 和未知回退；
4. Shadow Selector 与完整矩阵对照；
5. Execution Equivalence、Candidate Provenance、Gate Satisfaction 三层证明；
6. 跨运行 Evidence 复用；
7. 动态分片和执行顺序优化；
8. 复用 OfflineOptimizationController 协议的隔离 CI domain/family 实例生成 Policy 候选，并由下游合同指定的版本化 CI Policy Authority 写入；不得复用 Activation Pointer 或新增同义 CI 状态机。

任何 P0/P1 Coverage Leak、同执行合同绿红不一致或错误 Evidence 复用都立即撤销候选、恢复宽门禁并进入框架问题 Backlog。

三层证明权责不可合并：Candidate Provenance 只证明候选身份；Execution Equivalence 只授予复用资格，任一 base/merge identity、workflow、lockfile、runner image、工具链、环境、Test/Shard Manifest 或制品身份未知/不一致即为不等价；只有 Gate Satisfaction Proof 才能授权关闭或发布。并发、锁、stress、真实安装、制品 Smoke、用户指南旅程以及 Policy 指定的 Release Floor 不可跨运行复用。同执行合同绿红不一致必须传递撤销全部依赖的复用证据、Gate Proof 和尚未发布的 Certificate。

## 12. 专项二：Loop 收敛与成本治理

### FR-008：正式恢复 002 合同包

已通过历史双专家评审的 002 `spec.md` 和 `research.md` 必须先正式进入线上可追踪分支，再创建 data-model、contracts、plan 和 tasks。技术方案需要新的独立对抗评审通过后才允许开发。

### FR-009：Slice A——历史回放、基线与最小父级守卫

实现 HistoricalReplayManifest、InitialReviewBaseline 和只读 Graph-lite 投影。只对显式绑定 002 SlicePlan 的 WorkItem 安装最小父级守卫；强制 Slice 缺失或 Aggregate Evidence 不完整时，现有 Close Authority 拒绝父级 `completed/passed`。

Graph-lite 不拥有状态迁移、关闭、预算或调度权。

### FR-010：Slice B——交付准入与父级关闭

实现 Delivery Feasibility、完整 SlicePlan、父级 AC 到主责任 Slice 的唯一映射和 Aggregate Closure。无法在单 Slice Hard Budget 内完成、且存在多个可独立验收/回滚的强制领域时，必须在 Execute 前返回 `split_required`；缺用户原子性决策时返回 `needs_user`。

`split_required` 只是 `DeliveryFeasibilityDecision.reason`，不是新的持久化 Loop 终态。父 WorkItem 始终保持原非终态，决定必须携带冻结 reason/guard，并按以下唯一谓词映射：

| 冻结条件 | 现有 LoopStatus | 恢复入口 |
|---|---|---|
| 需要用户确认新 SlicePlan、原子性或范围选择 | `needs_user` | 用户提交被要求的冻结决定后继续 |
| 已有冻结拆分决定，但外部不可用依赖阻止执行 | `blocked` | 依赖恢复后按原决定继续 |

恢复时不得重新猜测映射；相同 DeliveryFeasibilityDecision 必须重放为相同状态。不得为此新建同义状态机。

### FR-011：Slice E——Evidence Scope 与分层验证

在 B 后优先实现 Evidence Scope Index、局部失效和 Targeted/Adjacent/Broad/Full 四层验证。外部 CLI/API、Schema、安全、恢复、工具链不兼容或未知依赖触发 Full；多个信号采用最宽层级。

该 Slice 优先于 C 和 D 的原因是：它直接降低后续每个专项的重复测试成本，同时保持可证明的完整性回退。

### FR-012：Slice C——语义收敛、预算和真实终态

每个获授权修复都生成 RepairAttempt。无关、未验证、重复、重复被拒和越权扩大范围的候选都计为 no-progress；只有有效修复证明改善。ResourceGovernor 继续承担唯一余额和 Hard Budget 权威。

Macro Round 与 Review Cycle 必须正交。预算耗尽、两次 no-progress、依赖阻断、用户决策缺失和不可闭环分别进入既有真实终态，不得通过新轮次重置。

### FR-013：Slice D——迟到 Finding 与 400/50

后续新增阻断 Finding 只允许：

- `regression_of` 已有 Finding；
- 首次评审后才可获得的 `new_critical_evidence`；
- 协议完整性或必需测试失败；
- 首次漏检但经证据确认的真实 P0/P1。

第四类必须阻断，并记录 `reviewer_coverage_leak` 和 `late_critical_finding`，用于离线角色质量治理。

400/50 继续作为风险信号。只有复杂度、重复、耦合、职责或范围共同恶化时才升级；`unsupported/unknown/parse_failed` 是分析覆盖缺口，不能反向证明安全。

### FR-014：Slice F——自动优化接入

Slice F 是 P2 可选扩展，只能把 Loop Policy 候选注册到复用 OfflineOptimizationController 协议的隔离 Loop domain/family 实例；实例不拥有 WorkItem、Loop 状态或关闭权，候选仍由下游合同指定的版本化 Loop Policy Authority 应用，不得复用 Activation Pointer。若依赖合同不稳定或收益不成立，必须以 `deferred/no_go` 结束，不阻断 A 至 E。

## 13. 专项三：动态专家产品化与数据驱动晋升

### FR-015：统一成熟度、Activation 状态和价值入口

CLI 和机器可读输出必须显示：

- `implemented/integrated/shadow/enforced/optimized`；
- 配置/Pointer Phase、运行时 `effective_enforcement_mode`、降级原因、Policy version/digest 和 effective time；机器合同必须分字段表达，禁止仅用一个 Phase 覆盖“Pointer 仍为 Phase 2 但当前 Session 已回退 Advisory”的事实；
- 启用 Stage 与 Risk Level；
- Shadow/Enforce 独立有效样本和成熟 Outcome 数量；
- 各覆盖维度缺口；
- 质量置信区间和阻断原因；
- 最近晋升、撤销、回滚和恢复结果；
- 唯一下一步。

Phase 1 必须明确显示 `non-blocking` 和 `offline optimization disabled`，不得让用户误以为 Finding 已经拥有关闭权。

同一专项还必须从正式安装包验证 Phase 1 普通用户价值 MVP：用户无需编辑内部 JSON 或选择统计参数，能够在已有项目或全新项目中触发动态专家、看见有效 Shadow Finding、获得唯一下一步，并完成接受、修复或忽略；结果必须明确 non-blocking，不要求 Final Gate 关闭影响或坏 Policy 回滚。状态入口、帮助、Release Note 和真实关闭权必须一致；只显示“数据不足”不算价值激活。

### FR-016：删除固定观察天数硬门槛

`observation_window_days=14` 不再作为永久晋升条件。其当前“最早样本距评估时间”语义既不能证明时间覆盖，也不能证明结果成熟。

新的晋升资格公式固定为：

```text
PromotionEligible =
    CoverageReady
    AND MaturityReady
    AND QualityReady
    AND IntegrityReady
    AND RollbackReady
```

任何一项未知或不满足都不得晋升。满足全部条件后，可以在 14 天之前晋升；等待超过 14 天但条件仍不满足，也不得晋升。

现有 Policy 中的 `observation_window_days` 和 `outcome_maturity_window_days` 必须按旧版本原语义保持可读和可重放，不得在相同 Policy digest 下静默改义。新语义必须发布新的 Policy Schema/Policy version，通过显式 Migration Decision 生成候选；迁移完成前，旧 Policy 可以继续提供既有 Shadow 事实，但不能因字段被忽略而自动扩大阻断权。迁移失败或证据不足时保持当前稳定 Phase，并输出唯一恢复路径。

Phase 晋升的单写合同固定为：`ActivationAssessment` 只生成评估事实和 Promotion Decision，`ActivationPolicyStore` 是唯一 Phase/Policy Pointer CAS 写入者。OfflineOptimizationController 在 Phase 1 至 3 不参与 Phase 写入，Phase 4 后也只能生成 Automation Envelope 内的参数候选，由 Store 校验并写未来 Pointer。

### FR-017：独立有效样本

有效样本必须绑定 `data_domain`、项目 lineage、WorkItem、Stage、Risk、候选族、语义变更族、Finding/事故族、专家角色/绑定、Provider/模型、Policy、平台和完成结果。

完全相同的执行指纹重复运行只保留血缘，不增加独立样本权重。近重复样本按项目 lineage、语义变更族、候选族、Finding/事故族和因果事件形成相关 cluster，每个 cluster 有版本化最大权重；单一 WorkItem、候选族、角色组合或短时间批量回放不得垄断项目内有效样本。

Replay 与 synthetic 数据只证明协议、完整性和回归召回，不能等价增加 prospective production outcome 权重。通过改 WorkItem ID、candidate digest、Provider 或模型制造表面差异，不能绕过 cluster 上限。

### FR-018：覆盖度而非原始总量

每次 Framework Activation Qualification 必须预先冻结 `TargetActivationDelta`，只资格化本次新增的 Stage/Risk/Provider/平台组合。Phase 1→2 只评价 Low Risk 目标组合；Phase 2→3 才评价新增中高风险组合，未来无关 Risk 不得阻塞当前迁移。

CoverageReady 至少按以下适用维度判断：

- 五个 Stage；
- 已启用和待启用 Risk Level；
- Windows、macOS、Linux 或明确声明不相关的平台；
- Provider/模型能力组合；
- 全新空项目和已有项目；
- 正常关闭、修复、回滚、迟到 Finding、证据缺失和恢复路径。

它只使用框架自有、合成或明确受信的测试项目，生成内容寻址的 Qualification Decision/Evidence、`QualificationApplicabilityProfile` 和 Envelope 候选；不得消费普通用户数据。Profile 至少绑定语言/Parser、项目与仓库拓扑、Stage/Risk、精确 Provider/模型身份或版本化能力等价证明、平台、Adapter/工具链、Policy Schema 和 TargetActivationDelta。

受保护 Publish Job 必须先冻结 Envelope payload；payload 绑定 Qualification 决策/证据 digest、Profile、TargetActivationDelta、默认 Policy/Schema、框架 commit/tree、Draft/Release identity、前序 Envelope digest 和版本化制品嵌入合同，但不包含自身 digest 或尚未生成的实际制品 digest。随后对该 payload 计算 detached `envelope_digest`，实际安装包嵌入该 digest；Release Satisfaction Proof、GitHub Release Attestation 和最终 Release Certificate 再共同绑定 `envelope_digest` 与实际制品 digest。该无环顺序是唯一内容寻址语义。Envelope 只授权已资格化组合，未资格化组合继续 Shadow；ceiling 只是摘要。

`Project-local Adaptation` 不要求单个项目模拟多个项目类型、全部平台或全部 Provider，只对当前项目适用维度采集证据。项目进入 Enforce 前必须证明当前语言/Parser、拓扑、Stage/Risk、Provider/模型或能力等价 digest、平台、Adapter/工具链和 Schema 与 Profile 强匹配；未知或不匹配保持 Phase 1 Shadow。`not_applicable` 只解释项目确实不使用的维度，不能替代尚未资格化或匹配未知的组合。

Policy 可以保留冷启动最小样本，但必须由目标质量阈值和置信要求反推，不得只凭经验固定一个长期数字。低数据项目使用已通过 Framework Qualification 的保守稳定 Policy，不因本地样本不足而失去基础价值。

### FR-019：数据驱动 Outcome 成熟期

Session 只有在 Finding 与 Attribution 已经完整关闭，并越过适用的迟到事件观察截止点后，才能成为 Mature Outcome。

成熟延迟按 Stage、Risk 和结果类型，使用带右删失的 time-to-event 估计历史 `late-critical/reversal/escape` 到达分布的保守 P95/P99，并附加版本化安全边界。Cutoff 只能由更早、冻结且与当前候选评价 cohort 分离的数据产生；同一 cohort 不能既估计 cutoff 又自证成熟。

没有历史数据时使用不可由当前候选自行缩短的保守 Bootstrap Maturity Floor。Bootstrap 只能维持或延长等待，不能从当前未成熟 cohort 自我缩短；数据达到预注册条件后才能由独立候选替代。

不得因为原始样本量大就把尚未成熟的结果计为成功，也不得用统一 14 天永久覆盖所有 Stage 和 Risk。

### FR-020：质量置信与顺序停止

QualityReady 必须只使用 Mature Outcome，分别计算 reversal、late-critical 和 escape 的事件数、冻结机会分母与置信上界。顺序提前停止必须使用 anytime-valid 单侧置信序列或预注册 alpha-spending，禁止反复窥视普通固定样本区间；分析单位、成功/伤害/无望停止边界，以及跨指标、Stage、Risk、Cohort 和多候选比较的 family-wise error budget 必须在评价前冻结。

候选在评价前预注册并冻结。`correlation_cluster_id` 是不可分割分区单位，同一 cluster 不得跨 Candidate Generation、cutoff estimation 和 qualification Holdout。拆分必须采用 group/time-blocked 策略并冻结 purge/embargo 边界；cluster 身份未知或不完整的样本不得进入 Holdout 或 QualityReady。Post-promotion monitoring 只消费晋升后新产生的 prospective cluster。

Candidate Generation、cutoff estimation、Replay、Shadow、Holdout qualification 和 post-promotion monitoring 按上述 cluster/lineage/time 形成不可变拆分，生成器不得读取 Holdout。Holdout commitment、重用次数、刷新条件、泄漏失效规则和 Alpha Ledger 必须复用现有统计治理能力；发生泄漏或重复试探超限时，相关候选全部失效。

P0/P1 严重度、逃逸原因集合、关闭完整性和质量 Floor 不可被优化器降低。任何真实 late-critical 或 escape 都触发 Attribution、候选撤销和适用范围回滚。

### FR-021：本地无感自动优化

普通用户项目中的适配闭环固定为：

```text
Project-local Facts
  -> immutable DatasetSnapshot
  -> Envelope-constrained Candidate Generation
  -> independent Replay / Shadow / Holdout
  -> deterministic Assessment / Promotion Decision
  -> ActivationPolicyStore CAS for next-session Policy Pointer
  -> outcome monitoring
  -> automatic rollback when violated
```

系统自动判断白名单内调整哪个参数、调整多少和何时回滚。用户不需要审批每次候选，也不需要理解统计阈值。所有决策可解释、可重放、版本化，并且不热更新当前 Session。未知字段、最低 Stage/Risk/角色/能力覆盖、P0/P1 定义、关闭完整性和质量 Floor 不可被优化；默认一个候选只改变一个可归因维度并受最大单步变化约束。

项目本地优化数据不得上传、跨项目共享或进入框架中央训练集。没有足够本地数据时保持当前稳定 Policy，不制造伪优化。

ActivationPolicyStore 在 Pointer CAS 和每个新 Session 加载时必须验证如下完整链：`Envelope -> Qualification Decision -> Release Satisfaction Proof -> GitHub Release Attestation -> Release Certificate -> 当前安装制品 digest`，并检查 Revocation Receipt 新鲜度。验证结果严格分为三类：

1. 完整链匹配且撤销状态在版本化本地缓存期限内新鲜：可以按 Envelope 授予 Enforce；
2. 组合未资格化/不匹配、能力等价未知、撤销源暂时不可达或本地缓存过期但没有已知失信事实：撤回动态专家 Enforce 与内部证明签发权，Finding 回到 Advisory，使用真正 non-blocking 的 Phase 1 基础关闭路径；不得把该回退显示为事故阻断；
3. 已知 Revocation、已确认 Envelope/Profile/Policy/制品篡改、跨 Release/Fork 重放、package digest 不一致或真实 P0/P1：进入既有 `needs_user/blocked` 事故事实，禁止权威关闭和新内部证明，不得伪装成普通 Phase 1。

专项合同必须版本化冻结撤销缓存期限、允许的离线恢复入口、最小联网行为和唯一用户提示。验证不可达不得上传项目数据或增加普通用户 CI；缓存过期时默认提示“动态专家验证不可用，已回退 Phase 1 Advisory，基础流程可继续”，只有已知失信事故才要求用户处置。当前 Session 一旦因验证不可达或缓存过期降级为 Advisory，即使新鲜度随后恢复也不得在 Session 内重新获得 Enforce；只能由新 Session 或绑定原输入的显式 replay 重新验证，避免权限往返和热更新。

为消除检查—提交 TOCTOU，复用现有权威 Evidence/Receipt Store 的单调 `revocation_generation`（或语义等价 revision）和序列化条件追加接口，不新建 Policy Pointer、撤销状态机或第二 Close Authority。线性化边界是已完成签名验证并落入该本地权威 Store 的 Receipt generation；中央撤销源到本地的传播受 FR-002 最大延迟约束，普通关闭不得同步依赖远程调用。StageCloseAuthorizer、Stage Close Attestation writer 和 Release Certificate writer 必须把读取到的 generation 与完整链摘要写入候选提交，并在同一事务或 fenced CAS 中确认 generation 未变化且无已知撤销后才原子提交；崩溃与重试必须从权威 revision 重读并幂等收敛，禁止复用提交前缓存结论。线性化规则固定为：Revocation 先提交时关闭/签发 CAS 必须失败；关闭/签发先提交时，随后 Revocation 必须确定性把该对象纳入影响集并撤销。

Rollback 不能只回退未来 Pointer。Revocation Receipt 提交并取得 generation 后，系统必须生成 Policy Impact Manifest，枚举坏 Policy 生效后的进行中 Session、待提交 Stage Close Attestation/Release Certificate、已关闭 Session 和下游 Evidence/Certificate；进行中与待提交对象先撤销关闭/签发权，已关闭对象撤销可撤销授权，并按影响执行 replay、reopen 或 `needs_user`。Impact worker 必须依据权威事件 watermark 幂等扫描至固定点；任何使用旧 generation 的晚到提交均由 CAS 拒绝，任何在线性化顺序上先于 Revocation 的关闭/证明都必须被扫描纳入。进程崩溃后从 watermark 恢复，无法自动补偿时保持显式阻断。Pointer 回退、进行中授权阻断和历史影响补偿必须分别可审计。

### FR-022：Phase 晋升顺序

- Phase 1：五阶段 Shadow，Finding 可见但不阻断；
- Phase 2：只有 Low Risk TargetActivationDelta 通过 Framework Qualification、发布 Envelope，且项目适用画像强匹配后，才允许保守默认 Policy 启用 Low Risk Final Gate；本地项目无需重新模拟其他项目，但不得越过 Envelope；
- Phase 3：在新组合 Shadow 和低风险 Enforce 结果成熟后扩展到中高风险；
- Phase 4：在生产 Outcome 和 Holdout 证明后启用 OfflineOptimizationController 的离线角色、Provider 与 Budget 参数优化。

开发完成不等于 Phase 已经生效。Phase 迁移只能由已发布 Policy、完整 Promotion Decision 和 ActivationPolicyStore 原子写入完成，禁止直接编辑 JSON 跳级。

## 14. 专项四：Lean Code 精简性治理

### FR-023：停止机械拆分

任何新增拆分必须提供 Split Benefit：职责更清晰、重复减少、耦合降低、可测试性改善、故障隔离增强或维护成本下降。仅因为文件超过 400 行或函数超过 50 行不得拆分。

### FR-024：建立 Lean 自身热点画像

基于调用图、单调用模块、循环依赖、重复逻辑、公共调用者、动态引用、修改频率、缺陷密度和测试运行成本生成只读热点投影。热点投影不拥有强制重构权。

### FR-025：定向合并与行为金样

优先处理只做代理转发、只有一个调用者且不形成稳定边界、重复相同语义或增加调用解析复杂度的模块。每个合并候选必须绑定行为金样、AST Corpus、Finding 稳定性和性能对比；不能以模块数下降代替质量证明。

### FR-026：跨语言独立成熟度

Python 能力不得被宣传为通用语言能力。TypeScript、Java、Go 等必须分别定义 Parser/AST、未知语义、测试 Corpus、风险规则和回退行为，并独立通过验收后才能标记为 supported。

## 15. 共享数据、工件和权威边界

下一阶段允许新增的顶层工件类别如下。具体 Schema 在专项 data-model 中定义：

| 工件 | 用途 | 权威边界 |
|---|---|---|
| Release Satisfaction Proof | Publish 前证明精确候选满足发布条件 | 由 Publish Job 一次性消费；不替代 GitHub 权威状态 |
| Release Certificate | Publish 后绑定 GitHub Release Attestation 与最终推荐资格 | 不拥有发布迁移权；attestation 未验证时不得生成 |
| Release Revocation Receipt | 记录已发布证明失效与处置 | 绑定现有 WorkItem；不新建 Release 状态机或改写历史证书 |
| Test Collection/Execution Manifest | 描述测试集合、环境和执行结果 | 不拥有测试选择 Policy 的晋升权 |
| CI Telemetry Snapshot | 框架自开发统计输入 | 只消费完整 CI Evidence，不进入普通用户项目 |
| Historical Replay Manifest | 重建旧 Loop 输入与兼容事实 | 不拥有 Loop 状态迁移权 |
| Initial Review Baseline | 冻结首次评审候选与覆盖 | 不复制 Finding Ledger |
| Evidence Scope Index | 计算哪些证据失效 | 不直接授权父级关闭 |
| Cost Receipt | 投影 ResourceGovernor 事实 | 不拥有独立余额 |
| Qualification Bundle / Release Activation Envelope | 内容寻址冻结决策、证据、适用画像、目标增量、默认 Policy 与组合级资格 | 受保护 Publish Job 唯一发布；绑定 Release/制品证明链；ceiling 仅摘要 |
| Activation Dataset Snapshot | 冻结项目本地有效样本与成熟结果 | 以 domain/lineage/family 隔离，不跨项目、不热更新当前 Session |
| Activation Promotion Decision | 授权 Phase/Envelope 内的未来 Policy Pointer | ActivationAssessment 生成，只有 ActivationPolicyStore 可以 CAS 写 Pointer |
| Optimization Candidate Decision | 记录 Phase 4 或其他隔离 family 的参数候选评价 | 与 Phase Promotion 使用不同 artifact kind；不能迁移 Phase 或直接写 Pointer |
| Policy Impact Manifest | 枚举坏 Policy 已影响的 Session 与授权 | 不自行关闭 WorkItem；驱动 replay/reopen/needs_user |
| Maturity Status Projection | 面向用户展示真实成熟度 | 不改变 Phase 或阻断权 |
| Lean Hotspot Projection | 提供减重候选 | 不直接阻断发布 |

线上问题的内部生命周期唯一权威是现有 WorkItem。Finding、CI、Release、Rollback 和 Coverage Leak 只形成绑定事件及关闭证据；GitHub Issue 是可选外部投影，必须携带 `work_item_id` 和同步收据，不拥有独立完成权。缺陷、已接受缺口和 Roadmap 分别使用 `defect/accepted_gap/roadmap` 分类，禁止新建同义 Backlog Store。

## 16. 开发优先级

### P0：可信交付止血

| 顺序 | 工作项 | 依赖 | 完成出口 |
|---:|---|---|---|
| 0 | Emergency Publish Freeze | 无 | 精确候选 Required Gate 非 Success 时只允许 Draft；不等待 Windows 根因 |
| 1 | Windows Activation Lease 并发根因修复 | 无，可与 P0-0 并行 | 冻结复现 Profile、定向压力、长链和完整候选验证通过 |
| 2 | 永久 Release Truth 与不可取消的精确候选证据 | P0-0，不依赖 P0-1 完成 | Pending/Fail 不能发布，Certificate 可重放、防篡改、可撤销 |
| 3 | Bootstrap Case/Execution Identity、Baseline Authority 与静态双层门禁 | P0-0 | 普通推送不重复启动完整矩阵；成员身份跨正常快照稳定；单调新增可审计；负 delta fail-closed；完整质量基线仍保留 |
| 4 | 线上已确认缺陷修复与 WorkItem 真值 | P0-0 至 P0-3 | 主线红灯、handoff 部分写入、escaped issue 等已知缺陷都有权威状态、修复证据与关闭结论 |

### P1：用户价值激活

| 顺序 | 工作项 | 依赖 | 完成出口 |
|---:|---|---|---|
| 5 | 动态专家状态与普通用户价值 MVP | P0 发布护栏和可安装候选 | 五阶段真实状态可见；安装包纵向路径无需内部 JSON/人工调参即可完成 |
| 6 | Framework Qualification、数据驱动 Assessment 与项目本地适配代码 | P1-5、P0 真值 | 双数据域隔离；无固定 14 天硬门槛；单写晋升、Holdout、回滚补偿可重放 |
| 7 | Low Risk Phase 2 条件式生效 | P1-6、成熟 Framework Qualification | 只在已发布 Release Activation Envelope 的授权组合内生效；未达证据则保持 Phase 1 |
| 8 | 002 正式合同包和新一轮技术评审 | P0 稳定基线 | spec/research/data-model/contracts/plan/tasks 一致通过 |
| 9 | Loop Slice A | P1-8 | 历史回放、首次基线和最小父级守卫通过 |
| 10 | Loop Slice B | P1-9 | 交付准入、SlicePlan 和 Aggregate Closure 通过 |
| 11 | Loop Slice E | P1-10 | 局部失效和四层验证通过 |
| 12 | Loop Slice C | P1-10 | RepairAttempt、语义收敛、预算和真实终态通过 |
| 13 | Loop Slice D | P1-10 | 迟到 Finding、覆盖漏检和 400/50 语义通过 |

动态专家 P1-5 至 P1-7 只消费当前已发布五阶段事实，可以与 Loop 002 并行，不以完整 A 至 E 为前置。若后续 Loop E/C/D 改变 Evidence Scope、迟到 Finding、RepairAttempt 或真实终态语义，必须生成新的 Activation Policy/Schema 版本并重新 Qualification，禁止沿用旧 Promotion Decision。

Slice E、C、D 在 B 后可以并行开发，但合并顺序优先 E，以尽早降低后续验证成本。

### P2：自适应治理

| 顺序 | 工作项 | 依赖 | 完成出口 |
|---:|---|---|---|
| 14 | Bootstrap→稳定 Test ID、完整元数据和跨运行 CI Telemetry | P0-3 | 一一迁移可证明；数据可重建、作用域隔离、重命名血缘有效 |
| 15 | CI Shadow Selector 与 Selection Manifest | P2-14 | 与完整矩阵对照，无未处置 P0/P1 Coverage Leak |
| 16 | Execution Equivalence 与 Evidence 复用 | P2-14、P2-15 | 错误复用 fail-closed，同合同不一致可归因 |
| 17 | 动态分片与 CI Policy 自动优化 | P2-16 | 复用优化协议但使用独立 domain/family/instance；Shadow/Holdout/回滚完整 |
| 18 | Loop Slice F | Loop A 至 E、稳定优化合同 | 可选优化通过或真实 `deferred/no_go` |
| 19 | 动态专家 Phase 3/4 生效 | P1-7、成熟生产数据 | 只由完整数据、Promotion Decision 和 Store CAS 迁移 |

### P3：维护性与扩展

| 顺序 | 工作项 | 依赖 | 完成出口 |
|---:|---|---|---|
| 20 | Lean Code 热点测量和定向合并 | P0/P1 稳定测试基线 | 维护面积下降且 Finding/行为不漂移 |
| Roadmap | 非 Python 语言语义扩展 | 真实用户需求、独立语言 PRD 和 Corpus | 不属于本阶段 DoD；每种语言独立 supported 证据 |

## 17. 依赖关系

```text
Emergency Publish Freeze
  -> Permanent Release Truth
  -> Static CI layering
  -> stable development baseline

Windows lease root cause
  -> long-chain / full-candidate proof
  -> release eligibility restored

Loop 002 contract
  -> A
  -> B
  -> E / C / D
  -> optional F

Expert status + Value MVP
  -> Framework Qualification + project-local adaptation contract
  -> independent mature evidence + released Activation Envelope
  -> Phase 2 low-risk enforce
  -> Phase 3 medium/high-risk enforce
  -> Phase 4 offline optimization

Static CI
  -> stable test identity and telemetry
  -> Shadow selector
  -> Evidence reuse
  -> dynamic sharding and policy optimization
```

不得为了并行开发绕过上述语义依赖。不同 Worktree 可以并行实现无共享写面的 Slice，但合并前必须重新绑定最新主线和同一版本合同。

## 18. 指标与自动使用规则

### 18.1 发布与 CI

| 指标 | 用途 | 禁止用途 |
|---|---|---|
| Fast first-result / first-red | 优化测试顺序和 Fast Gate | 删除质量 Floor |
| Push-to-safe-to-merge | 判断真实交付反馈 | 把成本移到 Merge/Nightly 后假装提速 |
| Runner Minutes / Cancelled Minutes | 发现重复执行和取消浪费 | 单独决定测试是否必要 |
| Same-execution green/red inconsistency | 触发不确定性事件 | retry 后覆盖第一次失败 |
| Fast-green then late-red | 评估选择器 Coverage Leak | 缩窄严重度分母 |
| Release lead time | 优化发布流程 | 绕过精确候选证据 |

Fast Gate 的 15/30 分钟和 Runner 预算只能是 Bootstrap 目标。至少形成足够风险 Cohort 数据后，系统按版本化 Budget Policy 自动提出调整；没有数据时不由用户猜测修改。

### 18.2 Loop

| 指标 | 自动使用 |
|---|---|
| RepairAttempt / Effective Repair | 判断 no-progress 和候选质量 |
| Targeted 到 Full 验证分布 | 优化 Evidence Scope Policy |
| Budget exhaustion / split-required / needs-user | 调整准入和 Slice 建议 |
| Aggregate Closure rejection | 发现父级覆盖与依赖缺口 |
| Estimated Provider Cost / Active Execution Time | 生成可比较成本并调节 Soft Policy |

### 18.3 动态专家

| 指标 | 自动使用 |
|---|---|
| 独立有效样本覆盖 | 决定 CoverageReady |
| Mature Outcome 数量 | 决定 MaturityReady |
| 带右删失的 Late-event time-to-event P95/P99 | 由独立冻结 cohort 自动提出成熟期候选 |
| Reversal / Late Critical / Escape anytime-valid 置信上界 | 在预注册错误预算内决定晋升、撤销和回滚 |
| Role coverage leak / late critical finding | 调整角色覆盖与专家数量候选 |
| Provider/模型质量与成本 | 在安全包络内调整绑定候选 |

### 18.4 普通用户价值

| 指标 | 用途 | 禁止用途 |
|---|---|---|
| Phase 1 Time-to-first-valid-finding | 判断安装到首次可行动 Shadow 价值 | 用伪造 Finding 或阻断权降低时长 |
| Phase 1 无维护者介入路径完成率 | 判断小白能否独立处理 non-blocking Finding | 排除失败用户美化分母 |
| 运行时人工参数决策次数 | 验证无感自动治理；目标为 0 | 隐藏系统自动回退或错误 |
| Phase 2 Finding 采取率 / 错误阻断率 | 判断 Enforce 是否产生净价值 | 降低 P0/P1 Floor 提高采取率 |
| Phase 2 Rollback/Recovery 完成率 | 验证坏策略影响可补偿 | 只统计 Pointer 回退，或漏掉进行中 Session、待提交内部证明、已关闭 Session 任一影响层 |

### 18.5 Lean Code

| 指标 | 自动使用 |
|---|---|
| 单调用模块、重复语义、调用解析成本 | 生成合并候选 |
| 修改频率和缺陷密度 | 排序热点 |
| Finding 稳定性和 AST Corpus | 阻止语义退化 |
| 400/50 | 仅提供大小信号，不单独授权重构 |

## 19. 验收标准

### AC-001：同 Tree 可重复性

修复后的 Windows 并发场景按修复前冻结的 Profile 在受支持 Python 版本中定向重复通过，并在清洁 Runner 的真实长链及精确候选完整 Merge Assurance 中复核；相同执行合同出现绿红不一致时，发布和证据复用保持阻断并产生可归因事件。无法复现或归因时不得关闭缺陷。

### AC-002：发布不可抢跑

人为制造 Required Gate Pending/Failed/Cancelled、Artifact digest 不一致或用户 E2E 缺失时，Release 保持 Draft 且无法获得 Proof。携带 Activation 的 Release 若 Qualification/Profile/Envelope/默认 Policy/Schema/制品任一 digest 缺失或不一致，同样不得 Publish。未启用 GitHub Immutable Releases 时只能发布不受信任 prerelease；正式 Publish 后 tag/资产修改必须由平台拒绝，平台自动生成的 GitHub Release Attestation 验证通过，且内部 Certificate 以最新 `revocation_generation` 条件提交成功后，才能从 `published_unverified` 生成最终 Certificate、`trusted` 推荐和安装选择。Certificate 不得通过事后改写 immutable Release asset 实现。注入 Publish API 成功后响应丢失、进程崩溃或重复投递时，系统重读 GitHub 权威状态并幂等收敛，不产生重复 Release。发布后同合同红灯、Gate/Equivalence 后验失效、attestation 异常或迟到 P0/P1 产生 Revocation Receipt、停止推荐、撤销进行中 Session 关闭权与待提交内部证明，并进入 WorkItem 事故闭环。

### AC-003：测试完整性不压缩

Fast Gate 不拥有完整发布授权；P0 静态分层下，Merge Assurance 和 Release Candidate 的每格测试成员必须等于受保护 Baseline Authority 的当前版本。注入候选自生成 Baseline、缺前序 digest、writer spoof、未授权负 delta、无映射重命名、缺一测试、重复测试、Shard 交集、unexpected deselection/skip、not-run、timeout、cancel、early-exit、空/部分/损坏 JUnit 或 Manifest 时，集合等价和聚合 Gate 必须失败。把关键/失败测试改成 exclusion、批量/全量 exclusion、候选自签、过期/未知 Policy 或临时 quarantine 均不得获得正式 Gate Satisfaction；只有预声明且可证明的 `contractual_not_applicable` 可计入完备性。仅修改非测试代码、修改测试体但不改变规范化 node identity/参数实例、或单独新增一个测试时，所有既有 `bootstrap_test_case_id` 和 `bootstrap_execution_member_id` 必须保持不变，成员 delta 只能包含真实新增成员；tree/Baseline digest 只改变 `baseline_snapshot_binding`。P2 迁移必须证明每个仍存活的 `bootstrap_test_case_id` 恰好映射一个稳定 `test_case_id`；重命名、合并或拆分通过显式血缘处理，不得依靠 tree 变化隐式重建。

### AC-004：普通用户隔离

干净用户项目安装和运行后，不新增框架自开发 CI、远程遥测、分支设置或上传行为。框架 CI 数据不得进入安装包的默认用户数据路径。同一框架仓双作用域、Fork、复制目录、修改 remote、伪造 domain/policy family，以及跨 Fork/Release 复制 Envelope 的负向测试必须证明 CI、Framework Qualification 与项目本地 Dataset/Pointer/Holdout/预算互不可读、互不可授权。

### AC-005：Loop 父级真实关闭

显式采用 002 SlicePlan 的 WorkItem 在任一强制 Slice 或 Aggregate Evidence 不完整时，现有 Close Authority 拒绝 `completed/passed`；完成后可重放得到唯一相同结论。

### AC-006：局部验证和未知回退

局部变更只失效相关 Evidence；外部接口、Schema、安全、恢复、工具链或未知依赖确定性升级到 Full。人为损坏 Scope Index 时 fail-closed。

### AC-007：真实收敛和成本

无关、重复和未验证候选不能重置 no-progress；Hard Budget 到达后不启动新动作，并形成可解释终态。Cost Receipt 与 ResourceGovernor 不一致时拒绝关闭。

### AC-008：400/50 非机械阻断

仅大小超限保持 Advisory；复杂度、重复、耦合、职责或范围共同恶化时才升级。未知分析覆盖不能被标记为安全。

### AC-009：Activation 不依赖固定 14 天

足够数量的独立、分层覆盖、Outcome 成熟且 anytime-valid 置信达标的样本可以在 14 天前产生 Promotion Decision；等待超过 14 天但任一条件缺失时不能晋升。提前晋升不得由短时间高样本量、反复窥视、多候选试探或当前 cohort 自行缩短 maturity floor 触发。

### AC-010：重复样本不刷量

完全相同或同一项目 lineage、语义变更族、候选族、Finding/事故族和因果事件 cluster 内的近重复运行受同一最大权重限制；更换 ID、Provider 或模型不能刷量。Replay/synthetic 不能冒充 prospective outcome。Framework Qualification 的真实适用覆盖可以增加 Envelope 资格证据；项目本地不适用维度必须显式 `not_applicable`。

### AC-011：成熟结果与迟到问题

未越过由独立冻结 cohort 产生、且正确处理右删失的 maturity cutoff，或 Attribution 未关闭的 Session，不进入质量分母。Holdout 泄漏、重用超限、Alpha Ledger 不完整、普通固定区间被重复窥视或多候选错误预算缺失时不得晋升。晋升后出现真实 late-critical/escape 时，候选自动撤销、适用范围回滚并保留事件链。

### AC-012：本地自动优化无感

用户无需选择参数或批准候选。系统只能在 Automation Envelope 白名单、最低覆盖和最大单步变化内本地生成、Shadow、Holdout、晋升和回滚；当前 Session 的 Policy digest 不热更新，但撤销安全栅栏始终有效；跨域/跨项目复制数据或 Policy 默认拒绝。注入坏 Policy 后，验收必须证明不仅未来 Pointer 回退，Policy Impact Manifest 还枚举进行中 Session、待提交内部证明和已关闭 Session：前两者通过 `revocation_generation` 条件提交被拒绝，后者完成 Evidence/Certificate 撤销及 replay、reopen 或 `needs_user`。仅撤销源不可达或缓存过期时不得制造事故阻断，必须回到 Phase 1 Advisory 并允许原有基础关闭路径继续。

### AC-013：动态专家状态真实

CLI、JSON、Policy 和阶段关闭输出对 Phase、Risk、Shadow/Enforce、样本水位和阻断权描述一致；Phase 1 不得显示为正式阻断或已优化。

### AC-014：CI 自适应先 Shadow

Selector、Evidence 复用、动态分片和 Policy 候选在成为 Required 前必须与完整矩阵进行可重放对照。Candidate Provenance、Execution Equivalence 和 Gate Satisfaction 权责分离；任一等价字段未知即不可复用，Release Floor 不跨运行复用。任何 P0/P1 Coverage Leak 或同合同绿红不一致立即恢复宽门禁，并传递撤销依赖的复用证据、Gate Proof 和未发布 Certificate。

### AC-015：Lean 减重不改变行为

定向合并前后行为金样、AST Corpus、Finding 身份和受支持调用语义一致；模块数下降但行为或 Finding 漂移不得通过。

### AC-016：线上问题真值

主线失败、发布抢跑、Coverage Leak、Late Critical、错误复用和自动回滚失败都进入现有 WorkItem，绑定 Owner、状态、影响范围、修复证据和关闭结论。GitHub Issue 只作带同步收据的外部投影；`accepted_gap/roadmap` 不计为 defect，任一投影关闭不能替代 WorkItem Close Authority。

### AC-017：CI 指标可重建且无隐式授权

从冻结的 Collection Manifest、JUnit、Job/Shard 记录和执行合同可以重建同一风险 Cohort 的 P50/P95、取消成本、Runner Minutes、晚红率和发布 Lead Time。Baseline/Candidate 必须在同一冻结人口和 Cohort 定义配对比较；Failure/Timeout/Cancelled/Incomplete 进入删失/下界统计，后移成本进入端到端指标。重分类 Cohort、取消慢任务或删除任一必要输入时，Budget Candidate 必须 fail-closed；指标或 Budget 超限本身不能删除测试、缩窄质量 Floor 或获得发布授权。

### AC-018：Activation Policy 兼容迁移

旧 Policy digest 在升级前后重放得到旧语义下的同一结论。数据驱动晋升只能由新版本 Policy、显式 Migration Decision、完整 Promotion Decision、受信 Release Envelope 链和 ActivationPolicyStore CAS 生效；OfflineOptimizationController 不能写 Phase。不得通过复用旧 digest、复制旧 Envelope、忽略旧字段或直接编辑 JSON 获得更高 Phase。迁移异常、撤销源不可达或缓存过期时只撤回 Enforce 权并回到 non-blocking Phase 1 Advisory，保留旧 Pointer 及可恢复事件链；已知撤销或篡改才进入明确事故终态。

### AC-019：普通用户纵向价值

Windows、macOS、Linux 的正式安装包至少验证全新项目和已有项目中的 Phase 1 纵向路径：用户无需内部 JSON、统计阈值或维护者介入，能够获得有效 Shadow Finding、唯一下一步，完成接受/修复/忽略，并明确看到 non-blocking 结果。记录 Time-to-first-valid-finding、完成率、人工参数决策次数和 Finding 采取率；失败样本不得从分母排除。该 AC 不要求或伪造 Phase 2 关闭权。

### AC-020：Loop Slice F 独立止损

Slice F 只能在 A 至 E 已发布、收益假设和最大投资预算预注册后接入既有优化合同。依赖不稳定、净收益不成立或预算到达时以 `deferred/no_go` 结束，且 A 至 E 的已发布价值不受影响。

### AC-021：跨语言独立成熟度

非 Python 语言不属于本阶段 DoD。后续每种语言必须基于独立 PRD、Parser/AST Corpus、行为金样、未知/解析失败回退和真实用户需求单独获得 `supported`；不得用 Python 结果、通用文本规则或文件扩展名推导支持。

### AC-022：Activation 组合适用性与 Phase 2 价值

Framework Qualification 必须对 Low Risk TargetActivationDelta 生成绑定语言/Parser、项目与仓库拓扑、Stage/Risk、精确 Provider/模型或能力等价 digest、平台、Adapter/工具链和 Policy Schema 的 Profile/Envelope。语言、拓扑、Adapter、Provider/模型、平台或 Schema 未资格化、未知或不匹配时，项目保持 Phase 1 Shadow；`not_applicable` 不得替代资格缺失。匹配项目在保守默认 Policy 下验证真实 Final Gate 关闭影响、错误阻断率、坏 Policy Pointer 回退、进行中 Session 关闭权撤销和历史 Session 补偿。撤销源仅不可达或缓存过期时，验收必须证明无 Enforce/内部 Certificate、Finding 为 Advisory、Phase 1 基础关闭继续且用户只得到唯一回退提示；已知 Revocation、篡改或真实 P0/P1 时则拒绝关闭并进入明确事故终态，不得显示为普通 Phase 1。

负向注入 Envelope/Profile/Policy 篡改、跨 Release/Fork 重放、换包、旧 Envelope、已知撤销、package digest 不一致，以及 Provider 相同但模型变化且无能力等价证明时，ActivationPolicyStore 在 CAS 和新 Session 加载不得授予 Enforce；只有内容寻址 Qualification→Proof→GitHub Release Attestation→Release Certificate→当前制品完整链可以授权 Enforce，ceiling 摘要不能授权。撤销并发验收必须覆盖三种线性顺序并注入崩溃重试：检查前 Revocation 使关闭/内部证明提交失败；检查通过后、最终提交前 Revocation 使携带旧 generation 的 CAS 失败；关闭/内部证明先提交而 Revocation 后提交时，Impact worker 按 watermark 扫描至固定点并撤销该对象。三种路径都必须保留原 Policy 血缘，不留下撤销后仍有效的权威关闭或内部 Certificate，且 Impact Manifest 覆盖进行中、待提交和已关闭对象。

### 19.1 功能需求—验收追踪矩阵

| 功能需求 | 主验收标准 | 补充约束 |
|---|---|---|
| FR-001 | AC-001 | NFR-002、NFR-004 |
| FR-002 | AC-002 | AC-003、NFR-001、NFR-002 |
| FR-003 至 FR-004 | AC-003 | AC-001、NFR-004 |
| FR-005 至 FR-006 | AC-017 | AC-003、AC-004、NFR-006 |
| FR-007 | AC-014 | AC-001、AC-003、AC-016 |
| FR-008 至 FR-010 | AC-005 | NFR-001、NFR-003、NFR-007 |
| FR-011 | AC-006 | AC-003、NFR-001 |
| FR-012 | AC-007 | NFR-001、NFR-002、NFR-006 |
| FR-013 | AC-008 | AC-016、NFR-008 |
| FR-014 | AC-020 | AC-012、AC-014、NFR-003 |
| FR-015 | AC-019 | AC-013、NFR-008 |
| FR-016 | AC-009 | AC-018、NFR-007 |
| FR-017 至 FR-018 | AC-010 | AC-009、AC-022、NFR-001 |
| FR-019 至 FR-020 | AC-011 | AC-009、NFR-001 |
| FR-021 | AC-012 | AC-004、AC-011、AC-022、NFR-005 |
| FR-022 | AC-022 | AC-011、AC-013、AC-018 |
| FR-023 至 FR-025 | AC-015 | AC-008、NFR-003 |
| FR-026 | AC-021 | AC-015、NFR-004、NFR-007 |

追踪矩阵只证明顶层覆盖存在，不替代专项中的 Given/When/Then、负向、并发、崩溃、跨平台和迁移测试。任何专项若无法把主验收标准分解成可执行证据，必须在设计阶段返回补充本 PRD，不能用“顶层已覆盖”绕过。

## 20. 非功能需求

### NFR-001：确定性

相同冻结输入、Policy、执行合同和事实集必须产生相同选择、关闭、晋升和发布决定。

### NFR-002：可恢复

并发写、进程崩溃、网络中断、Runner 取消、用户中断和 Worktree 切换后，系统能够从权威事实重建，不依赖内存状态或人工修 JSON。

### NFR-003：精简性

每个专项新增前必须证明无法通过现有 Adapter、Store、Ledger、State Machine、Close Authority 或 OfflineOptimizationController 扩展。禁止新增第二套同义控制面。

### NFR-004：跨平台

只有平台相关行为复制到多系统；共享语义仍需在代表性平台和 Python 边界版本验证。发布制品在 Windows、macOS、Linux 均有真实安装证据。

### NFR-005：隐私与本地优先

用户代码、Diff、Finding、角色评价、成本数据和优化数据默认留在项目本地。框架 CI Artifact 只包含框架仓自身数据并遵守版本化保留策略。

### NFR-006：性能

任何新增治理能力必须记录自身 Active Execution Time、Runner、Token、Provider 估算成本和存储增长。等待用户、排队和审批不计 Active Execution，但单独记录 Wall Clock。

### NFR-007：兼容

旧项目、旧 Loop Artifact 和旧 Policy 在未显式迁移时保持可读；未知 Schema 不自动升级为通过。新版本必须提供重放、迁移或明确 `needs_user` 路径。

### NFR-008：可解释

所有自动决策必须输出使用的事实、Policy digest、满足和未满足的 Guard、候选影响范围、回滚目标和唯一下一步，但普通用户默认只看到简明结果。

## 21. 失败、停止与回滚

| 场景 | 必须动作 | 禁止动作 |
|---|---|---|
| Windows lease 仍随机失败 | 保持发布阻断并继续根因诊断 | 增加 timeout、skip、retry-to-green |
| Fast 与完整矩阵结果不一致 | 记录 Coverage Leak，恢复宽门禁 | 只保留较快的绿色结果 |
| Release 证据不完整 | 保持 Draft 或终止发布 | 先 Publish 后补证据 |
| Certificate 签发后候选/资产变化 | 使 Certificate 失效并重新验证 | 使用旧证明 Publish 新状态 |
| 发布后证明被严重证据推翻 | 生成 Revocation Receipt、停止推荐并启动 WorkItem 事故处置 | 改写历史证书或静默保留推荐 |
| Loop Slice No-Go | 保留已发布前置价值，父级保持未完成 | 扩大原 Slice 无限继续 |
| Activation 样本不足 | 保持稳定 Policy 和 Shadow | 手工改 JSON 跳 Phase |
| Outcome 未成熟 | 不进入质量分母 | 用原始样本数替代成熟结果 |
| Holdout 泄漏或统计错误预算耗尽 | 失效候选并刷新独立评价资产 | 继续反复试探同一 Holdout |
| 自动候选质量退化 | 回滚 Pointer；阻断进行中关闭和待提交内部证明；按 Impact Manifest 补偿已关闭 Session | 只修未来 Pointer 或热修当前 Session |
| Lean 合并行为漂移 | 回退合并候选 | 以模块数下降覆盖失败 |
| 数据域、policy family 或身份越界 | 隔离、拒绝消费并记录安全事件 | 静默合并 Dataset/Pointer/Holdout |

## 22. 风险与控制

| 风险 | 控制 |
|---|---|
| 顶层 PRD 再次演变成超大单 PR | 四个能力域拆成独立价值工作包；只通过已冻结接口连接 |
| 为追求速度压缩测试 | Fast 与 Merge Assurance 分权；完整发布基线不可优化 |
| 以大量重复样本提前激活 | 独立指纹、维度覆盖、成熟结果和置信联合门禁 |
| 近重复、右删失或窥视导致提前激活 | Cluster 权重、独立冻结 cutoff cohort、anytime-valid 置信和 Alpha Ledger |
| 数据驱动变成无约束自优化 | 冻结 Constitution、Automation Envelope 和不可优化字段；单写 Store；Shadow/Holdout/补偿回滚 |
| 普通用户承担框架 CI 成本 | 明确 framework scope；安装包负向 E2E |
| Framework Qualification 与项目适配混域 | domain + lineage + family 隔离；同仓双作用域负向验收 |
| 新投影成为第二真值 | 所有新增工件标记权威来源并支持重建 |
| Lean 减重成为长期重构 | Split Benefit、时间盒、行为金样和 No-Go |
| 指标被人为优化 | 同时保留速度指标与晚红、逃逸、返工和发布 Lead Time 反指标 |
| 人工无法判断调参 | 自动生成版本化候选；用户不参与日常调参 |
| 自动系统自证成功 | 候选预注册；生成、cutoff、评价、晋升和监控数据隔离；Holdout 防泄漏 |
| 状态入口替代真实价值 | 安装包纵向价值 AC 和低数据保守默认 Policy |

## 23. 下游专项拆分

本 PRD 定义四个能力域，但必须拆成以下独立价值工作包；不得把四个域或全部 FR 放入一个 WorkItem：

以下编号只标识工作包，不是执行依赖顺序；唯一执行优先级和依赖以第 16、17 节的 P0/P1/P2/P3 与 DAG 为准。

1. `emergency-publish-freeze`：FR-002 的最小无依赖护栏；
2. `windows-concurrency-root-cause`：FR-001；
3. `release-truth`：FR-002 的永久 Certificate/Publish/Revocation 闭环；
4. `dynamic-expert-status-and-value-mvp`：FR-015；
5. `activation-maturity-and-promotion`：FR-016 至 FR-022；
6. `framework-ci-static-layering`：FR-003 至 FR-006；
7. `loop-convergence-cost-governance`：FR-008 至 FR-013，继承 002 已冻结内容；
8. `loop-policy-optimization`：FR-014，仅在 A 至 E 发布后可选启动；
9. `framework-ci-adaptive-governance`：FR-007；
10. `lean-code-sustainability`：FR-023 至 FR-025；
11. `cross-language-semantic-support`：FR-026，仅作 Roadmap，需独立需求后才创建；
12. `framework-defect-truth-closure`：AC-016，先复用 WorkItem/GitHub Issue 投影，不预设新 Store。

每个专项必须提供：

- 线上事实与差距；
- 非目标和不允许新建的控制面；
- 数据模型与权威来源；
- 状态、错误、恢复和回滚；
- 正向、负向、并发、崩溃、跨平台和普通用户隔离验收；
- Shadow/Enforce/Release 迁移条件；
- 独立可发布价值、版本化 Hard Budget、最大无进展轮次和 `deferred/no_go` Sponsor Stop；
- 独立对抗评审和输入指纹。

上述 Budget 不采用全项目固定日历天数。专项根据行为复杂度和风险声明版本化执行/Runner/Token/轮次 Hard Budget；到达预算、连续无进展或净价值假设不成立时必须真实停止。停止一个工作包不得阻断已经发布的前置价值，也不得以“继续完善平台”为理由自动扩大范围。

## 24. 顶层 Definition of Done

本顶层 PRD 只有在以下条件全部满足后才能冻结：

1. 背景、现象、根因、目标、非目标和作用域无矛盾；
2. 四个能力域都明确复用现有控制面，不存在第二状态机、Ledger、Close Authority、Release Authority 或 Pointer writer；
3. 框架 CI、Framework Qualification 与普通用户本地适配以 domain/lineage/family 完全隔离；
4. 固定 14 天已被替换为独立覆盖、右删失成熟 Outcome、anytime-valid 置信、完整性和回滚联合门禁；
5. 原始与近重复样本不能刷量，cluster 不跨评价分区，Holdout 不能反复试探，足够成熟证据允许在错误预算内提前晋升；
6. Release Activation Envelope 绑定 TargetActivationDelta 与适用画像，未资格化或不匹配组合保持 Shadow；
7. Emergency Publish Freeze 无依赖先行，正式发布启用平台不可变保护，发布/撤销、测试集合等价、严格 not-applicable 和证据复用权责明确；
8. Phase 1 与 Phase 2 普通用户价值由各自正式安装包纵向路径证明，不以状态可见、代码完成或越权阻断代替；
9. P0/P1/P2/P3 的依赖、独立价值、Hard Budget 和止损出口明确；
10. 每项功能需求至少被一个验收标准覆盖；
11. 三位不同领域独立专家读取相同文件哈希并完成对抗评审；
12. 所有 P0/P1 Finding 完成整改并经过同等强度复评；
13. 文档无待补占位符、版本控制冲突标记和未解释的线上事实宣称；
14. 通过后仅授权拆分专项 spec/plan/tasks，不等于授权跳过专项评审直接开发。

## 25. 评审维度

至少三位专家必须独立覆盖以下维度，不能互相看到初轮结论：

1. AI-Coding / Agentic SDLC 架构：控制面复用、依赖 DAG、确定性、恢复、可实现性和精简性；
2. 测试与发布可靠性：质量不压缩、CI 分层、发布真值、并发不确定性、Evidence 等价和回滚；
3. 产品价值与自动治理：用户价值、普通用户隔离、自动调参、数据充分性、指标激励、优先级和范围止损。

任一专家给出 P0/P1 时，顶层 PRD 不得冻结。整改后必须重新冻结哈希，并由三位专家基于同一新快照复评。
