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
