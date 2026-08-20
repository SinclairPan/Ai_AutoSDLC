# Task 2 Fix Round 1：三套公开 Fixture 与密封外部评估门禁

## 冻结边界

- Fix 基线：`51f60fd654ca0159c5705bcfca04ed86bf4b455a`。
- 范围仅限计划 Task 2；未进入 Task 3 arms、runner、网站、summary 或正式实验。
- Provider、`codex exec`、正式实验调用均为 `0`。
- tracked `protocol.json` 未修改，四个 paired commitment 继续保持 `pending-unbound`。

## Fresh RED 与聚合修复

Fix Round 1 先新增七组独立 RED，真实运行结果为 `7 failed`：

1. Requirement 非空占位可得分；
2. intent map 未独立 materialize；
3. T2 缺少六个 root-cause 行为 Oracle 与 Finding 混淆矩阵；
4. Frontend 缺真实 lock/preinstall/browser E2E；
5. canonical pre-state 未递归 closed；
6. leak scanner 未覆盖 payload inventory、binary、path name 与 Git objects；
7. isolation 未显式覆盖 raw results、source Git、其他 protected root 与动态 launch refusal。

第八组“两次 fresh prepare/visible/sealed 完全一致”沿用并加强原有三 fixture 参数化测试：现在每套 fixture 都从两个全新目录独立 prepare，再比较 visible results 和 evaluator result。

统一 GREEN 后又执行两项针对最终实现的 fresh RED：一项模拟 sealed inventory 读取错误，确认 leak scanner 必须返回 fail-closed finding；另一项验证 evaluator 的候选执行也必须经过动态隔离启动器，而不是直接调用一次性 Seatbelt profile。修复前为 `2 failed`，修复后为 `2 passed`。

最终逐条回看冻结 spec 时又加入一项 held-out 边界 RED：public fixture 中不得出现外部浏览器 harness 或四类 held-out marker。修复前为 `1 failed`；将 harness 改由 evaluator 在候选根外运行时 materialize 后为 `1 passed`。

## 关闭后的执行合同

### Requirement

- v2 sealed payload 是 closed object；criteria 也是按 kind closed。
- 只接受 `json_literal`、`json_enum`、`json_set_contains`、`json_relation`、`json_no_contradiction`、`verification_command` 六类结构化规则。
- 逐项覆盖冻结 intent、版本 guard、终态、事务/通知关系、显式 blocker 与可复算命令。
- 错误但非空的合同覆盖率为 0，`external_verified_delivery=false`。

### 独立 intent / approval service

- sealed manifest v2 必须单独 commitment `intent-map.json`；tracked 文件只保存 SHA，不保存路径或答案。
- `FrozenIntentApprovalService.from_sealed_root` 直接校验 manifest/path/SHA 后加载。
- question item 与 approval surface closed；答案可为结构化值；未知问题仍为 `unresolved`。
- proposal digest 仍需 controller 先注册；不匹配、零 digest 或未知 approval type 均返回 `revise`。
- 事件只记录 automated actor 与 result digest，不计为 human work。

### T2 六根因行为 Oracle

- v2 T2 禁止 `file_contains` / 源码注释参与得分，所有 criteria 必须是隔离子进程中的 `security_oracle`。
- 六个独立 root cause：tenant isolation、separation of duties、request lifecycle、role allowlist、action allowlist、atomic audit。
- 覆盖 self-approval、non-pending、expired、unknown role、unknown action、audit failure、authorized audit shape 与拒绝无副作用。
- evaluator 输出 Finding→root 的 TP/FP/FN、precision、recall、severe miss；没有 Finding 时 precision 保持 N/A，而不是伪造 0。

### 真实 Frontend 工程与浏览器证据

