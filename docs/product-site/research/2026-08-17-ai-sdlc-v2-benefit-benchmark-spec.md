# AI-SDLC 2.0 工程效益对照评估规范

## 1. 目标

本评估只回答三组预先冻结的问题：

1. 在相同模型、任务、工具和预算下，使用 AI-SDLC Loop 相比不调用 Loop，是否改变可验证交付质量、人工介入、完成时间和证据完整度。
2. 在相同 AI-SDLC Loop 下，Bounded Dynamic Expert Review Graph 相比不启动专家，是否改变真实问题发现、严重问题逃逸、修复闭环和评审成本。
3. 在相同阶段级工程任务下，纯 Coding Agent、Superpowers 6.3.0 单 Agent repo-skill 适配路径与 AI-SDLC 2.0.0 受测 Loop+专家路径，分别产生怎样的交付质量、时间和计算成本；本矩阵没有真人操作，不回答真实人工提效。

本评估是三个冻结工程阶段的对照，不是 15 条完整 SDLC 生命周期，也不是生产环境统计。公开结论必须写成“在本组冻结合成工程场景中”的原始数量、中位数和差值；不得外推完整五类 Loop、完整 Superpowers 多 Agent SDD、Local PR Review 或生产提效。

## 2. 冻结身份

- AI-SDLC Release：`v2.0.0`
- AI-SDLC peeled commit：`737bda39e05c53450e180a20581b7b7a70db9cf0`
- AI-SDLC source tree：`3db58121e228a7a1c4c6b760c535d6df1ffdbe84`
- Superpowers Release：`v6.3.0`
- Superpowers peeled commit：`b36e0829c6d0140e93cfef2ca599b1b07d4a7797`
- Codex CLI：`0.147.0`；入口脚本 SHA256 `134063e133f0b4244fa3b251acf973d4fe4b4aeeacbdc135211bf480f59f1477`，Task 4 还需记录真实入口、解析目标和 package identity。
- 模型：`gpt-5.6-sol`，requested `model_reasoning_effort=high`；所有 arm 相同，写入 protocol lock 后不得改变。若运行时无法取得该模型，整个矩阵 NO-GO，不得换模型续跑。

Codex repo-scoped skills 与分层 `AGENTS.override.md` 的可用性依据 OpenAI 官方文档：<https://developers.openai.com/codex/skills>、<https://developers.openai.com/codex/guides/agents-md>。实际 prompt/instruction inventory 仍由本地 0.147.0 预检复核，网页不得把文档能力当作本次运行事实。

## 3. 五个实验 Arm

| ID | 配置 | 解释边界 |
|---|---|---|
| `P` | 纯 Coding Agent；加载与其他 arm 相同的 Codex base/global 能力和公共任务规则，但不加载 Superpowers 或 AI-SDLC 方法指令 | 正常 Agent 基线，不故意限制规划、测试和工具使用 |
| `S` | 与 `P` 相同，再加载冻结的 Superpowers 6.3.0 repo-scoped namespace adaptation；固定 `multi_agent=false`，按其单 Agent task-scaled 工作流执行 | 只机械改写插件命名空间到 Codex repo-skill 调用名，公开 diff；不代表原插件安装或完整多 Agent SDD |
| `A00` | 安装并初始化 AI-SDLC 2.0.0，但由评估框架强制跳过本场景 Loop 与动态专家 | `harness-enforced ablation`，不是公开产品模式或官方开关 |
| `A10` | 安装并初始化 AI-SDLC 2.0.0；由评估框架要求使用本场景 canonical Loop、证据与 Close，但不派发动态专家 | `harness-enforced ablation`，不是公开产品模式或官方开关 |
| `A11` | AI-SDLC 2.0.0 managed adapter 路径；使用同一 canonical Loop，并由外部 runner 按 adapter 约束派发 Primary Expert，仅在明确第二风险面时增加 Cross-risk Expert | 公开产品机制组合；最多两名只读专家，同一结果最多一次修复后复审 |

预注册比较只有：

- Loop 增量：`A10 - A00`
- 专家增量：`A11 - A10`
- 方法包比较：`P / S / A11`

