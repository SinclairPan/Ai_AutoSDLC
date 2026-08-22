# Task 3：五臂环境、指令隔离与零 Provider 执行边界

## runtime-capsule/v2 与 r3-ready 语义修复（2026-08-20）

- 本批次基线为 `5820f39190dacab4537feef253f12d1ecbf0d8e4`，仅实现代码、closed schema 与 TDD；没有创建 r3 source、disposition、sealed root，也没有执行 Provider、`codex exec`、正式 authorization、ledger、results 或 experiment。
- 新 `ai-sdlc-v2-benefit-runtime-capsule/v2` 将根目录 `.` 的稳定身份独立为 `root_identity`：持久化 canonical path、no-symlink、device/inode/uid/gid/mode/nlink/size，但不持久化根目录 `ctime_ns/mtime_ns`；其余 1648 个 runtime closure entry 继续完整绑定 path/type/device/inode/uid/gid/mode/nlink/size/ctime/mtime，regular file 继续绑定 SHA256。
- 扫描以 canonical root 的 `O_DIRECTORY | O_NOFOLLOW` dirfd 为锚，比较 lstat-before、fstat-opened、两次完整 stdlib snapshot、fstat-after 与 lstat-after；临时 TOCTOU identity 包含根目录 ctime/mtime，任一扫描中变化立即 `runtime-capsule-drift`。volatile root times 不进入持久 manifest，避免出现“显示但未绑定”的字段。
- r3 compiler 只接受 `sealed-source/v2`，生成 `sealed-manifest/v5`、`candidate-commitments/v4`、`materialization-receipt/v4`、`isolation-attestation/v2`，并绑定 exact clean Git HEAD/tree、完整 Python runtime identity 与 capsule/v2 digest。stale r2 schema、capsule/v1、缺失/额外字段与交叉版本 pair 均 fail-closed。
- 旧 Task2 validator/loader 保持原语义，只接受 manifest/v4、candidate/v3、receipt/v3、attestation/v1 与 capsule/v1；Task2 CLI 和五臂 production-surface 显式固定 `R2_ROOT / R2_TRUSTED_SOURCE_ROOT / R2_DISPOSITION_ROOT`，不读取 r3 defaults，也不把旧 artifact 重解释为 v2。
- production materializer 的 lock/target/source/disposition 单调预置为 r3；invalid r1、validated r2、r2 source 与 r2 disposition 全部作为 exact immutable predecessor 保留。新 disposition/v2 仅构造 r1 → r2 → r3 的 closed successor preview，不发布外部对象。
- Fresh RED 为 `21 failed / 0 passed`；首批实现后 `21 passed`，扩展 root canonical/no-symlink、group/world write、全 dependency content/mode/rename、actual 1648-entry closure、r3 schema/stale authority 与 successor disposition 反例后继续全绿。
- actual r2 仍使用 capsule/v1，expected `ed26993a…caf5`、current `2e5d0b51…2cd8`，明确保持 `runtime-capsule-drift`；本批次不能把 Task2 authority 或 Task4 表述为 bound/ready。
- 最终 focused runtime/materializer/arms 回归为 `198 passed / 1 existing system-outside skip`；完整项目回归在 frozen headless shell 与 macOS Seatbelt 环境下为 `4320 passed / 5 skipped / 0 failed`。浏览器实际启动路径未出现 keychain/popup 信号，system Chrome fallback 的独立回归继续证明 `--use-mock-keychain` 与 `--password-store=basic` 均存在。

**当前裁决：仍为 NO-GO。** 下一步必须在独立批次创建并复核 r3 source，再执行 r3 materialization、disposition 与 authority binding；本批次不得代替该流程。

## Fix Round 1 与浏览器启动器加固（2026-08-20）

