# 功能规格：Permanent Release Truth v1.0.4 Fresh Bootstrap

**功能编号**：`008-v1-0-4-permanent-release-truth`
**创建日期**：2026-08-08
**状态**：已批准
**前序裁决**：`005-permanent-release-truth = terminal NO-GO / bootstrap budget exhausted`

## 1. 背景与终止裁决

`v1.0.3` 的第二次且最后一次业务 bootstrap（GitHub Actions run `31252263430`）在 Proof 生成前失败。受保护 writer 已完成 12 格 Release Assurance、Windows shell、三个 Build Smoke Candidate 与 Proof Inputs，但发布上传清单的最后一行没有被 Bash `while read` 消费，只写入 5/6 个候选资产，缺失 `ai-sdlc-offline-1.0.3-windows-amd64.zip.sha256`。Live Draft 资产集合校验随后 fail closed。

因此：

- `005` 必须固定为 `terminal NO-GO / not released / bootstrap budget exhausted`；
- `v1.0.3` 必须固定为 `aborted / unpublished frozen residue`；
- Draft release `366658361`、annotated tag object `ff55bddae056fbe869cdb2a3844e872e17fb0798`、tag commit `fde2d5899bc0b976ea71ccae49b4adbf0c0f6537`、现有 5 个资产及两次运行证据不得修改、删除、补传、发布或复用；
- `v1.0.3` 没有 Proof、Publish CAS、immutable attestation 或 generation-0 Certificate，不得描述为已发布或可信 Permanent Release Truth。

本 WorkItem 是独立恢复代际，不继承 `v1.0.3` 的 attempt 资格、Draft authority、资产或证据。

## 2. 范围

### 2.1 覆盖范围

1. 持久化 `005` 的 terminal NO-GO 与 `v1.0.3` 冻结残留证据。
2. 修复发布资产清单“最后一行无尾换行导致未消费”的生产者和消费者边界。
3. 增加真实执行生产者到 Bash 消费者边界的回归测试，覆盖 6、1、0 个待上传资产和不一致拒绝。
4. 在上传后、Proof 前验证 6/6 精确资产集合，保持 Proof/CAS/immutable/Certificate 全部门槛不变。
5. 将所有公开版本、校验器、工作流、用户指南和发布文档真相同步迁移到 `1.0.4`。
6. 在受保护 `main` 的最终 writer SHA 上创建全新 annotated tag `v1.0.4` 与全新 0-asset Draft release。
7. 仅由受保护 `release-build` 为 `v1.0.4` 生成全新 3 OS × 4 Python Assurance、三个离线候选、三个 SHA256 sidecar、Proof、immutable attestation 与 generation-0 Certificate。

### 2.2 明确不覆盖

- 不修复、补齐、发布、删除或重建 `v1.0.3`。
- 不移动或复用 `v1.0.2`、`v1.0.3` tag。
- 不从 runs `31167911172`、`31252263430` 或 Actions artifact 中复用任何资产或证据。
- 不引入动态选测、Evidence Reuse、第四种离线工件、候选 verifier 自证或降低任何发布门槛。
- 不启动已分配的 WorkItem `007`；仅在 `v1.0.4` 发布闭环成功后进入。
- 不顺带扩大到在线/离线安装脚本的其他 EOF 循环；本项只修复 release writer 边界。

## 3. 用户故事与验收

### 用户故事 1：失败代际不可被洗白（P0）

作为发布治理者，我希望失败的 `v1.0.3` 代际被明确冻结，以便任何人都不能把残留资产误认为可信发行版。

**独立测试**：通过 GitHub Release API、tag peel 与仓库 incident record 对账。

1. **Given** `v1.0.3` 第二次 bootstrap 已失败，**When** 查询 release `366658361`，**Then** 它仍为 Draft、非 immutable、未发布，且 5 个资产原样保留。
2. **Given** 代码或文档提及 `v1.0.3`，**When** 做发布真相审计，**Then** 只能把它描述为 aborted/unpublished residue，不得描述为发布成功。

### 用户故事 2：上传清单逐项完整消费（P0）

作为受保护 release writer，我希望待上传清单的每一项都被消费，尤其是最后一项，以便 6 个候选资产不会因文本 EOF 语义静默丢失。

**独立测试**：真实生成清单并由 Bash loop 消费，比较输入和消费后的有序集合。

1. **Given** 6 个资产均缺失，**When** 生成并消费清单，**Then** 恰好消费 6 项且最后一项为 Windows sidecar。
2. **Given** 仅最后一个 sidecar 缺失，**When** 生成并消费清单，**Then** 恰好消费该 1 项。
3. **Given** 6 个资产均已存在且摘要/大小匹配，**When** 生成清单，**Then** 清单为空且不上传任何资产。
4. **Given** Draft 有意外资产或同名资产摘要/大小不匹配，**When** 规划上传，**Then** 在任何新上传前 fail closed。

### 用户故事 3：v1.0.4 全新发布证据（P0）

作为安装者和审计者，我希望 `v1.0.4` 的 tag、commit、tree、资产、Proof、attestation 与 Certificate 全部来自同一新代际，以便发布真相可独立复核。

