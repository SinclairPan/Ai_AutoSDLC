# AI-SDLC Review Kernel 删除契约

状态：已获用户确认，作为后续清理设计边界；本文件不授权恢复 WorkItem 010 或执行发布。

## 1. 产品边界

AI-SDLC 保留原有 `init`、`run`、WorkItem、五个 Loop 及必要的工件和状态迁移。围绕评审能力只保留以下三项特性。

### 1.1 本地独立提交前 Reviewer

- 输入为精确 staged diff；只有用户明确选择时才纳入其他工作树内容。
- Reviewer 使用独立进程和独立上下文，不继承 Implementation Agent 的推理历史。
- Reviewer 只读，不得修改代码、提交、push、创建 PR 或执行发布动作。
- 执行前后绑定 HEAD、index、staged diff 和 worktree；输入或仓库状态变化使结果失效。
- 输出仅为 `PASS` 或 findings，不生成证书、签名、attestation 或远端 authority。

### 1.2 代码精简性软约束

- 检查超长文件/函数、明显重复、不必要抽象、职责混杂和过度复杂实现。
- 所有精简性结论均为 Advisory，不能阻止提交、Loop close 或发布。
- 用户可直接接受突破，最多记录一句原因；不需要 waiver、exception、No-Go、receipt 或审批生命周期。
- 正确性、安全、授权或兼容缺陷应按真实缺陷报告，不得借精简性规则升级严重度。

### 1.3 五个 Loop 结果的动态专家 Review

被审结果固定为 Requirement、Design Contract、Implementation、Frontend Evidence、Local PR Review。

- 从当前结果及必要上游上下文提取领域和风险。
- 每个结果选择一个主专家；存在明确交叉风险时最多增加一个反方专家。
- 专家身份是本次审查的临时视角，必须展示选择理由，但不持久化为账号、资格或资产。
- 专家只产生 findings，不启动或修改 Loop，不拥有关闭权，不自动修复。
- findings 去重后回流原 Loop，由原状态机决定 `needs_revision` 或 `closed`。
- Local PR Review 的专家审查是终点，禁止递归评审聚合结果或 Reviewer 本身。
- 同一输入最多审查一次；输入变化后最多复审一次。

## 2. 明确删除的能力

以下能力不属于产品边界，必须撤销规范、切断入口并物理删除，不能以默认关闭、兼容层或未来扩展点保留：

- Shadow/Enforce、Phase 晋升、样本成熟度和观察窗口。
- Activation、Policy CAS、Pointer、Rollback、Revocation 和 Impact worker。
- Stage Close Authorizer、Close Authority、Binding、Lease 和第二套关闭状态机。
- Certificate、CI Certificate、Attestation、Satisfaction Proof、whole-tree seal 和 Release Proof。
- Session/Transaction/Recovery/Replay、Finding Ledger 和全局历史投影。
- Panel solver、quorum、持久专家身份、角色评级、Provider 资格和模型路由治理。
- Resource Governor、token/成本预算、grant/reservation、CostReceipt。
- Dataset、Holdout、Alpha Ledger、Offline Optimization、自动晋升和自动回滚。
- Lean Code 的阻断式 close、waiver、exception、No-Go、regression receipt 和复杂语义传播引擎。
- 与上述能力配套的 CLI、workflow、policy、schema、artifact codec、测试和文档。
- WorkItem 010 sealed-transition foundation、authority contract 及其未合并实现。

普通本地测试、标准 CI、分支保护、构建和 Release workflow 不在删除范围；AI-SDLC 只是不再自建第二套发布真实性基础设施。

## 3. 最小目标架构

新 review kernel 不得依赖旧 `stage_review` 或阻断式 `lean_code`，只允许以下最小模型：

- `ReviewInput`：Loop 类型、工件引用、必要上游上下文、精确输入摘要和风险信号。
- `ReviewFinding`：严重度、证据位置、问题说明和建议。
- `ReviewOutcome`：实际角色、findings、`pass/needs_revision` 和输入摘要。

角色选择必须是无状态纯函数。输入摘要只用于检测漂移，禁止称为 seal、proof、authority 或授权依据。

本地 pre-commit Reviewer 继续保留 exact HEAD/diff、独立 subprocess、dirty 检查和执行前后 no-mutation guard。代码精简性实现缩为轻量 `SlimmingAdvice[]`，分析失败、缺失或阈值超出均不得阻断。

## 4. 迁移顺序

最多两个 PR，不建立第三个专项。

### PR1：原子切换

1. 冻结五个 Loop 的基本状态迁移和本地 Reviewer no-mutation 合约测试。
2. 建立极小 review kernel 和五类输入 mapper。
3. 将五个 Loop close 接到新 kernel，由原 Loop writer 保持唯一状态迁移权。
4. 将 Lean Code 替换为纯 Advisory。
5. 移除 activation、certificate、attest、promotion 等公共 CLI 和 workflow 入口。

### PR2：物理清除

1. 删除旧 `stage_review`、阻断式 `lean_code_*` 及其生产引用。
2. 删除旧测试、schema、policy、artifact、workflow 和文档。
3. 明确废止要求 Shadow/Enforce、Certificate、Holdout 和自动晋升的路线图条款。
4. 删除 WorkItem 010 未合并分支所代表的实现方向；保留必要历史证据但不合并代码。
5. 加入负向架构测试，防止被删除能力重新进入生产依赖。

## 5. 规模和评审上限

- 两个 PR 为硬上限。
- 每个 PR 最多一轮对抗评审和一轮修复复审；第二轮后的非回归问题进入 backlog。
- 新增生产代码不超过 2,000 行。
- 最终 review 子系统不超过 25 个生产文件、8,000 行。
- 目标域删除/新增代码比例至少为 10:1。
- 不得新增 workflow、数据库、全局 store、pointer、CAS、lease、后台任务、网络 API 或 required check。
- 不得以兼容为由继续导入旧实现；旧命令只允许直接移除，或在一个版本内以零写入的薄提示命令返回 unsupported。

## 6. 验收条件

- 五个 Loop 各有一次真实动态角色选择和 finding 回流测试。
- Local PR Reviewer 使用独立上下文且不能修改仓库；staged 内容变化使结果失效。
- Lean finding、Lean 缺失和 Lean 分析失败均不能阻止提交或 Loop close。
- `src/` 对旧 `stage_review`、Lean close/authority、activation/certificate authority 的生产导入为零。
- CLI 帮助不再宣称 activation、attest、certificate、seal 或 promotion。
- 删除配套远端权威 workflow，且不新增替代远端权威。
- 被废止的路线图条款标记为删除，不得使用“暂缓”“默认关闭”或“以后启用”。
- 原有五个 Loop、WorkItem、本地测试、普通 CI、构建和发布能力通过回归验证。

## 7. 禁止重新引入

以下任何设计都视为超出本契约：把摘要改名为 seal，把结果文件改名为 Pointer，为角色选择增加 Shadow/Enforce，为 Provider 增加资格注册，为 findings 增加全局 Ledger/Replay，为超时增加 Lease/Resource Governor，把本地结果导出为 CI attestation，或让 GitHub required check 消费新的评审证明。