- 修复基线严格固定为 `86b8a66341bfc21b8cf3a44ef74a691d465c6e3b`；本轮只聚合关闭三方终审反例，Task 4 仍为 NO-GO。
- 浏览器启动收口为单一 closed builder：优先选择显式路径且 exact SHA256 绑定的 Playwright `chromium_headless_shell`；该路径的 SHA 缺失或不匹配时立即 fail-closed，不回退到系统浏览器。
- 只在未配置 headless shell 时使用 exact SHA256 绑定的系统 Chrome；macOS fallback 强制包含 `--use-mock-keychain` 与 `--password-store=basic`，且始终使用独立临时 profile。
- direct CLI 与 Playwright adapter 共用同一组 closed safety arguments；`benefit_benchmark` / fixture / materializer / arms 路径穷举未发现第三条浏览器启动路径。
- Fresh RED：3 failed / 0 passed（builder 缺失两项，system Chrome 缺 `--use-mock-keychain` 一项）。Targeted GREEN：4 passed in 2.32s（headless shell 优先、system fallback 安全参数、不完整 result fail-closed、Playwright 参数同源）。
- 真实 frozen headless shell smoke：1 passed in 2.81s；真实 system Chrome fallback smoke：1 passed in 31.28s。未修改/重置 keychain，未终止用户 Chrome。
- 格式化前分层门禁：browser targeted `4 passed`，arms + FixR1 `84 passed / 1 system-outside skip`，core + fixtures + materializer + CLI `565 passed / 1 skip`，full benefit `633 passed / 2 skip`，全部 `0 failed`。
- Ruff check 通过；Ruff format 首次如实报告 8 个本轮变更文件待格式化，执行机械格式化后 `9 files already formatted`；格式化后 browser targeted 再次 `4 passed in 2.15s`，arms + FixR1 再次 `84 passed / 1 skip in 80.34s`，`git diff --check` 通过。
- actual `benefit-evidence verify-sealed-commitments` 返回 `sealed-commitments / no-go`。追踪精确定位为 `runtime-capsule-drift`：1649 个 capsule entry 的数量、路径与文件内容均一致，唯一差异是外部 Python runtime 根目录 `.` 的 `ctime_ns/mtime_ns` 由 `1784432700565526991` 漂移为 `1787219031950878446`；expected capsule 为 `ed26993aef508d2361d9c772b91fa2a9a691262e363d6328168b957eef07caf5`，current 为 `2e5d0b51ad460f5f3ccc3e64638723727f99b47c140cd0c64df18e7ffe152cd8`。
- 因此本次只能是独立 checkpoint：Task3 candidate / Task2 authority / Task4 均为 **NO-GO**，不得声称 bound 或 complete。本轮未回拨外部 runtime 时间戳，未弱化 capsule 算法，未重签/重物化 Task2 authority。capsule-v2 + r3 属于后续独立流程。
- tracked `protocol.json`、`sealed-commitments.json` 与 Task2 report 相对 FixR1 基线 byte-unchanged；Provider / `codex exec` / formal authorization / ledger / results / experiment = 0。

> 以下内容保留原 Task3 commit 的历史冻结记录；其中“Task2 authority bound”与“最终验证”只描述 `86b8a663...` 当时状态，已被上述 FixR1 actual NO-GO 结果覆盖。

## 冻结边界

- Task 3 基线：`d8404e09b200b26b39184e321a467b055edf2797`。
- 只实现 `P / S / A00 / A10 / A11` 环境、纯命令构造、静态/系统隔离证明、fake-only callback 状态机与 authorization v2 合同；未进入 Task 4 rehearsal 或 Task 5 正式矩阵。
- Provider、`codex exec`、正式 authorization、attempt ledger、result、experiment arm 调用/生成均为 `0`。
- tracked `protocol.json`、`sealed-commitments.json` 与 Task 2 report 相对基线 byte-unchanged；正式 r2/r1/legacy/source/disposition authority 只读复核，未写入。

## Fresh RED 与关闭结果

首个 RED 在 `ai_sdlc.benefit_benchmark_arms` 尚不存在时于 collection 失败。随后逐批加入并关闭以下反例：