- 公开 fixture 现在含真实 `package.json`、208 KB lockfile、Vite/Vue 配置、ESLint、Prettier、TypeScript 和浏览器 harness。
- lockfile SHA：`74eb5f6fbf5e3a8fb828125ba8456ebd61cdc647c3513be3b107ec5b6bf05002`。
- frozen preinstalled dependency tree SHA：`0ad5e7b7fab55b73cbeaef8bf218e5f9fd9f97ac03f587a88e5cb1dc760403a6`。
- Node 与 Chrome 同时绑定版本和 executable identity SHA。
- `validate_frontend_runtime` 实测校验 lockfile、dependency tree、Node 和 browser executable。
- Playwright 启动真实 Chrome，经 loopback HTTP 同源页面运行 normal、failure→recovery、连续失败→恢复、delayed race、rapid double click、malformed response。
- held-out 浏览器 harness 由外部 evaluator 在运行时 materialize；public candidate root 不含 harness 或四类 held-out marker，Provider 不能修改验收页伪造通过。
- AC-001 字段呈现、AC-002 筛选、AC-006 console/basic a11y 均来自浏览器行为，不从源码 token 得分。
- 同一预装依赖树实际执行 `lint`、`format:check`、`build` 均 exit 0。
- public `program-manifest.json` 只描述共同 solution target，不包含 arm ID、treatment 或 A-arm confirmation state；pending 状态留给 Task 3 runner 的不可见运行合同。

### Closed parity、泄漏与隔离

- 三 fixture 的 semantic surface 逐层 closed；任何额外 `answer`、`risk`、`implementation_hint` 或未声明 AC 字段均拒绝。
- normalized parity 比较 fixture/stage/pre-state/solution target/完整 semantics，不再只摘取 `semantics` 子树。
- leak inventory 从 evaluator root 内真实 manifest/payload/intent commitment 生成；扫描 path name、binary、symlink、hardlink、Git index/reflog/all objects。
- finding 只返回 opaque code 与 opaque location，不回显 sealed phrase、filename 或绝对路径。
- final isolation profile 显式包含 sealed root/parent、control、raw results、source Git、other run 和额外 protected roots。
- run/protected 嵌套、任意 outward symlink、任意 regular `st_nlink>1`、扫描错误、`--add-dir` 与 `--add-dir=...` 均 fail closed。
- `run_provider_isolated` 在每次子进程启动前重新扫描；direct/root reads 由 Seatbelt 实际拒绝，hardlink/env/add-dir 由同一 launch wrapper 以 exit 126 实际拒绝，不再用“参数不存在”代替证明。

## Tracked commitments

- public fixture + evidence-contract pair：`96c67f8be165b52b7471fa9955c36ecc62efe29361d8b4c0d2ba56cfa58d02e1`。
- evidence-contract template pair：`7b32d614533e4c51438415bbcbb9cc885177d0752b814d95c344c8925382060c`。
- independent intent-map commitment：`7b2eb34f26aba327a29f518ecd43902a921a34980c060eef6cfd60e746d31815`。
- evidence template 为 15 个 run 增加 requirement structured result、frontend browser/quality、security root-oracle/Finding metrics 槽位，仍通过 Task 1 closed consumer。
- protocol 仍为 pending；以上 tracked 值不得在 Task 2 三专家终审前绑定。

## 验证

嵌套 sandbox 内：

```text
fixture focused: 27 passed, 4 skipped
related benchmark/site regression: 494 passed, 4 skipped
Ruff: All checks passed
git diff --check: clean
```

四个 skip 均属于需要 macOS Seatbelt 或 loopback browser 的 exact system-outside 检查。同一 fixture test file 已在 sandbox 外全量实跑：`31 passed in 18.99s`，无 skip；其中包括六根因 Oracle、v2 browser evaluator、真实 Playwright+Chrome、held-out harness 隔离、逐 protected-root Seatbelt、动态 hardlink/env/add-dir launch refusal，以及候选 evaluator 复用动态隔离启动器。

Frontend 同一预装依赖树：

```text
lint exit=0
format:check exit=0
build exit=0; Vite 7.3.6; 201 modules transformed
```

这些是 fixture/evaluator 健康证据，不是 15-run 实验结果，不得用于网站效益结论。

## 当前唯一 blocker

安全审查拒绝覆盖旧 protected evaluator root，也拒绝在新的 sibling protected root 写入评分 plaintext，错误明确要求不得绕过。故 tracked v2 evaluator/runtime/tests 已完成，但新的 protected materialization 尚未落盘，旧 v1 sealed manifest/payload commitment 不能冒充 Fix Round 1 结果。

解除方式必须是：用户明确授权或受信任 materializer 向一个全新、空、候选不可读的 protected root 写入 `intent-map.json`、三个 v2 payload 与 sealed manifest；随后运行 `validate_sealed_commitments == []`、两次 fresh evaluator 完全一致、opaque leak scan 和 exact system-outside isolation canary。完成前 Task 2 不得宣称 execution-ready，protocol 必须保持 pending。

