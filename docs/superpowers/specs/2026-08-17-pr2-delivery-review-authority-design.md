# PR2 唯一交付与评审权威设计

## 基线与目标

本设计落实《AI-SDLC 2.0 内部优化执行合同（修订冻结版）》中的逻辑 PR2。

- 实施基线：`cf91572e5877d54dba963b4b28c067f7f084aa3f`
- 基线 tree：`365430814b9364b3d7eb47e1d02644111d749021`
- PR #29 已完成逻辑 PR1：恢复用户项目真值。
- PR #30 提前完成逻辑 PR3 的部分升级提示，不改变本 PR 边界。
- 本 PR 只形成唯一交付与评审闭环，不继续 PR3，也不执行 PR4 物理删除。

当前缺口：

1. `ai-sdlc run` 仍可进入旧 runner，五个 Loop 尚不是唯一运行真值。
2. 五个 Loop 的 Close 只校验可复制的 input digest，不能证明独立专家真实返回结果。
3. Implementation 与 Local PR 的质量证据仍可由命令字符串冒充。
4. Local PR 虽能阻断 reviewer 修改工作区，但未把 reviewed staged tree 与最终 commit tree 精确绑定。

## 产品边界

必须保留：

1. 本地独立提交前 Reviewer；未配置、失败或修改工作区时阻断。
2. 代码精简性建议严格 advisory，不改变状态、verdict 或 Close。
3. Requirement、Design Contract、Implementation、Frontend Evidence、Local PR Review
   五个 Loop 的动态专家评审；每轮最多两名专家，最多一次修复复审。

明确不做：

- 不建设 session、ledger、authority、CAS、proof、certificate、quorum、score 或 policy registry。
- 不新增 Agent runtime、Graph 编排器、后台服务或运行时专家学习。
- 不管理多人分支、WorkItem 分布式编号、锁或共享 checkpoint/handoff。
- 不删除 Program、Telemetry 或旧 runner 文件；物理删除属于 PR4。
- 不补做升级提示、规则注入、status 精简；这些属于逻辑 PR3。
- 不把 slimming advisory 变成硬门禁。

## 动态专家评审

`review_kernel.py` 保持纯函数、只读和无持久化。它根据当前 Loop 选择主专家：

| Loop | 主专家 |
| --- | --- |
| requirement | `product-value-and-acceptance` |
| design-contract | `architecture-and-maintainability` |
| implementation | `correctness-and-regression` |
| frontend-evidence | `ux-accessibility-and-evidence` |
| local-pr-review | `cross-stage-delivery` |

若结果包含交叉风险，按固定优先级再选择一名不同专家：

1. security → `security-and-permissions`
2. data-integrity → `data-integrity-and-migration`
3. concurrency → `concurrency-and-recovery`
4. public-api → `api-compatibility`
5. frontend → `frontend-integration`

角色集合与原因进入 `ReviewInput` 和 input digest。最多两名，不允许第三名。

正常流程由 Codex、Cursor、Claude Code 等宿主 Agent 自动执行：

1. `ai-sdlc loop review --type <type> --loop-id <id> --json`
2. CLI 返回 digest、可读工件、风险信号和一至两个专家角色。
3. 宿主 Agent 为每个角色启动独立、只读 reviewer。
4. 每名 reviewer 输出一个单角色 `ReviewExecution` JSON。
5. Agent 调用 `loop review-record` 记录结果。
6. CLI 重建 input，校验 digest、角色集合、数量和结果结构后，写入当前轮 outcome。

用户不需要理解或手动触发专家命令。CLI 不新增模型/Agent 编排器，只校验宿主 Agent
提交的独立结果。

## 最小持久结果与轮次

每个现有 Loop 目录最多保存：

- `review-outcome-round-1.json`
- `review-outcome-round-2.json`

Local PR 使用现有 `.ai-sdlc/reviews/pr/<review-id>/`。不新增 pointer、registry、store
或历史目录。

`LoopReviewOutcome` 仅包含：loop id/type、round number、input digest、completed/failed、
expert roles、findings、failure kind/reason、recorded_at。不得加入跨 Loop credential。

状态机：

- 无 outcome：准备 round 1。
- reviewer failed：Close 阻断；只有同轮、同 input digest 可原子重试。
- round 1 有 blocker/important：`needs_fix`；实质输入变化后才能进入 round 2。
- round 2 有 blocker/important：`needs_user`；禁止第三轮。
- 只有 advisory 或无 finding：`passed`；允许 Close。
- `completed` outcome 永不可覆盖。clean round 1 不可重录；completed round 2 无论
  findings 为何都不可重录。
- 写入前必须重新读取目标文件，避免并发覆盖 completed outcome。

outcome 文件和 `loop-run.json` 的评审状态字段不进入实质 input 的原始字节 digest，避免
写入 outcome 后自失效；业务报告、源码、上游工件仍按稳定原始字节绑定。

