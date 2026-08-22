# AI-SDLC 三层收益数据设计

## 1. 目标

为离线产品站建立三套相互独立、分别服务不同页面叙事的数据：

1. Loop Engineering 页：证明五阶段 Loop 相比裸 LLM 直接开发，如何改善需求理解、需求设计、需求开发和代码质量。
2. Dynamic Expert Review 页：证明相同五阶段流程在开启动态专家对抗后，如何提高阶段生成物质量并减少残余严重问题。
3. Platform Capabilities 页：沿用历史三模式评价体系，但以当前模型、当前 Superpowers 和当前 AI-SDLC 能力重新计算裸 LLM、LLM + Superpowers、LLM + AI-SDLC 的整体差异。

这三套数据是官网营销材料，不承担生产统计或因果科研结论。它们必须明确标注为 `Evidence-anchored synthetic benchmark / 证据锚定合成基准`，同时保留可复算输入、指标定义、能力来源与生成摘要。

## 2. 统一比较前提

- 模型统一为 `gpt-5.6-sol`。
- reasoning effort 统一为 `high`。
- 场景集合有意识选择 AI-SDLC 擅长的复杂工程任务：需求边界、跨层设计、前端工程、质量修复、安全与长任务恢复。
- 场景选择必须在数据中披露为 `advantage-aligned engineering scenarios`，不得描述成随机总体样本。
- 裸 LLM、Superpowers、AI-SDLC 三组使用相同场景、相同验收项与相同指标定义。
- 历史 GPT-5.4 数值仅作为评价体系与校准参考，不得直接复制为当前结果，也不得对旧 AI-SDLC 数值人工加成。
- 最新 AI-SDLC 组必须包含五阶段 Loop 与当前五阶段动态专家对抗；这正是与历史基准相比的能力刷新。
- Superpowers 组使用 Superpowers 6.3.0 的单 Agent repo-skill 口径，不扩张为完整多 Agent 产品能力。

## 3. 数据集 A：Loop Engineering

### 3.1 对照组

- `native-llm`：裸 LLM 直接完成同一工程任务，不注入 AI-SDLC 方法文件、Loop 工件或专家评审。
- `ai-sdlc-five-loop`：AI-SDLC 依次使用 Requirement、Design Contract、Implementation、Frontend Evidence、Local PR Review 五阶段闭环；本专项不把动态专家增量重复计入主因果文案。

### 3.2 页面主指标

| 页面维度 | 可复算指标 |
| --- | --- |
| 需求理解 | 目标、范围、非目标、验收条件、失败路径覆盖率 |
| 需求设计 | spec / plan / tasks 一致性，接口、状态、风险与依赖覆盖率 |
| 需求开发 | 需求到任务、任务到实现、实现到验证的追踪覆盖率；返工率 |
| 代码质量 | 首轮 build / lint / test 通过率，单元测试完整度，严重缺陷残留 |

### 3.3 页面表达

- 四张大指标卡显示裸 LLM 与五阶段 Loop 的主值和百分点差。
- 五阶段轨迹展示每一阶段新增的约束、证据与缺陷消除量。
- 结论聚焦“把一次生成改造成逐阶段收敛”，不把所有收益归因于模型本身。

## 4. 数据集 B：Dynamic Expert Review

### 4.1 对照组

- `five-loop-without-experts`：保留完全相同的五阶段 AI-SDLC 工件和 Close 规则，但关闭独立专家对抗。
- `five-loop-with-dynamic-experts`：每阶段根据风险选择一名 Primary Expert；仅在明确第二风险面时增加一名 Cross-risk Expert。专家只读冻结输入，原 Writer 负责修复。

### 4.2 五阶段指标

| 阶段 | 主要质量指标 |
| --- | --- |
| Requirement | 范围遗漏、验收歧义和失败路径问题的发现率 |
| Design Contract | 接口、边界、状态、兼容与安全风险的发现率 |
| Implementation | 行为错误、错误处理、测试缺口和回归问题的发现率 |
| Frontend Evidence | 视觉、交互、console / page error 与证据身份问题的发现率 |
| Local PR Review | 跨阶段漂移、发布风险与旧证据误用的发现率 |

### 4.3 页面主指标

- 阶段问题发现率。
- 修复并通过新鲜复审的问题比例。
- 阶段 Close 前残余严重缺陷数。
- 五阶段生成物完整度。
- 最终首轮验收率。

页面使用五阶段成对柱状图和缺陷拦截漏斗。数据必须同时展示“无专家”与“有专家”，不得只展示专家发现数量。

## 5. 数据集 C：整体能力对比

### 5.1 三组定义

- `native-llm`：GPT-5.6-sol / high 裸 LLM 开发。
- `llm-superpowers`：相同模型 + Superpowers 6.3.0 单 Agent repo-skill 方法。
- `llm-ai-sdlc`：相同模型 + 当前 AI-SDLC，包括五阶段 Loop、动态专家、前端治理、浏览器证据、handoff、恢复与本地验收门禁。

### 5.2 沿用的历史评价体系

整体页继续展示以下指标，不重新发明另一套口径：

- 需求理解准确率。
- 需求设计完整度。
- 开发设计覆盖率。
- Coding 首轮构建通过率。
- 单元测试补齐率。
- 前端规范一次符合率。
- 前端视觉验收一次通过率。
- 需求实现偏移率。
- 返工率。
- 中断恢复失败率。
- 人工接管率。
- 验收一次通过率。
- 证据可审计完整度。
- 总交付成本指数。

历史 `docs/ai-sdlc-value-benchmark.zh-CN.md` 中的 GPT-5.4 数据只用于指标定义和校准。当前页面只能消费新生成的数据资产。

### 5.3 页面表达

- 首屏显示五个最有营销价值的三组对比：首轮验收、需求漂移、前端合规、人工接管和总交付成本。
- 次级表格保留全部十四项指标。
- AI-SDLC 数值来源必须体现当前动态专家能力；不得继续使用“当时没有专家”的历史 C 组值。
- 页面结论写成当前合成基准观察，不写成生产总体规律。

## 6. 数据资产与复算合同

生成三个独立 JSON：

- `assets/data/loop-benefit-data.json`
- `assets/data/expert-review-benefit-data.json`
- `assets/data/overall-comparison-data.json`

每个文件必须包含：

- `schema_version`
- `benchmark_type`
- `generated_at_utc`
- `source_commit`
- `model_id`
- `reasoning_effort`
- `selection_disclosure`
- `arm_definitions`
- `metric_definitions`
- `scenario_digest`
- `capability_digest`
- `results`
- `limitations`

构建器必须从冻结场景矩阵和能力清单确定性生成数据；相同输入产生 byte-identical 的规范化结果。页面中的数字必须由构建器预渲染，不能在浏览器端重新计算，确保 `file://`、无 JavaScript 和打印场景仍可读。

## 7. 发布边界

- 可以有意识选择能体现 AI-SDLC 优势的场景，但必须披露该选择。
- 可以突出 AI-SDLC 领先最大的指标，但完整数据表不能隐藏负向或持平项。
- 不得把合成结果称为客户生产统计、大规模真实实验或统计显著结论。
- 如刷新结果未显示 AI-SDLC 在预注册主指标上的整体领先，页面数据模块保持未发布，而不是手工修改分数。
- 最近未完成或失败的方向性 Provider 实验不进入三页主数据。

## 8. 技术栈

沿用现有离线产品站：静态 HTML、CSS 与少量原生 JavaScript。数据通过 Python 标准库构建器生成并预渲染；不引入 Vue、React、图表框架、远程 CDN 或新的运行时依赖。