## Fix Round 2：受信任物化器

### 冻结范围

- Fix 基线：`196025b694ff49dd685c9d0a87ca0ccd834b459f`。
- Provider、`codex exec`、正式实验调用继续为 `0`；没有启动 Task 3 arms。
- 最终 r1 evaluator root 尚未物化；旧 root 未修改；tracked protocol 四项仍为 `pending-unbound`，tracked commitments 未替换。
- tracked 代码只保存 closed schema、编译器、发布器和测试用合成 source；正式 sealed-source、真实答案、held-out plaintext 仍必须从仓外受保护文件或 FD 输入。

### Fresh RED 与关闭结果

Fix Round 2 依次运行了以下真实 RED，而不是仅以代码审阅代替失败证明：

1. 新模块尚不存在时，focused test collection 以 `ModuleNotFoundError` 失败；
2. 首版编译器运行至 `4 passed, 15 failed`，暴露 canonical key 排序、criterion path 类型和发布链未闭合；
3. 初次 focused GREEN 前为 `30 passed, 2 failed`，暴露 CLI 窄终端帮助截断和 sealed parent/candidate root 重叠；
4. 递归 evaluator 字段、repo-before-source、意外异常脱敏新增反例为 `5 failed`，逐项关闭；
5. pinned-parent staging 与父目录替换竞态新增反例为 `2 failed`，逐项关闭；
6. FD 指向 tracked repo source 的别名反例为 `1 failed`，通过 descriptor canonical path 识别并关闭；
7. 六根因 Oracle 的根因错配、拒绝后状态错配和非法时间新增反例为 `3 failed`，通过 root-specific 行为前置条件、状态无副作用和 timezone-aware 时间校验关闭。

最终 focused 结果为 `43 passed, 1 skipped`；唯一 skip 是嵌套 sandbox 不能再次应用 macOS Seatbelt，随后已在 system-outside 验证中实跑通过。

### 受信任输入与 closed compiler

- CLI 只接受互斥的受保护 path/FD、调用时冻结的 source SHA256、exact HEAD、固定 lock id 和旧 root tree SHA256；最终 target 是代码内不可覆写 literal。
- source leaf 必须 `O_NOFOLLOW` 打开，并在读取前后同时满足 regular、owner=euid、mode `0600`、`nlink=1`、inode/size/mtime 稳定；FD 也必须能解析到 canonical path，不能借 FD 绕过 repo/protected overlap 检查。
- source 必须是无尾随换行的 canonical JSON；拒绝 NaN/Infinity、未知 top-level、payload、criterion 和递归 scenario/expected 字段。
- 编译器生成独立 `intent-map.json`、三个 v2 sealed payload、closed manifest、candidate commitments 和 materialization receipt。receipt 绑定 source HEAD/tree、materializer bytes、fixture manifest/tree、evidence contract、source bundle、target lock、manifest、intent 和三个 payload SHA。
- CLI 对预期与非预期错误都只输出稳定 NO-GO code，不回显 source path、plaintext 或内部异常。

### 起止门禁与原子发布

- 读取 sealed source 前先断言 exact HEAD、完整 tree clean、protocol 四项 pending、Provider ledger/results 不存在；编译后、发布前和发布后重复断言同一绑定。
- 旧 root 使用本物化器定义的 canonical tree 算法复算：按 relative path 排序，绑定 type/mode/size，以及 regular content SHA 或 symlink target；同时冻结 inode。当前只读 fingerprint 为 inode `400173643`、tree SHA256 `2fb91cccb95b3bee4168041d75bda1ba567eb2f701bff94cf1107fa793283370`。该值不替代调用时 fresh fingerprint。
- target ancestor 逐级 `lstat`，拒绝 symlink、非 euid owner、group/world writable 和跨 device；parent 以 dirfd 固定并在关键边界重验 lexical inode。
- staging 和 quarantine 都以 pinned parent dirfd、随机 nonce 和 `mkdirat` 等价安全语义创建为 `0700`；文件以 `openat(O_CREAT|O_EXCL|O_NOFOLLOW)` 创建为 `0600`，short write 循环写完，逐文件与目录 `fsync`。
- 发布只使用 macOS `renameatx_np(..., RENAME_EXCL)`；API 不可用即 fail closed，不回退到普通 replace。
- final root 通过 dirfd reopen，逐文件复核 owner/mode/nlink/content digest；失败前只删除本次 nonce staging，失败后仅在 target inode 仍匹配时移入 nonce quarantine 并清理。清理不完整返回 `cleanup-failed`，不得继续。

