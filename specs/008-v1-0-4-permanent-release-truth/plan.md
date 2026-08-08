# 实施计划：Permanent Release Truth v1.0.4 Fresh Bootstrap

**编号**：`008-v1-0-4-permanent-release-truth` | **日期**：2026-08-08 | **规格**：`specs/008-v1-0-4-permanent-release-truth/spec.md`

## 概述

本计划先持久化 `005/v1.0.3` terminal NO-GO，再通过可执行 TDD 修复资产清单 EOF 边界，将全部版本真相迁移到 `1.0.4`，最后在受保护 main 上以全新 tag、全新 0-asset Draft 和全新固定矩阵证据完成发布。实现阶段保持单代理；完成前使用一个专职只读 reviewer 做对抗复核，并按本仓 PR 协议执行 GitHub Codex Review 与 required checks。

## 技术背景

**语言/版本**：Python 3.11-3.14、Bash、PowerShell、GitHub Actions YAML
**主要依赖**：Typer、pytest、GitHub CLI/API、Actions artifacts/attestations
**存储**：Git 仓库、GitHub tag/release/Actions authority
**测试**：pytest 定向单元/集成测试、Ruff、YAML parse、verify constraints、release smoke
**目标平台**：Ubuntu、macOS、Windows amd64
**约束**：不修改 v1.0.3；不跨 attempt 复用证据；不动态选测；不增加第四种工件；不降低 Proof/CAS/immutable/Certificate 门槛；不重复本地全量测试。

## 宪章检查

| 宪章门禁 | 计划响应 |
|----------|----------|
| Persist decisions | 在 `specs/008-*` 与 `docs/releases/v1.0.3-bootstrap-no-go.md` 固化 terminal decision、根因、残留与恢复边界。 |
| Contract-level verification | 测试真实 producer→Bash consumer 边界，并在发布后核对 tag/commit/tree/assets/Proof/attestation/Certificate。 |
| Docs and code traceable | spec FR、tasks、测试、代码改动、execution log、PR/run/release 链接逐项对账。 |

## 项目结构

```text
specs/008-v1-0-4-permanent-release-truth/
├── spec.md
├── plan.md
├── tasks.md
└── task-execution-log.md
docs/releases/
├── v1.0.3-bootstrap-no-go.md
└── v1.0.4.md
.github/workflows/
├── release-build.yml
├── release-artifact-smoke.yml
├── posix-user-guide-e2e.yml
└── windows-user-guide-e2e.yml
scripts/
├── release_truth.py
└── validate_public_release_identity.py
src/ai_sdlc/core/
└── release_truth.py
tests/
├── integration/test_github_workflows.py
└── unit/test_release_truth.py
```

## 阶段计划

### Phase 0：终止裁决与设计冻结

**目标**：把 005 NO-GO、v1.0.3 residue、禁止动作和 v1.0.4 新代际边界写入仓库。
**产物**：本 WorkItem 四件套、incident record、program truth snapshot。
**验证方式**：placeholder scan、formal parser、`program truth sync`、文档对账。
**回退方式**：docs 分支未合并前可整体放弃；不得触碰远端 v1.0.3。

### Phase 1：RED—真实边界回归

**目标**：用当前生产者与 Bash 消费模式稳定复现最后一项丢失。
**产物**：6/1/0 项和 mismatch 的定向测试。
**验证方式**：新测试在生产代码变更前至少有一个因缺陷而失败，失败原因必须是末行未消费。
**回退方式**：只回退测试草稿，不改发布状态。

### Phase 2：GREEN—EOF 安全与集合断言

**目标**：生产者写尾换行、消费者 EOF-safe，并保持 live exact-set fail closed。
**产物**：可维护的 upload plan helper/子命令、workflow 调用、定向单元/集成测试。
**验证方式**：6/1/0/mismatch 全绿，删除任一保护会使测试失败；Ruff/YAML parse 通过。
**回退方式**：实现 PR 未合并前整体回退；不触发 release。

### Phase 3：版本与公开真相迁移

**目标**：所有当前产品真相统一为 `1.0.4`，`1.0.3` 只保留明确历史/失败语境。
**产物**：包版本、工作流、校验器、README、用户指南、合同、release checklist、lockfile、release note。
**验证方式**：版本扫描、release identity 定向测试、用户指南/离线打包定向测试、verify constraints。
**回退方式**：代码 PR 未合并前整体回退；不移动既有 tag。

### Phase 4：PR 门禁

**目标**：单代理实现后接受独立只读 review 与 GitHub 门禁。
**产物**：Draft PR、Codex review、required checks、精确 final head SHA。
**验证方式**：Draft 重矩阵正确 skipped；一次 Ready 后完整 3×4、Windows shell、Fast Gate、Baseline、Protocol probes、Merge Assurance、Compatibility Gate Result 全绿。
**回退方式**：仅在同分支做可操作问题的定向修复；不得重复全量本地测试。

### Phase 5：v1.0.4 fresh immutable bootstrap

**目标**：从新的受保护 main writer SHA 创建全新 v1.0.4 authority 并发布。
**产物**：annotated tag、0-asset Draft、release-build run、6 assets、Proof、immutable release、generation-0 Certificate。
**验证方式**：Release Assurance 12/12、三个 Build Smoke、Proof Inputs、Publish CAS、attestation、Certificate 及发布后独立核验。
**回退方式**：失败保持 Draft 并 fail closed；每次修复产生新 writer 和全新 attempt，最多两个，不允许手工 salvage。

## 关键路径验证策略

| 关键路径 | 主验证方式 | 次验证方式 |
|----------|------------|------------|
| upload manifest EOF | 执行真实 Python producer 与 Bash consumer | workflow integration/static contract |
| existing asset validation | 摘要/大小/mime 与 unexpected asset 定向测试 | live Draft exact-set Proof 前校验 |
| version truth | public release identity/constraints tests | `rg` 人工对账 |
| v1.0.3 freeze | release API + tag peel | incident record exact IDs |
| v1.0.4 release | release API + Proof/CAS/attestation/Certificate | 三平台离线 smoke |

## 实施顺序

1. 完成并验证 formal docs；合并 docs PR。
2. 从 docs 已合并的 `main` 创建 `feature/008-v1-0-4-permanent-release-truth-dev`。
3. 运行定向基线，写 RED 测试并记录失败。
4. 实现 upload plan EOF 双重防护与 exact-set 断言，跑定向测试。
5. 更新 1.0.4 版本/文档/lockfile，跑 release identity 与约束验证。
6. 更新 execution log，做专职只读对抗复核并修复可操作问题。
7. 推送 Draft PR，请求一次 Codex review，监控 required checks；满足门禁后一次 Ready 并合并。
8. 对精确 main writer 创建 v1.0.4 annotated tag 和 0-asset Draft；触发 fresh release-build。
9. 验证 immutable release 全绑定，关闭 008，确认 005/v1.0.3 residue 未变，进入 007。

## 决策冻结

- docs 和实现都不使用 subagent 分摊；仅最终允许一个专职只读 reviewer，避免共享实现判断。
- 上传规划逻辑优先从 workflow inline Python 抽成可直接测试的内部 helper/命令，工作流只负责调用和流式上传。
- 清单生产者与消费者都修复，形成 defense in depth；测试必须真实执行边界。
- 当前没有需要用户补充的开放问题。