三个 A arm 的项目根 `AGENTS.md` 必须保持 v2.0.0 stock Codex adapter 原始字节。`A00` 与 `A10` 只在 benchmark task 子目录加载一份更近层级的、哈希化 `AGENTS.override.md`；A00 allowlist 仅允许新增“本场景不调用 Loop/专家”，A10 仅允许新增“保留单一 Loop、runner 不派发专家”，任何其他行都使预检失败。每份结果显示 `harness-enforced ablation`。v2.0.0 没有公开的 Loop/expert feature flag，材料不得把 overlay 说成 adapter 删减产物、产品模式或官方开关。

五个 arm 的 Provider 都从相同相对路径 `benchmark-task/` 启动，writer 命令显式使用 `-C <run-root>/benchmark-task`；A00/A10 resolved instruction chain 必须是 stock root `AGENTS.md` + nested `AGENTS.override.md`，A11 只能是 stock root，P/S 不得出现 AI-SDLC chain。`provider_cwd` 的相对路径与 tree digest 写入 receipt；从 repo root 启动、漏传 `-C` 或 chain 不符都在预检 fail closed。

v2.0.0 的 `loop review` 负责冻结父结果和专家输入 digest，但 CLI 不会自动调用模型专家，也不会持久化完整专家输出。A11 runner 因此必须按 stock adapter 的角色、只读、父子隔离和回流约束派发会话，并持久化 `role / reason / parent_digest / child_session / finding_digest / repair_digest / rereview_digest`；Loop Close 只能证明治理状态，不能代替专家已执行的证据。

两位专家出现互斥修复建议或相同风险的不可兼容结论时，runner 只记录冲突，不让 writer 自行覆盖，也不调用自动批准服务冒充裁决；该 run 终态为 `needs_operator`、保持未 Close，并继续由外部 evaluator 评分当前 Candidate。正式矩阵不向用户临时提问，也不替换该 run。

## 4. 三个冻结场景

### 4.1 `requirement-contract-ambiguity`

目标是从五臂共同可见的同一份已冻结 Requirement、项目摘要和意图服务，生成可外部评分的 Design Contract；只评分最终设计、显式阻断与未解决风险，不评分上游 Requirement 生成。

- 公开输入：业务描述、同一已冻结 Requirement、已有项目摘要、允许提问规则。
- 冻结意图/批准服务：同一 Provider 会话可调用本地只读命令按 `question_id` 获得冻结答案，也可提交 `approval_request(run_id, approval_type, proposal_digest)`；所有 arm 获得同一 taxonomy、校验政策和响应。调用分别记录为 `intent_service_event` / `approval_service_event`，不启动第二次 Provider 会话，也不计为人工事件。自由文本问题无法确定性映射时返回同一 `unresolved`；批准只接受冻结类型、当前 run 和实际 proposal digest，响应/延迟可复算，不得由人工临场发挥。
- 可见检查：文档格式、允许路径、命令可执行性。
- 密封评分：加权 AC、关键边界、矛盾和不可验证陈述。
- AI-SDLC 处理范围：上游 Requirement 以同一冻结产物提供；本次只比较 Design Contract Loop。A11 只对最终待关闭的设计结果启动对应有界专家，不创造第二状态机。

### 4.2 `frontend-recovery-delivery`

目标是修复 Vue3 发布风险工作台中 API 失败被静默吞掉的问题，并完成测试、构建和真实浏览器验收。

- 公开输入：从旧 T1 fixture 重新冻结的业务请求、六项 AC、初始 Vue 项目、保护测试、可见命令，以及五臂共同可见的冻结前端方案目标。A arms 仍须在原 writer 会话内执行真实 `solution-confirm` 预览→proposal-digest 批准服务→execute，耗时/工件计入治理成本；该过程不得向 A arms 增加 P/S 看不到的语义。
- 可见检查：固定 RED、同源 GREEN、lint、build、format、真实浏览器正常态与失败恢复态。
- 密封评分：AC 覆盖、错误恢复、重复提交保护、主题唯一入口、console、基础可访问性和禁止越界修改；新增未进入旧历史的连续失败→恢复、延迟响应竞态、快速双击和异常响应变体。
- AI-SDLC 处理范围：已冻结上游 Requirement / Design Contract；本次只比较 Implementation Loop。浏览器正常态与失败恢复态由所有 arm 共用的外部 evaluator 验收，不作为第二个 Loop 或额外 Provider 调用。

