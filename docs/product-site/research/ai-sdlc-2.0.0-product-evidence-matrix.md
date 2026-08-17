# AI-SDLC 2.0.0 产品能力与证据矩阵

## 1. 证据基线

- 产品版本：`v2.0.0`
- 标签 commit：`737bda39e05c53450e180a20581b7b7a70db9cf0`
- 标签 tree：`3db58121e228a7a1c4c6b760c535d6df1ffdbe84`
- 取证规则：能力、CLI、状态、工件和失败语义只从上述标签树读取，不使用当前开发分支补强正式版本主张。

成熟度定义：

- **mature**：CLI、状态与持久工件均已落地。
- **conditional**：机制已经落地，但只在特定任务、Provider、Agent 或人工确认条件下成立。
- **advisory**：只提供建议，不参与状态迁移或 Close。

所有“工件”均指项目内可验证工件；是否适合公开仍需由项目自行脱敏，不能默认视为安全公开材料。

## 2. 七个产品价值主题

| 产品价值主题 | 成熟度 | 现实痛点 | AI-SDLC 2.0 机制 | CLI、状态与工件 | 失败语义 | 产品表达边界 | v2.0.0 证据 |
|---|---|---|---|---|---|---|---|
| **从意图到可信交付** | mature，限正式 WorkItem 路径 | 需求、计划、实现与验收分散，模型写完代码便自报完成 | 将需求、计划、任务、执行记录与关闭前核验绑定到同一 WorkItem 工件链 | `workitem init/link/close-check/truth-check`；`spec.md`、`plan.md`、`tasks.md`、`task-execution-log.md`、checkpoint linkage | close-check 存在 blocker 时非零退出 | 只能描述正式绑定 WorkItem 的交付路径；不能说任意聊天输入都会自动形成完整工件链 | `src/ai_sdlc/cli/workitem_cmd.py:136-217,370-408,572-606,653-707`；`src/ai_sdlc/core/workitem_scaffold.py:15-17,35-36,60-76`；`docs/product-contract.md:11-16` |
| **证据先于完成** | mature | 模型用文字总结替代真实测试、构建、浏览器或审查结果 | Loop 从项目工件重算状态；复核输入绑定 `input_digest`，close/freeze 在同一进程重建输入并拒绝漂移 | `loop … start/check/status/review/close|freeze`；`loop-run.json`、input、snapshot、report、close、Implementation progress 与 `verification-evidence.json`；`needs_fix/needs_review/needs_user/blocked/passed/closed` | 输入缺失、不可读、目标身份或 digest 漂移时保持未关闭 | 有证据机制不等于已经证明生产效果、质量提高或零缺陷 | `README.md:86-122`；`src/ai_sdlc/cli/loop_review_cmd.py:210-303`；`src/ai_sdlc/core/loop_models.py:59-79,97-113`；`src/ai_sdlc/core/implementation_store.py:206-214` |
| **有界专家复核** | conditional | 同一上下文自审会重复盲点，无界多 Agent 讨论又带来成本、冲突和责任模糊 | 原 Writer 之外临时选择一名 Primary Expert；只有明确交叉风险时再加一名 Cross-risk Expert；专家只读同一 snapshot，只返回 Findings；原 Writer 修复，最多一次复审 | `loop review --type … --loop-id … --json`；`input_digest`、内联 `review_snapshot`；原 Loop 状态保持唯一状态源 | 专家失败、超时或无效输出必须停在 `needs_review`，不能解释为零 Finding | `Bounded Dynamic Expert Review Graph` 是解释性产品概念，不是 CLI、类名、持久图、调度器或第二状态机 | `README.md:122`；`docs/product-contract.md:43`；`src/ai_sdlc/adapters/codex/AI-SDLC.md:18-30`；`src/ai_sdlc/core/review_kernel.py:15-46,73-139,220-263` |
| **可恢复的项目事实** | mature | 会话压缩、中断、换人或换工具后，只剩不可靠聊天记忆 | checkpoint、Git 分支、项目工件和 handoff 共同确定续作点；工件领先 checkpoint 时先停止，再显式 reconcile | `status`、`recover`、`recover --reconcile`、`handoff update/show/check`；`.ai-sdlc/checkpoint.yml`、`.ai-sdlc/state/codex-handoff.md`、WorkItem scoped handoff、Loop 工件 | stale checkpoint 会阻止 `run`，避免基于过期状态继续执行 | 不是模型思维恢复、进程栈恢复、事务回滚、向量长期记忆或无损会话迁移 | `README.md:133-141`；`src/ai_sdlc/cli/run_cmd.py:192-208`；`src/ai_sdlc/core/reconcile.py:82-225`；`src/ai_sdlc/core/handoff.py:25-135` |
| **从前端意图到验收证据** | conditional | 技术栈、组件、主题、实现和浏览器验收各自为政，能编译不等于可使用 | 实现前推荐并确认技术栈、Provider 与 Style Pack；确认后物化 snapshot；Browser Gate 绑定页面、交互、console/page error、截图、视觉和基础 a11y 证据 | `program solution-confirm --dry-run|--execute --yes`、`program browser-gate-probe --execute`、`loop frontend-evidence doctor/start/status/review/close/skip`；solution snapshot、browser gate artifact、frontend-evidence input/report/close | Provider 不可用、证据缺失、真实质量 blocker 或身份漂移时不能正常关闭；无浏览器能力时只能显式 skip | Playwright 是可选执行路径；不能称为 WCAG 认证、完整 E2E 平台或任意组件库零配置支持 | `README.md:108-116`；`src/ai_sdlc/cli/program_cmd.py:2135-2299`；`src/ai_sdlc/models/frontend_solution_confirmation.py:221-330`；`src/ai_sdlc/core/frontend_evidence_loop.py:738-854`；`src/ai_sdlc/models/frontend_browser_gate.py:18-201` |
| **跨 AI 工具的一致治理** | mature 的专用规则适配；conditional 的工具执行能力 | 更换 AI Coding 工具时需要重新解释规则，项目治理被锁在聊天窗口 | Claude Code、Codex、Cursor、VS Code/Copilot 四个专用 Adapter 将项目规则写入各自 canonical 入口；generic 只生成兼容提示，要求人工引用宪章和 CLI，不提供 canonical proof carrier；状态和 Loop 工件保留在项目内 | `init --agent-target …`、`adapter select/shell-select/status`；`.claude/CLAUDE.md`、`AGENTS.md`、`.cursor/rules/ai-sdlc.mdc`、`.github/copilot-instructions.md`、`.ai-sdlc/memory/ide-adapter-hint.md` | generic 或其他工具能力不满足时不能冒充与专用 Adapter 等价支持 | 不等于多个 Agent 并行编排、共享实时会话、能力完全一致或支持工具数量领先 | `src/ai_sdlc/integrations/agent_target.py:15-37,85-109`；`src/ai_sdlc/integrations/ide_adapter.py:168-226`；`src/ai_sdlc/adapters/generic/ide-hint.md:3-8`；`src/ai_sdlc/cli/adapter_cmd.py:109-245`；`USER_GUIDE.zh-CN.md:208-248` |
| **与风险匹配的工程控制** | mature 的显式策略；advisory 的代码精简 | Agent 容易跳过确认、隐去失败或过度设计；治理系统也可能产生无价值阻断 | dry-run、confirm、人工节点、秘密与遗漏文件策略、最多两轮；身份和证据严格，代码体积与复杂度只给建议 | `run --dry-run`、`run --mode confirm`；`needs_user`；policy `max_rounds=2`、`high_risk_secret_policy=needs_user`、`redaction_strictness=fail-closed`；Implementation advisories | dry-run 不执行任务；缺少用户决定时保持 `needs_user`；精简建议不改变 Loop 状态 | 不能称为通用风险评分引擎、Lean 强制门禁、自动重构、自动合并或自动发布 | `README.md:118-141`；`src/ai_sdlc/cli/run_cmd.py:172-208`；`src/ai_sdlc/core/loop_models.py:124-154`；`src/ai_sdlc/core/slimming_advice.py:1-154`；`docs/product-contract.md:45,62-69` |

