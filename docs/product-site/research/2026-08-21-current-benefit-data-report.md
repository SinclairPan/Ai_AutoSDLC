# 2026-08-21 当前 AI-SDLC 三层收益数据报告

## 口径

- 类型：Evidence-anchored synthetic benchmark / 证据锚定合成基准。
- 模型标签：`gpt-5.6-sol`。
- Reasoning effort：`high`。
- 场景选择：`advantage-aligned engineering scenarios`。
- 场景数：50。
- 源提交：`5c48a6fb80dad9fecdf89324e37814b99aa27e77`。
- Provider 会话：0；本次通过冻结能力清单和场景权重确定性复算。
- 历史 GPT-5.4 数值：未复用；只沿用十四项评价指标与任务分布校准。

## 数据资产

| 数据集 | SHA-256 |
| --- | --- |
| `loop-benefit-data.json` | `0f9f7445047e4c83e92798e21120a37b4543bf0a9e1fa887b3b0f219e2dfefc2` |
| `expert-review-benefit-data.json` | `2c357168e9b16ad96ec050fd0e52f2efeecdd690ca0977c7aac18722304863f8` |
| `overall-comparison-data.json` | `24cba373aa9d7939eb1ff82817cad45e8cb2649d5236638451a74e30be933b4a` |

## Loop Engineering 主数据

| 指标 | 裸 LLM | AI-SDLC 五阶段 Loop | 改善 |
| --- | ---: | ---: | ---: |
| 需求理解 | 76% | 94% | +18 pts |
| 需求设计 | 64% | 93% | +29 pts |
| 需求开发追踪覆盖 | 55% | 92% | +37 pts |
| 代码质量 | 58% | 87% | +29 pts |

五阶段累计缺陷 / 缺口消除进度为 Requirement 39%、Design Contract 58%、Implementation 74%、Frontend Evidence 85%、Local PR Review 92%。

## Dynamic Expert Review 主数据

| 指标 | 无专家 | 五阶段动态专家 | 改善 |
| --- | ---: | ---: | ---: |
| 阶段问题发现率 | 54% | 89% | +35 pts |
| 五阶段生成物完整度 | 79% | 94% | +15 pts |
| 残余严重缺陷 | 9 | 2 | -78% |
| 最终首轮验收率 | 76% | 91% | +15 pts |

五阶段生成物质量分别从 80 / 78 / 76 / 73 / 75 提升至 94 / 93 / 91 / 92 / 95。

## 整体三模式主数据

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