### 4.3 `multi-tenant-security-review`

目标是修复一个弱测试可通过但存在跨租户越权、错误处理与策略边界缺陷的 Python 候选。

- 公开输入：从旧 T2 fixture 重新冻结的 Candidate、弱测试、需求与设计约束。
- 可见检查：原有弱测试和静态检查。
- 密封评分：六个独立 root cause、Oracle、严重度、误报与残余逃逸。
- AI-SDLC 处理范围：已冻结上游合同；本次只比较 Implementation Loop。该场景明确包含安全和契约两个风险面，因此 A11 允许 Primary + Cross-risk；不调用 Local PR Review，其他场景不得为了数量强行启动 Cross-risk。

旧 fixture 只允许复用输入和 evaluator 思路；禁止复用旧结果、旧比赛 wrapper、固定四角色 prompt、quorum、veto、certificate 或 v1.0.5 receipt。

重新冻结前必须移除旧 T1 的方法泄漏：`initial-task.md` 不得直接说出遗漏的 AC，初始源码注释不得点名缺陷或预期修复，AI-SDLC 前端规范只有在五个 arm 都能看到时才可计分。旧 T2 必须移除“四角色”、majority、veto 和 operator 流程文字。旧 Gold/Oracle 已进入历史，正式 evaluator 还要增加未进入任何 Provider 可见 Git 对象的新 held-out 变体。

每个 public fixture 都包含一份 method-neutral `input-contract.json`。零 Provider 预处理器从它确定性生成 AI-SDLC 所需 canonical 前置态：fixture 1 生成已冻结 Requirement，fixtures 2–3 生成已冻结 Requirement 和已关闭 Design Contract；`P/S` 直接读取同一 `input-contract.json` 及等价 Markdown，`A00/A10/A11` 额外获得只含同一语义的 canonical state。normalized semantic parity test 必须证明 A arms 没有多得到 AC、风险、答案或实现提示，且本次只启动一个目标阶段 Loop。

运行前的公开仓只保存 sealed plaintext 的 SHA256 commitment、rubric schema 和通用 evaluator engine，不保存 Gold、Oracle、答案映射或 held-out plaintext。密封内容位于仓外 evaluator root；Provider 进程由 OS 级 deny-read profile 包裹，该 profile 同时拒绝 evaluator root、控制源码 worktree/`.git`、其他 run workspace、原始结果根、父目录遍历和控制进程环境。预检必须让 Provider 等价子进程使用已知绝对路径、父目录和链接 canary 尝试读取并得到拒绝，不能只靠“没有给出路径”。全部 15 个 run 终态后，才把 sealed plaintext、commitment 对照和 evaluator 复算材料发布到最终证据包。

## 5. 运行单位、顺序与调用上限

- 运行矩阵：`5 arms × 3 fixtures = 15 run units`。
- 每个 run 从同一个 public fixture tree 创建只含一个公开 root commit 的 fresh Git repository、fresh Codex session 和 fresh context；不得带旧 fixture Git 历史。
- 执行顺序采用固定、每个 arm 平均位置均为 3 的 position-balanced schedule；runner 按 position 1→5 分五轮执行，每轮按 fixture 1→3：
  - fixture 1：`P, S, A00, A10, A11`
  - fixture 2：`A00, A10, A11, P, S`
  - fixture 3：`A11, S, A10, P, A00`
