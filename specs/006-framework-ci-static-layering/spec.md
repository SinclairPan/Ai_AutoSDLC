# 006 — Framework CI Static Layering

## 1. 文档信息

- 状态：Draft for adversarial review
- 优先级：P0-3
- 独立价值：保留完整质量基线，同时把普通 PR 的必选反馈从 12 格全量重复中解耦，停止小修复反复消耗数小时 runner 与 Agent token。
- 依赖：003 Emergency Publish Freeze；不依赖自适应选择器
- 上游需求：顶层 PRD 的 FR-003 至 FR-006、AC-003、AC-017、P0-3
- 输入基线：`main@067d51d146d46f6d83958384270e76072733b85d`，tree `d5851b1b2a7cbad2b5fc99807fbb160d711e44cb`
- 上游 PRD SHA256：`D24969634BF4F919578F51331F5F1C4B9CC2D7188423B2F3B51BA27FB2DB4877`
- 事实输入：`.github/workflows/compatibility-gate.yml` 当前 3 OS × 4 Python 全量矩阵及 2 个 Windows shell smoke

## 2. 冻结目标

建立静态两层门禁：普通 PR 运行固定 Fast Gate 以快速反馈；Merge Assurance、Release Candidate、nightly/手工基线保留精确完整测试集合。通过受保护 Baseline Authority 和稳定 bootstrap identity 证明集合没有被候选静默缩窄。

本专项不做动态风险选择、历史证据复用、智能分片或自动调参。

## 3. 线上事实与差距

1. 当前 Compatibility Gate 在每次 PR/main push 上执行 3 系统 × Python 3.11/3.12/3.13/3.14 的几乎全量 pytest，另有两个 Windows shell job。
2. `cancel-in-progress` 会在新推送时取消旧矩阵并从头启动；Agent 轮询不应消耗 LLM token，但 runner 已执行成本仍然存在。
3. 重 CLI、Git 子进程、并发、崩溃恢复和故障注入测试被无差别复制到每个组合，导致反馈长尾。
4. 仅把全量矩阵移走会产生“测试是否被漏跑”的新风险；当前缺少受保护集合基线、稳定成员身份和正/负 delta 证明。

## 4. 范围

### 4.1 必须完成

- 定义固定、版本化的 Fast Gate，不依赖运行时预测或 LLM 选择。
- 定义完整 Merge Assurance/Release Candidate 执行点，保留当前受支持平台与 Python 质量地板。
- 引入 Test Collection Manifest 和稳定的 bootstrap test case/execution member identity。
- 建立候选不可自签的 Baseline Authority；P0 只允许真实新增成员，以及不减少任何 cell 成员数量的一对一 rename/move。真实删除、cell 缩小、多对一合并、拆分或无法证明成员守恒的迁移必须 fail-closed，并进入 FR-007/P2 Shadow。
- 聚合器验证 collection、execution、JUnit、shard 和终态完整性；非成功或未知 fail-closed。
- 采集框架自开发的 runner minutes、端到端时延、取消成本和晚红率，指标只用于观察，不自动缩窄门禁。

### 4.2 明确非目标

- 不实现 P2 自适应测试选择、Scope Index、Evidence Reuse、动态 sharding 或 Budget 自动晋升。
- 不删除、quarantine、skip 或降低完整测试质量地板。
- 不创建 CI 平台、调度服务、遥测数据库或普通用户项目 agent。
- 不把框架 CI 采集写入发布包默认用户数据路径。
- 不以固定 15/30 分钟硬切测试；时延预算是回归信号，不是质量压缩授权。

## 5. 静态两层合同

| 层级 | 触发 | 固定责任 | 无权做的事 |
|---|---|---|---|
| Fast Gate | 普通 PR/小修复 push | 固定 lint/contract/unit/smoke 与必要 Windows 用户路径，快速发现高频错误 | 授权 Release、声称完整矩阵通过、动态删测 |
| Merge Assurance | PR Ready 的精确 merge candidate/merge queue | 作为必需合并条件，对受保护 Baseline 的完整成员做集合等价和结果聚合 | 接受缺成员、候选自签基线或合并后才补跑 |
| Release Candidate/nightly | tag 候选、nightly、手工 | 保留完整平台/Python质量基线及发布专用 E2E | 用历史绿色替代当前候选证明（P0 阶段） |

