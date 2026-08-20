# PR4 保留能力重接与历史膨胀物理删除设计

## 1. 冻结身份与目标

- 基线 commit：`ffdd850de1806b75b349db9242d2597438f6ff95`
- 基线 tree：`2e6475e903b82b836cead3264de82c2cd12b2588`
- 分支：`codex/pr4-reconnect-delete`
- 逻辑 PR1、PR2、PR3 已完成；本设计只落实固定四 PR 合同中的 PR4。

本 PR 只有两个有序阶段：

1. 阶段 A：把仍有用户价值的能力从巨型 Program 历史链中抽出，并解除普通路径对
   Program、Telemetry、Provenance、AgentOps 和旧七阶段 Runner 的依赖。
2. 阶段 B：只有在保留入口真实可执行、拟删模块生产入边为零、fresh installed E2E 通过后，
   才物理删除所有不属于产品边界的实现、产物、测试和文档。

不得派生第五个治理 PR，不得先删后补。

## 2. 产品边界

### 2.1 必须保留并真实可执行

1. Requirement、Design Contract、Implementation、Frontend Evidence、Local PR Review 五个 Loop。
2. 五 Loop 的动态专家评审：每轮最多两名专家、最多一次修复复审。
3. 本地独立提交前 Reviewer、reviewed tree 精确绑定和 stale 检测。
4. 仅 advisory 的代码精简建议，不改变 verdict、Loop 状态或 Close。
5. 前端 solution confirmation、managed apply、browser evidence、visual regression、accessibility
   和 Frontend Evidence Close。
6. 普通 release、公开发行身份核验、在线/离线安装、离线包与 self-update。
7. 通用 Continuity handoff；本 PR 只删除 Program 专项 handoff 链。
8. 明确的 self-development/release profile；普通用户项目不得继承框架自研发布规则。

### 2.2 必须删除

- 历史 Program 的 validate/status/truth/plan/integrate/remediate 平行权威。
- Program handoff、provider patch/apply、cross-spec writeback、governance、proof、archive、
  final-proof 和 cleanup 链。
- 默认 Telemetry、Provenance、Trace、AgentOps CLI、配置、产物和运行时写入。
- 无正常用户入口的 studio、host-runtime 与旧七阶段 runner/executor/dispatcher/stage 表面。
- 硬编码 WorkItem 编号、专项基线、自研发布模型和不可达 frontend 模型/生成器。
- fresh init 会生成的 program-manifest、telemetry、proof、authority 或 AgentOps 噪声。
- 仅验证上述已删能力的测试、文档、模板和仓库生成物。

### 2.3 明确不建设

- 不建设替代 Program、Graph、第二套 Loop 状态机或编排平台。
- 不建设 authority、CAS、certificate、proof 中心、digest 平台或规则兼容控制中心。
- 不建设新 Telemetry、Provenance、AgentOps、ROI 平台；未来外接只能是核心外可选薄适配器。
- 不建设 side-by-side runtime、current pointer、后台更新器或新安装平台。
- 不做运行时规则学习、多人分支协调、分布式 WorkItem 编号或共享 checkpoint。
- 不把 PrimeVue、Vue2 或任何固定技术栈提升为通用企业规范。

## 3. 当前事实与根因

### 3.1 巨型 Program 仍占有保留能力

`core/program_service.py` 约 1.7 万行，`cli/program_cmd.py` 约 7 千行。保留能力仍位于隐藏的
`program` 命令中：

- `solution-confirm`
- `managed-delivery-apply`
- `browser-gate-probe`
- `browser-gate-baseline`

apply 请求仍读取 host-runtime、page UI schema、generation constraints、quality platform 和
provider runtime adapter；browser 上下文仍读取 quality-platform/provider handoff。若直接删除
Program，保留的前端链会失效；若整体改名保留，则历史膨胀没有被删除。

### 3.2 Frontend Evidence Close 已经是正确权威

`loop frontend-evidence start/status/close` 已绑定独立 review digest，并在
`.ai-sdlc/loops/frontend-evidence/<loop-id>/` 保存 input、snapshot、report 和 close truth。
因此阶段 A 不建立新状态机，只把确认、apply、browser capture 和 baseline 变成这个 Loop 下的
薄命令，最终 Close 仍由 Frontend Evidence Loop 决定。

