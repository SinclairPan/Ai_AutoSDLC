# AI-SDLC 2.0 离线产品站内容设计

## 1. 文档目的

本文定义 AI-SDLC 2.0 离线产品介绍网站的内容架构、页面边界、阅读路径、产品实录模块和新用户指南合同。

本阶段只确定内容设计，不包含 HTML、CSS、JavaScript、视觉稿、视频制作或部署实现。

## 2. 产品目标

网站应让第一次接触 AI-SDLC 的开发者在不依赖讲解人的情况下完成五件事：

1. 理解 AI-SDLC 与通用 AI Coding Agent 的职责差异。
2. 理解 AI-SDLC 如何把市场上常见的 Spec、Skills、专家复核和验证方法组织成具有状态、失败语义、恢复路径与 Close 权的本地交付控制层。
3. 理解 Loop Engineering 如何把需求、设计、实现、前端证据和提交前审查组织成可关闭的工程闭环。
4. 理解有界动态专家复核如何在不引入无限 Agent 讨论的前提下挑战实质结果。
5. 根据自己的项目状态、安装方式和操作系统，独立完成 AI-SDLC 2.0.0 的安装、验证、初始化和首次需求输入。

网站表面必须是一套完整的 AI-SDLC 2.0 产品官网和技术说明站。正文不得出现“参赛材料”“课题响应”“评分项”“评委问答”“覆盖矩阵”“30 秒答案”等表述。

## 3. 事实来源与版本边界

### 3.1 唯一产品事实源

- 正式版本：`v2.0.0`
- 标签提交：`737bda39e05c53450e180a20581b7b7a70db9cf0`
- 标签树：`3db58121e228a7a1c4c6b760c535d6df1ffdbe84`
- 产品能力、命令、状态和边界统一从 `v2.0.0` 标签树取材。
- GitHub Release、下载资产名和 SHA256 必须从同一正式 Release 元数据生成，不得在多个页面手工维护不同版本。

### 3.2 正式离线资产

- `ai-sdlc-offline-2.0.0-windows-amd64.zip`
- `ai-sdlc-offline-2.0.0-macos-arm64.tar.gz`
- `ai-sdlc-offline-2.0.0-linux-amd64.tar.gz`

不得扩大为 Windows ARM、macOS Intel 或 Linux ARM 的正式离线支持承诺。

离线产品站只展示以上资产的正式下载链接、资产名和 SHA256 校验链接，不把三个离线安装包二进制包含在网站交付物中。网站离线可读不代表下载动作也能离线完成。

### 3.3 必须清除的旧版叙事

前台内容不得恢复或改名复活以下旧机制：

- Shadow / Enforce 激活模型。
- Stage Review、Panel、Quorum、Veto、Role Gap。
- 持久专家身份、review session、Finding ledger、certificate、attestation、authority/store。
- 固定四 Reviewer、20 Findings、8 Veto、两轮四角色复审等旧案例和数字。
- `lean-check`、`lean-verify`、`lean-regression`、`lean-no-go` 等旧阻断入口。
- 400/50 阻断阈值、Release Proof、CI certificate 或无限自动收敛。

代码精简只能描述为非阻断建议，不改变 Loop 状态，也不阻止 Close。

### 3.4 市场反向研究基线

产品价值主题不能从 AI-SDLC 自身已有模块直接罗列生成。内容设计先研究高关注度的 AI Coding Skills、开发方法、工作流和直接影响开发连续性的插件，再反查 v2.0.0 的真实实现。

本次样本冻结于 `2026-08-16`。候选集由 GitHub Search 对 `agent skills + coding`、`Claude Code skills + development`、`coding agent + workflow/methodology`、`spec-driven development + AI coding` 四组查询取并集，再采用以下可复核口径筛选：

- GitHub 公开仓库。
- GitHub API 在采样时识别根许可证为 `MIT` 或 `Apache-2.0`。
- 能直接安装到 AI Coding 工具，或提供面向软件交付的 Skill、方法、角色、Spec Workflow、前端工程能力、代码精简或跨会话连续性。
- 按 GitHub Stars 降序取前十。
- 排除 Awesome/资源清单、完整 IDE、完整 Coding Agent 或通用 Agent Runtime、MCP 工具、单语言框架和非软件交付领域技能。

Stars、许可证、默认分支与 commit 由 GitHub API 在同一采样批次读取。这是一套有明确边界的市场样本，不是对 GitHub 全站的绝对排名。Stars 只表示采样时的公开关注度，不能推导质量、采用率、成熟度、性能或竞赛得分。

研究结论进入正文生产前，必须同时生成内部机器可读采样清单并记录 SHA256。清单至少包含：

- UTC 采样时间；完整 REST API 请求参数，包括 `q / sort / order / per_page / page`。
- 每组查询的分页范围、GitHub 返回总数和去重后的候选全集。
- 仓库 ID、`owner/name`、Stars、根许可证、默认分支、冻结 commit 和 API 响应来源。
- 每个候选的纳入或排除决定、排除理由，以及 Stars 并列时按仓库 ID 升序排序的规则。
- 最终 Top 10 列表、清单自身 SHA256 和生成环境说明。

冻结 commit 只证明所分析的源码版本，不能单独证明采样时的候选池和 Stars 排名；没有上述清单时，Top 10 只能作为待复核草案，不得用于产品价值结论。