## 6. 身份与权威

### 6.1 Bootstrap Test Case Identity

身份来自版本化 collection namespace、规范化测试语义位置与参数实例，不包含 commit/tree digest。仅修改测试体或业务代码时，存活 case ID 必须稳定；namespace 升级和 rename/move 必须提供完整一一 lineage，且各 cell 成员数量守恒，不能静默重建全部 ID。合并、拆分和不守恒迁移不属于 006。

### 6.2 Bootstrap Execution Member Identity

由 case ID、OS、Python、shell/profile 等执行维度组成，不包含一次性 run ID。相同执行合同跨正常快照保持稳定。

### 6.3 Baseline Authority

唯一 Baseline 更新权威是受保护 Baseline Update Job，Collection Manifest 只是投影。首次 genesis 必须绑定冻结的受保护 main commit/tree、完整 12 格终态、独立 Gate Satisfaction 和 manifest digest。此后每次 `baseline_snapshot_binding` 必须绑定前序 Baseline digest、候选 commit/tree、collection command、版本化 namespace、稳定 execution member IDs 和独立 Gate Satisfaction，并按提交血缘 fenced CAS；旧候选晚完成、响应丢失或重试不能覆盖新 Baseline。候选只能提交单调正 delta，或不减少任何 cell 成员数量的一对一 rename/move lineage；任何真实负 delta 即使携带受保护 lineage 也必须失败。

Baseline 是集合权威，不拥有测试结果或 Release 状态。

## 7. 功能需求

### FR-006-001：固定 Fast Gate

Fast Gate 的 job/测试范围必须在受保护仓库配置中静态声明。候选变更该清单时按 CI 配置高风险处理，不能同时修改清单并用修改后的结果自证。

### FR-006-002：完整集合等价

Merge Assurance 与 Release Candidate 必须证明执行成员集合等于 Baseline 加授权正 delta 或成员守恒的一对一 rename/move；缺一成员、重复成员、shard 交集或未执行成员均失败。精确 merge candidate 的 Assurance 是必需合并条件；若 merge queue/受保护候选能力缺失或配置漂移，则保留当前 PR 完整矩阵，不激活静态分层。

### FR-006-003：严格终态

not_run、unexpected deselection/skip、cancelled、timeout、early-exit、空/损坏/部分 JUnit、artifact 缺失、未知 policy 或无终态均使聚合 Gate 失败。只有结构性不适用于目标 cell 的 `contractual_not_applicable` 可排除；合同必须绑定 test/member/cell、Policy digest、WorkItem、独立受保护审批者、创建/到期时间和补偿证据，候选不得自批。flaky、失败、timeout、资源不足、临时 quarantine、未知、过期或批量/全量排除均不能满足正式完整性。

### FR-006-004：推送去重与取消成本

同一 PR 的 superseded run 可以取消，但新 run 必须只对最新候选负责；已取消 runner 时间计入成本。未变化的绿色状态通过 GitHub API/普通轮询监控，不触发 LLM 分析。

### FR-006-005：框架域隔离

Collection/JUnit/成本指标绑定 framework domain、repository lineage、candidate 与 execution contract；Fork、复制目录、修改 remote 或普通用户项目不能读取或获得授权。

### FR-006-006：预算只告警不删测

先采集同一冻结风险定义、输入人口、窗口和质量 Floor 的配对 Baseline/Candidate，统计滚动 P50/P95、runner minutes、取消成本、晚红和发布 lead time；failed/timeout/cancelled/incomplete 按预声明删失或下界进入人口，不得删除。超过版本化预算触发治理 WorkItem 或恢复宽门禁，不得自动删除测试或降低质量 Floor。

## 8. 验收标准

### AC-006-001：普通 PR 快速反馈

普通非高风险变更只触发固定 Fast Gate；不会同时启动 12 格全量 pytest。Fast Gate 失败可在单独 job 中归因，且不宣称完整发布资格。

### AC-006-002：完整基线保留