### 3.3 普通路径仍有第二套权威和默认副作用

- init 的 safe rehearsal 仍实例化 `SDLCRunner`。
- runner 仍加载 Program 并初始化 RuntimeTelemetry。
- status/doctor/verify/workitem close-check 仍导入 Program、Telemetry 或 Provenance。
- ProjectConfig 和模板仍默认生成 AgentOps/Telemetry 字段。
- verify constraints 的旧路径仍会创建 telemetry session/evidence 等项目产物。

这意味着“隐藏命令”并不等于“退出产品”。阶段 A 必须先解除这些生产入边。

### 3.4 注销命令不足以减小发行包

wheel 会递归携带 `src/ai_sdlc/**`，sdist 还会携带更宽的源码与模板。仅从 Typer 注销命令不会
删除模块、历史 WorkItem 标识或 `src/frontend-governance` 等生成物。阶段 B 必须同时验证
wheel、sdist、由 sdist 重建的 wheel 和 offline bundle 的成员列表。

## 4. 目标状态

### 4.1 唯一前端交付链

目标链固定为：

```text
solution snapshot
  -> managed apply receipt
  -> browser / visual / a11y evidence bundle
  -> Frontend Evidence review
  -> Frontend Evidence Close
```

唯一用户入口归入现有顶层 `loop`：

- `ai-sdlc loop frontend-evidence solution-confirm`
- `ai-sdlc loop frontend-evidence apply`
- `ai-sdlc loop frontend-evidence capture`
- `ai-sdlc loop frontend-evidence baseline`
- 现有 `start`、`doctor`、`skip`、`status`、`close` 保持。

不新增 `frontend` 顶层命令，不保留 `program` 兼容入口。旧命令在阶段 B 后必须返回
`No such command`，不能继续 hidden。为使普通 CLI 启动不再 eager import 旧模块，阶段 A 的
入口切换即注销 Program、Telemetry、Provenance、Trace、AgentOps、studio、host-runtime 等退役
命令；阶段 B 再物理删除其源码。注销不是删除门禁的替代品。

### 4.2 最小 frontend delivery service

新增一个窄的 `core/frontend_delivery_service.py`，只负责编排已有最小能力：

- solution snapshot 与用户确认；
- 从项目事实和 snapshot 构造 managed apply 请求；
- 调用现有 `managed_delivery_apply.py`；
- 从 snapshot + apply receipt 构造 browser execution context；
- 调用 browser runtime 和 visual/a11y provider；
- 管理独立于 quality-platform 的 visual baseline；
- 将 evidence artifact 交给 Frontend Evidence Loop。

它不是状态机，不拥有 Close，不维护 Program manifest，不生成 provider handoff、page schema、
quality-platform 或 proof 树。具体技术栈只能来自项目事实或用户明确选择；内置 provider/profile
只是可选实现。

### 4.3 普通路径只依赖现行真值

- `run` 继续只读路由五 Loop。
- init safe rehearsal 只检查环境、项目配置与五 Loop 路由，不构造旧 Runner。
- doctor 只报告安装/环境/adapter/浏览器能力，不报告 Telemetry/Program。
- status `--details`/`--json` 保留诊断选项，但只输出项目、adapter、五 Loop、update 和必要的
  frontend delivery 事实。
- verify constraints 直接返回 report/blockers，项目执行零写入。
- workitem close-check 保留通用 task/log/branch/quality/local-review/Loop 核验，删除 Program truth、
  provenance advisory 和专项 release-gate evidence。
- 旧配置出现未知 AgentOps/Telemetry 键时宽容读取，但 fresh init 不再生成，也不进入运行决策。

不创建替代 Telemetry abstraction。少数正常模块需要的 UTC、显示或命令质量小工具移到中性小模块，
不能为了保留普通时钟而保留整个 Telemetry 包。

### 4.4 发行与 release 边界

保留：

