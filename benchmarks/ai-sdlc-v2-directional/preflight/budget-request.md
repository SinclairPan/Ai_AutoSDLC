# 正式运行预算确认

请仅在接受以下完整边界后授权正式运行：

- 模型：`gpt-5.6-sol`
- 推理强度：`high`
- Writer 会话：15
- Expert 会话：4（A11 Primary × 3；安全 Cross-risk × 1）
- Provider 会话硬上限：19
- 技术重试：0
- Expert 复审：0
- Token 估算：未知（Provider 权威 usage 尚未产生）
- 货币成本估算：未知（不臆造价格或使用未冻结价格表）
- 矩阵不完整时：停止并标记 incomplete，不宣布赢家
- 当前 Provider 调用：0
- 当前授权状态：未请求

确认字段：

- `approve_directional_provider_run`: `true | false`
- `approved_session_cap`: 必须为 `19`
- `approved_model`: 必须为 `gpt-5.6-sol`
- `approved_reasoning_effort`: 必须为 `high`
- `accept_null_cost_until_authoritative_usage`: `true | false`
- `confirmed_at_utc`: 待确认