1. closed manifest 缺失、额外字段、SHA 漂移、vendor reference 漂移；
2. destination 复用、嵌套 symlink、hardlink、gitfile/伪 `.git`、跨 workspace inode 复用；
3. P/S/A 方法论互染、prepare 后 global skill 污染、额外 `AGENTS*`、额外 method namespace、S skill tree 自修改；
4. repo-root CWD、reservation arm 漂移、隐式 model、reasoning effort、JSON、ephemeral、sandbox、`--add-dir`、network capability 漂移；
5. sealed/control/Git/raw/other/template/runtime/method-instruction 直接、链接与写入绕过；
6. A11 Cross-risk 越界、第三角色、重复 child、parent mutation、replacement writer、early Close、schema、timeout、冲突、缺 Primary、无效 rereview snapshot/digest；
7. authorization v2 protocol/commit/arm/envelope/adaptation/preflight/identity/budget/scope/time/closed-field/mode/link metadata 漂移，以及旧 v1 formal scope；
8. Task 3 任何 `Popen`/Provider launch、ledger/result/formal authorization 生成的 explode guard。

最终 focused arms 为 `60 passed / 1 nested-sandbox skip`；唯一 skip 已在 macOS 原生 Seatbelt 下单独实跑通过。

## 五臂公平合同

- 所有臂使用相同三套 public fixture bytes、相同 `benchmark-task/` 相对 Provider CWD、相同 intent/approval client 和相同 Codex base/global capability envelope。
- 15 个 fresh workspace 均为 mode `0700`、真实内置 `.git/`、恰好一个 root commit；public input 与 Git/inode 均跨 run 隔离。
- P 只有 method-neutral common agent contract；没有 `.ai-sdlc`、repo skill 或其他 method namespace。
- S 只有冻结 Superpowers 适配与 common contract；`multi_agent=false`，prompt 显式激活 `$using-superpowers`，运行 capability envelope 同时关闭 `multi_agent` 与 `multi_agent_v2`。
- A00/A10/A11 从 exact AI-SDLC v2.0.0 Git object 构造共享只读 runtime；每次 A prepare 都实际执行 zero-provider `init`。A00/A10 只有 exact nested allowlisted ablation overlay，A11 无 override。
- 三种 A 臂都从同一 public contract 构造 closed canonical pre-state；递归 semantic view 完全相同，不新增答案、风险、验收或实现提示。frontend source digest 在 approval 前冻结，状态保持 pending。

15-workspace 测试实际完成 15 次 prepare，其中 A* 共 9 次 real init；Provider attempt 仍为 0。

## Superpowers v6.3.0 provenance

本地最初不存在所需 Git object，因此严格只执行一次官方 upstream 的 exact tag fetch 到隔离临时仓：

- source：`https://github.com/obra/superpowers.git`
- tag：`v6.3.0`，annotated tag object `86babb696875227929e85420f287d6309374b93f`
- peeled commit：`b36e0829c6d0140e93cfef2ca599b1b07d4a7797`
- source tree：`21219529a4e224bcb27baf8816b039c8bf7c6673`
- full archive SHA256：`8d795dfb2141e467bdf448474fd9acfa97dffa4da5837f0f6cf0dc2c290640ba`
- `skills/** + LICENSE` closure archive SHA256：`f1a01aecbbaa8093208760af8dbc585012e48328ea72211c6eb7b4840793f278`
- LICENSE SHA256：`a37e0e9697144819e1d965176ac4ae5bc3fa02d11e7812036bbcadf6dafe2400`
- vendor closure：51 个 regular file，无 symlink；每个文件绑定 mode、upstream SHA、adapted SHA 与 rewrite count。
- 唯一内容改写：26 处 `superpowers:<name>` → `$<name>`；validator 反向重建 upstream bytes 和完整 unified diff，证明不存在第二类改写。

旧的本机 5.x cache、相邻 tag 或近似版本均未使用。

## Codex 0.147 actual inventory

在每个 fresh external `HOME` / `CODEX_HOME` 下实际执行纯本地命令：version、`exec --help`、`features list`、plugin JSON、MCP JSON 与 `debug prompt-input`。`debug prompt-input` 在 0.147 可用且成功；没有伪造 actual proof。

