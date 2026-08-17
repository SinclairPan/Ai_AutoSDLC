# AI-SDLC 2.0 离线产品站最终闭世界验收记录

Reviewed product baseline: `9154a47a36026133e4a587043aeb32d1a21efb0e`

Browser receipt input commit: `08519e03d6f08d04d56ed386deb09efe2da8a5db`

Reviewer attestation commit: `924d29465a9ad9ffa28a0359ed8bc334013be4ae`

Deliverable manifest SHA-256: `3910199d193bc3965092936784a1576f353b9939c83db92598dc947780dd122d`

Browser receipt SHA-256: `c1ea3eca93f6356484ffc452c85ac603aec4c51fa9c3b9113968f60dbb9c4b88`

## 冻结范围与可重算输入

- 交付根：`deliverables/ai-sdlc-2.0-offline-product-site/`，manifest 共 13 个相对、排序且不含自身的条目。
- browser runner：`scripts/run_offline_product_site_browser_acceptance.mjs`，SHA-256 `f56950501ce5308d8f33ccf3b1188768ef15e3651f919c278b4a5eae3bfb6bcf`。
- reviewer attestation verifier：`scripts/verify_offline_product_site_reviewer_attestations.py`，SHA-256 `980eb6f003b3908127268142368b432f8ab63e5405d1dfb39f95b34aea3f359e`。
- validator：`scripts/validate_offline_product_site.py`，SHA-256 `e69488dc53aff35e21664320c9d4f8b21b10fde801766884d67016d65e44dc17`。
- unit contract：`tests/unit/test_offline_product_site.py`，SHA-256 `05fe17fe4a511447edd135b5446ff4c867228b13b5b3010e329f508b01f985e3`。
- interaction record：`docs/product-site/design/qa/interaction-verification.md`，SHA-256 `cf178b904313812f7bf15963ca6d0bf83f99a3b91913208c13e3594ff4e3d3d6`。
- 批准 copy、指南、视觉规范、批准参考图和 11 张 QA 截图的 baseline blob SHA 均逐项保存在三份 reviewer 原始输出中。

schema 3 receipt 从可达 input commit 直接读取 runner 与 manifest 字节，并检查交付目录、runner、validator、manifest 和 11 张截图到 reviewed product baseline 无漂移。verifier 还从明细数组重算摘要、请求归属和截图确定性；删除历史结果或注入伪重叠的 mutation 都会返回 `RECEIPT_INVALID`。

## 浏览器与交互闭世界结果

- 五视口、135 个页面状态：状态失败 `0`；每个状态都有 `interactiveAudit`。
- 135 次逐状态几何审计：视口水平/垂直裁切 `0`、overflow 祖先裁切 `0`、关键控件重叠 `0`。
- 重叠定义限定为“同一交互区域内的可见关键控件”，只比较链接、按钮、Tab 和受控视频，排除文本行与父子包含关系。
- 移动菜单打开 → `Escape` → 焦点返回：`1 / 1`。
- Loop、Expert、Platform、Guide 的 selected → Back → Forward → Reload：`4 / 4`；receipt 保存 selected、hash、focus 与 panel 可见性。Back/Forward 焦点跟随 Tab，Reload 保留 hash/selected 并按浏览器导航边界重置到 `BODY`。
- 四个相关 Tab 面 × 桌面/移动的 `ArrowRight`、`End`、`Home`、`ArrowLeft`：`8 / 8`。
- skip-link 五视口激活后焦点进入 `main#main`：`5 / 5`。
- 指南四场景 × 桌面/移动选择：`8 / 8`；selected、hash、focus、`aria-current` 和可见 Tab 数全部一致。
- 指南 48 条命令 × 五视口：`240 / 240`；无 JavaScript：六页 × 两视口 `12 / 12`。
- 默认视频保持诚实空态；临时本地 MP4/VTT 配置 smoke 的 native controls、caption、fullscreen 与 `file:` source/track 全部通过。
- 六页共 33 requests、13 unique URLs；remote、site-root escape、repository back-reference 均为 `0`。
- 专家截图在固定 hash、selected Tab、focus、tablist/page scroll、fonts 和 animation 后连续两次字节一致，SHA-256 均为 `2abf4273f4fb3aa771cdb5dcb76d9b6e4e8f0923cd7026055627cd7399018cb7`。