## 3. Loop Engineering 的五类闭环

五类 Loop 在源码中由 `src/ai_sdlc/core/loop_models.py:59-79` 明确枚举。Frontend Evidence 是条件阶段，Local PR Review 是提交前跨阶段终审；不得写成五个在所有任务中永远串行、全部必跑的阶段。

| Loop | 成熟度 | 输入与闭环 | 代表工件 | 失败和关闭边界 |
|---|---|---|---|---|
| Requirement | mature | idea、acceptance → brief、questions、checklist → review → freeze | requirement input、brief、questions、checklist、`requirement-freeze.json` | 范围、验收标准或澄清未完成时不 freeze；WorkItem 绑定可以为空，因此端到端身份主张必须限定正式 WorkItem 路径 |
| Design Contract | mature | `spec.md/plan.md/tasks.md` → coverage → review → close | design input、coverage matrix、report、close | 设计覆盖不足、复核未完成或输入漂移时不 close |
| Implementation | mature | required tasks → progress、verification commands/evidence → report → review → close | progress、verification evidence、report、close | blocked task 进入 `needs_fix`；required tasks 完成后才进入 `needs_review`，不是直接 closed |
| Frontend Evidence | conditional | browser artifact → snapshot/report → review → close | browser gate artifact、frontend-evidence input/snapshot/report/close | Provider 或证据不可用可显式 skip；skip 不能冒充真实浏览器验收 |
| Local PR Review | conditional | Git range/index/worktree/patch → Review Pack → findings → fix/rerun → close/final report | Review Pack、findings、evidence、terminal report | 独立本地 Reviewer 不可用或仍有未解决 Finding 时保持未关闭；不是 CI 证书或远程 PR 平台替代品 |