### 发布前独立验证

- validation scratch 使用独立 `protected/` 与 `runs/` 根，不与 target staging 共用；写入后的七个文件逐字节匹配 compiler output。
- intent service 实测 known/unknown question，以及 correct/wrong/zero/expired approval；所有事件 actor 都是 automated service。
- 三个 fixture 各自从两套 fresh copy prepare；public tree、initial commit、visible results 和 sealed evaluation 完全一致；baseline 均保持 non-delivery。
- 真实 payload-derived opaque inventory 对两套 candidate 分别执行 path/text/binary/link/Git object leakage scan。

### 验证证据

嵌套 sandbox：

```text
materializer focused: 43 passed, 1 skipped
related benchmark/fixture/materializer/site: 537 passed, 5 skipped
Ruff: All checks passed
git diff --check: clean
```

system-outside 使用冻结 dependency tree、Playwright module 和真实 Chrome 运行 fixture + materializer 全套：

```text
75 passed in 42.66s
```

该批次为 `0 failed / 0 skipped`，覆盖 exact Seatbelt、真实 browser、两次 fresh scratch evaluator 和发布安全反例。它仍只是 evaluator/materializer 健康证据，不是 15-run 效益实验结果。

同一冻结 dependency tree 的 Node 回归再次实跑：

```text
npm run lint: exit 0
npm run format:check: exit 0
npm run build: exit 0; Vite 7.3.6; 201 modules transformed
```

### 当前门禁

Fix Round 2 的 tracked materializer 已具备复审候选条件，但当前仍不是 execution-ready：必须先完成独立复审并冻结本次 materializer commit。通过后由受信任控制方创建仓外 mode `0600` canonical sealed-source，fresh 读取 old-root fingerprint 与 source SHA，在 clean exact HEAD 上调用 hidden materialize CLI。物化成功后仍需单独验证新 root receipt/commitments；只有之后的父任务才可以决定是否绑定 tracked protocol。

## Fix Round 3：密封浏览器程序与发布后最终隔离证明

### 冻结范围与 RED

- Fix 基线：`7f3ac42d432b34bb0a3ab565e91ba0b78f8c52e4`。
- Provider、`codex exec`、正式实验继续为 `0`；未启动 Task 3 arms，未物化最终 r1，未绑定 tracked protocol，旧 root 未修改。
- 首批 fresh FixR3 定向测试结果为 `4 failed / 4 passed`：真实暴露 production browser harness 明文、可信 source literal/FD 边界、pending receipt 和 root metadata fingerprint 缺口；intent 缺失/额外反例已由已有 closed schema 提前拒绝。
- 其后补齐 source direct-child/mode、target exact mode、child identity/content/rename/owner drift、exact final profile、canary failure rollback、各 attestation write/fsync failure point和 CLI opaque success 反例，统一进入回归门禁。

### 密封 browser program

- tracked production source 已删除真实 held-out scenario 输入、顺序、通过 Oracle、旧固定浏览器 harness 和旧 frontend node adapter；production `src/ai_sdlc/**/*.py` 不再含六类真实场景标记或真实样例数据。
- tracked evaluator 只保留 closed data-only DSL 校验器与通用解释器。scenario loader outcome、confirmer、action 和 assertion 均是 closed object；未知 operation/kind、重复 id、空 handle 或开放字段全部拒绝。
- 正式 scenario definitions、inputs、Oracle 和顺序必须来自仓外 canonical sealed-source 的 `browser_program`；materializer 将其原样编译进 frontend sealed payload，evaluator 只从该 payload 执行。
- 通用解释器在真实 Chrome 中按 sealed action 顺序执行 load/retry/deferred resolve/await/render/checkpoint/confirm/release，并从 JSON state、DOM、console 和 basic a11y 行为计算 assertion；production source 不知道正式 scenario 名称或 pass 条件。

### 可信 source 与公开 intent taxonomy

