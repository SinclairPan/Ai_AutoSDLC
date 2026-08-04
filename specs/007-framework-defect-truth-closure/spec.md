# 007 — Framework Defect Truth Closure

## 1. 文档信息

- 状态：Draft for adversarial review
- 优先级：P0-4
- 独立价值：让线上已确认缺陷只有一个可追踪 WorkItem 真值，并先修复 handoff“已写入却报失败”的真实部分提交，停止在 backlog、Issue、聊天和运行结果之间漂移。
- 依赖：003 至少已阻止发布抢跑；具体缺陷可独立修复，不等待 004–006 全部完成
- 上游需求：顶层 PRD AC-016、P0-4、工作包 `framework-defect-truth-closure`
- 输入基线：`main@067d51d146d46f6d83958384270e76072733b85d`，tree `d5851b1b2a7cbad2b5fc99807fbb160d711e44cb`
- 上游 PRD SHA256：`D24969634BF4F919578F51331F5F1C4B9CC2D7188423B2F3B51BA27FB2DB4877`
- 事实输入：`src/ai_sdlc/core/handoff.py`、`src/ai_sdlc/context/state.py`、`src/ai_sdlc/core/workitem_truth.py`、现有 framework defect backlog/guard

## 2. 冻结目标

修复一个已证实的线上 P0 缺陷：无 checkpoint 时，`handoff update` 先写 canonical 文件，随后 `load_resume_pack(strict=True)` 抛错，造成“动作已部分成功但 CLI 报失败”。同时冻结最小问题真值合同：线上缺陷、已接受缺口和 Roadmap 都绑定现有 WorkItem；其他文档/GitHub Issue 只是投影，不能独立关闭。

本专项不是“修完所有线上 bug”，也不新建缺陷管理平台。

## 3. 线上事实与差距

1. `update_handoff()` 当前先直接写 canonical/scoped Markdown，再调用 `_refresh_resume_pack_summary()`。
2. `_refresh_resume_pack_summary()` 调用 `load_resume_pack()`；后者在缺 `checkpoint.yml` 时 strict load 抛 `CheckpointLoadError`，不在当前捕获集合内。
3. 因此调用方看到失败，但 handoff 文件已经变化，重试与自动化无法判断是否安全。
4. 仓库同时存在 framework defect backlog、GitHub/聊天记录、程序 truth layers 和 WorkItem revision truth；缺少“谁拥有状态、谁只是投影”的统一最小合同。
5. 现有 `workitem_truth.py` 已能区分 `formal_freeze_only`、`branch_only_implemented`、`mainline_merged`，应扩展其投影视角而不是创建新 Backlog Store。

## 4. 范围

### 4.1 必须完成

- 使无 checkpoint 的普通 handoff update 成功写入 handoff，且不产生误导性失败；有 checkpoint 时继续同步 resume summary。
- 定义 canonical/scoped handoff 与 resume-pack 更新的一致性语义、异常边界和幂等恢复。
- 每个真实线上问题绑定一个现有 WorkItem，并区分 `defect`、`accepted_gap`、`roadmap`。
- framework defect backlog 和 GitHub Issue 只保存 `work_item_id`、同步状态/收据和只读摘要；投影关闭不能替代 WorkItem Close Authority。
- 把主线红灯、发布抢跑、coverage leak、late critical、错误复用和自动回滚失败作为可绑定事件类型，而不是新生命周期。

### 4.2 明确非目标

- 不一次性修复 003–006 或其他全部缺陷；每个缺陷仍由独立 WorkItem 实现和验收。
- 不新建 Bug DB、Backlog Store、Issue 同步服务、状态机或通用项目管理平台。
- 不让 GitHub Issue、Markdown backlog、Finding 或 CI job 拥有完成权。
- 不新增公开 CLI 命令；复用现有 `handoff`、`workitem truth-check` 和 WorkItem 生命周期。
- 不建立组织级 SLA、看板或自动派单系统。

## 5. 权威与投影边界

| 内容 | 唯一权威 | 其他表示 |
|---|---|---|
| 问题分类、Owner、状态、影响、关闭结论 | 现有 WorkItem | backlog/Issue/报告只读投影 |
| 代码是否已在主线 | Git revision + `workitem_truth` | Issue 标签不得覆盖 |
| handoff 当前内容 | canonical handoff；有活动 WorkItem 时 scoped copy同内容 | resume summary 是派生摘要 |
| CI/Release/Finding 事实 | 各自现有权威工件 | 通过 digest/event 绑定 WorkItem |

`defect/accepted_gap/roadmap` 是 WorkItem 分类，不是三个新状态机。

## 6. 功能需求

### FR-007-001：handoff 明确成功语义

无 checkpoint 时，handoff update 必须把“handoff 已写入、resume summary 不适用”作为成功返回。有 checkpoint 时，canonical、scoped copy 与 resume summary 必须来自同一渲染输入；不得出现 CLI 报失败但调用方不知道文件已变化。

### FR-007-002：handoff 有界恢复

写文件、scoped copy 或 resume summary 任一步骤失败时，结果必须明确列出已提交/未提交对象；重试同一输入应幂等。在现有 handoff 边界内复用文件锁或 generation/digest CAS 序列化 writer 并拒绝 stale writer；canonical 原子替换是唯一提交点，scoped/resume 是绑定同一 canonical generation/content digest 的派生副本。读取时只认 canonical，并忽略/修复不匹配副本；不能引入事务服务或新 Store。

### FR-007-003：单一问题身份

