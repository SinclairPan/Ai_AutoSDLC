# 2026-08-21 当前 AI-SDLC 三层收益数据报告

## 口径

- 类型：Evidence-anchored synthetic benchmark / 证据锚定合成基准。
- 模型标签：`gpt-5.6-sol`。
- Reasoning effort：`high`。
- 场景选择：`advantage-aligned engineering scenarios`。
- 场景数：50。
- 产品发布源提交：`9a59a3edd483b0e6526b67b03fbfcac3ba48d2e4`（`v3.0.1`）。
- Provider 会话：0；本轮未重新运行 Provider，也未重新计算数值。
- 数值来源：保留 2026-08-21 已冻结的 `gpt-5.6-sol/high` 合成数值，仅将能力证据、产品版本和来源元数据重新绑定到 v3.0.1；更早的 GPT-5.4 数值仍未复用。

## 数据资产

| 数据集 | SHA-256 |
| --- | --- |
| `loop-benefit-data.json` | `f919b51d9e098cb74c1c224bb65870d8868b5e37b961a10316bd597e1d1bbb91` |
| `expert-review-benefit-data.json` | `975f66f278181039c564a5d24d775e1132dbc59b722038958c5a4e9ee47a3abe` |
| `overall-comparison-data.json` | `50a1d62826667f02ffb7a3dd48ae31899458fb5462ed853608c221007253dcdf` |

## Loop Engineering 主数据（既有合成数值）

| 指标 | 裸 LLM | AI-SDLC 五阶段 Loop | 改善 |
| --- | ---: | ---: | ---: |
| 需求理解 | 76% | 94% | +18 pts |
| 需求设计 | 64% | 93% | +29 pts |
| 需求开发追踪覆盖 | 55% | 92% | +37 pts |
| 代码质量 | 58% | 87% | +29 pts |

五阶段累计缺陷 / 缺口消除进度为 Requirement 39%、Design Contract 58%、Implementation 74%、Frontend Evidence 85%、Local PR Review 92%。

## Dynamic Expert Review 主数据（既有合成数值）

| 指标 | 无专家 | 五阶段动态专家 | 改善 |
| --- | ---: | ---: | ---: |
| 阶段问题发现率 | 54% | 89% | +35 pts |
| 五阶段生成物完整度 | 79% | 94% | +15 pts |
| 残余严重缺陷 | 9 | 2 | -78% |
| 最终首轮验收率 | 76% | 91% | +15 pts |

五阶段生成物质量分别从 80 / 78 / 76 / 73 / 75 提升至 94 / 93 / 91 / 92 / 95。

## 整体三模式主数据（既有合成数值）

| 指标 | 裸 LLM | LLM + Superpowers 6.3.0 | LLM + 当前 AI-SDLC |
| --- | ---: | ---: | ---: |
| 验收一次通过率 | 52% | 69% | 90% |
| 需求实现偏移率 | 23% | 14% | 4% |
| 前端规范一次符合率 | 43% | 63% | 94% |
| 人工接管率 | 29% | 18% | 7% |
| 总交付成本指数 | 100 | 82 | 61 |

当前 AI-SDLC 组包含 Requirement、Design Contract、Implementation、Frontend Evidence、Local PR Review 五阶段 Loop，以及五阶段风险路由动态专家对抗。Superpowers 组限定为 6.3.0 单 Agent repo-skill 适配。

## 发布限制

1. 该数据用于官网、路演和产品营销，不是客户生产统计。
2. 场景有意识选择需求边界、复杂前端、跨层实现、安全、质量修复和长任务恢复等 AI-SDLC 优势场景。
3. 完整表必须保留全部指标；不得只截取领先项并删除持平或负向项。
4. 页面必须在数据附近显示“证据锚定合成基准”和场景选择披露。
5. 后续如果更换模型、Superpowers 版本、AI-SDLC 能力清单或场景矩阵，三个数据文件必须整体重建。

## 离线页面验收

- 验收输入提交：`343f087d43dd803e1ea2154d29fade92338a6da3`。
- 浏览器 receipt SHA-256：`f4c4040c736d18f1e738a532e9723a40367ae58d8c1d89ac16e697c4773919f5`。
- Loop 截图 SHA-256：`1f2547a0c88cf285f572f75ad0fc9e4003800e4bbec177bfb85ec901247886c4`。
- Dynamic Expert Review 截图 SHA-256：`0959de97d6b07aebb0a0b0300cb4097eca7ff8b63acf2821e5b605f131c0630f`。
- Platform Capabilities 截图 SHA-256：`d4cf65d91052a757b658bcbacf1c014a2d3afb4ed537b93baf47ce4530d92f5f`。
- 浏览器状态：80 / 80 通过。
- 无 JavaScript 分组：12 / 12 通过。
- viewport / ancestor clipping：0。
- 控件重叠：0。
- accessibility failures：0。
- console / page / network failures：0。
- request ownership：33 个请求全部属于外部新鲜站点副本，远程请求 0。
