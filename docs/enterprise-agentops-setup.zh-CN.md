# 企业 AgentOps 接入说明

AI-SDLC 可以把本地运行事实写入 outbox，并投递到企业 AgentOps Gateway。配置文件只保存端点、策略和令牌环境变量名，不保存令牌值。

## 配置原则

- 网关必须使用组织批准的地址；
- 令牌通过环境变量注入；
- 日志不得输出令牌、Cookie 或 Authorization 内容；
- 投递失败时保留本地 outbox，允许人工检查后重试；
- 未配置网关不影响本地 AI-SDLC 主流程。

## 查看配置入口

```powershell
ai-sdlc enterprise configure --help
```

按组织要求写入用户级企业配置，然后在当前终端设置对应的令牌环境变量。例如：

```powershell
$env:AGENTOPS_GATEWAY_TOKEN = "<由企业密钥系统注入>"
```

不要把该命令及真实值写入仓库脚本、README、日志或截图。

## 诊断

```powershell
ai-sdlc agentops doctor
```

诊断结果只应显示 `configured` 或 `missing`，不应回显敏感值。

## 查看投递状态

```powershell
ai-sdlc agentops status
```

重点检查：

- 最近 outbox 的生成状态；
- 网关回执；
- 失败原因与重试建议；
- 本地工件路径；
- 当前分支和提交身份。

## 重试

网络、网关或凭据问题解决后执行：

```powershell
ai-sdlc agentops retry
```

重试只处理已持久化 outbox，不会重新执行研发任务。

## CI 建议

1. 在 CI Secret 中保存令牌；
2. 把 Secret 映射到企业配置声明的环境变量；
3. 先运行 `agentops doctor`；
4. 执行 AI-SDLC 工作流；
5. 失败时保存脱敏后的 outbox 和回执作为 artifact；
6. 不在公开日志中打印请求头或完整响应体。

## 验收

- 未设置令牌时显示 `missing`；
- 设置令牌后显示 `configured`；
- 成功投递产生可关联回执；
- 失败投递保留本地 outbox；
- `retry` 不重复执行工程任务；
- 仓库与构建产物中不存在真实令牌。