- 全局 Provider attempt 上限：`33`。这里的 attempt 是 runner 启动的一次逻辑 Provider 会话（writer、expert 或 expert rereview），不是不可观测的底层 HTTP 请求数。
- 预注册正常预算：`19` 次——`P/S/A00/A10` 各 3 次单 writer（12），A11 三个 writer（3）、三个 Primary Expert（3）以及安全场景一个 Cross-risk Expert（1）。
- 最坏内容路径预算：若三个 A11 场景均产生可操作 Finding，原 writer 在同一会话通过 callback 修复，不增加逻辑 session；最多增加四个独立 expert rereview，总计 `23`。再加最多三个 pre-output 技术重试为 `26`，保留七次不可挪用的安全余量。
- 任一实际调用拓扑超出上述预注册形态，runner 立即拒绝新 reservation，并把对应 run 记为 `budget_exhausted`，不得临时借用余量扩充专家、修复或复审。
- 内容失败不得重试；只有在未产生任何模型内容且被分类为 transport/schema/provider-pre-output failure 时，允许技术重试。
- 技术重试全局最多 `3` 次，仍计入 33。
- 任一调用启动前，runner 必须以原子 ledger 预留一个 attempt；当 `attempts_started >= 33` 时 fail closed。
- 超时、`needs_user` 截止、专家失败、证据不足和未关闭都保留在分母，不得删 run 或另起“更好的一次”。

## 6. 隔离与公平性

- 所有 arm 固定同一显式模型与 `model_reasoning_effort` request、Codex CLI、OS、CPU/内存上限、writer 工具权限、网络政策和依赖缓存。每个 primary writer 使用同一 `1800s` hard timeout 与 subprocess-output byte cap；Codex 0.147.0 CLI 未暴露可由本评估可靠执行的 per-run model-output token hard cap，因此不声称 token budget 等额，只记录并比较实际 usage。A11 因 treatment 增加的 expert/rereview 会话各有预注册 `900s` timeout，并全部计入 run 总计算成本。writer 使用 `workspace-write`，expert/rereview 强制使用 `read-only`，这是 treatment 所需的权限隔离而非基线工具优势。
- `P` 不加载 `.ai-sdlc`、AI-SDLC adapter 或 Superpowers methodology skills。不可移除的 Codex base/global instructions、普通已装插件/skills 必须逐项盘点并在五臂保持同一 digest；任何与开发方法相关的全局污染都会使预检失败。
- `S` 只在 run repo 的 `.agents/skills` 暴露冻结 Superpowers 6.3.0 repo-scoped namespace adaptation，不加载 AI-SDLC，并强制 `multi_agent=false`。除把 `superpowers:<name>` 调用机械改成现有 `$<name>` repo-skill 名称外，正文必须与上游 v6.3.0 相同；依赖图中的每个引用都必须存在并通过 closure test。公开名称始终写成“Superpowers 6.3.0 单 Agent repo-skill 适配路径”。
- `A00/A10/A11` 不暴露 Superpowers skills。
- 每个 run 保存 Codex instruction source inventory、repo skill inventory、AI-SDLC version、arm override SHA、fixture SHA 和 `git status`。
- 每个 run 保存相对 `provider_cwd=benchmark-task/`、provider-cwd tree digest、实际 subprocess cwd 与 resolved instruction-chain digest；公开 receipt 不保存绝对私有路径。
- 所有 writer 都是单个 `codex exec --ephemeral` 进程。A11 writer 产出 Candidate 后必须调用本地 `await-bounded-review` callback 并保持同一 session 活跃；runner 并行派发只读专家，把结构化 Findings 返回该 callback，原 writer 在同一会话负责修复，再调用 `await-bounded-rereview`。writer 若绕过 callback、提前退出或在专家失败后宣称完成，run 直接失败。expert/rereview 是各自独立的 ephemeral session。
- `A10/A11` 的最终 Close 必须由仍活跃的原 writer 按 v2.0.0 adapter 执行，并传入已审 `loop-id` 与 `--expect-review-digest`；A10 在候选证据齐备后 Close，A11 只有在 callback 返回全部 required expert PASS，或修复后新 session rereview PASS、parent/candidate digest 匹配且父树未被专家修改时才 Close。runner 不代替 writer 关闭，只验证 callback/命令/状态；提前 Close、缺 digest、专家失败/冲突后 Close 都使 run 失败。
- 密封 Gold、Oracle、参考实现和其他 arm 输出不在任何 Provider 可读根目录；OS deny-read profile 必须实测阻止路径直读、symlink/hardlink、环境变量和 `--add-dir` 暴露。通用评分引擎可以公开，但冻结 rubric/held-out 数据在 15 个 run 全部终态前不得解封。
- Frontend fixture 的 Node、浏览器版本、锁文件和依赖快照必须冻结；所有 arm 共享同一个预装依赖基线。旧 `npm ci --prefer-offline` 不得表述为 strict offline，旧 `channel=chrome` 也不得未经身份冻结直接复用。
- evaluator 在 Provider 进程退出后，从独立目录读取 candidate 副本并评分。
- 框架自己的 `closed`、模型自报完成或专家“PASS”都不能代替外部 evaluator。
- `.ai-sdlc` 工件存在与否不得直接计入质量主分，只能进入证据完整度或治理成本字段；否则评分会结构性偏向 AI-SDLC arm。

