# Task 2：三套公开 Fixture 与密封外部评估门禁

## 冻结边界

- 实施基线：`15aab5a6959175cfc00199b32e8a0b549d220a25`。
- 只实现计划 Task 2：公开 fixture、稳定 manifest、密封 commitment、证据合同模板、零 Provider canonical pre-state、自动 intent/approval service、密封 evaluator 与 Provider deny-read profile。
- 未修改 tracked `protocol.json`；其中 fixture 与 evidence-contract commitment 继续为 paired `pending-unbound`，因此正式实验仍为 NO-GO。
- 未创建 Task 3 arm、runner、网站或 summary；Provider、`codex exec` 与正式实验调用均为 `0`。

## 来源审计与去偏

- T1 只从 Git object `9f430becd28d4dd6402e0f22b0c9bbed81c19b13` 提取业务输入、六项 AC、Vue 项目形态与 evaluator 思路。
- T2 只从 Git object `d4c7722917c96af2dc346f2271a984f15bac89d9` 提取 Candidate、弱测试、Requirement、Design Contract 与 evaluator 思路。
- 新的 Requirement/Design fixture 来自冻结 benchmark spec commit `7fc2366b8530265d58b1874e781b0b7274615d94`。
- 没有复制旧 outcomes、Provider prompts、比赛 wrappers、receipts、四角色流程、quorum、veto 或 operator decision。
- 删除 T1 明示“遗漏 AC”的任务句和点名缺陷/修复的源码注释；T2 公开输入只描述业务合同，不规定评审方法。

## Fresh RED

先加入 `tests/unit/test_benefit_benchmark_fixtures.py`，生产模块不存在时真实结果为：

```text
ModuleNotFoundError: No module named 'ai_sdlc.benefit_benchmark_fixtures'
1 error during collection
```

随后 GREEN 测试覆盖：三 fixture manifest、两次 fresh copy、single-root Git、visible command exit/signature、sealed baseline、semantic parity、五臂共同前端目标、A-arm pending confirmation、digest-bound 自动批准、sealed leakage、direct/parent/symlink/hardlink/env/other-run/add-dir 隔离以及 tracked plaintext 扫描。

## 实现结果

### 公开 Fixture 与稳定身份

- 三套 public tree 均复制为只含一个 deterministic root commit 的 clean Git repository。
- `manifest.json` 绑定每套 public tree、`input-contract.json`、visible commands 与来源 commit。
- fixture tree/commitment pair：`9a2c106ae3ec258f4d1fa98fe86c2509838a817168e5b3e8ac49ac8abe3ce718`。
- evidence-contract template/commitment pair：`bc61562698649ab8df533f9d196c6357d09352416bc4f4cb96a75e2eb7d87519`。
- sealed manifest commitment：`4bb7886ba3e5df1b820cc71982eada0a5925a4c17f6b76a7aa22e3f8ebac5e43`；plaintext 只存在于 `<protected-evaluator-root>/<lock-id>/`，tracked 文件只有 commitment。

### 公平输入与 canonical pre-state

- 每套 fixture 只有一个 method-neutral `input-contract.json`。
- Fixture 1 只生成 frozen Requirement；Fixture 2–3 只生成 frozen Requirement + closed Design Contract。
- `normalized_semantic_view` 逐 fixture 证明 A-arm canonical state 与 P/S 公开输入语义相等，不增加 AC、风险、答案或实现提示。
- 前端五臂共用 `vue3 / public-primevue / modern-saas`、同一 program manifest 与 environment lock；P/S confirmation 为 `not_applicable`，A00/A10/A11 为 `pending`。

### 自动意图/批准服务

- 问题只能使用公开 `question_id` taxonomy；未知问题统一返回 `unresolved`。
- controller 先注册真实 proposal digest，Provider 的 `approval_request(run_id, approval_type, proposal_digest)` 只有 exact digest 才返回 `approved`，缺失、过期或伪造 digest 统一返回 `revise`。
- 日志只写 `intent_service_event` / `approval_service_event`、自动 actor、configured delay 与 result digest，不写 human event。

### 密封 evaluator 与隔离

- Requirement 使用密封 literal weighted rubric。
- Frontend 新增未进入 Git 的连续失败→恢复、延迟响应竞态、快速双击和异常响应四类 held-out scenario。
- Security 新增 tenant/time/action/audit 四类 held-out scenario。
- Scenario 只把单个黑盒输入交给 Candidate subprocess；expected result 留在父 evaluator。Candidate subprocess 使用与最终 Provider 相同的 macOS Seatbelt deny-read profile，环境只保留 PATH。
- launch preflight 对 symlink/hardlink inode、环境路径、other-run root 和 `--add-dir` fail closed；Seatbelt 真实拒绝 sealed root、其父目录与 control worktree。

## 两次 fresh baseline 复算

这只是 fixture 健康检查，不是实验结果，不能进入网站效益结论：

```text
requirement-contract-ambiguity  copies_equal=true evaluation_equal=true delivery=false coverage=0.00 severe=8 leak=0
frontend-recovery-delivery      copies_equal=true evaluation_equal=true delivery=false coverage=0.12 severe=10 leak=0
multi-tenant-security-review    copies_equal=true evaluation_equal=true delivery=false coverage=0.20 severe=9 leak=0
```

exact final deny-read canary：

```text
direct=true parent=true symlink=true hardlink=true environment=true other_run=true add_dir=true
```

## 验证

```text
focused fixture tests: 19 passed, 1 skipped
```

唯一 skip 是 Codex 已处于 Seatbelt sandbox 时不能嵌套 `sandbox-exec`；相同 exact profile 已在 sandbox 外单独执行并七项全通过。

```text
benchmark + fixture + CLI related regression: 392 passed, 1 skipped
Ruff: All checks passed
Task 1 evidence-contract consumer: schema v1, 15 frozen runs accepted
manifest verification: []
sealed commitment verification: []
git diff --check: clean
```

## 剩余门禁

- Task 2 产物尚需三位独立专家在同一 exact HEAD 上终审。
- 只有三专家 PASS 后，父任务才能将本报告中的 paired commitments 原子绑定到 tracked protocol；绑定前不得启动 Task 3 以后任何 Provider 或实验路径。