- production source base 固定为 `/private/tmp/ai-sdlc-v2-benefit-source`，source root 固定为其直属 `sealed-source/`；base/root 必须为 euid owner、非 symlink、同 device、exact `0700`。
- CLI 只接受已打开的 `--sealed-source-fd`；source 必须是 source root 直属 regular `0600`、`nlink=1` 文件。任意 repo、candidate、run 或其他路径中的同内容文件都返回稳定 `source-security`。
- source root 使用与旧 root 相同的新 canonical identity tree fingerprint，在编译、发布前和发布后重复比对；receipt/candidate commitments 绑定 source-root tree SHA，final Provider profile自动把 source root加入 deny-read surface。
- requirement public service contract 增加 `approval_type=design-contract`；materializer 从 public requirement input 解析四个 question ID，并从 requirement/frontend service contracts 解析 `design-contract`、`frontend-solution`。source intent 空/额外/缺失/无关均拒绝。
- scratch 对每个 approval 分别注册并实测 correct、wrong、zero、expired proposal，另实测 unknown approval；question 实测 known/unknown。事件仍只允许 automated service actor。

### Identity tree 与原子两阶段成功

- tree fingerprint 现绑定 root 和每个 child 的 type/device/inode/uid/gid/mode/nlink/size；regular file 再绑定 content SHA，symlink 绑定 target。root/child chmod、模拟 chown、rename、write 均产生不同摘要。
- target protected parent 现在必须 exact `0700`，不再只拒绝 group/world write；pinned dirfd、same-device、exclusive rename、inode-bound quarantine 和逐 write/fsync failure injection 继续保留。
- compiler 生成的 `materialization-receipt.json` 只能声明 `published-pending-isolation` / `pending`，不得预称 validated。
- exclusive publish 与逐文件 postverify 完成后，materializer 才以**已发布的 exact final path**构建最终 Provider profile，显式覆盖 final root/parent、control repo、`.git`、trusted source root、raw results 和 other-run。
- 同一最终 launch profile真实执行 direct/parent/control/`.git`/results/other-run/env/symlink/hardlink/`--add-dir` 拒绝。任一失败按已发布 inode 移入 `0700` quarantine 并清理，整体返回 NO-GO。
- canary 全通过后，以 final root dirfd 和 `openat(O_CREAT|O_EXCL|O_NOFOLLOW)` 写入独立 closed `isolation-attestation.json`，逐文件/目录/parent fsync 后复读验证。CLI 成功只输出 `status/count/receipt_sha256`，不输出 path、inode 或 lock id；该 receipt SHA 指向最终 attestation，而 pending receipt 保留可审计的两阶段边界。

### FixR3 证据

当前 tracked public fixture pair 为：

```text
fixture tree / manifest: 3a5a2a09809c5c899324b8664bd9976c44ea818730cf5a6c2925989e92b4ff8a
manifest validation issues: []
protocol paired fields: pending-unbound (4/4)
final r1 root: absent
old root inode: 400173643
old root identity-tree SHA256: ee98e4d0b9f15e9937d252ff8a4cc3f9f1154eb3c7a567a6c4a258fa8e7910c2
```

嵌套 sandbox 最新门禁：

```text
fixture + materializer focused: 94 passed, 6 skipped
related benefit/fixture/materializer/site: 561 passed, 6 skipped
Ruff check: All checks passed
Ruff format: 5 files already formatted
git diff --check: clean
```

system-outside 使用同一冻结 Playwright module、真实 Chrome 和 macOS Seatbelt，完整 fixture/materializer 统一批次实跑 `100 passed / 0 skipped in 124.97s`；其中包括 post-publication exact-final-path materializer canary，未以静态 profile 文本或“文件不存在”代替真实拒绝。为保证两次 fresh baseline/evaluation 的可重复性，可见命令输出只对 Python unittest 自带的非确定性耗时字段做规范化，不改变退出码、测试计数、失败内容或业务结果。

同一预装依赖树的 frontend 回归：

```text
npm run lint: exit 0
npm run format:check: exit 0
npm run build: exit 0; Vite 7.3.6; 201 modules transformed
```

以上仍然只是 evaluator/materializer 健康证明，不是 Provider arms 的效益结果。FixR3 commit 通过独立复审、由父任务准备新的仓外正式 source 并实际物化 r1 之前，execution protocol 必须继续保持 pending。