- CLI：`codex-cli 0.147.0`
- entrypoint SHA256：`134063e133f0b4244fa3b251acf973d4fe4b4aeeacbdc135211bf480f59f1477`
- native binary SHA256：`19c4f144c5226a9f17c58e6f0fa854843b0f77a6eb420f40e2745a12f10f5d37`
- global builtin skills：`imagegen / openai-docs / plugin-creator / skill-creator / skill-installer`
- installed plugins/apps/MCP/global config rules：空；五臂 base/global digest 完全相同。
- 13 个 actual supported feature 均实测为 false：apps、browser/computer use、MCP apps、image generation、multi-agent、plugins、remote plugin 与 web search surfaces。
- writer 命令只构造为 exact `--ephemeral --json --ignore-user-config --ignore-rules --strict-config --model gpt-5.6-sol -c model_reasoning_effort="high" --sandbox workspace-write -C <run>/benchmark-task -`；expert variant 只改 `read-only + output-schema`。构造函数没有 launch API。

## A11 bounded review bridge

Task 3 只实现 fake-only、hash-bound state model，不启动真实 expert/Codex：

- Primary 覆盖三 fixture；Cross-risk 只允许 security fixture；最多两个不同角色，child session 唯一。
- expert 输入绑定 frozen snapshot 与 parent tree before/after；只接受 closed Findings。
- Findings 必须由仍存活的原 writer 修复并形成新 candidate digest，再由 fresh read-only child rereview。
- 只有 Primary 存在、无 timeout/schema/parent mutation/conflict/expert failure、修复与 rereview闭合后，原 writer 才能用 expected review digest Close。
- replacement writer、runner-owned/early Close、互斥 Findings、无效 rereview 一律 terminal NO-Close。

真实 callback 进程、JSONL/argv/exit 持久化属于 Task 4/5 runner，不在 Task 3 冒充已执行证据。

## Authorization v2 与旧 v1 退役

- 新 closed v2 绑定 canonical protocol、execution commit、arm manifest、neutral envelope、Superpowers adaptation、Task 4 preflight receipt、完整 execution identity、`33/19/4/3/7` budget、exact 15 run IDs、七项 operation 与 UTC validity window。
- authorization leaf 复用 Task 2 的 owner/mode/nlink/no-follow/pinned-read 安全读取；preflight receipt 也要求 owner-bound regular、`nlink=1`、stable read。
- 旧 v1 `single-frozen-matrix` 明确拒绝；v1 synthetic unit authorization 永远不能把 CLI 的 execution/provider/experiment 标记为 true。正式标记只接受完整通过 v2 gate 的文件。
- Task 3 只在 `tmp_path` 构造 synthetic v2 authorization 与 synthetic preflight 进行测试；仓库和外部控制面均未生成 formal authorization。

## Seatbelt 与方法不可变性

Task 2 strong profile 被复用并扩展到 exact control gitfile/gitdir/common Git dir、arms source、所有旧/新 sealed/source/disposition/raw/other/template surface、共享 runtime write deny、method file 与 method directory write deny。

macOS system-outside 实跑证明：

- sealed direct/parent/symlink/hardlink/environment/other-run/`--add-dir` 均拒绝；
- AGENTS、intent client namespace、S skill namespace与共享 runtime 均只读；
- Provider CWD 内普通交付文件仍可写；
- before/after method tree digest 保持一致。

该批次为 `1 passed / 0 skipped`；没有用 sandbox 文本检查代替真实系统拒绝。

## 原 Task 3 冻结时的 Task 2 authority（历史记录）

只读复核：

- actual r2：inode `403098441`，tree `b5b2b362952d00ab264f3fcef31312bdcd62c3775c047789a6a72f390be8615b`
- invalid r1：inode `402612600`，tree `9701e5fa4ebc55aeb2911d8eee8c97af9b618a9bfeac48ac8d9bfcfa8144dc30`
- legacy：inode `400173643`，tree `ee98e4d0b9f15e9937d252ff8a4cc3f9f1154eb3c7a567a6c4a258fa8e7910c2`
- prior source：inode `401826240`，tree `120669f885ca1791a54788c8f72a39cf076026921ebbe4221075a5914eef27a2`
- r2 source：inode `403084506`，tree `56387824d09679eaf2bca31e7afa62512cc3678dcb14d4add25eb4494afd9596`
- disposition：inode `403084461`，tree `527973568de93b07c65c2ab08bcbca8709c98424eb1b033e546f35cde46544ae`
- tracked protocol SHA256：`4f402736450a893b339b3f99faa3c71c1b8d3f5517d0bebb6bdaf03042179572`
- runtime identity/capsule commitments：`4e52fbf6…87a2` / `ed26993a…caf5`