**独立测试**：校验 release-build run、release API、Proof/CAS、attestation、Certificate 与三个离线工件 smoke。

1. **Given** 008 已合并到受保护 `main`，**When** 创建 `v1.0.4`，**Then** tag 和 0-asset Draft 都绑定精确 main SHA，且在 writer 上传前无其他 writer 修改。
2. **Given** `release-build` 全部门禁通过，**When** 发布 CAS 成功，**Then** release 为 non-draft、immutable，且恰有 3 工件 + 3 sidecar + Proof；Certificate 引用同一 release、tag、commit、tree、run 和 Proof。
3. **Given** 任一矩阵、smoke、集合、摘要、Proof、CAS、immutable 或 Certificate 检查失败，**When** 执行发布，**Then** fail closed，不得声明发布成功。

## 4. 功能需求

- **FR-001**：仓库必须包含 `v1.0.3` terminal NO-GO incident record，记录两次业务 bootstrap、精确 tag/release/run/writer/资产集合和禁止动作。
- **FR-002**：上传清单生产者必须为每个非空记录写入终止换行；空集合必须写为空文件。
- **FR-003**：Bash 消费者必须在输入缺少末尾换行时仍消费最后一个非空记录。
- **FR-004**：回归测试必须执行真实 producer→consumer 边界，不能只用字符串或 YAML 静态断言代替。
- **FR-005**：回归测试必须覆盖 6/6、仅最后 1 项、0 项、意外资产、摘要/大小不匹配。
- **FR-006**：上传后必须再次核对 live Draft 的精确 6 资产集合，Proof 仍必须绑定同一 `workflow_ref/run_id/run_attempt`。
- **FR-007**：所有产品版本真相面必须统一为 `1.0.4`，包括包元数据、双入口 `__version__`、工作流、校验器、README、用户指南、产品合同与发布清单。
- **FR-008**：`v1.0.4` 发布不得使用 `v1.0.3` 的任何资产、Proof Inputs、run evidence 或 Draft authority。
- **FR-009**：`v1.0.4` 只允许受保护 `release-build` 写入三个支持平台离线工件、三个 sidecar、Proof 和发布状态。
- **FR-010**：`v1.0.4` 每次 bootstrap 都必须重新运行完整固定矩阵，不得动态选测或 Evidence Reuse。
- **FR-011**：`v1.0.4` 最多允许两个业务 bootstrap attempts；每次 attempt 内的证据必须自洽，禁止跨 attempt 拼接。
- **FR-012**：任何闭环声明前必须通过本地只读对抗复核、GitHub Codex Review、全部 required checks 与发布后独立核验。

## 5. 关键实体

- **FrozenV103Residue**：`v1.0.3` 的 immutable audit residue；包含 tag object/commit、Draft release ID、5 个资产、两次 run 和终止原因，但不是发行制品。
- **UploadPlan**：从冻结的 Proof Inputs 候选集合与 live Draft 资产集合计算出的待上传有序清单。
- **V104ReleaseAuthority**：由唯一 exact-tag List Releases 解析并冻结的 numeric release ID 与 exact `uploads.github.com` upload URL。
- **V104GenerationEvidence**：同一 run attempt 的 Assurance、Build Smoke、Proof Inputs、6 assets、Proof、Publish CAS、immutable attestation 与 generation-0 Certificate。

## 6. 成功标准

- **SC-001**：producer→Bash consumer 测试对 6、1、0 个待上传资产分别消费 6、1、0 项；移除生产者尾换行或 EOF-safe consumer 任一保护都会使测试失败。
- **SC-002**：相关定向测试、Ruff、YAML parse、`git diff --check` 与 `uv run ai-sdlc verify constraints` 全部通过。
- **SC-003**：公开版本真相扫描不再把 `1.0.3` 描述为当前/已发布版本；其出现仅限历史 incident、兼容历史或明确冻结残留语境。
- **SC-004**：`v1.0.4` release 最终为 `draft=false`、`prerelease=false`、`immutable=true`、`published_at!=null`。
- **SC-005**：`v1.0.4` 恰有三个平台工件、三个匹配 sidecar 与一个 Proof，所有摘要、tag、commit、tree、run identity 和 Certificate 绑定一致。
- **SC-006**：008 完成后，005 仍为 terminal NO-GO，v1.0.3 residue 未被任何写操作改变，007 才可进入执行。

## 7. Stop-loss

- Draft 阶段只运行轻门，完整 3×4 Assurance 和 Windows shell 必须跳过；仅在 review 无可操作问题且 required checks 通过后进行一次 Draft→Ready。
- Ready 阶段同一最终 SHA 必须完成完整固定矩阵、Windows shell、required checks 后才能合并。
- `v1.0.4` 最多两个 fresh bootstrap attempts；失败时仅允许读取日志并修复代码进入新的受保护 main SHA，不得复用前一 attempt 证据。
- 两次后仍未形成 immutable release 时，本 WorkItem 同样终止 NO-GO，不得继续增加 attempt。