- `packaging_backend.py`
- 在线/离线安装脚本和 offline bundle
- `self_update_cmd.py`、`update_advisor.py`
- public release identity 校验
- release build/smoke、compatibility gate、三平台 user-guide/offline smoke
- self-development constraints profile

删除旧 WorkItem release-gate 模型不等于删除发布能力。普通项目的 verify/profile 不应加载框架
发行规则；self-development profile 仍必须发现 AI-SDLC 自身发布身份错误。

## 5. 阶段 A：重接保留能力

### 5.1 先建立失败回归

RED 必须证明当前缺口，而不是事后补断言：

1. 冻结“Program 不可导入、`program` 未注册时，新 Loop 前端完整链仍必须成功”的期望；
   该测试在当前基线因新 Loop 子命令不存在或仍有 Program 入边而 RED，阶段 A 后原样 GREEN。
2. apply 仍访问 host-runtime、quality-platform、page schema、generation constraints 或 provider
   handoff 时失败。
3. Frontend Evidence Next、doctor、README、browser runner 中出现 `program ...` 时失败。
4. fresh Node/Java/Python 项目执行 init/run/status/doctor/verify 后产生 program-manifest、telemetry、
   provenance、AgentOps、proof/authority 文件时失败。
5. verify constraints 或默认诊断命令修改 HEAD、index、worktree、Loop outcome 或 checkpoint 时失败。

### 5.2 迁移前端交付能力

- 从 Program 中迁移行为，不复制 Program 模型或整段历史治理树。
- snapshot 变化必须使 apply/browser evidence stale。
- 实现 tree 或 evidence 变化必须使 Frontend Evidence review stale。
- 缺 browser、visual 或 a11y 必需证据时 Close 阻断；显式 skip 保留既有风险接受语义。
- 无已有 baseline 时，第一次 capture 只允许生成 bootstrap 并要求 recheck，不能 Close；baseline
  只建立比较基准，不得修改或升级旧 browser artifact；必须第二次 capture 对当前 baseline 比较
  成功后才允许 review/Close。
- 已有有效 baseline 时允许一次 capture 完成 compare。baseline、实现或 snapshot 变化都会使旧
  evidence/review stale。
- baseline 建立及下一轮 visual compare 都不依赖 quality-platform。
- solution-confirm 前 apply 阻断；apply 未成功前 capture 阻断。

### 5.3 解除普通路径依赖

先从 `main`/sub-app 注销退役命令和 eager import，使直接调用返回 `No such command`，但暂不删除
源码；再按 init、doctor/status、verify、workitem close-check、runner/stage 消费者顺序切断
Program、Telemetry、Provenance 入边；迁移文档、browser runner、Loop E2E、Windows/POSIX E2E
与固定 fast-gate 列表。

### 5.4 阶段 A 硬门

只有全部满足才可进入阶段 B：

- 新前端完整真链通过，并由独立 Frontend Evidence review/Close 关闭。
- 普通 init/run/status/doctor/verify/close-check 零旧产物、零 Git/Loop 状态副作用。
- 从所有保留 CLI/Loop/release 入口可达的拟删模块 AST + 文本生产入边为零；尚未物理删除的
  退役家族内部自引用不计为保留入边，且其命令已注销、不可从发行入口到达。
- fresh wheel 和 fresh sdist-built wheel E2E 均通过。
- release/self-update/offline/self-development profile 无回归。
- 两名独立 Reviewer 对同一阶段 A candidate 合议 PASS。

任一项不满足必须留在阶段 A 修复，禁止删除后再补。

## 6. 阶段 B：物理删除

删除顺序固定，逐批要求入边为零：

1. 核验阶段 A 的命令注销仍生效，并删除退役 CLI 模块及残余命令集合/导出。
2. 删除 Program service/model、program manifest template、Program handoff、provider patch/writeback、
   governance、proof/archive/final-proof 链。
3. 删除 Telemetry 全包、Telemetry/Provenance/Trace/AgentOps/enterprise CLI 和 core，清除默认配置、
   项目产物、文档与测试。
