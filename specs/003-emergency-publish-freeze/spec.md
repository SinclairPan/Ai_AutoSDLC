# 003 — Emergency Publish Freeze

## 1. 文档信息

- 状态：Draft for adversarial review
- 优先级：P0-0
- 独立价值：在永久 Release Truth 尚未完成前，先消除“Required Gate 未成功仍可正式发布”的安全缺口。
- 上游需求：`docs/ai-sdlc-next-stage-trusted-delivery-and-value-activation-prd.zh-CN.md` 的 FR-002、AC-002、P0-0
- 输入基线：`main@067d51d146d46f6d83958384270e76072733b85d`，tree `d5851b1b2a7cbad2b5fc99807fbb160d711e44cb`
- 上游 PRD SHA256：`D24969634BF4F919578F51331F5F1C4B9CC2D7188423B2F3B51BA27FB2DB4877`
- 事实输入：`.github/workflows/release-build.yml`、`.github/workflows/release-artifact-smoke.yml`

## 2. 冻结目标

正式发布动作必须只消费一个精确 tag/commit 的成功门禁事实。任一必需门禁为 pending、failed、cancelled、skipped、missing、stale 或无法绑定精确候选时，Release 只能保持 Draft；不得先 Publish 再补证据。

本专项只提供紧急止血，不实现永久 Certificate、Revocation 或自适应 CI。

## 3. 线上事实与差距

1. `release-build.yml` 可由 `workflow_dispatch` 触发，并可向已存在的 GitHub Release 使用 `gh release upload --clobber` 上传资产。
2. `release-artifact-smoke.yml` 的自动触发点包含 `release: published`，因此它能发现发布后的问题，却不能阻止抢跑。
3. 当前发布入口没有在正式 Publish 前，以同一个 tag/commit 身份验证全部 Required Gate 已成功的最小硬护栏。
4. 这使“构建成功”“资产已上传”“Release 已正式发布”和“候选已满足全部发布条件”可能成为不同事实。

## 4. 范围

### 4.1 必须完成

- 在现有发布链路上增加一个不可绕过的正式发布前置判断。
- 冻结并校验候选身份：repository、tag、tag target commit、workflow run、asset set。
- 冻结最小 Required Gate 清单及可信来源；清单只能来自受保护主线配置，不能由发布候选自签或缩窄。
- Required Gate 全部成功前只允许创建或更新 Draft Release。
- 冻结正式 Publish writer/credential 拓扑；只有受保护入口可以执行 Draft→Published，其他 UI、API、workflow 或凭据路径必须被平台权限禁止或验证为失败。
- 重复投递、响应丢失或 runner 重启后，重读 GitHub 权威状态并幂等收敛。
- 保存足够的只读失败原因，使操作者能知道哪一项阻断发布。

### 4.2 明确非目标

- 不签发永久 Release Certificate、Revocation Receipt 或 Activation Envelope。
- 不创建内部 Release 状态机、数据库、队列、控制器或发布服务。
- 不修改普通用户项目的安装、运行、遥测或分支配置。
- 不重构三平台构建，不优化测试选择，不解决 Windows 租约根因。
- 不新增“强制发布”“忽略门禁”或候选自声明豁免入口。

## 5. 权威与复用边界

| 事实 | 唯一权威 | 本专项行为 |
|---|---|---|
| tag 与 target commit | Git/GitHub | 读取并绑定，不复制状态 |
| Required Gate 结果 | GitHub Checks/Actions | 针对精确 commit 读取终态 |
| Release 是否 Draft/Published | GitHub Release | 只通过现有发布入口迁移 |
| 构建资产与摘要 | 现有 release-build 输出 | 复用，不新建资产仓库 |

本专项的判断结果是发布入口的一次性 guard，不成为第二 Release 真值。

## 6. 功能需求

### FR-003-001：精确候选绑定

发布入口必须拒绝轻量 tag 漂移、tag target 与构建 commit 不同、跨仓库证据、旧 workflow run 或资产摘要不一致。校验开始后候选发生变化时，本次授权立即失效。

### FR-003-002：失败关闭的 Required Gate

只有冻结清单中的每个门禁都对精确 commit 返回 `success` 才能继续正式 Publish。每个 Gate 身份至少绑定 repository、受保护 workflow path/blob、trusted ref/event、run ID/attempt、check-suite/check-run 和 writer app；同名但来源不符的 success 不可信。所有非成功、未知或证据不完整状态统一阻断，不允许把 skipped/cancelled 或被更新 attempt 取代的旧 success 解释为成功。

### FR-003-003：Draft-only 止血

门禁不满足时，发布流程可以构建、验证并向 Draft Release 准备资产，但任何资产写入前必须重读 Release 状态；只有 Draft 可新增或替换资产。Published 或未知状态下，资产上传、覆盖、删除和正式 Publish 调用均为零，也不得把该版本标记为推荐安装版本。

### FR-003-004：幂等恢复

