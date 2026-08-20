# Task 3：五臂环境、指令隔离与零 Provider 执行边界

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

## Task 2 authority 未回归

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

## 最终验证

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