## Fresh clone 复验

在 `/private/tmp/ai-sdlc-task10-final-clone-a6fac11ff7584e0eac53c950a49af1d6` 以 detached `9154a47a36026133e4a587043aeb32d1a21efb0e` 重跑扩展 runner。fresh clone 结果与持久 receipt 一致：135 states、135 geometry、1 mobile menu、4 history、8 keyboard、5 skip-link、8 guide scenarios、240 copies、12 no-JS 全部零失败；33/13/0/0/0 请求归属不变；专家截图两次仍为 `2abf4273…`。

## 三份独立 reviewer 原始输出

下列文件是 reviewer 结论和 finding count 的唯一权威来源；本汇总不重新表述或替代其中的 verdict。canonical SHA 的规则是把文件内 `Canonical content SHA256` 值替换为 64 个 ASCII `0` 后，对完整 UTF-8 文件字节计算 SHA-256，从而避免自引用悖论。

| Reviewer ID | 原始输出 | Canonical SHA-256 | 实际文件 SHA-256 |
| --- | --- | --- | --- |
| `reviewer-requirements-copy-9154a47` | `docs/product-site/design/qa/reviewers/requirements-copy.md` | `586fae7a93145608c9dad401590c107f4e0c152601b292ab36030d7a46673dfa` | `52b266c680088255b6c5a6d509aef213aaba2096329928a6731436be73535d7f` |
| `reviewer-interaction-a11y-9154a47` | `docs/product-site/design/qa/reviewers/interaction-accessibility.md` | `83f1f30791f495032af13363e3a888ee080618ac8d94fe6673b0b293c8c7d18c` | `e7516e38704b9e84ec81d01b9e9b80f41ce9ff9d392af7c6780468d2bc291ac1` |
| `reviewer-visual-delivery-9154a47` | `docs/product-site/design/qa/reviewers/visual-offline-delivery.md` | `e2438422335345bc78e5726102c3fd048c212b234f72704e466e8e4d53fb618b` | `1dc6c33ef226fc0e6f6cedb78bb757d560e0ed2571c61f7ce1161f4232d86245` |

`verify_offline_product_site_reviewer_attestations.py` 对三份文件重算 canonical SHA、唯一 role/identity/task、完整 baseline、UTC、18 项相同 baseline 输入哈希和必须存在的 scope/verification/findings 结构，输出 `REVIEWER_ATTESTATIONS_VALID ... files=3 inputs=18`。

## 线性历史与无漂移

- `08519e03…` 是 `9154a47a…` 的祖先；`9154a47a…` 是 `924d2946…` 及最终 review-record HEAD 的祖先。
- reviewer 评审期间 HEAD 保持 `9154a47a…`，三份文件完成后才进入 `924d2946…`。
- 从 reviewed product baseline 到最终 HEAD，排除 `docs/product-site/design/qa/reviewers/` 与本最终记录后，产品、批准正文、runner、receipt、validator、tests、manifest 和截图 diff 为空。
- 旧 baseline 的聚合 verdict 不用于本轮结论。

## Fresh 门禁

- `104 passed`。
- Ruff：`All checks passed!`。
- validator：`OFFLINE_PRODUCT_SITE_VALID`；临时重建 manifest 与 tracked manifest 字节一致。
- browser receipt verifier 与 reviewer attestation verifier 均通过。
- Node syntax 与 `git diff --check` 均通过。
- 全站 28 个 allowlisted 外链均静态显示“需要联网”，并含 `target="_blank" rel="noopener noreferrer"`。
- 交付目录安装包数量 `0`；公开产品表面赛事话术扫描 `0`。

## 诚实边界

- 首页默认视频仍未配置；未来配置验证只证明本地 MP4/VTT 播放合同，不声称已经提供真实产品录屏。
- GitHub、Release、README、在线指南和安装包链接明确需要联网；站点页面、导航、复制与本地指南不依赖远程运行资源。
- runner 不下载 Node、Playwright 或 Chromium；执行者需显式提供已存在的本地 Playwright module 和 Chromium executable。
- fresh copy 与 fresh clone 路径是一次性执行证据；长期复验以可达 commits、manifest、runner、receipt、11 张截图、三份原始 reviewer 输出及本记录为准。
- 产品官网表面未加入评审、赛事或交付过程话术。
