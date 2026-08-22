# AI-SDLC v3.0.1 产品站最终对抗记录

Reviewed implementation commit: `343f087d43dd803e1ea2154d29fade92338a6da3`

本记录汇总实现后的可复验事实，不替代 `docs/product-site/design/qa/reviewers/` 中后续独立 reviewer attestations。

## 产品事实

- 唯一当前身份：AI-SDLC `v3.0.1`
- tag object：`408086505718fbd26824373bb72ed98c27c3b652`
- source commit：`9a59a3edd483b0e6526b67b03fbfcac3ba48d2e4`
- source tree：`fd5c2dac0a216f0eb17855d03cc7900d872d3c61`
- Windows / macOS / Linux 三个平台资产和同名 `.sha256` sidecar 均在 Downloads & Docs 明示。
- 中文指南绑定正式 v3.0.1 源文件，覆盖空项目 / 已有项目 × 在线 / 离线 × Windows / macOS / Linux 的 12 条自包含路线。

## 数据口径

- 首页只呈现三个入口数字：Loop `55% → 92%`、专家对抗 `9 → 2`、整体交付 `52% → 90%`。
- Loop 页使用五类 Loop，不伪称固定五阶段。
- Expert 页明确最多两名只读专家、原 Writer 修复、每角色最多一次复核。
- Platform 页先呈现 3 项头部指标，再将 14 项指标分为需求与设计、实现与质量、交付与恢复、成本与证据四组。
- 三页均固定披露“证据锚定合成评估｜50 个优势导向场景｜非生产统计”，并提供方法说明、选择偏差、原始 JSON 与 v3.0.1 source commit。

## 设计与响应式对抗

- `design-qa.md` 最终结果为 `passed`。
- 白 / 钴蓝 / 暖金、现有图片资产、字体层级与结构分隔保持一致。
- Loop、Expert、Platform 的证据标题与首行指标进入 1366 × 768 首屏。
- 首页 390 px 重排无横向溢出；14 指标表在移动端转为纵向行；指南长命令内部滚动，完整 URL 可换行。

## 自动门禁

- 产品站与指南单元测试：`116 passed`。
- 静态验证：`OFFLINE_PRODUCT_SITE_VALID`。
- Ruff check / format、Node 语法、`git diff --check`：通过。
- 浏览器：80 states、24 route activations、390 exact copies、12 no-JS groups，全部 0 failure。
- 请求归属：33 requests / 13 unique local URLs；remote、site-root escape、repository back-reference 均为 0。
- receipt verifier：`BROWSER_ACCEPTANCE_RECEIPT_VALID`。

## 绑定证据

- receipt：`f4c4040c736d18f1e738a532e9723a40367ae58d8c1d89ac16e697c4773919f5`
- manifest：`c08a89e3ab9b899c58559082261e95d6452d0ac5743db3e6f2cfd6374153483a`
- runner：`597a852fe8b70d3d2893ddad56e2ac5578bd9b3e50fe23034daa3a374d1128d8`
- validator：`f030b8aa27380036f5cd6946598d1de14a61aee9ae9b6536d868724105513406`
- home desktop：`4ae4c25e2285dc74401f2900cc6256e3cf6327c4a7e913cff8f71c22246ee126`
- home mobile：`071407fa619b22be6d371f4cf8b5db257ae464ebeb65a86b74e871d9b8ab9eec`
- Loop：`1f2547a0c88cf285f572f75ad0fc9e4003800e4bbec177bfb85ec901247886c4`
- Expert：`0959de97d6b07aebb0a0b0300cb4097eca7ff8b63acf2821e5b605f131c0630f`
- Platform：`d4cf65d91052a757b658bcbacf1c014a2d3afb4ed537b93baf47ce4530d92f5f`

当前结论：实现与自动证据 `PASS`。三位独立 reviewer 已针对 exact commit `75d6b216bd2c4806ecfbe5094029a70e271e4460` 重新出具 requirements/copy、interaction/accessibility、visual/offline-delivery 三份 attestation；聚合校验为 `REVIEWER_ATTESTATIONS_VALID`，Critical 0、Important 0。旧 v2 attestation 未沿用。
