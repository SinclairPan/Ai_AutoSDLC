# 005 — Permanent Release Truth

## 1. 文档信息

- 状态：Draft for adversarial review
- 优先级：P0-2
- 独立价值：把“精确候选已满足发布条件、GitHub 已发布、仍可推荐安装”连成可重放、防篡改、可撤销的单一证据链。
- 依赖：003 Emergency Publish Freeze 已生效；不依赖 004 完成
- 上游需求：顶层 PRD 的 FR-002、AC-002、P0-2
- 输入基线：`main@067d51d146d46f6d83958384270e76072733b85d`，tree `d5851b1b2a7cbad2b5fc99807fbb160d711e44cb`
- 上游 PRD SHA256：`D24969634BF4F919578F51331F5F1C4B9CC2D7188423B2F3B51BA27FB2DB4877`
- 复用输入：现有 StageCloseCertificate/Evidence Store/candidate binding、GitHub Release、release-build 与 release smoke

## 2. 冻结目标

受保护 Publish Job 必须一次性消费精确候选的 Release Satisfaction Proof，GitHub Release 保持发布状态唯一权威；Publish 后只有在 GitHub Release Attestation 和资产事实验证通过时才生成内部 Release Certificate。后验失效只追加 Revocation Receipt 并停止推荐，不改写历史证明。

## 3. 线上事实与差距

1. 现有 Stage Review 已有内容寻址 Candidate、证书模型、Evidence Store、唯一关闭权威和防身份 fork 习惯，可复用而无需第二证书引擎。
2. 当前 release-build 验证各平台制品并上传，但发布前授权、发布后 attestation 与推荐资格没有统一绑定。
3. post-publish smoke 能发现问题，却缺少标准化的撤销推荐与事故 WorkItem 绑定。
4. 003 只能临时阻止抢跑，不能证明发布后资产未变、证据仍有效或推荐资格应撤销。

## 4. 范围

### 4.1 必须完成

- 定义并内容寻址以下三个最小工件：Release Satisfaction Proof、Release Certificate、Release Revocation Receipt。
- Proof 绑定 repository、不可变 Draft Release ID/状态版本、tag object/commit/tree、Required Policy digest、Required Gate 集合与结论、精确 workflow run attempt、完整资产名称/摘要、不可变发布设置快照、发布工作流身份和证据截止点。
- 受保护 Publish Job 是唯一 Proof 消费者和正式 Publish 调用者。
- Certificate 绑定 GitHub Release ID/URL/状态、平台 attestation、最终资产摘要和最新 revocation generation。
- 后验失败追加 Receipt、停止推荐并绑定现有 WorkItem 事故闭环。
- 重放同一权威输入得到同一结论，身份分叉 fail-closed。

### 4.2 明确非目标

- 不创建内部 Release 状态机、Release 数据库、推荐服务或新发布平台。
- 不替代 GitHub Release/tag/checks 的外部权威。
- 不实现 CI 动态选择、Activation 晋升或普通用户项目遥测。
- 不让 Certificate 改写 GitHub 历史，也不把 Certificate 作为可变 Release asset 自我证明。
- 不新增日常人工审批；人工只处理明确的 no-go/事故授权。

## 5. 工件与权威边界

| 工件/事实 | 写入者 | 权限与限制 |
|---|---|---|
| Release Satisfaction Proof | 受保护预发布证据构建 | 只证明精确候选满足条件，不拥有 Publish 状态 |
| GitHub Release | 受保护 Publish Job/GitHub | 唯一 Published/Draft 权威 |
| Release Certificate | Publish 后验证步骤 | 只有 attestation 与最终资产验证后生成，不发布 |
| Release Revocation Receipt | 后验验证/事故闭环 | 只追加失效事实、停止推荐；不改写历史证书 |
| 事故状态 | 现有 WorkItem | 唯一内部问题生命周期权威 |

三个内部工件沿用现有内容寻址、immutable write、identity fork rejection 与 Evidence Store 约定，不创建平行 Store。

## 6. 功能需求

### FR-005-001：Proof 完整性

Proof 必须包含精确候选与所有必要证据 digest；pending/fail/cancel/skip/missing/stale、自签排除、资产缺失或摘要不一致均不能生成 Proof。删除并重建 Draft、tag/tree 漂移、run attempt 替换或不可变设置变化都使旧 Proof 失效；Publish 瞬间必须以 CAS 重验全部绑定仍成立。

### FR-005-002：单一 Publish Authority

只有受保护 Publish Job 能消费有效 Proof 并调用正式 Publish。Proof 已过期、已撤销、候选变化、调用者身份不符或并发消费冲突时必须阻断。

### FR-005-003：发布后验证与 Certificate

GitHub 显示 Published 只是 `published_unverified`。验证 GitHub Release Attestation、tag/commit、最终资产集合/摘要与 Proof 全部一致，并对最新 revocation generation 条件提交后，才可生成 Certificate 和 `trusted` 推荐资格。

### FR-005-004：不可变与幂等

启用并验证平台不可变保护后才允许正式 trusted Release。API 响应丢失、重复投递或并发恢复必须通过读取 GitHub 权威状态幂等收敛，不能重复发布或覆盖已证明资产。

### FR-005-005：后验撤销

同一合同后续红灯、Gate/等价后验失效、attestation 异常、迟到 P0/P1 或资产事实变化时，先在现有 Evidence Store 以 fenced CAS 条件追加单调 Revocation Receipt generation；该提交是唯一撤销线性化点。推荐停止、进行中授权撤销和 WorkItem/Impact 处置均由该权威事件派生，不能先持久化第二个“停止”真值。Certificate writer、推荐读取和进行中授权必须绑定同一 generation；历史 Proof/Certificate 保留原事实。