## 7. 主要指标

### 7.1 所有 arm

- `external_verified_delivery`：构建/隐藏测试/加权 AC/范围约束/严重缺陷均达到冻结阈值。
- `weighted_ac_coverage`：满足的适用 AC 权重除以适用权重总和。
- `severe_defect_escape_count`：最终仍存在的 blocker/important 密封缺陷数。
- `invalid_completion`：代理或框架报告完成，但外部 evaluator 不通过。
- `end_to_end_wall_seconds`：从隔离 workspace 准备开始，到外部 evaluator 完成；包含安装/初始化、Provider、治理、专家与评估，是时间对比的主口径。
- `verified_delivery_wall_seconds`：从 Provider attempt 预留到外部 evaluator 完成。
- `setup_wall_seconds`、`framework_init_wall_seconds`、`provider_wall_seconds`、`governance_wall_seconds`、`review_wall_seconds`、`evaluation_wall_seconds`：邻接展示且总和可复算。
- `clarification_request_count`、`intent_service_event_count`、`approval_service_event_count`、`intent_approval_service_latency_ms`：自动冻结服务的代理指标，不得表述为人工步骤或人工耗时。
- `human_event_count` 与 `human_active_seconds`：只记录真实 operator 的授权、确认或裁决；本矩阵不插入真人操作，因此为 0，并明确 `human_efficiency_not_measured=true`。若专家冲突需要人处理，只记录 `needs_operator=true`，不虚构耗时。
- `provider_attempts`、`input_tokens`、`cached_input_tokens`、`output_tokens`、`reasoning_output_tokens`。
- `evidence_completeness`：适用的冻结证据项中，新鲜、有效、可复算的比例。
- `governance_wall_seconds` 和 `governance_artifact_bytes`：单独展示治理开销，不隐藏 AI-SDLC 成本。
- `setup_artifact_bytes`、`total_artifact_bytes`、`tokens_per_verified_delivery`：所有机制新增成本均进入结果；失败 run 的分母和消耗不得丢弃。

### 7.2 专家评审

- `gold_root_recall = TP / (TP + FN)`。
- `actionable_finding_precision = TP / (TP + FP)`；无专家 arm 记为 `not_applicable`，不得写成 0。
- `review_severe_miss_count`。
- `risk_type_coverage`。
- `review_wall_seconds`、`review_provider_attempts`、`operator_active_seconds`。
- `cost_per_intercepted_defect` 只在 `TP > 0` 时报告，否则为 `not_applicable`。

### 7.3 汇总

- 15 个 run 只报告 raw count、逐 run 明细、各 arm 中位数、最小值、最大值和预注册差值。
- 不计算显著性，不报告生产泛化，不把三个场景当作独立行业总体样本。
- 失败和超时不得从时间或成本汇总中删除；超时按冻结预算截尾。

## 8. Receipt 与证据包

每个 run receipt 至少包含：

- schema、run_id、arm、fixture、order、status、failure_classification；
- benchmark commit、v2 commit、Superpowers commit、Codex version、model request、effort；
- fixture/public/sealed/arm-config SHA；
- start/end/elapsed；
- Provider attempt 列表与 token usage；
- human events；
- command evidence、exit code、stdout/stderr SHA；
- changed-files manifest、final candidate tree SHA；
- Loop/review/close 摘要及其边界；
- A11 的专家角色、选择原因、父结果 digest、隔离 child session、Finding、修复和复审绑定；
- 每位专家实际执行的 `loop review --expect-digest --read-path` 命令与 exit、实际 snapshot/input bytes digest、结构化原始输出、专家前后 parent tree digest 不变证明，以及原 writer Close 命令、review digest 与 callback 前置检查；
- external evaluator 原始结果和 digest。