`benefit-evidence verify-sealed-commitments` 返回 `authority=task2-commitment / status=bound / provider=false / experiment=false`；默认 protocol validate 返回 Task2 bound，但 execution/provider/experiment 均为 false。

## 原 Task 3 最终验证（历史记录）

```text
arms focused: 60 passed, 1 nested-sandbox skip
system-outside Seatbelt: 1 passed, 0 skipped
all benefit benchmark + fixture + arms + sealed materializer: 595 passed, 12 skipped
Ruff: All checks passed
git diff --check: clean
Task2 tracked fingerprints: unchanged
Provider / codex exec / formal auth / ledger / results / experiment: 0
```

12 个全量 skip 均是既有需要 macOS system-outside 或专用外部 runtime/browser 的门禁；Task 3 新增的唯一 system-outside 项已单独实跑，无 skip。本报告不把 preparation、fake callback 或 evaluator 健康证明表述为正式效益数据。

## Capsule-v2 FixR1：exact r2 predecessor 与动态 runtime 权限证明

- production capture 不再接受 pending protocol；只接受 tracked protocol SHA256 `4f402736…9572`、sealed authority SHA256 `e364aa7a…4ec9`、fixture pair `3a5a2a09…ff8a`、evidence pair `7b32d614…060c` 的 exact bound r2 predecessor。pending、partial、四字段任一错误、pair mismatch、protocol/authority 漂移及 already-r3 均在 source read/target write 前拒绝。
- 正式参数升级为 `expected_predecessor_r2_tree_sha256`，CLI 只暴露 `--expected-predecessor-r2-tree-sha256`；旧参数不兼容。r2 predecessor 对应 exact `R2_ROOT` identity/tree；legacy inode/tree 继续由 production policy 独立 hard-bound。
- final isolation attestation 在相同 exact Seatbelt profile 下动态证明 runtime capsule read 与 launcher exec 允许，append/create/chmod/rename 拒绝。六项结果、完整操作 transcript digest 与 profile digest 均写入 closed checks；规则未执行、伪 transcript、false result、permissive profile 和 cleanup failure 全部 NO-GO。
- write canary 位于 private sibling root，root/原文件均绑定 device/inode/owner/mode/content；创建中失败和最终成功/失败均执行安全清理，cleanup failure 独立 fail-closed。每次 launch 复验 runtime root、launcher、read target 与 canary identity。
- system-outside actual final profile与temporary publication：`2 passed / 0 skipped`；完整 benefit suite：`655 passed / 2 skipped / 0 failed`；完整项目：`4339 passed / 5 skipped / 0 failed`；changed-files Ruff、format check、`git diff --check` 全绿。
- 本轮仍未创建/物化 r3 source、disposition 或 target；Provider、`codex exec`、formal authorization、ledger、results、experiment 均为 `0`。Task 3 candidate/authority 与 Task 4 继续 NO-GO，等待独立 review 和后续 r3 materialization/binding。

FixR2 further closes the runtime proof window: the temporary canary now captures its minimal identity through a pinned parent dirfd immediately after `mkdtemp`, so chmod/first-lstat/directory-open failures are residue-free and a replacement is never deleted. After every generic/runtime probe and successful canary cleanup, runtime identity plus the full capsule-v2 manifest are reloaded and compared byte-semantically to the frozen pre-state before attestation serialization; drift in any non-selected stdlib/libpython/other entry returns `runtime-capsule-drift`. Fresh RED was `4 failed / 2 passed`, then GREEN `6 passed`; focused final was `101 passed / 4 skipped`, and system-outside final profile/publication was `2 passed / 0 skipped`. The prior full-project `4339 passed / 5 skipped` remains the unchanged-base full gate; FixR2 touched only the focused materializer path and its tests.

FixR3 removes the remaining path-resolution window from runtime canary creation. The private parent, random canary basename, root directory and all three probe files are now pinned and operated through directory descriptors: `mkdirat`-equivalent creation, `openat(O_DIRECTORY|O_NOFOLLOW)`, `fstat`, `fchmod`, and `openat(O_CREAT|O_EXCL|O_NOFOLLOW, 0600)`. Parent and child lexical bindings are rechecked against the pinned descriptors before permission change, before every file creation, and immediately before return. Partial cleanup removes only regular files reached from the original root descriptor and removes the basename only when its no-follow identity still matches; a directory replacement or same-name symlink therefore yields `runtime-canary-cleanup` without touching the replacement or symlink victim.