| 排名 | 冻结项目 | Stars | License | 冻结源码 | 主要市场形态 |
|---|---|---:|---|---|---|
| 1 | [obra/superpowers](https://github.com/obra/superpowers) | 272,856 | MIT | [`b36e082`](https://github.com/obra/superpowers/tree/b36e0829c6d0140e93cfef2ca599b1b07d4a7797) | 可组合 Skills 与开发方法论 |
| 2 | [affaan-m/ECC](https://github.com/affaan-m/ECC) | 240,523 | MIT | [`06c5e11`](https://github.com/affaan-m/ECC/tree/06c5e118c4d3e6c3b7f9445f973a2194c82de193) | Skills、Agents、Memory、Hooks、安全与验证系统 |
| 3 | [mattpocock/skills](https://github.com/mattpocock/skills) | 219,406 | MIT | [`068b6e0`](https://github.com/mattpocock/skills/tree/068b6e0c62393147daf03530149cdce209c93da8) | 小型、可改、可组合的真实工程 Skills |
| 4 | [msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents) | 145,853 | MIT | [`ebe9c99`](https://github.com/msitarzewski/agency-agents/tree/ebe9c99acb5c96f9468de368d8bead775387d1a7) | 专业角色与交付物导向的 Agent 库 |
| 5 | [github/spec-kit](https://github.com/github/spec-kit) | 129,580 | MIT | [`bf88c9f`](https://github.com/github/spec-kit/tree/bf88c9f9a82fa370c7a7257aa2b3cf10b457b65c) | Intent-driven Spec Workflow Harness |
| 6 | [garrytan/gstack](https://github.com/garrytan/gstack) | 128,287 | MIT | [`ae8914a`](https://github.com/garrytan/gstack/tree/ae8914af7edaf248f5b0dcd60518d2f6890ad0da) | 产品、设计、工程、QA 与发布 Skills 工具组 |
| 7 | [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) | 117,367 | MIT | [`a38d04c`](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill/tree/a38d04c3d5c298c851dbe5e6ee1965ee3de42cb5) | UI/UX 设计知识、规则与多栈实现 Skill |
| 8 | [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) | 104,171 | MIT | [`2ed6c52`](https://github.com/DietrichGebert/ponytail/tree/2ed6c52c9d7e5e56942508591085fd45dea277d3) | 代码极简与 YAGNI Skill |
| 9 | [thedotmack/claude-mem](https://github.com/thedotmack/claude-mem) | 90,921 | Apache-2.0 | [`fae697a`](https://github.com/thedotmack/claude-mem/tree/fae697a45d107aae567d605916391ab64d8ecae1) | 跨会话上下文采集、压缩与回注 |
| 10 | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) | 87,796 | MIT | [`df1edb2`](https://github.com/addyosmani/agent-skills/tree/df1edb2e05487d0aa6d93c747141e0aed1187f25) | 按研发阶段组织的工程 Skills 集合 |

[OpenSpec](https://github.com/Fission-AI/OpenSpec) 与 [BMAD Method](https://github.com/bmad-code-org/BMAD-METHOD) 作为相似工作流参照，用于校准轻量变更规格和 AI 驱动开发方法，但不进入上述 Top 10 排名。

市场研究得出四项共同趋势：专业能力正在 Skill 化，意图正在 Spec 化，开发职责正在角色化，长任务正在状态化与记忆化。AI-SDLC 的公开主张不能是“这些能力只有我有”，而应是：

> AI-SDLC 2.0 把已经成熟的工程方法组织成一套具有项目状态、证据、失败语义、恢复路径和 Close 权的本地交付系统。

Top 10 项目名、Stars、排名和逐项目对照仅保留在本内部设计依据中，不进入产品站前台。

## 4. 受众与阅读方式

### 4.1 核心受众

- 首次接触 AI-SDLC、希望快速判断产品价值的开发者。
- 已在使用 Codex、Claude Code、Cursor 等 AI Coding 工具，希望补齐工程闭环的团队。
- 关注需求治理、代码质量、前端交付、审查隔离和可恢复性的工程负责人。
- 需要下载安装并自行验证产品的技术审阅者。

### 4.2 三层阅读深度

1. **快速浏览**：从首页理解产品定位、完整工作流和系统价值主题。
2. **机制阅读**：进入 Platform Capabilities、Loop Engineering 或 Dynamic Expert Review，理解状态、反馈、失败语义和边界。
3. **动手验证**：从 Downloads & Docs 打开《中文新用户指南》，在手册中选择一条自包含安装路径，复制命令完成安装和初始化。

## 5. 顶层信息架构

离线站点采用一个 HTML 入口文件和五个产品视图。建议使用 `index.html` 内部 Hash 路由，直接双击即可打开，不需要后台服务或依赖安装。这里的“一个 HTML 入口”不等于“所有视频字节也必须编码进同一个文件”；视频、字幕和 poster 可以作为同一离线包中的本地相对资源。

主导航固定为：

1. `AI-SDLC 2.0`
2. `Loop Engineering`
3. `Dynamic Expert Review`
4. `Platform Capabilities`
5. `Downloads & Docs`

对应内部地址：

- `#home`
- `#loop-engineering`
- `#dynamic-expert-review`
- `#platform-capabilities`
- `#downloads-docs`

导航顺序刻意先完成两项核心机制的主线阅读，再进入通用平台能力；本文后续章节按内容设计依赖组织，不代表导航优先级。

前台不得使用“课题一”“课题二”作为导航或页面标题。

### 5.1 笔记本优先的响应式基线

网站不能以 27 英寸、32 英寸显示器或 1920px 以上视口作为唯一设计基线。主要阅读和验收环境是常见开发者笔记本，包括系统缩放后有效空间明显变小的场景。

主要设计视口：

- `1366 × 768`，100% 缩放。
- `1366 × 768`，Windows 125% 缩放下的等效可用空间。
- `1280 × 720`。
- `1440 × 900`。
- `1024 × 768`，用于检查窄窗口和分屏使用。

`1920 × 1080` 及更大屏幕只用于增强展示，不能成为内容完整、导航可用或图表可读的前提。

### 5.2 小屏布局原则

- 页面不得依赖固定大宽度、固定大高度或全屏 `100vh` 章节来成立。
- 首屏在 `1366 × 768` 下必须同时露出产品定位、核心说明和至少一个主要动作，不能只显示巨型标题或装饰背景。
- 主要内容宽度应控制在适合长时间阅读的范围；大屏增加留白，不横向拉长正文行长。
- 两列或三列能力卡在空间不足时必须依次收敛为单列，保持原有阅读顺序。
- 不允许页面整体出现横向滚动条。
- 固定或吸顶导航不能占用过多垂直空间，也不能遮挡 Hash 跳转后的标题。
- 导航空间不足时切换为紧凑菜单，但当前页面、返回方式和 Downloads & Docs 入口仍需清晰可见。
- 关键操作不能只依赖 hover；键盘和点击均可到达。
- 表格在窄屏下转换为纵向对比卡或局部可滚动容器，不能压缩到无法阅读。
- 机制图在窄屏下从横向链路重排为纵向流程，节点顺序和连线语义不得丢失。
- 页面间距、标题字号和卡片留白可按视口高度收紧，但正文可读字号、行高和控件可点击区域不能被牺牲。

### 5.3 各模块的小屏行为

#### 首页

- Hero 不使用超高首屏；主标题、副标题和首要动作在一屏内形成完整认知。
- 端到端架构图在笔记本上优先使用可阅读的纵向或分段流程，而不是缩小整张宽图。
- 三个核心价值入口最多两列；窄视口下按 `Goal to Close → Evidence → Expert Review` 顺序单列排列。
- “面向生产交付”区域只显示三个紧凑摘要，不在首页展开完整能力墙。

#### 工作流实录

- 播放器保持 `16:9`，宽度不超过内容区域，不因固定像素尺寸溢出。
- `1366 × 768` 下播放器、标题和主要控制不能被固定头部或底部浮层遮挡。
- 章节列表在空间不足时位于播放器下方，不与视频并排挤压。
- 全屏入口始终可见；全屏用于增强观看，不得成为看清视频内容的唯一方式。

#### Platform Capabilities、Loop Engineering 与 Dynamic Expert Review

- Loop 卡片和角色卡片在笔记本窄窗口下使用单列阅读顺序。
- BDERG 和 Loop 生命周期图提供纵向布局，不能通过缩小字体强塞进横向画布。
- 机制对比表在窄屏下按比较维度拆成卡片，保持双方结论相邻。
- Platform Capabilities 的价值主题最多两列；成熟度边界在窄屏下改为逐主题纵向卡片。
- 平台页的系统价值图在窄屏下使用纵向层次，不展示 Top 10 Logo 墙或横向品牌表。

#### Downloads & Docs

- 公共资源卡在窄窗口下按单列排列，资源名称、用途、版本、离线可用性和访问动作保持同屏可读。
- Release 资产按操作系统分组；空间不足时允许纵向排列，但不得隐藏平台、架构、资产名或 SHA256 入口。
- “打开用户指南”始终是单一明确入口，不在页面中展开四种安装场景或命令步骤。
- 外部链接必须清楚标识其联网要求；断网时仍保留资源说明、正式名称和目标地址信息。

### 5.4 缩放与可访问性

- 浏览器放大到 150% 时，正文、导航、播放器控制、资源卡和文档入口仍可使用。
- 文字放大后不得发生内容裁切、按钮重叠或不可恢复的隐藏。
- 键盘焦点顺序与视觉阅读顺序一致。
- 所有交互状态必须有文字或形状差异，不能只依赖颜色。
- 触发全屏、打开文档、访问仓库和下载资产的控件应具备清晰可见的焦点状态和足够点击区域。

## 6. 全站统一内容合同

重要能力统一采用以下表达链：

> 工程问题 → 产品机制 → CLI 与状态 → 可检查工件 → 失败行为 → 可信边界

内容编辑阶段必须确保每个能力模块能够回答：

- 它解决什么生产问题？
- 输入是什么？
- AI-SDLC 做出什么约束或状态迁移？
- Writer、Reviewer、人工分别拥有什么权限？
- 成功后产生什么可检查结果？
- 失败、漂移或专家不可用时发生什么？
- 哪些能力没有被实现或不应被外推？

以上七项只用于内部编辑检查，不得直接渲染成前台问答表、逐项勾选或重复的七段卡片。前台仍采用连续的问题叙事、机制图、CLI 示例、状态和边界说明。

## 7. 页面一：AI-SDLC 2.0

### 7.1 首屏定位

主标题建议：

> AI Agent 会写代码，AI-SDLC 负责把它交付完成。

副标题应说明：AI-SDLC 是本地优先、可恢复、可验证的 AI 原生软件研发框架，通过五类 Loop 和有界动态专家复核管理目标、工件、反馈、证据与 Close。

首屏主要动作：

- `Explore Loop Engineering`
- `Explore Expert Review`
- `Explore Platform Capabilities`
- `Download v2.0.0`

### 7.2 端到端产品架构

展示以下主链路：

```text
Project Init / Adopt
  → Requirement
  → Design Contract
  → Implementation
  → Frontend Evidence（仅前端工作）
  → Local PR Review（跨阶段）
  → Close
```

需要明确：Frontend Evidence 是条件启用的 Loop；Local PR Review 是跨阶段复核，不是与前四类完全同构的强制阶段。

### 7.3 市场反向抽象出的系统价值

首页不平铺功能列表，也不从 CLI 模块名组织内容。基于市场研究，首屏之后只展开三个最强结果价值：

1. **从意图到可信交付**：从需求、设计、实现到证据和提交前审查，AI-SDLC 管理的是一项工作何时可以关闭，而不只是一次代码生成。
2. **证据先于完成**：任务验证、测试、构建、前端浏览器证据或独立复核不足时，系统保持未完成或进入明确失败状态。
3. **有界专家复核**：专家按当前结果风险临时选择、只读检查、将 Findings 交回原 Writer，并限制修复与复审次数。

三个主价值之后使用一个紧凑的“面向生产交付”区域，只提供以下摘要和进入 Platform Capabilities 的入口：

- **持久化项目状态**：checkpoint、status、handoff、recover 与 reconcile 让工程事实跨会话延续。
- **受治理的前端交付**：技术栈与组件方案确认、Provider/Style Pack/Theme 合同和浏览器证据在同一上下文中传递。
- **跨 AI 工具、本地优先接入**：新项目或已有项目均可接入；AI 工具可以替换，规则、状态与工件留在项目侧。

不得把 Codex、GPT 或其他模型写成产品主角。模型是可替换的执行引擎；AI-SDLC 管理工作状态、证据和完成权。不得在首页展示 Top 10 项目、Stars、Logo 墙或逐品牌优劣表。

### 7.4 AI-SDLC 2.0 工作流实录

录屏作为首页核心模块，位于产品架构和两项能力入口之后、下载与快速开始之前，不单独建立视频页面。

标题：

> AI-SDLC 2.0 工作流实录

副标题：

> 一条真实生产流，两种治理视角

模块要求：

- 原生播放、暂停、进度、音量、倍速和字幕。
- 明确的“全屏观看”按钮。
- 支持双击播放器进入全屏、键盘操作和退出全屏后回到原位置。
- 禁止自动播放。
- 提供 poster，避免视频加载前出现黑屏。
- 显示产品版本、录制日期、运行环境、时长、是否剪辑、字幕语言和视频 SHA256。
- 根据最终视频真实展示内容生成章节时间点，并记录 `features_shown` 与 `features_not_shown`。Requirement、Design Contract、Implementation、Frontend Evidence、Local PR Review、Dynamic Expert Review、Close 是候选章节，不强制视频在未录制前承诺全部覆盖。
- Loop Engineering 与 Dynamic Expert Review 页面可链接首页播放器的对应章节，不重复嵌入视频。

播放器状态：

- `Preparing`：视频尚未加入，显示中性占位，不伪装为可播放。
- `Loading`：正在读取本地媒体元数据。
- `Ready`：显示时长和播放控制。
- `Playback error`：说明视频缺失、损坏或浏览器不支持，并保留文字版工作流入口。

视频只能说明实际录下的操作，不能被描述为 release certificate、效果证明、完整审计证明或所有产品能力均已运行的证明。

### 7.5 状态、证据与失败语义

突出以下产品原则：

- 证据不足时不宣布完成。
- 审查输入发生漂移时拒绝使用旧结果关闭新工件。
- 专家不可用、超时或返回无效结果时进入 `needs_review`。
- 人工确认保留在关键技术方案和关闭边界中。
- 核心治理本地优先，代码外发默认关闭；所选 AI Provider 是否远程必须单独说明。

### 7.6 快速开始与公共资源摘要

首页只展示最短入口和状态摘要；`Downloads & Docs` 只提供《中文新用户指南》入口，完整安装步骤仅存在于手册正文，不在产品站页面重复展开。

包含：

- v2.0.0 版本身份。
- 三个平台支持范围。
- GitHub、README、中文用户指南和 v2 迁移指南入口。
- `Install → Verify → Init → Enter your first requirement` 的四步摘要。

首页的 Platform Capabilities 入口应落到平台页的价值总览，不落到某条零散命令或单一 Adapter 说明。

## 8. Platform Capabilities 内容规范

### 8.1 页面目标

说明 AI-SDLC 不是又一个 Skills 集合、专家人格库、长期记忆插件或 Spec 模板生成器，而是把这些工程方法接入同一项目身份、状态、证据、失败和 Close 语义的本地交付控制层。

页面标题建议：

> 从智能体技能到受治理的工程交付

副标题不得使用“功能更多”或“替代所有工具”的口径，应强调：AI Coding Agent 负责生成、修改和执行候选结果，AI-SDLC 负责保存项目事实、约束状态迁移、消费验证证据并决定下一步。

### 8.2 页面叙事顺序

1. AI Coding 生态正在从通用对话走向专业 Skills、Spec Workflow、角色分工、长期上下文和垂直工程能力。
2. 这些能力已经成为行业共同基础，不属于 AI-SDLC 独有。
3. AI-SDLC 的差异是把方法和能力组合成有状态、可失败、可恢复、可关闭的生产系统。
4. 用一张紧凑价值图呈现七个系统主题；“从意图到可信交付”“证据先于完成”“有界专家复核”只给摘要并深链接到两项核心机制页。
5. 只深入解释平台页负责的四组通用机制：项目事实恢复、工具可替换与本地接入、前端意图到浏览器证据、克制的工程控制。
6. 用“AI-SDLC 不作何种承诺”收束，并链接 Loop Engineering、Dynamic Expert Review 与 Downloads & Docs。

前台只描述不带品牌的市场能力层，不展示 Top 10 项目名、Stars、排名、Logo、总分或逐仓勾选矩阵。

### 8.3 七个系统价值主题

| 系统价值 | 解决的 AI Coding 痛点 | 用户结果 | 主要内容归属 |
|---|---|---|---|
| **从意图到可信交付** | 需求、计划、实现和验收分散，Agent 写完代码就把任务当作完成 | 在正式绑定 WorkItem 的交付路径中，需求、设计、任务、实现、验证和 Close 使用同一身份与工件链 | 首页总览；Loop Engineering 详解 |
| **证据先于完成** | 模型以文字总结代替真实测试、构建、浏览器和审查结果 | 完成由可检查证据与状态决定，不由 Agent 自报 | Platform 总览；Loop Engineering 详解 |
| **有界专家复核** | 同一上下文自审容易重复盲点，无界多 Agent 讨论又带来成本和冲突 | 按风险选择临时只读专家，Findings 回到原 Writer，修复和复审有上限 | Dynamic Expert Review 详解 |
| **可恢复的项目事实** | 会话压缩、中断或换人后，只剩不可靠的聊天记忆 | 跨会话继续读取项目事实、开放门禁、当前分支和下一步 | Platform Capabilities 详解 |
| **从前端意图到验收证据** | 技术栈、组件、主题、实现与浏览器验收各自为政，页面“能编译”却未必可用 | 技术方案、组件治理、主题约束、页面运行与浏览器验收在同一交付上下文中传递 | Platform Capabilities 详解 |
| **跨 AI 工具的一致治理** | 每换一个 Agent 就重新解释规则，项目治理被锁在工具聊天窗口里 | 更换 AI 工具时，项目侧规则、状态和工件不随聊天窗口消失 | Platform Capabilities 详解 |
| **与风险匹配的工程控制** | Agent 容易过度设计；治理系统也可能为了“规范”增加无价值阻断 | 对证据与身份严格，对代码精简保持建议性；遇到缺口时停止、求证或恢复 | Platform Capabilities 详解 |

同一主题只在主要归属页面完整解释。其他页面使用一段摘要和深链接，不能复制成重复 Feature Tour。

### 8.4 可恢复的项目事实

从生产问题“长任务跨会话后只剩聊天记忆，无法确认做到哪里”出发，展示以下机制链：

```text
checkpoint
  → status
  → human-readable handoff
  → compare branch and project artifacts
  → recover
  → reconcile when explicitly required
```

正文必须说明：

- checkpoint 记录阶段、分支、开放门禁和下一步，不保存“模型脑内状态”。
- `status` 读取当前本地事实；`recover` 对照 checkpoint、当前分支和项目工件给出恢复路径。
- `handoff` 服务于跨会话人工可读交接；重要事实仍需回到受治理工件验证。
- 发现 checkpoint 过期、分支变化或工件不一致时，不能把旧状态直接当作完成。

不得描述为自动捕获全部对话、向量长期记忆、跨项目语义回注、事务回滚或精确恢复到进程中断的上一条指令。

### 8.5 跨 AI 工具的一致治理与本地优先接入

页面使用名称“AI 工具适配矩阵”，不得写成“多 Agent 编排适配器”。

公开支持面包括 Claude Code、Codex、Cursor、VS Code/Copilot 和 generic 入口。每个 Adapter 将统一项目规则安装到对应工具的 canonical 路径；项目一次使用一个当前适配目标，也可以显式切换。

该能力的价值不是“支持工具数量最多”，而是：

- AI 工具是可替换执行入口。
- WorkItem、Loop 状态、checkpoint、验证工件和 Close 结果保留在项目侧。
- Adapter 差异不会改变同一项目的产品治理概念。

不得宣称多个工具共享实时会话、隐式上下文可无损迁移、所有 Adapter 具有完全一致的并行能力，或 AI-SDLC 在支持工具数量上领先市场。

同一章节用以下事实说明项目如何落地，但不把安装能力写成核心创新：

- 新项目通过 `init` 接入；已有项目通过 `init → adopt` 扫描和索引现有事实。
- Windows、macOS、Linux 提供受支持的在线安装路径和正式离线发行资产。
- 核心规则、状态、扫描、Loop、复核输入和证据工件本地优先。
- 代码外发默认关闭；如果用户选择远程 AI Provider，数据边界由该 Provider 单独决定。

“离线产品站”“离线安装包”“本地优先治理”和“完全离线 AI 推理”是四个不同概念，正文必须分开。

### 8.6 受治理的前端交付

前端能力不能写成“内置几个组件库”，而要表达为从方案选择到浏览器证据的治理链：

```text
frontend requirement
  → solution recommendation
  → explicit human confirmation
  → Provider / Style Pack / Theme contract
  → page and generation constraints
  → managed or project-owned implementation
  → browser entry and E2E evidence
  → Frontend Evidence state
```

三种路径必须分别标注成熟度：

| 路径 | 公开定位 | 必须说明的边界 |
|---|---|---|
| `vue3 / public-primevue / modern-saas` | **内置默认推荐路径** | PrimeVue 是第三方组件库；AI-SDLC 提供 Provider profile、语义组件映射、白名单、Style Pack、Theme Token、唯一 `theme.ts` 入口和交付约束。推荐不等于已经安装或应用。 |
| `vue2 / enterprise-vue2` | **内置企业兼容路径** | AI-SDLC 内置私有 Provider profile、组件映射、白名单和安装策略；信服云组件包、网络、授权与目标环境由企业侧提供，站点不公开私有 registry 地址，也不声称安装包内附送组件库。 |
| 自定义或不使用组件库 | **兼容执行与证据路径** | 不是第三个内置 Provider。项目需提供可加载 browser entry、执行上下文和兼容 Browser Gate 工件，再由 Frontend Evidence 消费。 |

Vue3 默认治理可展开但不堆砌依赖名：页面重点说明 `primary / surface / highlight` 主题语义、`theme.ts` 唯一入口、`pages/` 与历史 `views/` 的互斥约束，以及公开默认方案与高级自定义选择并存。

同一章节继续说明 AI-SDLC 如何消费并绑定代码与浏览器证据；测试、TDD、Review 与 Browser QA 已是市场常见能力，不能宣称由 AI-SDLC 首创：

- Implementation 任务记录 verification/evidence；测试、lint、build 和其他项目验证结果可进入关闭判断。
- Frontend Evidence 读取真实 browser entry、交互、console/page error、截图、视觉结果与基础可访问性证据。
- Playwright 是内置可选执行路径；Codex browser control、浏览器插件、Cypress、Selenium 或企业 E2E Runner 可以输出兼容的项目本地 Browser Gate 工件。
- 项目自己的 E2E Suite 与 Frontend Evidence 互补：前者执行项目测试策略，后者验证工件身份、时效、结构和 Loop 状态。

不得把基础可访问性检查写成 WCAG 认证，不得把 Frontend Evidence 写成完整 E2E 平台，也不得声称任意自定义前端无需配置即可运行。

### 8.7 与风险匹配的工程控制

该主题用于表达 AI-SDLC 对不同问题使用不同强度的控制：

- 输入身份、验证证据、审查漂移和 Close 条件使用明确状态与拒绝语义。
- 技术栈和前端方案在实现前保留人工确认。
- 代码体积、重复、复杂度和拆分只生成非阻断精简建议，由实现者结合行为正确性、维护成本和交付价值决定是否采纳。
- 缺少用户决定进入 `needs_user`，结果需修复进入 `needs_fix`，专家复核未完成进入 `needs_review`；不会把失败改写为通过，也不会无限自动重试。

这不是硬性极简策略，也没有公开实验支持“减少多少代码、时间或成本”。前台不得使用代码行数、效率或成本提升数字。

### 8.8 产品定位边界

页面结尾使用一个紧凑边界区，不再增加第二张市场分层图。AI-SDLC 2.0 不以最大 Skills/角色目录、长期语义记忆或自动学习、Coding Agent/IDE/通用 Agent Runtime、大规模 UI 设计知识库、通用云部署平台作为产品目标，也不引入持久投票、常驻委员会或第二套专家治理状态机。

该区只说明产品取舍，不做品牌比较；不得使用 Logo、红叉、冠军色、总分、Stars 或“有/无”勾选墙，也不得给出未经同任务、同模型、同预算实验支持的速度、质量或成本排名。

### 8.9 平台能力表达合同

每个主题必须使用以下链路生成最终正文：

> 现实生产问题 → 行业共同方法 → AI-SDLC 的组合方式 → CLI/状态 → 可检查工件 → 失败行为 → 明确边界

前台可以自然写成连续叙事、机制图和状态示例，不把这七项渲染为问答模板或评分清单。

## 9. Loop Engineering 内容规范

### 9.1 页面目标

说明 AI-SDLC 如何把“模型输出了一段代码”提升为“一个可检查、可反馈、可修复、可关闭的工程结果”。页面应呈现一条完整生命周期，而不是五个平铺功能卡。

### 9.2 内容顺序

1. AI Coding 的完成幻觉：目标漂移、跨阶段丢上下文、测试后补、界面只看截图、提交前才发现跨阶段回归。
2. 一个 WorkItem 的完整生命周期。
3. 四个阶段结果 Loop 与一个跨阶段 Local PR Review 的全景关系。
4. 五类 Loop 的共同协议。
5. 五类 Loop 逐项展开。
6. 反馈、修复和最多一次复审。
7. 漂移拒绝、`needs_review` 与恢复。
8. CLI、工件和开发者验证。
9. 适用场景与能力边界。

### 9.3 五类 Loop

#### Requirement

- 目标、范围、验收标准、角色和风险。
- 缺失信息需要澄清，不能直接进入编码。
- freeze 前重新绑定复核输入。

#### Design Contract

- 接口、数据、边界、技术栈和验证策略。
- 前端需求必须先进行技术栈、组件提供方和 Style Pack 确认。
- 默认推荐与高级自定义入口分层呈现。

#### Implementation

- 可执行任务、业务代码、测试和验证证据。
- 说明真实测试、lint、build、format 等工程结果如何进入关闭判断。
- 代码精简仅为非阻断建议。

#### Frontend Evidence

- 仅前端工作启用。
- 覆盖真实页面来源、交互、console、视觉、基础可访问性和浏览器证据。
- Browser 是外部证据 Gate，不是实现者或主审者。

#### Local PR Review

- 面向当前 Git 变更的跨阶段、提交前独立只读复核。
- 关注需求、设计、实现、测试和前端证据之间的回归。
- 不递归审查 Reviewer 或最终报告。

### 9.4 每类 Loop 的统一卡片

每个 Loop 使用同一内容结构：

1. 生产问题。
2. 输入工件。
3. Writer 产出。
4. 当前状态与下一步。
5. 专家选择与选择理由。
6. `input_digest`。
7. `review_snapshot`。
8. Findings 回流。
9. 修复与复审上限。
10. close/freeze 条件。
11. 失败语义和恢复方式。

### 9.5 支撑能力

支撑能力在 Loop 页面中必须嵌入相应生命周期节点；其平台级机制统一深链接到 Platform Capabilities，不在本页复制成独立 Feature Tour：

- `init` 与已有项目 `adopt`。
- checkpoint、status、recover。
- 前端 Provider、Style Pack、Theme Token 和 `theme.ts` 唯一入口。
- 人工方案确认。
- 可检查工件和本地优先边界。

## 10. Dynamic Expert Review 内容规范

### 10.1 页面目标

说明 AI-SDLC 如何把专家协作和对抗限制在可预测、可检查、不会无限扩张的复核关系中。

产品能力名称使用：

> 有界动态专家复核 / Bounded Dynamic Expert Review

`Bounded Dynamic Expert Review Graph（BDERG）` 只作为机制图标题，不作为独立运行时产品名称。

### 10.2 BDERG 事实声明

机制图旁固定说明：

> BDERG 是对 Writer 与临时只读专家复核关系的说明性拓扑，不是持久化 Graph、调度器、专家注册表或第二状态机。

### 10.3 三种职责

- **Writer**：当前或原实现代理，负责生成实质结果，并拥有唯一修复权。
- **Primary Expert**：根据当前内容和主风险选择的一名只读专家。
- **Cross-risk Expert**：仅在存在明确第二风险面时加入的最多一名只读专家。

Cross-risk Expert 是条件角色。产品不得宣称所有 Loop 永远运行三个 Agent。

### 10.4 机制图

```text
Writer 产出
  → Loop 构造 digest-bound Review Input
  → Primary Expert（必选）
  → Cross-risk Expert（存在明确交叉风险时可选，最多一名）
  → 合并 Findings（不是 verdict）
  → 原 Writer 最多修复一次
  → 最多复审一次
  → 原有 Loop Close 或 needs_review
```

### 10.5 Bounded / Dynamic / Expert / Review Graph

- **Bounded**：最多两名只读专家；同一结果最多一次修复和一次复审；禁止递归审查。
- **Dynamic**：角色由当前工件内容和风险信号决定，不使用固定常驻专家名单。
- **Expert**：新鲜、只读、无修改权的临时上下文，不代表认证身份或永久权威。
- **Review Graph**：解释一次复核中的关系和信息流，不代表持久图数据库或 Agent 网络。

### 10.6 输入绑定和职责隔离

页面必须解释：

- 所选的一至两名专家读取同次生成、由 `input_digest` 绑定的 `review_snapshot`。
- 专家不得重新打开会继续变化的工作区路径。
- 专家只返回 Findings，不能直接修改工件。
- Findings 不构成 verdict、passed、closed 或 Close credential。
- 修复责任始终返回原 Writer。
- close/freeze 在同一进程重新计算输入，发生漂移时拒绝关闭。

### 10.7 跨风险三职责实例

选用“为已有系统增加具备权限控制的发布审批功能”作为说明案例，覆盖：

- 公共 API 兼容性。
- 授权与安全风险。
- 前端交互与回归风险。

该实例用于展示 Writer、Primary Expert 和 Cross-risk Expert 三种实际职责。必须同时说明：这是明确交叉风险下的选择结果，不代表所有结果都固定使用三种职责。

### 10.8 机制对比

只比较可验证的机制差异：

- 自审还是新鲜只读上下文。
- 固定角色还是按风险选择。
- 审查输入是否绑定。
- Reviewer 是否拥有修改权。
- Finding 由谁修复。
- 修复复审是否有上限。
- 专家失败如何表达。
- 完成权属于模型还是原有 Loop 状态。

没有同模型、同任务、同预算的正式实验前，不得声称“发现更多缺陷”“质量提高”“交付更快”或“成本更低”。

## 11. 页面五：Downloads & Docs

### 11.1 页面目标

集中呈现版本、仓库、发布资产、README、中文新用户指南和迁移指南等公共资源，让用户快速找到权威入口。该页面不复制安装手册正文，也不把四种安装场景设计成站内页面或独立功能模块。

### 11.2 公共资源

- GitHub 仓库。
- v2.0.0 Release。
- README。
- 中文新用户指南。
- v2 迁移指南。
- Windows、macOS、Linux 离线资产与 SHA256。
- 三个平台只提供指向正式 Release 资产和同名 `.sha256` 的下载链接，不在产品站目录中附带安装包。
- 在线安装器与适用前提。
- License 和产品契约。

### 11.3 页面呈现合同

- 每项公共资源使用统一资源卡，显示名称、用途、版本或适用范围、访问入口和离线可用性。
- 《中文新用户指南》只显示手册简介、适用对象和“打开用户指南”入口，不在资源卡下展开安装命令。该入口优先打开离线包内与正式源文件摘要绑定的本地只读副本；联网权威源链接作为次级入口。
- 四种安装场景、逐步命令、预期输出和异常处理全部属于用户手册内部结构，不进入产品站主导航、二级导航或页面章节。
- 本地用户指南是文档资源，不是第六个产品视图；打开后提供明确返回 `Downloads & Docs` 的入口。
- GitHub、Release、README 和用户指南均为公共内容，不归属 Loop Engineering 或 Dynamic Expert Review。
- 离线安装包只提供正式 Release 下载链接、资产名和 SHA256 校验入口，站点交付目录不携带安装包二进制。

## 12. 独立文档资产：中文新用户指南

本节定义《中文新用户指南》的内容合同，用于后续单独撰写和验收手册，不代表产品站页面结构。网站只在 `Downloads & Docs` 中提供该手册的入口和简介。

### 12.1 路径选择

用户只需要完成三个选择：

1. 项目状态：`Existing Project` 或 `New Project`。
2. 安装方式：`Offline Package` 或 `Online Install`。
3. 操作系统：离线路径选择 `Windows AMD64`、`macOS Apple Silicon` 或 `Linux AMD64`；在线路径选择 `Windows`、`macOS` 或 `Linux`，随后按 Python 3.11+、Git、包管理器和权限条件判断是否可继续。

随后只显示一条完整路径。浏览器可以建议平台，但必须由用户确认，不能静默判断。

四条路径固定为：

1. `Existing Project · Offline Package`
2. `Existing Project · Online Install`
3. `New Project · Offline Package`
4. `New Project · Online Install`

每条路径及每个平台页签必须完全自包含。禁止出现“安装步骤同上”“参见另一章”“查看 x.x 节”或要求用户在四条路径之间拼接步骤的文字。

每条路径在用户手册中按四个阶段分组并显示进度：

1. `Install`
2. `Verify`
3. `Initialize`
4. `Start`

原子步骤仍逐项保留，但不得把十几项操作同时平铺为一个无层级的长清单。

### 12.2 步骤内容合同

每一步固定包含六个区域：

1. **Step N｜本步目标**
2. **在哪里执行**：明确终端类型和当前目录。
3. **复制并运行**：完整命令块和复制动作。
4. **你应该看到**：只列稳定输出锚点，不复制长日志。
5. **如果结果不同**：本步骤内的最短修复、重试或停止条件。
6. **下一步**：明确下一步骤和动作。

命令块要求：

- 可整块复制。
- 不让用户自行拼接零散参数。
- 路径变量在当前路径的第一段命令中一次定义，后续沿用。
- 明确区分安装目录和业务项目目录。
- 正常成功路径不要求额外执行 `adapter status` 或 `run --dry-run`。

### 12.3 路径一：Existing Project · Offline Package

完整顺序：

1. 确认正式平台包名称、系统和 CPU 架构。
2. 使用页面提供的正式 Release 链接下载压缩包及同名 `.sha256` 文件；如果已经由管理员或其他可信方式提前获得，则直接确认两个文件均存在。文件缺失时停止。产品站本身不附带安装包。
3. 记录已有项目的绝对路径。
4. 查看 Git 状态和现有未提交改动；建议先提交、暂存或创建可恢复 checkpoint，不删除业务文件。
5. 校验压缩包 SHA256；不一致时立即停止。
6. 解压到长期安装目录。
7. 执行离线安装器并选择是否写入 PATH。
8. 保存安装器输出的 Direct CLI 绝对路径。
9. 使用 Direct CLI 验证版本严格为 `2.0.0`。
10. 返回已有项目根目录。
11. 在执行 `init` 前展示其声明写入范围：`.ai-sdlc/` 治理文件、`AGENTS.md` 或所选 Adapter 指引文件等；不得把业务源码修改描述为预期行为。
12. 使用 Direct CLI 执行 `init .`。
13. 选择真实使用的 AI Agent 与 Shell。
14. 核对 `Initialized AI-SDLC project`、`Result` 和 `Next`，并再次查看 Git delta；出现声明范围外的业务文件修改时立即停止。
15. 执行 `adopt .`。
16. 核对“原任务文件不会被修改”、识别结果和推荐继续点。
17. 在所选 AI 工具中输入增量需求。

### 12.4 路径二：Existing Project · Online Install

完整顺序：

1. 确认网络、Git、Python 3.11+ 和必要权限。
2. 记录已有项目绝对路径并检查 Git 状态；建议先提交、暂存或创建可恢复 checkpoint。
3. 创建独立于业务项目的长期安装目录。
4. 从不可变 `v2.0.0` 标签或固定发布提交取得 `install_online.ps1` 或 `install_online.sh`，保存到本地后再执行。
5. 使用站点随 v2.0.0 内容清单冻结的安装器 SHA256 校验本地脚本；若未来 Release 正式发布同名校验文件，则以 Release 校验值为准。校验不一致时停止。
6. 显式指定安装环境目录，执行已校验的在线安装器。禁止 `curl | shell`、`Invoke-Expression` 或下载后立即执行的单行命令。
7. 保存 Direct CLI 绝对路径。
8. 使用 Direct CLI 验证版本为 `2.0.0`。
9. 返回已有项目根目录。
10. 在执行 `init` 前展示其声明写入范围：`.ai-sdlc/`、`AGENTS.md` 或所选 Adapter 指引等治理文件。
11. 执行 `init .`，选择 AI Agent 与 Shell。
12. 核对 `Result / Next` 并检查 Git delta；出现声明范围外的业务文件修改时停止。
13. 执行 `adopt .` 并核对识别结果。
14. 在所选 AI 工具中输入增量需求。

在线安装不得默认把 `.venv` 创建在业务项目根目录中。在线安装正文必须基于 v2.0.0 正式安装器，而不是只给裸 `pip install` 命令。

### 12.5 路径三：New Project · Offline Package

完整顺序：

1. 确认平台包、系统和 CPU 架构。
2. 使用页面提供的正式 Release 链接下载压缩包及同名 `.sha256` 文件；如果已经提前下载，则直接确认两个文件均存在。文件缺失时停止。产品站本身不附带安装包。
3. 校验 SHA256；不一致时停止。
4. 解压到长期安装目录并运行安装器。
5. 保存 Direct CLI 并验证版本为 `2.0.0`。
6. 创建新的空项目目录。
7. 进入空项目目录。
8. 执行 `init .`。
9. 选择真实使用的 AI Agent 与 Shell。
10. 核对 `Initialized AI-SDLC project`、`Result` 和 `Next`。
11. 解释新空项目出现 open gates 可能是正常状态，表示尚缺需求、设计或验证工件。
12. 在所选 AI 工具中输入第一条自然语言需求。

全新项目不得执行 `adopt .`。

### 12.6 路径四：New Project · Online Install

完整顺序：

1. 确认网络、Git、Python 3.11+ 和必要权限。
2. 创建独立安装目录和空项目目录。
3. 从不可变 `v2.0.0` 标签或固定发布提交取得正式在线安装器，先保存到本地。
4. 使用站点随 v2.0.0 内容清单冻结的安装器 SHA256 校验本地脚本；校验不一致时停止。
5. 显式指定长期安装环境并执行已校验脚本；禁止 `curl | shell`、`Invoke-Expression` 或下载后立即执行。
6. 保存 Direct CLI 并验证版本为 `2.0.0`。
7. 进入空项目目录。
8. 执行 `init .`。
9. 选择 AI Agent 与 Shell。
10. 核对 `Result / Next`。
11. 在所选 AI 工具中输入第一条自然语言需求。

全新项目不得执行 `adopt .`，安装环境不得与业务项目目录混在一起。

### 12.7 就地异常处理

每条路径必须在相关步骤中重复提供处理办法，不依赖公共 FAQ：

- 下载失败、超时或 404。
- SHA256 不一致。
- 平台或 CPU 架构不匹配。
- PowerShell 阻止脚本执行。
- Python 3.11+、Git、Homebrew、apt、dnf、yum 或权限不可用。
- `ai-sdlc` 不在 PATH。
- 当前终端仍命中旧版本。
- `No module named ai_sdlc`。
- Direct CLI 路径失效。
- 安装目录与项目目录混淆。
- 安装后移动或删除运行环境。
- `init` 选错 AI Agent 或 Shell。
- `init` 出现正常 open gates。
- 已有项目未先 `init` 就执行 `adopt`。
- `adopt` 识别为零。
- `adopt` 推荐继续点错误，需要使用 `--prefer`。

用户手册底部可以提供额外的 `Troubleshooting` 索引，但四条成功路径不依赖该索引。

## 13. 离线交付合同

建议交付结构：

```text
AI-SDLC-2.0-Product-Site/
├── index.html
├── docs/
│   └── USER_GUIDE.zh-CN.html
└── assets/
    ├── ai-sdlc-product-walkthrough.mp4
    ├── ai-sdlc-product-walkthrough.vtt
    └── video-poster.webp
```

约束：

- 双击 `index.html` 即可浏览全部文字内容和导航。
- 这是“单 HTML 入口的离线站点包”，不是“所有媒体均内嵌的单文件 artifact”。
- `docs/USER_GUIDE.zh-CN.html` 是从正式中文用户指南源文件生成并绑定版本与 SHA256 的本地只读副本，只由 `Downloads & Docs` 资源卡打开，不进入主导航，也不作为第六个产品视图。
- HTML、CSS、JavaScript、图标和字体 fallback 不依赖 CDN。
- 视频、字幕和 poster 使用固定本地相对路径。
- 产品站交付目录不包含 Windows、macOS 或 Linux 离线安装包；下载按钮仅指向正式 Release 资产。
- 不需要 Node.js、Python、Web Server 或后台进程。
- 视频缺失不影响正文阅读。
- 不建议将完整 MP4 Base64 编码进 HTML，以避免体积、内存和兼容性问题；如果后续明确把“单物理文件”提升为硬约束，则必须重新评估视频体积和浏览器兼容性后再修改交付合同。
- GitHub、Release 和下载链接在联网时可访问；离线时正文、命令和产品说明仍可完整阅读。

## 14. 内容语气与禁用表达

### 14.1 推荐语气

- 具体、克制、工程化。
- 先说明用户问题，再解释机制。
- 明确写出状态和失败边界。
- 命令与预期结果使用可复核事实。
- 营销表达服务于产品价值，不取代技术解释。

### 14.2 禁用表达

- “首创多 Agent 软件工程系统”。
- “行业绝对领先”。
- “保证提高质量、速度或成功率”。
- “多 Agent 共识决策”。
- “自动自愈直到成功”。
- “全自动无人交付”。
- “不可篡改的完整审计链”。
- “每次固定三个 Agent”。
- “完全离线运行”，除非明确限定为核心治理和本地工件；远程 AI Provider 必须单独说明。

## 15. 内部编辑完整性检查

本节用于内容制作与验收，不进入前台页面。

### 15.1 Platform Capabilities 完整性

- 产品主题从冻结 Top 10 市场样本反向抽象，不从 AI-SDLC 模块名直接罗列。
- 市场通用能力与 AI-SDLC 组合式差异分开说明。
- 七主题价值体系在首页和三个机制页之间具有唯一主要归属，不重复成为功能墙。
- Platform Capabilities 只完整展开“可恢复的项目事实”“跨 AI 工具的一致治理与本地优先接入”“受治理的前端交付”“与风险匹配的工程控制”四组机制；其余三个主题只摘要并深链接。
- “可恢复的项目事实”不冒充长期语义记忆。
- AI Tool Adapter 不冒充多 Agent 并行编排。
- 前端三条路径分别标注“内置默认推荐路径”“内置企业兼容路径”“兼容执行与证据路径”。
- 代码精简保持非阻断，证据与 Close 保持严格状态语义。
- 前台不展示 Top 10 名称、Stars、Logo 墙或逐项目优劣矩阵。
- 明确列出 AI-SDLC 不做 Skills 市场、长期记忆、IDE、Agent Runtime、设计知识库或自动部署平台。

### 15.2 Loop Engineering 完整性

- 清晰目标输入。
- 自动拆分与计划。
- 工具、脚本或外部证据调用。
- 执行结果判断、反馈、修复或停止。
- 多轮但有界的任务调整。
- 异常和失败处理。
- 人工方案确认。
- 状态与过程可视化。
- 多类型任务和五类 Loop。
- 可运行安装入口、README 和新用户指南。

### 15.3 Dynamic Expert Review 完整性

- Writer、Primary Expert、Cross-risk Expert 三种清晰职责。
- 明确的读写权限和信息传递。
- 同一 digest-bound snapshot。
- Findings 回流和原 Writer 修复。
- 最多一次修复复审。
- 冲突、失败和 `needs_review`。
- 人工确认与完成权边界。
- 与单上下文自审、固定 Reviewer 和普通 Subagent 的机制对比。
- 明确交叉风险案例。

内部检查只验证内容完整性，不得以“要求覆盖矩阵”的形式渲染到产品站。

## 16. 验收标准

### 16.1 内容验收

- 五个主导航全部使用英文产品名称。
- 主导航顺序固定为 `AI-SDLC 2.0 → Loop Engineering → Dynamic Expert Review → Platform Capabilities → Downloads & Docs`。
- 前台不出现赛事、评分或材料响应话术。
- Platform Capabilities、Loop Engineering 和 Dynamic Expert Review 三个机制页面都可以独立理解，不依赖先阅读首页。
- 所有能力事实与 v2.0.0 标签树一致。
- BDERG 不被描述为持久运行时或第二状态机。
- Cross-risk Expert 的条件性和最多两名只读专家边界清晰。
- 未提供正式实验时不出现性能、质量或效率提升数字。
- Platform Capabilities 可以独立说明 AI-SDLC 与 Skills、Spec Workflow、Memory Plugin 和垂直工程能力的控制层差异。
- 市场研究样本记录查询日期、许可证、Stars 和冻结 source ref；前台不显示榜单。
- 所有比较使用 `industry baseline / AI-SDLC integration / boundary`，不把“非核心定位”写成“竞品没有”。
- `enterprise-vue2` 不被写成随产品附送的私有组件包；自定义/无组件库路径不被写成第三个内置 Provider。

### 16.2 视频验收

- 播放器位于首页。
- 支持播放、字幕、章节和全屏。
- 不自动播放。
- 视频缺失或错误时正文仍可使用。
- 元数据与实际视频一致。

### 16.3 新用户指南验收

- 四条路径全部自包含。
- 每条路径分别覆盖 Windows、macOS 和 Linux 的受支持平台。
- 每一步都有命令、预期结果、异常处理和下一步。
- 离线包校验失败时必须停止。
- 每条路径都使用 Direct CLI 验证 `2.0.0`，不假设 PATH 已刷新。
- 已有项目严格执行 `init → adopt`。
- 全新项目不执行 `adopt`。
- 三个平台分别完成一次从零人工复现后，才能把对应路径标记为可用。

### 16.4 离线站点验收

- 直接双击 `index.html` 可浏览所有正文。
- 无后台、无构建步骤、无外部 CDN 依赖。
- Hash 导航、资源卡、文档入口和外部链接提示在本地文件协议下正常工作。
- 断网时可以从 `Downloads & Docs` 打开本地中文用户指南并返回产品站；联网权威源不可达不影响手册阅读。
- 所有本地资源使用相对路径。
- 在线链接不可用时不影响离线正文。
- 离线安装包不属于站点交付物；下载链接在联网时指向正式 Release，断网时仍应显示资产名、平台和校验说明。

### 16.5 笔记本与窄窗口验收

- 在 `1366 × 768`、`1280 × 720`、`1440 × 900` 和 `1024 × 768` 下逐页检查全部五个产品视图。
- 在 `1366 × 768` 的 Windows 125% 缩放等效空间下完成导航、播放、全屏、公共资源浏览和用户指南入口操作。
- 浏览器缩放至 150% 后，核心内容和操作仍可到达。
- 页面主体无横向滚动，长命令只在命令块内部滚动。
- Hero、播放器、机制图、对比内容和公共资源列表均不依赖大屏才能阅读。
- Hash 导航目标不被吸顶导航遮挡。
- 大屏版本只增加空间和信息并列程度，不出现仅在大屏可见的关键内容。

## 17. 非目标

本设计不负责：

- 编写或生成 HTML。
- 确定最终颜色、字体、插画和动画风格。
- 录制、剪辑或验证产品视频。
- 虚构演示运行、实验数据、性能数据或效果数字。
- 修改 AI-SDLC 2.0 产品代码、安装器或发布资产。
- 替代 README、正式用户指南或 v2 迁移文档的源文件维护。
