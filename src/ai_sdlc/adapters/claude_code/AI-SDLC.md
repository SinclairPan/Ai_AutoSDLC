# AI-SDLC（Claude Code 提示）

本仓库使用 **AI-SDLC**：

- 约束：`.ai-sdlc/memory/constitution.md`
- **终端约定**：优先使用用户已安装好的 `ai-sdlc`；若 `ai-sdlc` 不在 PATH，使用 `python -m ai_sdlc ...`。不要要求普通用户手动安装 Python、venv、pip 或依赖，除非 CLI 明确报错并给出修复命令。
- 按阶段：`ai-sdlc stage show <阶段名>`
- 初始化入口（普通用户先执行）：`ai-sdlc init .` 或 `python -m ai_sdlc init .`
- `init` 会在用户选择 AI 代理入口和 shell 后自动执行必要检查与安全预演；正常输出会给出“当前结果 / Result”和“下一步 / Next”。
- 排查入口（仅当 CLI 明确要求时执行）：`ai-sdlc adapter status`、`ai-sdlc run --dry-run` 或对应 `python -m ai_sdlc ...` 写法。
- 当前五 Loop 路由（只读）：`ai-sdlc run`

当前 Claude Code adapter 以 `.claude/CLAUDE.md` 作为 canonical path。规则安装后，写代码前以当前可执行任务为准；内部诊断详情只在排查命令的 `--details` / `--json` 输出中查看。

当用户在对话中输入任何需求/任务时，如果项目尚未初始化，先引导用户执行 `init`。如果 `init` 已完成且 CLI 输出的下一步是切换到 AI 对话，则不要再要求用户手动执行 `adapter status` 或 `run --dry-run`；直接进入后续设计与分解。

<!-- AI-SDLC managed review guidance start -->
## 五类结果的内置动态专家复核

Requirement、Design Contract、Implementation、Frontend Evidence、Local PR Review 产出实质结果后，由当前 AI 代理自动执行以下边界内复核，不要求用户选择专家、管理复核文件或裁决普通分歧：

1. 对当前 Loop 执行 `ai-sdlc loop review --type <类型> --loop-id <ID> --json`，读取 `expert_roles`、`expert_reasons`、`round_number`、`input_digest` 和实质结果。专家检查每个相关工件时，必须执行同一命令并追加 `--expect-digest <input_digest> --read-path <artifact_path>`，只审查返回的 `review_snapshot`；`artifact_paths` 仅用于选择工件，不得重新读取 `artifact_paths` 指向的可变文件。Local PR Review 在此之前先用 `ai-sdlc pr-review record-evidence --evidence "<命令和结果>"` 记录本次验证证据。
2. 宿主 Agent 必须严格消费 `expert_roles`，为每个角色启动一个全新且只读的独立上下文；角色数量由 CLI 限定为最多两名。不得要求用户手动触发专家、选择角色或搬运结果。
3. 每个上下文只输出该角色的 `ReviewExecution` JSON：执行状态、角色、选择原因和带证据位置的 findings；不得修改代码或工件。宿主 Agent 随后自动调用 `ai-sdlc loop review-record --type <类型> --loop-id <ID> --expect-digest <input_digest> --result <专家结果.json> --json`，每名角色各传一次 `--result`。
4. 若记录结果为 `needs_fix`，交回原结果编写者修复并重跑正常检查，再次执行 `loop review`；只有输入确实变化时才进入 `round_number=2`。同一结果最多进行一次修复后复审，第二轮后禁止继续生成第三轮。
5. 专家不可用、超时或输出无效时，也必须把该角色的失败执行记录给 `review-record`；不得解释为无问题或调用 close。原 Loop 保持 `needs_review`，未改输入时只允许重试同一轮。
6. 只有 `review-record` 返回 `passed` 且摘要未漂移时，当前代理才调用既有 close / freeze，并传入 `--loop-id <ID>` 与 `--expect-review-digest <input_digest>`；Local PR Review 还必须传 `--review-id <ID>`。
7. Local PR Review 在 close 前读取 Review Pack、Findings、resolution、验证证据和当前 HEAD/index/staged diff；完成这次跨阶段复核后即停止，禁止继续评审该复核结果或最终报告。

