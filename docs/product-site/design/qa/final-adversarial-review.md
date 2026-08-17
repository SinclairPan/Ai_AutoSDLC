# AI-SDLC 2.0 离线产品站最终对抗验收

Reviewed baseline commit: 17f4970fb1b8aaaff39d95d98eb1d0a56ce4420d
Browser receipt input commit: 7d1409a1c9f2a377a61a64a66fdbcbe2e361b2ab
Deliverable manifest SHA256: 3910199d193bc3965092936784a1576f353b9939c83db92598dc947780dd122d
Browser receipt SHA256: 6f1c75f8077cec876461d56c1635b8ee51847a56e105c50cb13e03c049441cae
Final result: PASS / PASS / PASS

## 冻结范围

- 交付根目录：`deliverables/ai-sdlc-2.0-offline-product-site/`
- 闭世界清单：`docs/product-site/design/qa/package-manifest.sha256`，共 13 个交付文件；路径均相对交付根、按路径排序、不包含清单自身。
- 浏览器 runner：`scripts/run_offline_product_site_browser_acceptance.mjs`，SHA-256 `53d7831dab9a791b90097c54bff1596a7394601f9a8038a13314510b2962473f`。
- 持久 receipt：`docs/product-site/design/qa/browser-acceptance-receipt.json`，schema version 2。
- Validator：`e69488dc53aff35e21664320c9d4f8b21b10fde801766884d67016d65e44dc17`。
- Unit contract：`01a7af0dd1d21ec3488d2b68fb1b8850bd4b9824dc08f9a0fd48a2a1fa7b72f5`。
- 交互记录：`33b934b02d98134c8f19a654f97d57ad5c14a605c1fa784ac2b606ec872a1b6f`。
- 批准文案：`2ee0d75cb38e742b98ac956a588f37f276fefbe095284cf1ac7838a4ce688ffa`。
- 冻结指南源：`8466b8535cea8f0a17e15181060b954ad84a815be96c7e2b269f84cfce054d67`。
- 视觉规范：`08f0ff785f2a4229f56477c6cab4e2d32ea5d43627f47e18eb2f16bee930d31c`。

## Fix Round 1 闭环

### 1. 全站外链边界

六个 HTML 入口共有 28 个 allowlisted `http(s)` anchor。每个 anchor 都在静态 HTML 中显示唯一的“需要联网”标记，并带有 `target="_blank"` 与 `rel="noopener noreferrer"`。合同已提升到 validator 和全站参数化测试；旧的仅依赖运行时 JavaScript 设置属性不再作为接受证据。

TDD 证据：初始 focused run 为 `4 failed`；validator 增加三类失败代码后为 `1 failed, 3 passed`；补齐 28 个静态合同后为 `5 passed`。完整 validator 返回 `OFFLINE_PRODUCT_SITE_VALID`。

### 2. 可达 baseline 与可移植 receipt

旧的 amend/dangling baseline 不再作为证据。线性历史保留 `532d5599…`，产品、runner、receipt、截图和 manifest 的后继提交均可从当前分支到达。fresh clone 中：

- `git show 17f4970fb1b8aaaff39d95d98eb1d0a56ce4420d` 成功；
- schema 2 verifier 从 input commit `7d1409a1…` 直接读取并重算 committed manifest 与 runner SHA；
- verifier 同时确认交付目录、runner、validator、manifest 和 11 张截图从 input commit 到 reviewed baseline 无漂移；
- persisted receipt 在 fresh clone 自校验通过；
- fresh clone 内完整 Chromium runner 再次得到与本记录相同的零失败摘要。

实践专家首轮发现 receipt 曾错误绑定父提交 `532d5599…`：该提交中的 manifest 为 `f71a9f60…`，而 receipt 记录修复后 `3910199d…`。新增 Git-tree regression contract 先以该差异产生 RED，随后 schema 2 receipt 改为绑定实际包含 runner/manifest/受审产品的 `7d1409a1…`，focused `2 passed`。旧 baseline 的全部 verdict 已作废；三专家只评审 `17f4970f…`。

### 3. 专家截图确定性与完整性

`expert-review-1366x768.png` 在 `1366×768`、`#review-design`、Design Contract Tab 选中且聚焦的状态下生成。runner 在截图前固定：

- hash 与选中 Tab；
- focus；
- `tablist.scrollLeft=0`；
- `scrollX=0`、`scrollY=0`；
- fonts ready、双 `requestAnimationFrame`；
- animation、transition 与 caret 停止。

