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