主要证据：`README.md:86-122,165-190`；`src/ai_sdlc/core/requirement_loop.py:997-1003`；`src/ai_sdlc/core/design_contract_store.py:156-162`；`src/ai_sdlc/core/implementation_loop.py:1170-1203`；`src/ai_sdlc/core/implementation_store.py:206-214`。

## 4. Bounded Dynamic Expert Review Graph

BDERG 用一张图解释既有复核合同，而不创造新的产品运行时：

```text
Writer 产出实质结果
  → AI-SDLC 绑定 input_digest 与 review_snapshot
  → Primary Expert（必选、全新只读上下文）
  → Cross-risk Expert（仅存在明确第二风险面时加入）
  → 合并带位置的 Findings，不合并 verdict
  → 原 Writer 修复
  → 最多一次复审
  → 原 Loop close / freeze，或保持 needs_review
```

角色边界：

- **Writer**：原 Requirement、Design Contract、Implementation、Frontend Evidence 或 Local PR Review 结果编写者，负责修复和状态动作。
- **Primary Expert**：每次至少一名，由当前 Agent 根据实质结果内容选择。
- **Cross-risk Expert**：只有明确交叉风险时增加；专家总数最多两名。
- **专家权限**：只读同一 digest-bound snapshot，不读取 Writer 推理历史，不修改候选，不拥有 Close 权。
- **输出**：只返回带位置的 Findings；失败不能折算成零 Finding。
- **生命周期**：专家是当前复核的临时职责，不持久化身份、session、ledger、certificate、attestation、authority/store 或优化历史。

主要证据：`README.md:122`；`docs/product-contract.md:43`；`src/ai_sdlc/adapters/codex/AI-SDLC.md:22-25`；`src/ai_sdlc/core/review_kernel.py:19-46,105-139,220-263`。

## 5. 前端治理的成熟度边界

| 路径 | 正式定位 | 可以证明 | 不得宣称 |
|---|---|---|---|
| `vue3 / public-primevue / modern-saas` | 内置默认推荐路径 | Provider profile、语义组件映射、白名单、Style Pack、Theme Token、`theme.ts` 唯一入口、Vite/TypeScript/UnoCSS 交付约束 | PrimeVue 为 AI-SDLC 自研；推荐已经自动安装；任何项目均可零配置使用 |
| `vue2 / enterprise-vue2` | 内置企业兼容路径 | 私有 Provider profile、组件映射、白名单、安装策略与兼容约束 | 公开站点附送信服云组件包、许可证、私有 registry 或企业网络环境 |
| 自定义或无组件库 | 兼容执行与证据路径 | 项目提供可加载 browser entry 和兼容 Browser Gate 工件后，Frontend Evidence 可以消费 | 第三个内置 Provider；任意栈无需配置即可通过 |

主要证据：`src/ai_sdlc/rules/pipeline.md:51`；`src/ai_sdlc/models/frontend_solution_confirmation.py:270-415`；`src/ai_sdlc/models/frontend_generation_constraints.py:160-292`；`src/ai_sdlc/models/frontend_provider_profile.py:411-512`。

## 6. 代码精简是建议，不是门禁

`src/ai_sdlc/core/slimming_advice.py` 会对长文件、长函数、同文件重复、单调用 wrapper 和混合职责生成确定性建议。Implementation report 可以携带这些 advisories，但状态仍由 required task 和 blocker 决定。

可以宣传：帮助实现者发现体积、重复、复杂度和职责拆分机会。

禁止宣传：Lean 强制门禁、自动重构、质量证明、receipt、waiver、No-Go 或阻断 Close。

证据：`src/ai_sdlc/core/slimming_advice.py:1-154`；`src/ai_sdlc/core/implementation_loop.py:1170-1203,1251-1284`；`README.md:118-120`；`docs/product-contract.md:45,69`。

## 7. 安装与用户指南边界

- `README.md:26-48` 提供 Git 在线安装和源码运行入口。
- `README.md:221-229` 提供 Windows、macOS、Linux 三个正式离线资产名称及用户指南入口。
- `USER_GUIDE.zh-CN.md:9-18,284-288` 的 v2.0.0 正式源文件当前包含两条完整主路径：空项目和已有项目，两者均按离线 Release 包展开。
- “已有/全新项目 × 离线/在线”四条完全自包含路径是本次需要新编写的用户指南内容，不得描述为 v2.0.0 原用户指南已经存在的结构。
- 产品站 `Downloads & Docs` 只提供公共资源和用户指南入口，不在页面正文重复四条路径。

## 8. 前台使用规则

每条能力正文必须同时具备：

> 现实问题 → 产品机制 → CLI 或状态 → 可检查工件 → 失败行为 → 能力边界

只出现功能名而没有状态、工件和失败语义的内容，不进入最终产品站正文。只出现源码但用户无法从产品入口理解或验证的内容，不作为首页主价值。没有正式实验的数据，不写成效率、质量或成本提升结论。