连续两次捕获均为 `2abf4273f4fb3aa771cdb5dcb76d9b6e4e8f0923cd7026055627cd7399018cb7`。receipt 保存 header/Tab 的元素矩形、文字矩形、`scrollWidth/clientWidth`、重叠和裁切数组；两次均为 `unclipped=true`、`clipped=[]`、`overlaps=[]`。fresh clone 再次得到同一 SHA。

## 代表截图

| 文件 | SHA-256 |
| --- | --- |
| `home-1440x900.png` | `367ce22dc32c130dd060fa54148face9cac90b85d881dd7646c19a7b58b16ee4` |
| `home-1366x768.png` | `368ad3dfa48b033335a839154d1bda72c86f8b4c424cd0f5b7bcab3f9ba9cc90` |
| `home-1280x800.png` | `d433e372b6de4008002dc7668c830a619e625756bd1d2de89d78e6db7586408b` |
| `home-1024x768.png` | `4f8a222c154a6de713db606b32d7e9c6b9043b1db70544b55f1f3813ce3f3ab0` |
| `home-390x844.png` | `88f5e5e7cc29eddd55bf370d44cbb2fb8f4f7174c23303659c3a578e55975c86` |
| `loop-1366x768.png` | `95dafb8bd7913c94b3730251d1fe59009f7c5aa8ca0a6f4f1caa8419aa946d27` |
| `expert-review-1366x768.png` | `2abf4273f4fb3aa771cdb5dcb76d9b6e4e8f0923cd7026055627cd7399018cb7` |
| `platform-1366x768.png` | `be9fe1cbb1e332aed0b6fc5db3f9a8aee4c6e5b8a7b5d41a74265011605080de` |
| `downloads-1366x768.png` | `d28f6296bbfc366fa83387944fec9e14bff3034c44c60a6d2a520737e856dbd6` |
| `guide-1366x768.png` | `0e4938b90f3c728c4585bd181276d12ee9ff7f1b335396ac391887884fa40125` |
| `guide-390x844.png` | `20de5d6b62d4c40a40dfcab7db506f476719ea6f93952afb04f081fef9b45f1e` |

## Fresh 门禁与可重算 receipt

- 站点 contracts：`100 passed`。
- Ruff：`All checks passed!`。
- 完整 validator 与冻结指南 parity：`OFFLINE_PRODUCT_SITE_VALID`。
- Node syntax：4 个交付/浏览器文件，failure `0`。
- manifest：13 entries，rebuild exact match，relative/sorted/no-self。
- 五个视口 × 27 个状态：`135 / 135`，failure `0`。
- 指南 48 条命令 × 5 个视口：`240 / 240`，failure `0`。
- no-JS：6 页 × 2 视口，`12 / 12`，failure `0`。
- configured local MP4/VTT：native controls、caption、fullscreen 与本地 `file:` source/track，failure `0`。
- accessibility 与 runtime：failure `0`。
- 请求归属：33 requests、13 unique URLs；remote、site-root escape、repository back-reference 均为 `0`。receipt 保存全部 33 条 URL 与归属字段，不只保存摘要。
- 交付目录安装包数量 `0`；公开官网赛事/评委话术扫描 `0`。

## 三专家 exact-hash 复审

三位 reviewer 均以 `17f4970fb1b8aaaff39d95d98eb1d0a56ce4420d`、manifest `3910199d…`、receipt `6f1c75f8…`、runner `53d7831d…`、11 张截图与上述批准源 hash 为同一只读输入。旧 `4582210f…` 及更早 baseline 的 verdict 全部作废。

1. AI-SDLC 实践专家：`PASS`
2. AI Coding 行业专家：`PASS`
3. Technical Evaluator：`PASS`

不存在 conditional PASS 或 stale PASS。

## 诚实边界

- 首页视频仍按要求保持未配置空态；未来配置 smoke 只证明本地 MP4/VTT、native controls、caption 与 fullscreen 合同，不声称已有真实产品录屏。
- GitHub、Release、README、在线指南与安装包链接需要联网；站点自身阅读、导航与本地指南不依赖网络。
- runner 不下载浏览器或 Node 依赖；执行者必须显式提供已存在的本地 Playwright module 与 Chromium executable 路径。
- receipt 的 `copiedSiteRoot` 是一次 fresh external copy 的来源记录；长期可复验输入是可达 commit、manifest、runner、receipt、截图和本记录。
- `local-first` 指项目治理与证据留在项目内，不表示远程 AI Provider 可以离线推理。

最终产品官网表面未出现赛事叙述、评委脚本、虚构指标、客户 Logo 或 unsupported claim。
