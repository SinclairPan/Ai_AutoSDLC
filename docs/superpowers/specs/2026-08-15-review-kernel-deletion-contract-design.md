# AI-SDLC Review Kernel 删除契约

状态：已获用户确认，作为后续清理设计边界；本文件不授权恢复 WorkItem 010 或执行发布。

## 1. 产品边界

AI-SDLC 保留原有 `init`、`run`、WorkItem、五个 Loop 及必要的工件和状态迁移。围绕评审能力只保留以下三项特性。

### 1.1 本地独立提交前 Reviewer

- 输入为精确 staged diff；只有用户明确选择时才纳入其他工作树内容。
- Reviewer 使用独立进程和独立上下文，不继承 Implementation Agent 的推理历史。
- Reviewer 只读，不得修改代码、提交、push、创建 PR 或执行发布动作。
- 结果有效性只绑定 reviewed HEAD、index 和 staged diff。执行期间另以 before/after 快照检测任何仓库写入；未被用户选入的既有 unstaged 内容不进入结果身份。
- 输出仅为 `PASS` 或 findings，不生成证书、签名、attestation 或远端 authority。

### 1.2 代码精简性软约束

- 检查超长文件/函数、明显重复、不必要抽象、职责混杂和过度复杂实现。
- 所有精简性结论均为 Advisory，不能阻止提交、Loop close 或发布。
- 用户可直接接受突破，最多记录一句原因；不需要 waiver、exception、No-Go、receipt 或审批生命周期。
- 正确性、安全、授权或兼容缺陷应按真实缺陷报告，不得借精简性规则升级严重度。

### 1.3 五个 Loop 结果的动态专家 Review

被审结果固定为 Requirement、Design Contract、Implementation、Frontend Evidence、Local PR Review。

- 从同一冻结结果及必要上游上下文提取领域和风险。
- 每个结果选择一个主专家；存在明确交叉风险时最多增加一个反方专家。角色名称、关注点和选择理由随当前内容临时生成。
- 每个专家使用独立于结果 writer 的新鲜只读上下文；不得复用 writer 的推理历史或仅更换角色标签自评。
- 专家身份只存在于本次返回值中，不持久化为账号、Profile、Registry、资格或资产。
- 专家只产生 findings，不启动或修改 Loop，不拥有关闭权，不自动修复。
- 单次返回内去重后的 findings 回流原 Loop，由原 reducer/writer 独立决定状态；Review kernel 不返回 verdict，不写关闭状态。
- 专家执行失败、超时或输出损坏必须显式返回 execution failure，由原 Loop 映射到既有 `needs_user/blocked`，不得伪装为无 finding。
- Local PR Review 的专家审查是终点，禁止递归评审聚合结果或 Reviewer 本身。
- 同一输入最多审查一次；输入变化后的复审轮次只使用既有 `LoopRun/LoopRound`，禁止新建 review session、history 或 ledger。

## 2. 明确删除的能力

以下旧 `stage_review`、阻断式 `lean_code` 和 WorkItem 010 专属能力不属于产品边界，必须撤销规范、切断入口并物理删除，不能以默认关闭、兼容层或未来扩展点保留：

- Shadow/Enforce、Phase 晋升、样本成熟度和观察窗口。
- Activation、Policy CAS、activation/authority 专属 Pointer、Rollback、Revocation 和 Impact worker。五个基础 Loop 的 current pointer、artifact ref 和既有状态迁移继续保留。
- Stage Close Authorizer、Close Authority、Binding、Lease 和第二套关闭状态机。
- 旧 stage-review/release-authority 的 Certificate、CI Certificate、Attestation、Satisfaction Proof、whole-tree seal 和 Release Proof；不得按关键词误删 Frontend Evidence 等保留能力的普通证据。
- Session/Transaction/Recovery/Replay、Finding Ledger 和全局历史投影。
- Panel solver、quorum、持久专家身份、角色评级、Provider 资格和模型路由治理。
- Resource Governor、token/成本预算、grant/reservation、CostReceipt。
- Dataset、Holdout、Alpha Ledger、Offline Optimization、自动晋升和自动回滚。
- Lean Code 的阻断式 close、waiver、exception、No-Go、regression receipt 和复杂语义传播引擎。
- 仅服务于上述废止能力的 CLI、workflow、policy、schema、artifact codec、测试和文档。共享基础设施必须先迁移或证明仍被保留能力使用。
- WorkItem 010 sealed-transition foundation、authority contract 及其未合并实现。

普通本地测试、标准 CI、分支保护、构建和 Release workflow 不在删除范围；AI-SDLC 只是不再自建第二套发布真实性基础设施。

## 3. 最小目标架构

新 review kernel 不得依赖旧 `stage_review` 或阻断式 `lean_code`，只允许以下最小返回模型：

- `ReviewInput`：Loop 类型、工件引用、必要上游上下文、精确输入摘要和风险信号。
- `ReviewFinding`：严重度、证据位置、问题说明和建议。
- `ReviewExecution`：`completed/failed`、实际临时角色和 findings；不包含 `pass/needs_revision/closed` 或任何可被复用为关闭凭证的字段。

角色选择每次只消费当前输入，不读取历史结果；禁止 score/search/learning、Provider/模型选择、反馈优化或跨轮聚合。输入摘要只在执行前后检测漂移，不落为授权工件，也不能作为 Loop close 的后续输入。

