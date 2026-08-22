# Task 1 三专家终审聚合修复报告

## 冻结范围

- 修复基线：`3d48550ed90949841f22b23c885693cb848c2c13`。
- 本轮只处理三位终审专家在同一基线提出的 protocol、attempt ledger、run evidence、failure classification 与公共输出隐私边界。
- 未修改 fixture、网页、Task 2 或实验配置；未启动 Provider、`codex exec` 或任何正式实验。

## Fresh RED

先为终审反例建立独立测试，生产实现未修改时：

```text
UV_CACHE_DIR=.uv-cache uv run pytest -q tests/unit/test_benefit_benchmark.py -k 'release_gate'
10 failed, 3 passed, 283 deselected
```

失败覆盖：全零 fixture commitment、跨 run 重复 child session、非安全场景伪造
`needs_operator`、receipt 自证阶段耗时/工件、可自由选择 failure classification、编码
`api_key[]`、普通 `notoken / nosecret / mypassword / myapikey` 被误脱敏。随后为 runner
原子写入入口增加真实子进程 RED：

```text
test_release_gate_cli_atomically_snapshots_real_run_evidence: 1 failed
```

## 统一修复

### 1. protocol 与逻辑调用身份

- bound `fixture_tree_sha256 / fixture_commitment` 必须相等且为非全零 SHA-256；
  tracked `pending-unbound` 仍保持结构有效但执行 NO-GO。
- attempt ledger 升级为 `ai-sdlc-v2-benefit-attempt-ledger/v5`；旧 v4 fail closed，
  不做隐式迁移。
- 所有已终态逻辑 Provider attempt 的 `child_session` 在整本 ledger 全局唯一；
  `raw_provider_output_sha256` 允许确定性相同输出重复，不被误当作调用身份。

### 2. 安全场景真实冲突闭包

- `needs_operator` 只允许 `A11:multi-tenant-security-review`。
- writer 必须停在 `review_pending` Candidate；Primary 与 Cross-risk 两个 required expert
  必须都真实完成、各自产生非占位且互异的 Finding；不得存在 repair 或 rereview。
- P / S / A00 / A10 及 A11 另外两个场景在 ledger 写入点直接拒绝伪造冲突终态；
  receipt 继续 exact closure 每个 started attempt 与 immutable callback evidence。

### 3. runner-owned 权威测量证据

- ledger v5 新增不可变 `run_evidence`，独立持久化六阶段事件、冻结工件适用性、
  changed files、自动服务/澄清事件与真人事件。
- `record_run_evidence(...)` 与 Provider reservation/completion 共用跨进程锁和原子
  fsync + replace 写入；仅在该 run 所有已启动 attempt 终态后接受一次快照。
- 工件由 core 从真实 workspace 读取 bytes，自行计算 SHA-256 与 size；缺失但适用的
  工件明确保存为 `observed=false / sha256=null / size_bytes=0`，不允许 receipt 填值冒充。
- CLI 新增 `record-run-evidence`，后续 runner 可提交闭合 manifest；重复写入只有字节
  完全相同才幂等，文件后续被篡改导致 snapshot 改变时拒绝且 ledger bytes 不变。
- receipt 升级为 `ai-sdlc-v2-benefit-run-receipt/v5`，其中 phase、artifact、changed-files、
  automated/human events 必须逐字精确投影 ledger 权威证据；旧 v4 fail closed。
- 阶段分项、工件字节、evidence completeness、澄清/服务计数与 latency 仍从投影后的
  闭合证据复算，形成“runner 采集 → ledger 持久化 → receipt 投影 → verifier 复算”链。

### 4. 唯一 failure classification

- classification 不再只校验 status 对应的可选集合，而是由 terminal writer、已启动
  expert/rereview 状态、evidence completeness 与 external evaluator 状态按固定优先级
  唯一推导；receipt 选择同 status 下另一个合法枚举也会失败。

### 5. Core / CLI 共用单次解码隐私分类器

- 公共 URI 只执行一次 percent-decode，不递归解码。
- query/fragment 的 secret key 使用 exact name 识别，支持 `_ / - / percent encoding`
  与 `[]`；只替换 value，保留 scheme、host、path、key、separator 与普通参数。
- 编码 POSIX、Windows、UNC、`file://` 私有路径会在 URI query/fragment 中被识别；
  valid receipt 扫描、validation issue 和 exception message 共享同一实现。
- `notoken / nosecret / mypassword / myapikey` 等仅包含敏感子串的普通 key 原样保留，
  防止扩大误报面。

## 验证