4. 删除 studio、host-runtime，以及生产入边归零的旧七阶段 runner/executor/dispatcher/stage、
   backends/parallel/stages。仍被五 Loop 或通用 verify 使用的 gate/task helper 不删。
5. 删除不可达 frontend 专项模型、生成器、baseline、根 governance/kernel/providers/managed 生成物、
   `src/frontend-governance` 和硬编码历史 WorkItem 标识。
6. 删除或重写只服务已删实现的测试、文档和 CI 固定列表；保留行为回归。

每批删除前必须证明生产引用为零；删除后立即运行 import/help/focused tests。首个无法证明零调用的
模块停止删除并回到阶段 A 接线，不允许“先删再看全量测试”。

## 7. 发行成员合同

wheel、sdist、sdist 重建 wheel、offline bundle 必须包含：

- 五 Loop 与 review kernel；
- Local PR Review 与 slimming advisory；
- 最小 frontend delivery/browser/visual/a11y 实现；
- browser runner asset；
- self-update/update advisor；
- 通用 rules、adapter 和文档模板。

必须不包含：

- Program、Telemetry、Provenance、Trace、AgentOps、studio、host-runtime；
- 生产入边归零的旧 runner/executor/dispatcher/stage/backends/parallel；
- provider patch/writeback/proof/archive/final-proof；
- 旧 frontend baseline/generator、`src/frontend-governance`、program manifest template；
- 历史 WorkItem 标识。

对 wheel 与 sdist-built wheel 分别 fresh install，并执行：

- `ai-sdlc --help` 与 `python -m ai_sdlc --help`；
- 所有保留命令的 help/import；
- `pkgutil.walk_packages` 全包 import；
- 旧命令 direct invocation exit 2；
- Node、Java、Python 企业样例；
- 前端完整链与 release/self-update/offline smoke。

## 8. 统一验收

1. 五 Loop 全部通过，Local PR Review 通过，reviewed tree 不 stale。
2. 无 baseline 时，`solution-confirm -> apply -> capture(bootstrap/recheck) -> baseline ->
   capture(compare) -> review -> Close` 真实执行通过；有有效 baseline 时允许一次 compare capture。
3. fresh init 不生成 program/telemetry/provenance/AgentOps/proof/authority 噪声。
4. 默认与 details/JSON 路径都不加载已删模块；JSON stdout 仍纯净。
5. wheel、sdist、offline bundle blacklist 为零，全包 import 为零错误。
6. macOS、Windows、Linux 与 Python 3.11–3.14 compatibility gate 通过。
7. online/offline install、上一公开版本更新提示、self-update 成功/失败恢复通过。
8. self-development profile 仍能发现 AI-SDLC 自身发布错误。
9. 发行包历史 WorkItem 标识为零。
10. 根目录和 fresh 用户项目不再依赖历史生成噪声。

## 9. 对抗规则、允许路径与停止条件

每个阶段：

- 开始前冻结 base、tree、允许路径、测试和候选 hash。
- 先写 RED，再实施最小 GREEN。
- 最多两名动态专家，只处理可真实复现的 Critical/Important。
- 最多一次聚焦修复复审；第二轮非回归新议题进入 backlog，不扩张本 PR。
- 不能因“更专业”增加治理层、替代平台或第五 PR。

阶段 A 允许修改正常入口、最小 frontend delivery/browser/visual/a11y、status/doctor/init/verify/
close-check、项目配置模板、命令文档/E2E 和对应测试。阶段 B 允许删除冻结旧家族及其专属
tests/docs/generated artifacts。安装器、packaging backend 和 release workflow 只允许做命令迁移或
发行成员断言，不得改变权限、发布身份或 self-update 协议。

出现以下任一情况立即停止并报告：

- 保留链无法真实执行或 Close 仍依赖 Program；
- 拟删模块生产入边非零；
- fresh init 仍生成旧噪声；
- wheel/sdist/offline 任一含禁用模块或 token；
- 任一 OS 的安装/import/help/五 Loop/frontend/self-update smoke 失败；
- self-development release 检查丢失；
- 修复需要新平台、第五 PR、破坏 release 或超出冻结边界。

全部验收满足即结束 PR4，不继续顺便优化或发布。
