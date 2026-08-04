# 004 — Windows Activation Lease Root Cause

## 1. 文档信息

- 状态：Draft for adversarial review
- 优先级：P0-1
- 独立价值：根除 Windows 上 activation safety 读租约/写 fence 的并发长尾和偶发失败，不以增加 timeout、skip 或 retry-to-green 掩盖问题。
- 上游需求：顶层 PRD 的 FR-001、AC-001、P0-1
- 输入基线：`main@067d51d146d46f6d83958384270e76072733b85d`，tree `d5851b1b2a7cbad2b5fc99807fbb160d711e44cb`
- 上游 PRD SHA256：`D24969634BF4F919578F51331F5F1C4B9CC2D7188423B2F3B51BA27FB2DB4877`
- 事实输入：`src/ai_sdlc/core/stage_review/activation_fence.py`、`tests/unit/stage_review/test_activation_safety.py`、`tests/integration/test_stage_review_shadow_planning.py`

## 2. 冻结目标

在修复前冻结可重复、可归因的 Windows 失败 Profile；修复现有 `activation_safety_read_lease` 与 `activation_safety_mutation_fence` 的真实并发/文件共享根因，使相同合同在支持平台上结果一致，并通过定向压力、真实长链及精确候选完整验证。

“Activation Lease”是工作项名称，不授权创建新的 `ActivationLease` 类型或并行租约系统。

## 3. 线上事实与差距

1. 当前实现已拥有跨进程 reader marker、writer intent、owner lock、进程/线程身份和 `_unlink_with_retry`。
2. 已有单元测试模拟 writer 扫描 reader marker 时 Windows sharing violation，并验证一次瞬态 unlink 失败可恢复。
3. 已有集成测试验证 phase-one writer 的 read lease 阻止 phase-two promotion，以及不受信 ambient lease 不能绕过最终 refresh。
4. 现有证据覆盖若干已知交错，但长时间 Windows 全量任务仍出现偶发长尾/失败；仅继续追加相同重试无法证明根因已消失。
5. 缺少修复前统一的复现 Profile、事件时间线、失败分类和“无法复现不得关闭”的出口。

## 4. 范围

### 4.1 必须完成

- 冻结最小复现 Profile：Windows 版本、Python 版本、文件系统、进程/线程拓扑、reader/writer 数量、交错点、超时、失败签名，以及等待时长 P50/P95/P99/max 与删失口径。
- 记录不含敏感信息的事件时间线，以区分 live owner、stale owner、共享冲突、身份不可得、marker 清理延迟和真正死锁。
- 在现有 fence 内修复已证实根因，保持同一租约权威与 fail-closed 语义。
- 用修复前 Profile 做可重复的定向压力、崩溃恢复、长链与完整候选验证。
- 相同执行合同绿红不一致时保持发布/复用阻断，并形成可归因事件。

### 4.2 明确非目标

- 不新建租约 Store、Lock Service、Scheduler、后台 daemon 或分布式协调平台。
- 不通过放大 60/300 秒 deadline、无限重试、skip、xfail、降并发或删除测试宣告修复。
- 不改变 Activation Policy、Phase 晋升、Finding Ledger 或 Close Authority。
- 不把全部 Stage Review 性能优化塞入本专项。
- 不要求普通用户上传锁事件或启用远程遥测。

## 5. 权威与复用边界

| 能力 | 现有权威 | 本专项约束 |
|---|---|---|
| 读租约/写 fence | `activation_fence.py` | 原地修复，不平行实现 |
| marker 与 owner 身份 | canonical shared state | 不复制到新 Store |
| Policy/Phase | ActivationPolicyStore | 不修改写权 |
| 发布阻断 | 003/005 | 只提供一致性结果，不发布 |

## 6. 功能需求

### FR-004-001：修复前证据冻结

任何代码修复前必须保存 Profile、失败签名、最小交错和基线失败频率/运行时间。无法稳定复现时，必须保留 `investigating`，不能将偶然绿色视为关闭。

### FR-004-002：根因分类

诊断必须明确失败属于原子性、锁顺序、owner 活性判断、PID/线程身份复用、Windows 文件共享、marker 生命周期、异常清理或资源饥饿中的哪一类；“Windows 慢”不是根因。

### FR-004-003：安全语义不退化

修复后仍必须满足：writer intent 建立后不接纳新 reader；writer 在现存 reader 退出前不能进入；read-to-write upgrade 被拒绝；异常退出不会把 live owner 误清理；stale owner 最终可回收。