## 五个 Close

所有 Close 共用 `validate_review_outcome_for_close`：

1. 重建当前实质 ReviewInput。
2. 校验调用方 expected digest，防止 stale。
3. 加载与 digest 对应的最新 outcome。
4. 要求 outcome completed、角色集合精确匹配且最多两名。
5. 要求不存在 blocker/important。
6. 写 Close 工件前再次校验。

Digest 只证明输入未变，不能单独授权 Close。findings 不投影到平行 issue/store；
`status` 和 `run` 直接从当前 Loop outcome 派生 Result、Next、Blockers。

## 可执行质量证据

新增小型 `QualityCommandResult`：

- argv、cwd、exit code
- started/completed time
- source digest before/after
- stdout/stderr SHA-256 与有界 tail

命令使用 `subprocess.run(argv, shell=False)`；cwd 必须位于项目内。源码摘要绑定 HEAD、
index tree、tracked diff 和 untracked 文件内容，排除 AI-SDLC runtime 工件。只有 exit 0
且前后源码摘要相同才是成功证据。

子进程默认继承调用方环境，确保企业代理、包管理器、凭证转发、私有依赖和
`GIT_SSH_COMMAND` 正常工作。只清除会把 Git 命令重定向到另一个仓库、index 或对象库的
变量：`GIT_DIR`、`GIT_WORK_TREE`、`GIT_INDEX_FILE`、`GIT_OBJECT_DIRECTORY`、
`GIT_ALTERNATE_OBJECT_DIRECTORIES`、`GIT_COMMON_DIR`、`GIT_REPLACE_REF_BASE`。不得清除
HTTP(S)/NO_PROXY、包管理器环境、SSH/credential 环境或普通 Git 配置环境。

Implementation 新增 `loop implementation verify --task-id ... --cwd ... -- <argv...>`。
旧 `verification_commands` 字符串仍可读取和展示，但不满足 Close。每个 required DONE
task 至少一个当前源码摘要上的成功结果。

Local PR 新增 `pr-review verify --cwd ... -- <argv...>`。旧 `record-evidence` 返回明确
legacy blocker，不能进入动态专家输入或满足 Close。

## Local PR 精确 tree

正常交付源固定为 `local-staged`：

- `pr-review start` 默认 staged source。
- `SourceSnapshot`/`ReviewPack` 保存 `staged_tree_oid = git write-tree`。
- reviewer 前后继续校验 HEAD、index、worktree、ignored 文件与 index flags。
- 动态专家 outcome 同时绑定 review pack 与 staged tree。

支持两种提交路径：

1. `pr-review commit --message ...`：提交前复核 staged tree，调用普通 `git commit`
   且不跳过 hooks；提交后校验唯一 parent 与 commit tree，再 Close。
2. 用户普通 `git commit` 后调用 Close：当前 HEAD 的唯一 parent 必须等于 reviewed HEAD，
   commit tree 必须等于 reviewed staged tree。

命令不自动 add、不回滚历史、不在 `run` 中自动提交。hook 产生不同 tree 时只报告 stale，
要求对当前 HEAD 重审。range/patch/SCM source 只用于诊断，不能完成交付 Close。

## `run` 唯一路由

`ai-sdlc run` 不再构造 `SDLCRunner`、Executor、旧 gate registry 或默认 Telemetry。
它只读五个现有 current pointer 和 review outcome：

- 一个 active Loop：显示其 Result/Next/Blockers。
- 前序 closed、后继 active：路由到最深的 active Loop。
- 无 Loop：`needs_user`，Next 为 Requirement start。
- 多个无法证明同一 predecessor chain 的 active Loop：`needs_user`，要求显式选择。
- 全 closed：显示完成，不进入旧 pipeline。

`--dry-run` 同样只读。`mode=confirm` 与 `--acknowledge-execute-batch` 保留解析兼容，
但返回迁移 blocker，不触发旧执行器。

## 兼容、验收与终止

- 旧 Loop 无 outcome：可读，但 Close 阻断并指向 `loop review`。
- 旧 verification string：可读但非权威。
- 旧 range review：可查看，需以 staged source 重开才能 Close。
- 新字段使用默认值；outcome schema 为 1；不伪造历史评审成功。

验收必须覆盖：五 Loop 缺 outcome；角色缺失/错配/第三专家；两轮上限；failed 同 digest
重试；completed outcome 不可覆盖；advisory 不阻断；真实命令非零/改源码/stale；reviewer
修改状态；staged/commit tree 精确一致；`run` 零 Git/checkpoint/Telemetry 写入；slimming
不被状态/Close/commit 路径导入；Node/Java/Python 企业样例。

以上通过即终止逻辑 PR2。升级提示、规则/status、物理删除和发布不得顺便加入。