一个问题必须有稳定 `work_item_id` 和分类。相同根因/影响不得因出现在 CI、Release、Finding、backlog 或 GitHub Issue 而复制为多个独立完成权威。

### FR-007-004：事件绑定与关闭证据

问题 WorkItem 至少绑定触发事实 digest/URL、影响范围、Owner、修复候选、定向验证、必要完整验证和关闭结论。无法复现/归因或证据仍红时不能关闭 defect。

### FR-007-005：投影同步

保留现有 backlog 或 GitHub Issue 时，必须携带 `work_item_id` 和最后同步收据/时间。投影状态冲突时以 WorkItem 为准并报告漂移；不得双向自动争夺写权。

### FR-007-006：分类不混淆

`accepted_gap` 必须记录接受者、边界和复核条件；`roadmap` 表示未承诺当前修复；二者不计为已关闭 defect，也不能降低现有质量门禁。

## 7. 验收标准

### AC-007-001：无 checkpoint handoff

在只有 `.ai-sdlc/`、没有 `checkpoint.yml` 的仓库执行现有 handoff update：命令成功，canonical 文件内容正确，不创建伪 checkpoint/scoped copy/resume-pack，不报部分失败。

### AC-007-002：有 checkpoint 与故障注入

有活动 WorkItem 时 canonical 与 scoped 内容一致，resume summary 同源。分别在每个准备/替换点注入写失败与进程中断，并覆盖 A/B writer 交错、陈旧重试、锁持有者崩溃；读取始终返回 canonical 最新 generation，副本可修复，重试可收敛，不出现两个不同的“最新 handoff”。

### AC-007-003：truth 分类

对 formal freeze、branch-only implementation、mainline merge 三种现有 revision 场景，问题分类与 WorkItem 身份保持稳定；Issue/backlog 关闭不能把未合主线实现标成完成。

### AC-007-004：重复与漂移

同一事件被 CI、Release 和 Finding 重复上报时只关联一个 WorkItem；伪造不同 Issue、关闭投影或删除 backlog 行不会改变 WorkItem 权威状态，并产生漂移提示。

### AC-007-005：关闭门禁

defect 只有在修复进入目标 revision、定向验证通过、所需完整门禁成功并有关闭结论时才能关闭；无法复现、仅文档计划或 accepted_gap/roadmap 均不能伪装为 defect fixed。

### AC-007-006：普通用户隔离

普通用户项目不会被迫连接 GitHub Issue、上传问题数据或维护框架 defect backlog；投影治理只作用于框架自开发域。

## 8. 迁移与兼容

- 现有 backlog 条目按需补充 `work_item_id`，不批量复制成第二套记录。
- 无法立即关联的历史低风险条目标记为 `unlinked_projection` 并安排独立梳理，不阻断 handoff 缺陷修复，但不得计入 truth-closure 完成。当前已知 P0/P1 必须全部绑定 WorkItem 或形成显式 Sponsor `no_go`。
- 现有 WorkItem status/state machine 不新增同义终态。
- 现有 CLI 输出保持兼容，只补充真实成功/失败与投影漂移信息。

## 9. 复杂度与收益合同

### 9.1 允许的最小增量

- 新 Store、状态机、顶层 Controller/Service、公开 CLI：均为 0
- handoff 修复限定在现有 handoff/context state 边界及其测试
- truth 扩展限定在现有 WorkItem 模型/`workitem_truth`/backlog guard 投影边界
- 不实现通用 Issue 双向同步；最多支持现有投影写入的轻量 receipt 字段

### 9.2 可量化收益

- handoff “文件已变但命令失败且无提交说明”：0
- 一个线上问题拥有多个关闭权威：0
- branch-only/plan-only 被报告为 mainline fixed：0
- 为闭环问题新增的 backlog 平台或数据库：0

### 9.3 Stop-Loss

第一交付只包含 handoff 原子语义和最小 WorkItem truth 投影合同。若需要 Issue service、历史全量迁移或统一可视化，拆为后续需求；不得阻塞当前 P0 修复。

Hard Budget v1：handoff 与 truth projection 各最多 1 轮最小实现、2 次定向验证；handoff 最多 1 次并发/崩溃压力，truth closure 只处理执行前冻结的当前 P0/P1/accepted_gap/roadmap 清单。Runner 上限等于这些命令在 clean baseline 的实测总量乘 1.5，Agent Token 上限等于获批 plan 估算乘 1.5。连续 1 轮无新增可验证真值收益时停止；handoff 可独立发布，truth closure 未完成则明确 `deferred/no_go`，不得以历史全量迁移扩大范围。

## 10. 迁移与回退

handoff 原子修复经定向故障/并发 AC 后直接替换现有缺陷路径；投影先 Shadow 报告漂移，不改变 WorkItem 状态，冻结清单全部绑定后才 Enforce 单一真值。回退时 canonical 仍为唯一 handoff，WorkItem 仍为唯一问题权威；不得恢复 Markdown/Issue 独立关闭。

## 11. 评审范围护栏

阻断 Finding 仅限 handoff 真实性、WorkItem 单一权威、关闭证据、普通用户隔离或复杂度预算。看板、SLA、自动派单、跨项目缺陷平台属于 `out_of_scope_advisory`。

同一冻结哈希最多两轮整改复评；仍有 P0/P1 则 no-go，不扩范围。

## 12. Spec 完成出口

- 三位专家同哈希独立评审且 P0/P1=0；
- handoff 缺陷与问题真值合同可分别形成小任务并独立判定完成；当前已知 P0/P1 未绑定时不得宣称 truth closure 完成；
- spec 冻结前不创建 plan/tasks，不声称所有线上问题已修复。
