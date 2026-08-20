# AI-SDLC 2.0 方向性效益实验预检

- 当前状态：离线预演完成，正式 Provider 调用为 0。
- 实验定位：`directional engineering observation`，不冒充审计级或统计显著性结论。
- 评估边界：复用只读固定公开任务与 `legacy-directional-evaluator`；actual r2 authority 仍为 `NO-GO`。
- 冻结矩阵：5 arms × 3 tasks × 1 run，共 15 个 Writer 单元；按任务分块并轮转臂顺序。
- 会话预算：15 个 Writer + 3 个 A11 Primary + 1 个安全 Cross-risk，硬上限 19；无技术重试、无复审、无预留会话。
- 失败处理：模型超时、非零退出或无效输出记为对应单元终态并继续；Provider/网络/限流/主机/隔离/账本异常中止整个矩阵并标记不完整。
- 可发布门槛：15 个单元必须全部形成原始 receipt；不完整矩阵不发布排名或赢家。
- 统计边界：`n=3 per arm`、`single run per task`、`not statistically significant`、`not production SLA`、`no generalization`。
- 指标边界：主质量仅来自盲评外部结果；过程可审计性单列。Token 与货币成本只接受 Provider 权威字段，当前均为 `null`。
- 展示边界：首页只呈现 P、S、A11 三条产品路径；A00、A10 明确标记为研究控制；展示原始配对值、失败样本和质量—成本前沿，不挑选“赢家”。

正式调用前仍需一次独立预算确认；本预检不构成调用授权。