```text
benchmark + CLI 全回归：325 passed
ledger/state/concurrency/release-gate 聚焦：118 passed, 197 deselected
Ruff：All checks passed
```

正式实验调用仍为 0；fixture commitment 仍是 `pending-unbound`，执行继续 fail closed。

## Fix Round 2：证据生产权与 ledger 封印

### 冻结边界

- 修复基线：`48cade5d048c8ab286d26a5503f387d085bfd95e`。
- 只修改 protocol/receipt identity、benchmark core/CLI 与测试；未修改 fixture、网页、Task 2 或任何实验结果。
- Provider、`codex exec` 与 benchmark experiment 调用均为 0。

### Fresh RED

在生产实现修改前新增 release-gate-round2 反例，得到 `7 failed`。反例覆盖：

- protocol 尚未绑定 evidence contract；
- bulk `RunEvidenceRequest` 仍允许 finalizer 声明 required/applicable、changed files、阶段和人工事件；
- 本冻结矩阵可写入伪造 human event；
- 全编码 key/`[]` 与全编码或部分编码 `Bearer`、`sk-`、`ghp_`、`AKIA` token 会从 CLI 公共错误面泄漏。

随后补充 cross-run sealed transplant、封印后 attempt 变化、未来时间、evaluation end-after-seal、late technical retry/event、合同字节篡改、私密 service transaction 以及真实文件 hash/stat/diff 反例，统一纳入 GREEN 门禁。

### 统一修复

1. **Evidence contract 生产权**：protocol/receipt identity 增加成对的
   `evidence_contract_sha256 / evidence_contract_commitment`。tracked protocol 保持
   paired `pending-unbound`；execution-ready 时必须为相同、非全零 SHA-256。CLI/Core 在每次
   Provider reserve/completion 以及 phase/service/seal 前都必须显式接收、读取并校验 exact
   committed bytes。合同按 15-run 顺序冻结 artifact slot/category/required/applicable、
   changed-files baseline/candidate scope 和允许的自动事件类型。
2. **Ledger v6**：旧 v5 fail closed，不做隐式迁移。首个 writer reservation 由 core clock
   建立 `run_started_at`；controller phase API 只接收 phase/action。service transaction 由
   `start` 与 `complete` 两次 core-clock 事件包围真实事务，complete 只接收 closed evidence，
   不接收任何 caller timestamp/duration；terminal attempt 不允许遗留 open transaction，且
   closed event 同时绑定 service evidence digest 与可复算公开 timing digest。Provider phase
   直接由真实 reservation/terminal history 推导；自动/澄清事件只能来自绑定 active attempt
   的 closed service transaction；本矩阵没有 human event 写入入口，封印值恒为 `[]`。
3. **不可变 run seal**：只有 run 内所有 attempt 终态后才能封印。core 从合同定位实际文件，
   通过打开文件的 `fstat`、真实 bytes SHA-256 与 baseline/candidate 内容 diff 生成权威证据；
   ledger 只保存相对路径和 digest，不保存 workspace absolute root。seal 绑定 run_id、ordered
   attempt IDs、全量 attempt digest、terminal sequence、evidence-contract digest、完整 seal
   binding digest 与 core `recorded_at`。封印后 reserve、completion、phase/service event 和技术
   重试全部拒绝且 ledger bytes 不变。
4. **Reload fail closed**：run registry 必须与 attempt run 集合精确闭合；拒绝 orphan、
   cross-run sealed transplant、封印后 attempt 变化、未来 core timestamp、final phase end 超过
   seal，以及 evidence-contract digest 漂移。
5. **先校验再持久化**：reservation、completion、controller phase、service transaction 与
   reload 均执行 closed schema、时序、拓扑和公共隐私校验；坏数据不会到达 immutable ledger。
   failure classification 继续由 terminal writer、真实 expert 状态和 evaluator/evidence 状态
   唯一推导。
6. **单次 percent-decode 隐私边界**：Core 与 CLI 共享同一个 bounded single-decode
   classifier。它支持首字符 `%xx`、全编码 secret key、`[]`、以及全编码/部分编码
   `Bearer/sk-/ghp_/AKIA`；只替换匹配 value/raw token span，普通
   `notoken/nosecret/mypassword/myapikey` 原字节保真。valid receipt、validation issue、
   exception message 三个表面均有正反回归。

### 验证

```text
benchmark + CLI 全回归：354 passed
ledger/state/concurrency/release-gate 聚焦：111 passed, 233 deselected
tracked protocol validate：structurally_valid=true, execution_ready=false
  protocol.fixture-pending + protocol.evidence-contract-pending
run-receipt / summary 两份 JSON schema：schemas-ok
Ruff：All checks passed
git diff --check：clean
```

