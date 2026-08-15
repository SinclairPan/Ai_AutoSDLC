# AI-SDLC 框架问题登记

本文件用于登记当前开发中发现的框架缺陷、规则缺口、状态漂移和门禁问题。条目必须绑定可验证证据，并在完成修复后记录回归结果。

## 登记要求

- 唯一 ID 与发现时间；
- 现象、触发场景和影响范围；
- 根因分类与风险等级；
- 修复落点：rule / policy、middleware、workflow、tool、eval；
- 可验证成功标准与回归测试；
- 当前状态与对应工作项。

## 当前状态

当前没有开放的框架问题。

## 已删除的非产品机制

以下机制已从产品、CLI、模板、工作流和发行包中删除，不作为延期需求重新引入：Shadow/Enforce 激活体系、stage close certificate、review session/ledger、authority/store、离线优化、资源治理、阻断式 Lean governance。保留范围仅为五阶段动态专家复核、本地独立 PR reviewer，以及非阻断代码精简建议。
