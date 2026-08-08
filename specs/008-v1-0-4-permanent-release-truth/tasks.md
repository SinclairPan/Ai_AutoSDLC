# 任务分解：Permanent Release Truth v1.0.4 Fresh Bootstrap

**编号**：`008-v1-0-4-permanent-release-truth` | **日期**：2026-08-08
**来源**：`plan.md + spec.md`

## Batch 1：005 terminal NO-GO 与 formal truth

- [x] **T11（P0）建立 canonical WorkItem**：创建 spec/plan/tasks/execution-log，冻结 v1.0.3 不可 salvage 与 v1.0.4 fresh-generation 边界。
- [x] **T12（P0）落档 incident**：新增 `docs/releases/v1.0.3-bootstrap-no-go.md`，记录精确 release/tag/run/writer/5 assets/缺失 sidecar/禁止动作。
- [x] **T13（P1）同步 program truth**：填写 manifest goal，注册 incident/defect truth sources，生成 truth snapshot，运行 formal placeholder/约束检查。
- [ ] **T14（P0）合并 docs 基线**：提交、推送 docs PR，经 Codex review 与 required checks 后合并受保护 main。

## Batch 2：RED—上传边界回归

- [ ] **T21（P0）定向基线**：运行 release truth/workflow/release identity 相关测试和 `verify constraints`，不运行本地全量 pytest。
- [ ] **T22（P0）6 项 RED**：真实 producer→Bash consumer，证明当前实现丢失最后一个 Windows sidecar。
- [ ] **T23（P0）1/0 项 RED/contract**：仅最后 sidecar 缺失时必须消费 1 项；0 项不上传。
- [ ] **T24（P0）fail-closed contract**：unexpected asset、摘要/大小不一致必须在上传前拒绝。

## Batch 3：GREEN—EOF 安全 upload plan

- [ ] **T31（P0）抽取可测试 upload plan**：从 frozen Proof Inputs 与 live Draft 计算 ordered missing assets，保留 numeric release ID/upload URL authority。
- [ ] **T32（P0）生产者尾换行**：非空清单逐行以 LF 结束，空集合为空文件。
- [ ] **T33（P0）消费者 EOF-safe**：Bash loop 使用 `read ... || [[ -n ... ]]` 语义，并对上传数量/最终 live 6/6 集合断言。
- [ ] **T34（P0）定向验证**：6/1/0/mismatch 测试、Ruff、YAML parse 与 mutation sanity 全通过。

## Batch 4：v1.0.4 版本与文档真相

- [ ] **T41（P0）版本面迁移**：更新 pyproject、双 `__version__`、uv.lock、release workflows、validators 和约束规则为 1.0.4。
- [ ] **T42（P0）公开文档迁移**：更新 README、USER_GUIDE、product contract、发布约定、checklist、offline README，并新增 `docs/releases/v1.0.4.md`。
- [ ] **T43（P0）身份回归**：运行 release identity、user guide、offline bundle 相关定向测试和版本 truth scan。

## Batch 5：实现 PR 与受保护 main

- [ ] **T51（P0）治理验证**：`git diff --check`、Ruff、YAML parse、`uv run ai-sdlc verify constraints`、plan/close/branch checks。
- [ ] **T52（P0）专职只读对抗复核**：固定 diff/SHA，由一个本地 reviewer 检查 release/supply-chain/governance 可操作问题。
- [ ] **T53（P0）Draft PR**：提交、推送、创建 Draft PR，对最终 SHA 请求一次 GitHub Codex review并建立约 5 分钟 heartbeat。
- [ ] **T54（P0）Ready/merge**：Draft 轻门通过且重矩阵 skipped 后一次 Ready；同一 SHA 完整门禁全绿后合并 main。

## Batch 6：v1.0.4 fresh bootstrap 与闭环

- [ ] **T61（P0）创建新 authority**：用精确受保护 main SHA 创建 annotated tag `v1.0.4` 与全新 0-asset、non-prerelease Draft release。
- [ ] **T62（P0）受保护 writer 发布**：执行完整 3 OS×4 Python Assurance、三个 Build Smoke、Proof Inputs、6 assets、Proof/CAS、immutable attestation、generation-0 Certificate。
- [ ] **T63（P0）独立发布核验**：验证 tag object/commit/tree、3 工件+3 sidecar+Proof、Certificate 与同一 run 绑定。
- [ ] **T64（P0）残留复核与清理**：确认 v1.0.3 residue 未变，关闭 008，进入 007，清理分支/worktree并删除 heartbeat。

## 依赖与并行性

- T11→T14 必须先合并，T21 才能开始。
- T22-T24 同属 RED 批次，但由同一实现代理顺序执行，不分派实现 subagent。
- T31-T34 完成后才能开始版本迁移。
- T61 必须等待实现 PR 合并且精确 main SHA 已冻结。
- T62 不能复用任何旧 run/artifact/evidence；每个 fresh attempt 内证据自洽，最多两次。
