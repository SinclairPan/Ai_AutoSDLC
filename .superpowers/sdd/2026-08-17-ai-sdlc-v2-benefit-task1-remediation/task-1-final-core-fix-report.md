# Task 1 总门禁 Core / Evidence 最终修复报告

## 范围与边界

- 实施基线：`7277ef0a502ecab02d20351519d3b96707ab5afa`。
- 只修改 receipt schema、benchmark core 和 focused tests；CLI 公共参数与 ledger v4 状态机、并发、retry 拓扑均未改变。
- receipt 显式从 v3 升为 `ai-sdlc-v2-benefit-run-receipt/v4`；旧 v3 fail closed，不做隐式迁移。
- 全程未启动 Provider、`codex exec`、fixture 或 benchmark experiment，也未派生 agent。

## Fresh RED

第一批在 BASE 上加入 union/schema/path/taxonomy/measurement/digest 反例：

```text
UV_CACHE_DIR=.uv-cache uv run pytest -q tests/unit/test_benefit_benchmark.py -k 'final_core'
20 failed, 1 passed, 227 deselected
```

唯一先通过项是独立合法 HTTP URI 正例；其余 20 条均证明旧门禁存在对应缺口。第二批在 BASE 行为上加入 A11 partial execution closure：

```text
UV_CACHE_DIR=.uv-cache uv run pytest -q tests/unit/test_benefit_benchmark.py \
  -k 'final_core_failed_expert or final_core_writer_repair or final_core_needs_operator'
4 failed, 248 deselected
```

该批证明 failed expert、Finding 后已发生 repair、failed rereview 与 needs-operator duplicate callback 未形成统一、可接受的 exact disclosure。

## 八项 GREEN

1. **Union / integer 语义**：frozen JSON Schema validator 严格解释 string 或 type union；`boolean` 不再命中 `integer`，nullable exit code 的 `false` 被拒绝。顶层 token 语义检查也统一使用 non-bool integer。
2. **A11 exact attempt closure**：callbacks 直接由 ledger 中所有已启动 first-review attempt 派生 exact ID 集合，completed / technical-failure / failed / timeout / needs-operator / budget-exhausted 均绑定 role、parent、session、token、raw、argv、exit 与只读树证明。writer Finding 后已经发生的 repair/new Candidate 必须披露。每个 callback 的 nested `rereviews[]` 精确闭合所有已启动 rereview 与 technical retry；失败复审同样绑定 attempt、repair、新 Candidate、exit、raw 与成本。合法 partial failure 保持可发布。
3. **Non-placeholder digest**：递归扫描所有 `*_sha256`、`*_digest`、artifact `sha256`、`evidence_id`、`receipt_sha256` 与 fixture commitment；适用值必须为非全零 SHA-256，不适用值只能是 schema 允许的 `null`。覆盖 evaluator、summary row、callback、rereview、phase、command 与 artifact。
4. **needs_operator multiset**：callback attempt ID 必须 exact、唯一；conflict 必须来自 required completed roles，role 也唯一，且不得混入 repair 或 rereview。重复 callback 不再被 set 去重掩盖。
5. **可复算 measurement evidence**：六个 phase 各自保存 started/end/canonical evidence digest，形成从 receipt start 到 evaluator end 的连续分区，并逐项精确复算 timing；直接重分配 provider/governance 等分项失败。artifact inventory 按 relative path、nonzero digest、正 size、category、required/observed 闭合，路径唯一，setup/governance/total bytes、evidence completeness 与 changed files 都精确复算。clarification count 从自动事件类型精确计数；每个自动事件的 latency 与 digest 由 start/end 复算。
6. **冻结 failure taxonomy**：status×classification 矩阵写入 schema 和 semantic validator；`completed→none`、`timeout→timeout`、`needs_operator→expert_conflict`、`budget_exhausted→provider_budget_exhausted`，failed 只接受冻结的五类失败原因。
7. **Provider schema closure**：`required` 与 `enum` 元素必须唯一；type union 元素必须唯一且来自支持集合；const/enum 必须命中 union；string/numeric/array operand 只能作用于相容的单类型或 nullable union。无效 schema 只返回 issue，不消耗 Provider 调用。
8. **HTTP 内嵌私有路径**：HTTP(S) 仅豁免经过 `urlsplit` 验证的 scheme/authority/public path；query/fragment 解码后继续扫描 POSIX、Windows drive、UNC、`file://`。独立合法 HTTP URI 保持允许。

## 补强自审

- 真实 clarification event 正例以及 latency/digest 篡改反例；
- artifact inventory duplicate path 反例；
- expert pre-output technical retry 的两个 started attempt 必须产生两个 callback；
- rereview pre-output technical retry 的两个 started attempt 必须在 nested closure 中全部披露；
- receipt v3 明确 fail closed。

## 验证

```text
UV_CACHE_DIR=.uv-cache uv run pytest -q \
  tests/unit/test_benefit_benchmark.py tests/unit/test_cli_commands.py
267 passed

UV_CACHE_DIR=.uv-cache uv run pytest -q tests/unit/test_benefit_benchmark.py \
  -k 'ledger or reservation or provider_attempt or retry or writer or expert or concurrent'
86 passed, 171 deselected

UV_CACHE_DIR=.uv-cache uv run pytest -q tests/unit/test_benefit_benchmark.py \
  -k 'task12 or task13 or final_core'
152 passed, 105 deselected

UV_CACHE_DIR=.uv-cache uv run ruff check \
  src/ai_sdlc/benefit_benchmark.py \
  scripts/ai_sdlc_v2_benefit_benchmark.py \
  tests/unit/test_benefit_benchmark.py tests/unit/test_cli_commands.py
All checks passed!

UV_CACHE_DIR=.uv-cache uv run python -c '<parse both frozen JSON schemas>'
schemas-ok

git diff --check
clean
```

## 变更文件

- `benchmarks/ai-sdlc-v2-benefits/schemas/run-receipt.schema.json`
- `src/ai_sdlc/benefit_benchmark.py`
- `tests/unit/test_benefit_benchmark.py`
- `.superpowers/sdd/2026-08-17-ai-sdlc-v2-benefit-task1-remediation/task-1-final-core-fix-report.md`

## Blocker

无。tracked protocol 的 fixture commitment 仍保持 `pending-unbound`，因此正式实验继续 NO-GO；本修复没有放宽该边界。