Review kernel 禁止导入 Loop writer/store，禁止写 Loop 状态或全局状态。若需要保留审查文本，只能附着在现有 `LoopRound` 的当前结果中并随该轮替换；禁止事件流、全局 finding ID、跨输入 lineage、reducer、replay 或历史查询。

本地 pre-commit Reviewer 继续保留 exact HEAD/diff、独立 subprocess、dirty 检查和执行前后 no-mutation guard。代码精简性实现缩为轻量 `SlimmingAdvice[]`，分析失败、缺失或阈值超出均不得阻断。

## 4. 迁移顺序

最多两个 PR，不建立第三个专项。

### PR1：原子切换

1. 冻结五个 Loop 的基本状态迁移和本地 Reviewer no-mutation 合约测试。
2. 建立极小 review kernel 和五类输入 mapper。
3. 在五个 Loop 的原 reducer/writer 前调用新 kernel；kernel 只同步返回 execution status 与 findings，原 Loop writer 保持唯一状态迁移权。
4. 将 Lean Code 替换为纯 Advisory。
5. 直接移除 activation、certificate、attest、promotion 等公共 CLI 和 workflow 入口，不保留兼容命令。
6. PR1 合并前要求：六个旧 `execute_stage_close` 调用归零；待删除目录的外部生产导入归零；scope/design authority 后置提交和阻断式 Lean close 归零；`verify_constraints` 不再要求 attestation/history。

### PR2：物理清除

1. 按实施计划中的冻结删除清单，删除旧 `src/ai_sdlc/core/stage_review/**`、阻断式 `lean_code_*` 及所有仅服务于它们的外部实现。
2. 删除仅服务于废止能力的测试、schema、policy、artifact、workflow 和文档；先把保留行为的回归测试迁移到新 kernel。
3. 明确废止要求 Shadow/Enforce、Certificate、Holdout 和自动晋升的路线图条款。
4. 主线不得合并、引用或继续 WorkItem 010 未合并实现；历史仅存在于 Git 历史或仓外归档，主线运行时、测试、workflow、模板和用户文档均不可达。远端分支处置不由本契约自动执行。
5. 加入负向架构测试，防止被删除能力改名进入新 kernel。
6. 删除清单必须覆盖 `src/`、`tests/`、`.github/`、`scripts/`、`rules/`、`docs/`、`templates/`、`packaging/`、`.ai-sdlc/`、wheel/sdist、fresh init 和已有项目升级；旧用户 artifact 只忽略并提示，不迁移、不回放、不兼容读取。

## 5. 交付和评审上限

- 两个 PR 为硬上限。
- 每个 PR 最多一轮对抗评审和一轮修复复审；复审后仍有 Critical、Important、回归或本契约验收违规时为 Delivery No-Go，必须缩小或重做该 PR，不得残缺合并。只有不影响验收的 Minor 可以进入 backlog。
- LOC、文件数和删除/新增比只作为本次清理报告中的 Advisory，不得写入运行时、CLI、constraint 或 required gate，也不得产生 waiver/exception。清理必须呈现明确净删除。
- 不得新增 workflow、数据库、全局 store、pointer、CAS、lease、后台任务、网络 API 或 required check。
- 不得以兼容为由继续导入旧实现；旧命令直接删除，旧 artifact 不读取、不迁移、不恢复。

## 6. 验收条件

- 五个 Loop 各有一次真实动态角色选择和 finding 回流测试。
- 动态专家必须使用独立于 writer 的新鲜只读上下文；失败/超时/损坏输出不得被解释为无 finding。
- Local PR Reviewer 使用独立上下文且不能修改仓库；staged 内容变化使结果失效。
- 保留现有 Reviewer 的 HEAD 漂移、staged diff 漂移、timeout 后变更、ignored 文件变更和 reviewer commit 等 no-mutation 测试。
- Lean finding、Lean 缺失和 Lean 分析失败均不能阻止提交或 Loop close。
- `src/` 对旧 `stage_review`、Lean close/authority、activation/certificate authority 的生产导入为零。
- Review kernel 对 Loop writer/store 的导入为零，不能写 close/global 状态；复制或序列化旧审查返回值不能推进任何 Loop。
- CLI 帮助不再宣称 activation、attest、certificate、seal 或 promotion。
- 废止命令节点不存在；删除配套远端权威 workflow，且不新增替代远端权威。
- 冻结删除清单中的路径、symbol、打包副本、脚本、policy、fixture、模板和 artifact namespace 均不可达；不能只检查旧关键词。
- 被废止的路线图条款标记为删除，不得使用“暂缓”“默认关闭”或“以后启用”。
- 原有五个 Loop、WorkItem、本地测试、普通 CI、构建和发布能力通过回归验证。

## 7. 禁止重新引入

以下任何设计都视为超出本契约：让 review 返回值携带或推进关闭状态，把摘要改名为 seal，把结果文件改名为 Pointer，为角色选择增加 score/search/learning、Shadow/Enforce 或 Provider/模型路由，为 findings 增加全局 Ledger/Replay，为超时增加 Lease/Resource Governor，把本地结果导出为 CI attestation，或让 GitHub required check 消费新的评审证明。