### FR-004-004：有界恢复

瞬态 sharing violation 允许使用现有有界清理机制恢复；达到既定预算仍不能确认安全时必须失败并输出最后可信状态，不得 retry-to-green。

### FR-004-005：同合同确定性

冻结 Profile 的输入、版本和调度种子相同时，支持的 Windows/Python 组合必须稳定产生相同安全结论；Linux/macOS 不得因 Windows 修复出现回归。

## 7. 验收标准

### AC-004-001：定向压力

在干净 Windows runner 上，对冻结 Profile 覆盖多 reader/单 writer、reader 释放与 writer 扫描交错、stale/live owner、PID/线程身份复用和嵌套 fence；达到预声明迭代数且零死锁、零越权进入、零身份 fork。

迭代数在 research 阶段依据基线失败概率冻结，不得在看到结果后调低。

### AC-004-002：故障注入

注入 marker 创建/读取/删除的 PermissionError、共享冲突、owner lock 清理失败、Runner Cancel、持有 owner 的进程被强制终止、进程崩溃和响应丢失；残留 marker 必须可从权威身份重建或在预算内 fail-closed，并保留可归因输出。

### AC-004-003：长链

在真实 Stage Review phase-one/phase-two promotion 与 final refresh 链路重复冻结场景，writer 不穿透 reader，untrusted ambient lease 不获得提交权。修复前 Profile 必须预注册成功门槛：failure/timeout 不得从人口删除，目标场景错误为零，P95/P99/max 不劣于冻结基线且达到 research 阶段在看见修复结果前冻结的最低改善门槛；仅“解释了长尾”不能关闭缺陷。

### AC-004-004：完整候选复核

定向用例通过后，仅对精确候选运行一次发布级完整 Merge Assurance；结果必须与定向合同一致。若不一致，缺陷保持开启并记录 coverage leak。

### AC-004-005：反规避

仅增加 timeout/retry、降低并发、删除/skip/xfail 用例或把异常吞掉时，验收必须失败。

### AC-004-006：普通用户隔离

修复不新增远程遥测、常驻进程或项目配置；正常无竞争路径不产生新的用户操作步骤。

## 8. 复杂度与收益合同

### 8.1 允许的最小增量

- 新权威、Store、状态机、顶层 Controller、公开 CLI：均为 0
- 生产修改限定在现有 activation fence/artifact 锁辅助边界；新增并行 lease 类：0
- 诊断事件优先复用现有日志/测试证据；不得引入 telemetry pipeline
- 测试只增加能区分根因的新交错，禁止复制同一语义到 12 个组合

### 8.2 可量化收益

- 冻结 Profile 的越权 writer、死锁和未归因失败：0
- Windows 定向场景结果不一致：0
- 为获得绿色而增加的 timeout、skip、xfail：0
- 相比重复全量矩阵，根因反馈由定向测试给出并可独立归因

### 8.3 Stop-Loss

Hard Budget v1：最多 2 种根因假设、每种 1 轮最小修复、每轮 1 组定向压力和 1 组故障注入，最后仅 1 个精确候选完整 Merge Assurance。Runner 上限等于上述冻结 Profile 在 clean Windows baseline 的实测总量乘 1.5，Agent Token 上限等于获批 plan 估算乘 1.5。连续 1 轮没有提高复现解释率、失败率或预注册尾延迟收益，则 `needs_user/no_go` 并保留发布阻断；预算不得事后上调，不得演变为锁框架重写。

## 9. 迁移与回退

先以只读诊断/故障注入 Shadow 冻结 Profile，不改变现有租约决定；定向合同通过后才替换现有 fence 行为。正式 Release 资格必须经过精确候选完整复核。任一回归恢复旧安全阻断并继续调查，不能通过 timeout/skip 回退。

## 10. 评审范围护栏

阻断 Finding 仅限并发安全、根因可证、验收完整性、平台回归或复杂度预算。通用分布式锁、跨机器协调、监控平台和性能重构属于 `out_of_scope_advisory`。

同一冻结哈希最多两轮整改复评；未关闭 P0/P1 时保持 no-go，不扩大目标。

## 11. Spec 完成出口

- 三位专家对同一哈希独立评审且 P0/P1=0；
- Profile、反规避条件、独立价值和 Stop-Loss 可直接转为 research/plan 输入；
- spec 冻结前不创建实现 tasks。
