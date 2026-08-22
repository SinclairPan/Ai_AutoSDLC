# Continuity Handoff

- Updated: 2026-08-22T04:17:06+00:00
- Reason: 完成产品站实现批次，准备冻结浏览器输入提交
- Goal: 把 v3.0.1 产品事实、三组优势数据与 12 路线指南融入离线产品站，并生成提交绑定的浏览器证据
- State: 页面、样式、指南、截图与 design QA 已完成；预提交静态门禁 110 passed，浏览器预验收全绿；待提交输入后生成最终绑定回执
- Stage: none
- Work Item: none
- Branch: codex/ai-sdlc-2-offline-product-site-design

## Changed Files
- M deliverables/ai-sdlc-2.0-offline-product-site/assets/css/pages.css
- M deliverables/ai-sdlc-2.0-offline-product-site/assets/css/site.css
- M deliverables/ai-sdlc-2.0-offline-product-site/assets/js/site.js
- M deliverables/ai-sdlc-2.0-offline-product-site/assets/js/video-config.js
- M deliverables/ai-sdlc-2.0-offline-product-site/docs/USER_GUIDE.zh-CN.html
- M deliverables/ai-sdlc-2.0-offline-product-site/downloads-docs.html
- M deliverables/ai-sdlc-2.0-offline-product-site/dynamic-expert-review.html
- M deliverables/ai-sdlc-2.0-offline-product-site/index.html
- M deliverables/ai-sdlc-2.0-offline-product-site/loop-engineering.html
- M deliverables/ai-sdlc-2.0-offline-product-site/platform-capabilities.html
- M docs/product-site/content/USER_GUIDE.zh-CN.md
- M docs/product-site/design/qa/downloads-1366x768.png
- M docs/product-site/design/qa/expert-review-1366x768.png
- M docs/product-site/design/qa/guide-1366x768.png
- M docs/product-site/design/qa/guide-390x844.png
- M docs/product-site/design/qa/home-1024x768.png
- M docs/product-site/design/qa/home-1280x800.png
- M docs/product-site/design/qa/home-1366x768.png
- M docs/product-site/design/qa/home-1440x900.png
- M docs/product-site/design/qa/home-390x844.png
- M docs/product-site/design/qa/loop-1366x768.png
- M docs/product-site/design/qa/package-manifest.sha256
- M docs/product-site/design/qa/platform-1366x768.png
- M scripts/run_offline_product_site_browser_acceptance.mjs
- M scripts/validate_offline_product_site.py
- M tests/unit/test_offline_product_site.py
- ?? design-qa.md
- ?? docs/product-site/design/selected-homepage-direction.png
- ?? scripts/render_product_site_v3_guide.py
- ?? tests/unit/test_product_site_v3_release.py

## Key Decisions
- 保留白/钴蓝/暖金视觉体系；首页仅三条证据；详情页三级披露；Loop 使用五类口径；专家最多两名只读；平台展示三项头部与十四项分组；全站 v3.0.1

## Commands / Tests
- pytest 110 passed/6 receipt-attestation tests deselected; OFFLINE_PRODUCT_SITE_VALID; Ruff/node/diff check PASS; preliminary browser 80 states + 24 routes + 390 copies all zero failures

## Blockers / Risks
- 旧 browser receipt 与 reviewer attestations 仍绑定旧提交，必须在实施提交后重建；用户未跟踪 selected-homepage-direction.png 不得加入

## Local PR Review
- none

## Exact Next Steps
- 提交页面+截图输入；用新 exact commit 在非嵌套 Playwright headless shell 重跑验收；刷新 QA/attestations；全量验证并提交回执