Task 2 才能生成真实 fixture tree 与 evidence contract 并原子绑定 protocol；本提交没有生成
占位合同或放宽 NO-GO。

## Fix Round 3：外部证据复算与阶段闭合状态机

### 冻结边界与 RED

- 修复基线：`ecc197876cc21198be8c0872107c16b316041880`。
- 本轮只关闭 evidence authority 可自重签与 phase start/end 可拆分两个根因；未修改
  fixture、网页、Task 2 或实验结果。
- Provider、`codex exec` 与 benchmark experiment 调用继续保持为 0。

在生产实现修改前先加入三个根因级反例，实际结果为：

```text
UV_CACHE_DIR=.uv-cache uv run pytest -q tests/unit/test_benefit_benchmark.py \
  -k 'release_gate_round3'
3 failed, 344 deselected
```

失败分别证明：攻击者可修改 contract-owned `required` 后同时重签 ledger seal 与 receipt；
artifact 在 seal 后变化仍会通过；core 没有 `start_run / transition_run_phase`，仍暴露拆分式
phase event 写入。实现统一方案后再扩展为 21 条 release-gate/CLI 回归，覆盖 contract/root
缺失、baseline/candidate 漂移、非法顺序、pre-start reserve、post-provider reserve、future、
gap、orphan、pending no-write 与真实 CLI 完整正向链。

### 统一修复

1. **Contract 与真实 workspace 双重权威**：`verify_receipt` 和 CLI `verify-receipt`
   必须显式接收 exact hash-bound evidence contract 与可移植 workspace root。verifier 不再把
   ledger seal 当作唯一事实，而是按 contract 逐 slot 重读真实文件，以打开文件的 `fstat`、
   bytes SHA-256 和 size 重建 inventory，并重新计算 baseline/candidate 内容 diff 与两侧
   changed-scope tree digest。ledger 与 receipt 只能投影该结果；同时修改字段、digest 与
   seal binding 也无法绕过外部复算。
2. **可移植且无私有根路径**：ledger/receipt 新增的
   `changed_scope_tree_digests` 只保存 canonical tree digest；artifact 与 changed-file 只保存
   contract 内的相对路径。contract/root 缺失、contract exact bytes 漂移、artifact 编辑、
   baseline/candidate root 内容漂移均 fail closed，ledger 不保存 workspace 绝对路径。
3. **六阶段原子状态机**：新增 `start-run`，由 core clock 在任何 writer reservation 前建立
   `setup`；新增 `transition-phase(next)`，用一次 core timestamp 同时关闭当前阶段并打开固定
   后继。唯一顺序为
   `setup → framework_init → provider → post_provider → review → evaluation`。
   reserve/completion/service transaction 只能发生在 provider；离开 provider 前至少存在一个
   attempt 且全部 terminal；多 Provider attempt 始终留在同一 provider 阶段。
4. **Seal 是最终原子闭合**：seal 只允许在 open evaluation，由 core now 关闭 evaluation，
   随后构建 evidence、复用 receipt 同一 phase adjacency/additivity/digest validator、验证完整
   ledger，全部成功后才原子写入。失败路径不会把半闭合 phase 或坏 evidence 写回；sealed 后
   start/transition/reserve/complete/service/retry 全部拒绝。
5. **Reload 与 CLI fail closed**：ledger v6 closed shape 增加 `current_phase` 并拒绝旧 shape
   隐式迁移；reload 校验 run start、phase prefix、同 timestamp 邻接、attempt/service event
   必须落在 provider interval、terminal closure、future/gap/orphan 与 seal 时序。CLI 删除
   `record-phase-event`，新增 `start-run / transition-phase`；`verify-receipt` 的 contract 与
   workspace root 均为 required 参数。tracked pending protocol 在所有新 phase 命令上仍返回
  稳定 JSON input error 且不创建 ledger。

### 验证

```text
Round 3 + CLI 必填聚焦：21 passed, 342 deselected
benchmark + CLI 全回归：373 passed
ledger/state/concurrency/release-gate 聚焦：149 passed, 214 deselected
```

两份 frozen JSON schema、Ruff、tracked protocol 状态与 Git diff/status 在本轮最终提交前再次
独立校验。tracked protocol 仍为 fixture/evidence-contract paired `pending-unbound`，因此
`structurally_valid=true / execution_ready=false`，正式实验继续 NO-GO。