Fresh FixR3 RED was `8 failed / 105 deselected`; GREEN expanded to `16 passed / 98 deselected`, covering first dirfd stat/open failure, parent replacement before and after root acquisition, child rename/replacement, same-name symlink victim preservation, root identity at return and the retained full post-probe capsule equality. The exact system-outside final profile plus post-publication canary passed `2 passed / 0 skipped in 4.73s`. A first broad run inside the restricted app sandbox correctly failed for environmental reasons (local socket denied and the app's new standalone Codex path did not satisfy the frozen 0.147 npm layout); no product fix was made for that drift. Re-running the complete benefit suite system-outside with the already frozen Codex 0.147 path passed `670 passed / 2 skipped / 0 failed in 557.42s`; observed system Chrome fallbacks contained both `--use-mock-keychain` and `--password-store=basic`, with no keychain or popup signal.

Ruff check, Ruff format check and `git diff --check` pass. Actual r2/r1/legacy/source-r2/disposition-r2 inode/tree fingerprints remain exact, tracked protocol and sealed commitments remain `4f402736…9572` and `e364aa7a…4ec9`, and r3 target/source/disposition remain absent. Provider, `codex exec`, formal authorization, ledger, results and experiment execution remain `0`; Task3 authority and Task4 remain **NO-GO** pending the separately reviewed r3 materialization/binding flow.

## 2026-08-20 轻量方向性实验（替代正式审计矩阵）

审计级 runtime-canary authority 的未完成 WIP 已完整封存在独立分支 `codex/benefit-audit-wip`，commit `fccdbb262b87529d10f5fc0eb461e86dd54df909`。该 commit 明确标记 incomplete / NO-GO；产品分支在 exact clean `b9bd1c31fd2b1aa405e234d6ecabe0eb7c7e2d96` 上重新开始本轮工作，没有丢弃或混入 WIP。

本轮新增与正式 benchmark authority 完全分离的 `ai-sdlc-v2-directional-manifest/v1`：

- exact 5 arms × 3 fixtures × 1 run = 15 个 opaque Writer run；三个 task block 使用不同臂顺序。
- exact 19 Provider session table：15 Writer + A11 Primary × 3 + security Cross-risk × 1；第 20 次预约、技术重试、复审、额外专家、乱序和重复预约均在 append / launch 前拒绝。
- A11 fake rehearsal 记录 4 个只读、Findings-only expert event 与 3 个 original-writer same-live-session resume event；不启动 expert、subagent 或 Provider。candidate write、child subprocess、retry 与 Finding 缺失均 fail closed。
- 最小 attempt ledger 以 owner-bound、`0600`、`nlink=1` 的 pinned descriptor 读取和加锁追加；短写循环补齐，corrupt / duplicate / overwrite / symlink 均在新增 event 前拒绝。
- 模型固定 `gpt-5.6-sol` / `high`，Writer / Expert 输出上限固定 1800 / 900；同 fixture Prompt 跨五臂相同。
- 每个单元都真实准备为 fresh 0700 single-root Git；provider cwd 为 exact `benchmark-task`；HOME / CODEX_HOME / Git global / tool-plugin inventory 冻结且跨臂一致。
- Provider deny-read / write surface覆盖 actual r1/r2/r3/source/rubric/results/control、用户 `.codex`、audit WIP ref、run `.git` 与其他 14 个 run root。系统外 exact Seatbelt canary 实证 direct/list/parent escape/environment/`--add-dir` 和 output append/create/rename、method chmod 全部被拒绝，residue 为 0。

指标 schema 只接受外部交付、加权验收覆盖、严重缺陷逃逸、单调耗时、会话数，以及 Provider 权威 Token / currency cost；后两类缺失时必须为 `null`。模型级超时、非零和无效输出是单元终态并继续；Provider / network / rate-limit / host / isolation / ledger 异常中止全矩阵 incomplete。15 个 raw receipt 不完整时不发布完整结果或赢家。

展示数据合同只将 P / S / A11 作为首页产品路径，A00 / A10 标记为 research controls；必须展示 raw paired values、losses 和 quality-cost frontier，禁止 winner cherry-pick。所有摘要携带 exact 六条限制：`directional engineering observation`、`n=3 per arm`、`single run per task`、`not statistically significant`、`not production SLA`、`no generalization`。

### 本轮验证

```text
Fresh RED: 35 failed
Focused unit GREEN: 49 passed
Directional unit + 15-workspace rehearsal + isolation: 51 passed; prepared=15; simulated sessions=19; Provider=0
System-outside exact Seatbelt final: 1 passed, 0 skipped
Core benchmark/arms/directional: 504 passed, 1 existing nested-sandbox skip
Full benefit suite (system-outside): 718 passed, 2 skipped, 1 transient external Git-ref race
Transient failed test isolated rerun: 1 passed
Ruff check: All checks passed
Ruff format --check: clean
git diff --check: clean
```

完整系统外集合唯一失败发生在扫描 Codex 自身短生命周期 `refs/codex/turn-diffs` 时，该 ref 被外部 Codex 进程并发删除；同一定点测试随后通过。它不在方向性 runner 或 candidate 范围内，没有为此扩大产品修改。浏览器 fallback 全程带 `--use-mock-keychain` 与 `--password-store=basic`，无钥匙串或弹窗信号。

actual fingerprints 保持 exact：r2 `403098441 / b5b2b362…615b`，r1 `402612600 / 9701e5fa…dc30`，legacy `400173643 / ee98e4d0…10c2`，source-r2 `403084506 / 56387824…9596`，disposition-r2 `403084461 / 52797356…15e`。tracked protocol 与 sealed commitments blob 分别保持 `f16afc65…aa3c` / `d2ef5e51…101c`，与 base 完全一致。

本轮没有创建 formal authorization、formal ledger、formal results、r3 target/source/disposition，也没有运行 `codex exec` 或任何 Provider。fake rehearsal 的 token / currency cost 均为 `null`。正式矩阵仍为 **NO-GO**，等待用户对 `model=gpt-5.6-sol`、`effort=high`、`15 writer + 4 expert = 19`、`technical retries=0`、成本未知边界作一次最终预算确认。

### 轻量方向性实验 FixR1：可执行隔离与唯一启动门

- 修复 directional profile 的动态重建：absent read/write rules、冻结 Provider executable deny、network deny 均成为 typed profile 字段，refresh 后逐字节相同；不再通过 `replace(sandbox_text=...)` 制造必然 `ISOLATION_REFUSED` 的假隔离证明。
- system-outside 实证 `/usr/bin/true` 和候选输入读取返回 `0`；所有 deny 只接受真实 Seatbelt `Operation not permitted`，不再把 `ISOLATION_REFUSED` 或任意 nonzero 当成成功。
- protected roots 扩展为整个 main common Git 与 `.worktrees` 父面；control runner、所有 sibling worktree、common objects/refs/config 均不可读写。
- frozen Codex entrypoint/native binary 与 PATH 中已存在的等价 Provider/agent launcher 全部 process-exec deny，同时 network surface deny；直接执行及 `/bin/sh` 间接执行均由系统拒绝，普通 `/usr/bin/true`、`cat` 等本地工具保持可用。
- `launch_directional_provider_session` 是唯一 cap-gated Provider 入口：在同一 locked append 中写入 `reservation → launch-started`，执行后原子追加 `launch-completed` 或 `launch-failed`。reservation-only ledger、任意非冻结 Provider 命令、重复/乱序、未完成前序和第 20 次尝试均在 launch 前拒绝。

验证结果：fresh RED 为 unit `3 failed / 48 deselected` 加 system-outside `1 failed`（精确复现 `/usr/bin/true = 126 / ISOLATION_REFUSED`）；最终轻量集合 system-outside `55 passed`，共享 fixture isolation 定向回归 `13 passed / 3 existing system skips`。Ruff 与 diff 门禁见本提交最终记录。全程 Provider、`codex exec`、formal authorization/result 与正式 experiment 仍为 `0`。
