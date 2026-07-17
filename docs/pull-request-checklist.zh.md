# Pull Request 检查清单

## 范围与契约

- [ ] 变更目标、范围和验收标准明确；
- [ ] 需求、设计、任务与实现可以相互追踪；
- [ ] 未修改授权范围外的文件；
- [ ] 用户可见行为与文档一致。

## 代码与测试

- [ ] 新行为有自动化测试；
- [ ] 修复包含可复现的回归测试；
- [ ] `uv run pytest -q` 通过；
- [ ] `uv run ruff check src tests scripts` 通过；
- [ ] 没有提交密钥、令牌、环境文件或本地绝对路径。

## AI-SDLC 门禁

- [ ] `ai-sdlc run --dry-run` 没有未解释的失败；
- [ ] `uv run ai-sdlc verify constraints` 没有 BLOCKER；
- [ ] checkpoint 与当前分支、任务和证据一致；
- [ ] 前端变更包含浏览器、视觉或可访问性证据；
- [ ] 高影响动作具备确认、回滚或恢复路径。

## 对抗审查

- [ ] 已执行 `ai-sdlc pr-review doctor`；
- [ ] 审查输入来源和范围正确；
- [ ] BLOCKER 与 REQUIRED 发现均已处理；
- [ ] 复审结果与最终 attestation 已生成；
- [ ] 代码外发策略符合项目要求。

## 发布相关变更

- [ ] `README.md`、`docs/releases/v1.0.0.md`、`USER_GUIDE.zh-CN.md` 与 `packaging/offline/README.md` 描述一致；
- [ ] 当前发布版本为 `1.0.0`；
- [ ] 包版本、源码版本、锁文件和工作流一致；
- [ ] README、用户指南、发行说明和打包说明一致；
- [ ] 离线包名称与 manifest 一致；
- [ ] Windows、macOS、Linux smoke 覆盖对应平台；
- [ ] `python scripts/validate_public_release_identity.py .` 通过。