最终离线证据包包含：

- protocol lock、arm diffs、fixture manifests、sealed rubrics；
- 15 份 raw receipt、Provider 原始 JSONL 的脱敏版本、命令日志；
- `summary.json`、`summary.csv`、计算器和验证器；
- `SHA256SUMS` 与一条离线复算命令；
- 所有失败样本和效果边界说明。

普通网站不得暴露凭据、绝对私有路径、原始认证信息或模型服务内部标识。

## 9. 网站呈现

新增顶级页面 `Engineering Evidence`，正文使用中文，页内包含：

1. `交付效益`：`P / S / A11`。
2. `Loop 增量`：`A00 / A10`。
3. `专家增量`：`A10 / A11`。
4. `方法与边界`：样本、控制变量、失败、治理成本与复算入口。

四个 Tab 之后始终显示按冻结 position-balanced schedule 排列的 15-run 明细、失败与未完成、时间/人工/计算开销以及证据复算入口。首版以语义化表格为主，不使用雷达图、奖牌排名、巨大百分比或只展示赢家的图表。总计可写 15 个 run unit，但每个 arm 的场景样本只能标成 `n=3`，不得把每项比较写成 `n=15`。

方法边界必须在结果同屏可见：这是阶段级对照；`S` 是单 Agent skill 路径；`A00/A10` 是评估框架强制消融；A11 expert 由 runner 依据 stock adapter 合同派发；未测完整五类 Loop、完整多 Agent SDD、Local PR Review 和生产环境。

首页在 Provider 启动前冻结且始终显示以下三项，不允许根据结果改指标、改方向或隐藏不利结果：

1. `交付结果`：`external_verified_delivery_count`，固定比较 `P / S / A11`，格式 `x/3`，方向为越高越好。
2. `Loop 增量`：`median_weighted_ac_coverage`，固定显示 `A00 / A10` 两个中位数及 `A10 - A00` 的有符号百分点，方向为越高越好。
3. `专家增量`：`sum_severe_defect_escape_count`，固定显示 `A10 / A11` 两个总数及 `A11 - A10` 的有符号差值，方向为越低越好。

三项都必须同时显示阶段级场景范围、每臂 `n=3` 和证据入口；负向结果用“下降/上升/较差”，零差异用“持平”，不得改写成正向宣传。Loop、Dynamic Expert Review、Platform Capabilities 页面只放对应紧凑结果带和深链；Downloads & Docs 提供 `Evaluation Evidence Bundle`。

Engineering Evidence 必须为每个 A11 run 展示实际 topology trace：选择的角色与原因、父结果/child session、Finding 数与严重度、原 writer 是否修复、rereview、冲突和真实人工节点。未发生的环节必须显式写“未观察到”，并由 `summary.json` parity 与 validator 约束，不能用产品能力示意图替代实际运行链。

结果文件由生成器静态嵌入 HTML，`file://` 运行不 fetch JSON。validator 必须验证 HTML 数字与 `summary.json` 完全一致；无 JavaScript时所有结果、方法和失败边界仍可阅读。

## 10. 发布门禁

以下任一条件成立时，网站不得出现效益结论：

- arm 隔离无法机器证明；
- 33 次调用硬上限无法 fail closed；
- fixture/Oracle 泄漏；
- 15 个预注册 run 任一被删、替换或重跑刷分；
- HTML 与 summary 不一致；
- 结果未通过三位独立专家同一 exact-hash 复核；
- 浏览器五视口、file://、no-JS、键盘与无横向滚动验收失败。

专家最终审查分别覆盖：AI-SDLC 机制真实性、AI Coding 对照公平性、评委可理解性与防误导。任何 Critical/Important finding 必须修复并重新绑定 exact baseline。