Publish API 成功但响应丢失、重复 workflow dispatch 或 runner 崩溃后，恢复逻辑必须先读取 GitHub Release 与 tag 当前事实；若已对同一候选发布则返回同一成功结论，若身份不一致则阻断，不重复创建或静默覆盖正式资产。

### FR-003-005：可解释阻断

失败输出至少包含候选 commit、阻断 gate 名称、观察状态和安全下一步；不得输出 token、凭据或用户项目数据。

## 7. 状态、错误与恢复

本专项不持久化新状态机，只允许以下外部可观察结果：

- `draft_ready`：候选与资产已准备，但尚未满足正式发布条件；
- `publish_allowed`：精确候选的冻结门禁全部成功；
- `publish_blocked`：任一校验失败或未知；
- `already_published_same_candidate`：重放后确认同一候选已发布。

这些是运行结果，不是新的 Release 生命周期权威。任何异常默认映射为 `publish_blocked`。

## 8. 验收标准

### AC-003-001：正向发布

对同一 tag/commit，全部冻结 Required Gate 成功且资产摘要一致时，发布入口允许一次正式 Publish；重复执行得到相同 GitHub Release，不产生重复版本。

### AC-003-002：负向门禁

分别注入 pending、failed、cancelled、skipped、missing、stale、无终态、候选自签清单、同名错误 workflow/app 以及旧 success attempt 被新 failed attempt 取代，Release 均保持 Draft，资产变更和正式 Publish 调用次数均为零。

### AC-003-003：身份与 TOCTOU

在校验前后移动 tag、替换 target commit、复用另一仓库 run、替换资产或摘要时，授权失效且不得发布。

### AC-003-004：崩溃恢复

注入 Publish API 成功后响应丢失、进程崩溃和重复投递，恢复过程重读 GitHub 权威事实并收敛为单一结果；身份冲突时 fail-closed。

### AC-003-005：写入口与已发布资产

分别尝试由非授权 workflow、API/UI 身份和替代凭据执行 Draft→Published，均被平台权限或 guard 拒绝。以已 Published Release 为上传目标，或在检查后并发把 Draft 改为 Published 时，`--clobber`、上传、覆盖和删除调用均为零。若 writer 权限无法隔离，正式发布保持 `no_go`。

### AC-003-006：普通用户隔离

安装发布包并在干净用户项目执行 `init`/正常流程时，不出现框架自开发 Gate、GitHub token、远程上传或新本地状态目录。

### AC-003-007：独立发布价值

无需等待 004、005 或 006，即可证明 Required Gate 非 success 时无法正式发布；005 上线后可以替换本 guard 的临时判断而不改变其安全语义。

## 9. 复杂度与收益合同

### 9.1 允许的最小增量

- 新权威：0
- 新持久化 Store：0
- 新状态机：0
- 新顶层 Controller/Service：0
- 新公开 CLI 命令：0
- 新 GitHub Workflow：0；优先修改现有发布入口
- 生产 Python 模块：默认 0；若 workflow 无法可靠复用校验逻辑，最多允许 1 个内部纯校验 helper，且不得持久化状态

### 9.2 可量化收益

- Required Gate 非 success 时正式发布成功数：0
- 同一候选重复投递产生的重复 Release：0
- 为获得止血而新增的长期控制面：0

### 9.3 Stop-Loss

若实现需要新发布平台、数据库、队列、公共命令，或把永久 Certificate/Revocation 带入本专项，立即返回 `split_required/no_go`，转交 005；不得扩大 003。

Hard Budget v1 在 plan 执行前按“最多 2 轮实现假设、4 次定向验证、2 个精确候选 Draft 发布演练”冻结；Runner 上限等于这些冻结命令在 clean baseline 的实测总量乘 1.5，Agent Token 上限等于获批 plan 估算乘 1.5。连续 1 轮没有新增可归因证据或净安全收益即停止，Release 保持 Draft 并进入 `deferred/no_go`；预算不得在看到结果后上调。

## 10. 迁移与回退

本紧急安全护栏不设 Shadow 授权期：先以 Draft-only 验证写入口和负向合同，再直接 Enforce。005 接管后，003 的临时判断被永久 Proof 消费替换；回退只能恢复为“保持 Draft”，不能恢复无 guard 发布。

## 11. 评审范围护栏

阻断 Finding 只能证明本冻结目标、权威边界、验收、质量底线或复杂度预算存在 P0/P1 缺口。新增仪表盘、通用发布平台、未来 provider、额外状态模型等建议一律记录为 `out_of_scope_advisory`，不得阻断本专项。

同一冻结哈希最多进行两轮整改复评；两轮后仍有真实 P0/P1，则保持 `no_go/needs_user`，不得靠扩展范围消化。

## 12. Spec 完成出口

- 同一文件哈希经 AI-Coding 架构、业务治理/测试风险、产品价值/运营三位专家独立评审；
- P0/P1 Finding 为 0；
- 需求到 AC 可追踪；
- 仅在本 spec 冻结后才允许创建 research/data-model/plan/tasks。