### FR-005-006：推荐查询

现有安装/发布选择只能把“GitHub Published + 有效 Certificate + 新鲜 revocation generation”解释为 trusted。专项合同在 research 阶段依据现有发布/缓存路径冻结在线最大传播延迟和签名本地缓存 TTL；超时、缓存过期或离线无法证明新鲜时只能返回非阻断 `untrusted/unknown`，不得表述为“当前 trusted”，也不得静默回退到仅看 tag。该缓存是 Receipt 投影，不是新 Store 或撤销权威。

## 7. 错误与恢复

- Proof 生成前失败：保持 Draft，不生成 Proof。
- Publish 调用不确定：重读 GitHub Release ID、tag 与资产；同候选则继续验证，冲突则阻断。
- Certificate 提交冲突：重读最新 generation；同内容返回已有证书，异内容报 identity fork。
- 发布后失效：先提交 Receipt generation，再派生停止推荐/授权和 WorkItem 事件；安全信号后 Receipt 提交前崩溃必须重放该信号，Receipt 已提交但投影未更新时重启必须从 Receipt 重建停止状态。补偿失败保持显式事故状态。
- GitHub 不支持或未启用 Immutable Releases：只能发布显式 untrusted prerelease，不能产生 trusted Certificate。

## 8. 验收标准

### AC-005-001：端到端正向

Draft 候选在完整 Required Gate、三平台资产 smoke、用户指南 E2E 和摘要一致后生成唯一 Proof；唯一 Publish Job 发布；attestation 验证后生成唯一 Certificate 并成为 trusted 推荐。

### AC-005-002：负向矩阵

逐项注入非成功 Gate、候选/资产 digest 不一致、过期证据、自签排除、缺 attestation、未启用不可变保护、非授权 writer、Draft 删除重建、run attempt 替换和发布设置变化，均不能消费旧 Proof、产生 trusted Certificate 或推荐。

### AC-005-003：并发与崩溃

并发 Publish、Proof 重放、API 成功后响应丢失、runner 崩溃、Certificate 条件提交冲突，最终只存在一个 GitHub Release 与一个同 generation Certificate；冲突身份 fail-closed。

### AC-005-004：撤销

对已 trusted Release 注入迟到 P0/P1 或后验红灯，覆盖 Receipt-first、Certificate-first、推荐读后提交前撤销三种并发顺序，以及安全信号后 Receipt 提交前崩溃、Receipt 已提交但投影未更新时崩溃、过期缓存和传播超时。最终生成绑定原证书和新 generation 的唯一 Receipt，停止推荐并关联现有 WorkItem；历史工件不被改写，离线未知不声称 trusted。

### AC-005-005：重放与篡改

从冻结输入离线重放得到相同 digest/结论；篡改任一输入、GitHub Release ID、资产或 generation 后验证失败。

### AC-005-006：普通用户隔离

普通用户只消费已发布可信制品；其项目不获得框架 GitHub token、发布工件写权、远程上传或框架 CI 状态。

## 9. 复杂度与收益合同

### 9.1 允许的最小增量

- 新权威、Release 状态机、独立 Store、顶层服务、公开 CLI：均为 0
- 允许新增的 artifact kind：严格 3 个，且复用现有证书/内容寻址基类与 Store
- Publish writer：1 个现有受保护 workflow 入口
- 推荐判断：扩展现有发布/安装选择，不创建在线推荐服务
- 不为三个工件分别创建 service/controller/repository 层

### 9.2 可量化收益

- 无有效 Proof 的正式 Publish：0
- Published 但被标记 trusted 且无有效 Certificate：0
- 已撤销仍被推荐：0
- 同一候选重复 Release 或证书身份 fork：0

### 9.3 Stop-Loss

若方案需要独立 Release 平台、数据库、消息队列、通用 PKI 或超过三个 artifact kind，必须 `split_required/no_go`；不以“未来通用性”扩展 005。

Hard Budget v1：最多 2 轮工件/并发合同实现、每轮 1 组定向重放与 1 组并发/崩溃验证，最多 2 个 Draft 精确候选端到端演练。Runner 上限等于冻结命令在 clean baseline 的实测总量乘 1.5，Agent Token 上限等于获批 plan 估算乘 1.5。连续 1 轮没有新增可归因安全证据或 trusted/revoked 闭环收益，则保留 003 并将 005 置为 `deferred/no_go`；预算不得事后上调。

## 10. 迁移与回退

先在 Shadow 中生成 Proof/Certificate/Receipt 但不授予推荐；随后 Enforce Draft→`published_unverified`→`trusted` 单写链。正式 Release 只有完整 AC 通过后启用。失败或回退时保留 003 Draft guard，并把版本降为 untrusted/prerelease；不得绕过到仅看 tag。

## 11. 评审范围护栏

阻断 Finding 只能落在发布真实性、单写权威、撤销安全、验收或复杂度预算。通用供应链平台、组织级审批、跨托管平台兼容和可视化属于 `out_of_scope_advisory`。

同一冻结哈希最多两轮整改复评；之后仍有 P0/P1 则 no-go，不扩范围。

## 12. Spec 完成出口

- 三位专家同哈希独立评审且 P0/P1=0；
- 三工件每个字段、写入者和权威来源可在 data-model 阶段逐项落表；
- 未冻结前不创建 plan/tasks。