PR Ready 的精确 merge candidate 与 Release Candidate 仍对当前受支持 OS/Python 合同运行完整集合；其成员与受保护 Baseline 完全等价。Assurance missing/stale/cancelled 或候选变化时不能合并；平台不支持受保护 merge candidate 时继续使用当前 PR 完整矩阵。

### AC-006-003：身份稳定与 delta

仅改业务代码、仅改测试体、单独新增测试时，所有存活 bootstrap ID 保持不变；delta 只含真实新增成员。验证一次性 genesis 后，注入缺前序 digest、writer spoof、旧候选乱序完成、响应丢失/重试、collection namespace/算法全量换 ID、隐式重命名、候选自生成 baseline 或无授权负 delta，均失败；即使携带受保护 lineage，真实删除、cell 缩小、多对一合并、拆分或成员不守恒也必须失败并转入 P2。

### AC-006-004：完整性故障注入

逐项注入缺测试、重复测试、shard 交集、skip、timeout、cancel、early-exit、空/损坏 JUnit、artifact 丢失、候选自批 N/A、伪造审批、过期合同、临时 quarantine 及批量/全量排除，聚合 Gate 全部 fail-closed。

### AC-006-005：晚红保护

若 Fast 绿而完整层红，记录 coverage leak，阻断发布并恢复宽门禁；不得只保留较快绿色结果。

### AC-006-006：普通用户隔离

干净安装包与用户项目不新增 framework CI workflow、remote telemetry、branch setting、Collection Manifest 写入或上传行为。

## 9. 复杂度与收益合同

### 9.1 允许的最小增量

- 新 CI 调度服务、Store、状态机、顶层 Controller、公开 CLI：均为 0
- 优先重组现有 workflows；新 workflow 文件最多 1 个且必须替换而非叠加同责任 workflow
- 允许一个轻量 collection/identity 内部模型边界；不得分裂成 selector、scheduler、telemetry service
- Fast Gate 使用静态清单；运行时选择算法与 Evidence Reuse 为 0

### 9.2 可量化收益与反指标

- 普通 PR 自动启动的完整 12 格 pytest：0
- 完整基线成员漏检：0
- 相同 contract 的绿红不一致未被记录：0
- 观察普通 PR P50/P95、runner minutes 与取消比例，同时观察晚红、逃逸、返工和发布 lead time；速度改善不能以反指标恶化换取

### 9.3 Stop-Loss

若静态分层需要动态 selector、通用遥测平台或重写 CI 编排，立即拆到 P2 并 `no_go`；006 只交付静态层、身份、基线和完整性。

Hard Budget v1：最多 2 轮实现，先 1 轮 Shadow 采集再 1 轮 Enforce 候选；每轮最多 2 次定向 collection/aggregate 验证和 1 个精确 merge candidate 完整 Assurance。Runner 上限等于冻结命令在配对 clean baseline 的实测总量乘 1.5，Agent Token 上限等于获批 plan 估算乘 1.5。research 必须在看见候选结果前冻结最低 runner/反馈改善和 late-red/返工非回归门槛；连续 1 轮无净收益、人口不可比或复杂度超过本节上限时恢复当前完整 PR 门禁并 `deferred/no_go`，预算不得事后上调。

## 10. 迁移与回退

Shadow 阶段在现有完整 PR 矩阵旁生成 bootstrap identity、genesis/Manifest 和集合等价结果，不改变合并权。只有 Shadow 等价、唯一 writer/CAS 和负向 AC 全通过后，才启用 Fast PR + 必需 Merge Assurance。Release Candidate 始终完整验证。任何 coverage leak、配置漂移或净收益不成立都恢复现有完整 PR 门禁，不删除测试。

## 11. 评审范围护栏

阻断 Finding 仅限质量地板、集合证明、身份稳定、框架/用户隔离、独立收益或复杂度预算。预测模型、自适应 sharding、跨仓 CI 平台和精细仪表盘属于 `out_of_scope_advisory`。

同一冻结哈希最多两轮整改复评；不通过则 no-go，不扩大专项。

## 12. Spec 完成出口

- 三位专家同哈希独立评审且 P0/P1=0；
- Fast/完整层、Baseline Authority 与 identity 可在 data-model/contracts 中独立落地；
- spec 冻结前不创建 plan/tasks。