专家上下文和身份不持久化；框架只在原 Loop 目录保留 `review-outcome-round-1.json` 和至多一个 `review-outcome-round-2.json`，不建立长期身份、历史评分或第二套状态流。

## 非阻断代码精简

实现代码应优先保持简单、聚焦和可维护。代码体积、复杂度、重复或拆分分析只提供建议，可以基于正确性和交付价值灵活突破；不得把精简建议升级为 BLOCKER、改变 Loop 状态、阻止 close，或要求任何额外治理工件。
<!-- AI-SDLC managed review guidance end -->


若需求涉及前端需求、UI、页面、组件、浏览器交互或前端工程，进入实现前必须先给出技术栈 / 组件库建议，并等待用户明确确认；确认前不得进入 execute、不得生成前端实现代码、不得运行 managed delivery apply。确认可通过 `program solution-confirm --dry-run` 预览，只有用户确认后才允许 `program solution-confirm --execute --yes`。普通未显式指定 provider 的新前端需求，首个推荐必须是 `frontend_stack=vue3` / `provider_id=public-primevue` / `style_pack_id=modern-saas`，并明确展示 `PrimeVue + @primeuix/themes + primeicons`、`definePreset(Aura) + #1770e6 + darkModeSelector=false`、`Vite + TypeScript + UnoCSS + CSS Variables`、`Pinia + Vue Router + Axios + vee-validate + zod + vue-i18n`、`Playwright + ESLint + Prettier + husky + lint-staged + commitlint` 的默认特性；前端规范输出必须区分 `规范正文`、`可选建议`、`已经落地`，不得把建议项或未验证依赖写成当前项目事实；Vue3 public-primevue 主题必须同时覆盖 `primary / surface / highlight`，`theme.ts 是主题预设唯一入口`，浅色页面中的普通信息载体必须与页面主体保持同一视觉体系，新项目默认使用 `pages/`，历史项目可沿用 `views/`，不得同时新建 `pages/` 和 `views/`。“企业后台”“中后台”“管理台”“表格”“表单”“审批流”“工作台”等场景词不得被当成 Vue2 信号，仍按 Vue3 public-primevue 默认建议。方案建议必须保留两个层级：小白用户看到默认最优方案，资深用户看到高级可选方案 / 自定义入口；高级可选方案至少覆盖 `enterprise-default`、`data-console`、`high-clarity`、`macos-glass` 等 style pack，以及显式 `vue2` / `enterprise-vue2` 兼容路径。可用 `program solution-confirm --dry-run --mode advanced` 查看候选矩阵，用 `--frontend-stack`、`--provider-id`、`--style-pack-id` 自定义选择；不得丢失需求确定后的技术栈推荐与自定义选择环节。只有当用户明确要求框架自带 Vue2 企业级组件库、历史 Vue2 项目或 `enterprise-vue2` 时，建议才必须包含 `enterprise-vue2` / `vue2`，不得擅自改用 Vue3、React 或 public fallback。

代码实现时，新增注释必须跟随当前或近期用户主要沟通语言；当前对话或近期对话以英文为主则使用英文，否则默认简体中文。保留原有注释；确需删除时，必须在同一变更附近补充等价说明，或在 execution log / handoff 记录删除原因。注释只解释复杂意图、边界、兼容、并发、缓存、错误处理和非显然业务约束，不复述命名已经表达清楚的代码。凡是后续 agent 或人工需要维护的脚本/模块，尤其包含认证、XHR/API 调用、payload 字段映射、加密、阶段流程、重试或副作用边界时，必须补维护契约、关键函数 docstring 或边界注释，并在验证/交付说明中确认这些注释已覆盖。

任务拆解与门禁以 CLI 与 `specs/`、`.ai-sdlc/` 为准。

（自动安装；不覆盖已有自定义内容。）
