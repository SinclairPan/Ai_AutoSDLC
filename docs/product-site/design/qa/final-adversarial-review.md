# AI-SDLC 2.0 离线产品站最终对抗验收

Reviewed baseline commit: f8dc8cf4abdba7b1ec1803b463015d069a84844d
Deliverable manifest SHA256: f71a9f6009766246dfeb85de7ce7a12d87667e779ec733064ca63feee3899dc0
Final result: PASS / PASS / PASS

## 冻结范围

- 交付根目录：`deliverables/ai-sdlc-2.0-offline-product-site/`
- 闭世界清单：`docs/product-site/design/qa/package-manifest.sha256`，共 13 个交付文件；路径均相对交付根、按路径排序、不包含清单自身。
- Validator：`cc05bc8df80b1a3f6bd4064548017202eafd445f891c80503d5f6414d97a0067`
- Unit contract：`c06f1be37cd9cd6ef728ee2ea03f859468ee31c5688709f6a76f8ecab3b2d738`
- 批准文案：`2ee0d75cb38e742b98ac956a588f37f276fefbe095284cf1ac7838a4ce688ffa`
- 冻结指南源：`8466b8535cea8f0a17e15181060b954ad84a815be96c7e2b269f84cfce054d67`
- 视觉规范：`08f0ff785f2a4229f56477c6cab4e2d32ea5d43627f47e18eb2f16bee930d31c`
- 批准首页视觉参考：`0526f97df004537c3d3c758fe22127ebabe524965ba9143fe5a5523d72fb206d`
- 资产评审记录：`307655cdb9ea0bfb25fa3ce5e20cbcb91cff65276e80e658e5416e83c4f99491`
- 交互 QA 记录：`8ac5380fabcdd75846365beaca4b1f437b06a50b976103da0f0e0b5ea4ba8d80`

## 代表截图

| 文件 | SHA-256 |
| --- | --- |
| `home-1440x900.png` | `147852fd02e52e026085d41da50d65161f5869fade71884bcc1643debe978c44` |
| `home-1366x768.png` | `13f4248831a0d9a0a853351eee1eaf7b49fa25a8d9dcb84a37c961893415fc8c` |
| `home-1280x800.png` | `9195f963783c0e2ea39157ffe9c396b80cc09f3fd3337208eca548eea04dc3c9` |
| `home-1024x768.png` | `4af1240cfa8823a45dd5e43fff786e2e93c28bbfeb525b6b5b0b3fe3beac1f65` |
| `home-390x844.png` | `f37f65ce23c209127f3d1fe8ef7a40673a84d5ead8d40a1d3b2dddcdf2f71e75` |
| `loop-1366x768.png` | `452b18e9f2593c6e5701e5f205af1853af771a419579f0585bbfec2c0e9857b1` |
| `expert-review-1366x768.png` | `4ed3b00f1f83fef29d625906bb5f6b21ccc811272c88e6b9a6bde4fbf13207ef` |
| `platform-1366x768.png` | `d30c8a61c121cced95ce5d74cbef63d84c4048a340cbd4492300d0859bca135b` |
| `downloads-1366x768.png` | `d28f6296bbfc366fa83387944fec9e14bff3034c44c60a6d2a520737e856dbd6` |
| `guide-1366x768.png` | `345bdd5aa3f5c34f5f887218a885c7c2b38a1012de8e4a5e6eb26e0d825bd744` |
| `guide-390x844.png` | `3e863b2bb83daa618211c9a6e1facc2072dddd4d78e607334dd70636025f8db0` |

## 对抗评审

三位 reviewer 均以 reviewed baseline commit、manifest hash、validator/test hash、批准源 hash、QA 文档 hash 与上述截图 hash 为同一输入；reviewer 不编辑 baseline。

1. AI-SDLC 实践专家：`PASS`
2. AI Coding 行业专家：`PASS`
3. Technical Evaluator：`PASS`

不存在 conditional PASS 或 stale PASS。

## 接受的修正与驳回项

首轮 AI-SDLC 实践评审提出一个 P1：Local PR Review 将 final report 条件绝对化为“无未解决 Finding 才生成”，与 v2.0.0 的实际策略不一致。仓库 tag 源码证明：未解决 `BLOCKER` 阻断；未解决 `REQUIRED` 默认阻断，但显式接受风险时可按 `risk_accepted` 关闭并披露；`ADVISORY` 与 waiver 按策略记录并披露。

该 finding 被接受并形成唯一 consolidated correction：只修正 `loop-engineering.html` 的 Local PR Review 边界，并增加一个基于结构化属性的 v2.0.0 回归合同。测试先以 `1 failed` 证明旧页面缺少该合同，修正后 focused `2 passed`、完整站点 `94 passed`。批准文案、指南源与视觉源均未修改。实践专家随后 fresh 复审为 `PASS`；另两位专家也对 corrected baseline fresh 返回 `PASS`。

驳回 finding：无。

## Fresh 门禁

```powershell
uv run --no-sync pytest -q tests/unit/test_offline_product_site.py
# 94 passed

uv run --no-sync ruff check scripts/validate_offline_product_site.py tests/unit/test_offline_product_site.py
# All checks passed!

uv run --no-sync python scripts/validate_offline_product_site.py `
  --root deliverables/ai-sdlc-2.0-offline-product-site `
  --guide-source docs/product-site/content/USER_GUIDE.zh-CN.md
# OFFLINE_PRODUCT_SITE_VALID

node --check deliverables/ai-sdlc-2.0-offline-product-site/assets/js/site.js
node --check deliverables/ai-sdlc-2.0-offline-product-site/assets/js/video-config.js
git diff --check
# 均 exit 0
```

身份核验结果：origin 为 `https://github.com/SinclairPan/Ai_AutoSDLC.git`；远端 annotated tag `v2.0.0` peel 到 `737bda39e05c53450e180a20581b7b7a70db9cf0`，本地 tag tree 为 `3db58121e228a7a1c4c6b760c535d6df1ffdbe84`。GitHub Release 为非 draft、非 prerelease，并公开 Windows AMD64、macOS ARM64、Linux AMD64 三个安装包及各自 SHA 文件，共六个资产。

交付目录安装包数量为 `0`；远程运行依赖与公开赛事词扫描均为 `0`。

## 仓库外 evaluator path

将交付目录复制到 fresh `/private/tmp` 根后，以 Chromium `file://` 且 browser offline 执行：

- 五个验收视口、27 个状态，共 `135 / 135`；failure `0`。
- Back / Forward / Reload 历史序列 4 组；键盘、focus、skip link、移动菜单、reduced-motion、44px 目标均通过。
- 指南 48 条命令 × 5 视口，共 `240 / 240` 次精确复制；failure `0`。
- 6 页 × 2 视口 no-JS 共 12 组；正文、导航、全部面板与 12 条指南路径可读。
- 默认视频保持诚实空态；临时本地 MP4/VTT 配置显示 native controls、caption track 与 fullscreen 入口，page error `0`。
- console error、page error、remote failed request 均为 `0`。
- 六个入口产生 33 个请求、13 个唯一运行时 URL；remote、site-root escape、repository back-reference 均为 `0`。
- 仓库外副本重新构造的 manifest 与 committed manifest 完全一致。

临时浏览器 receipt SHA256：`bbd197d649c5568152aefc4023fe2fc5480f3670e434e7d76a7e5e5224c0d8ab`。临时路径不属于最终包；可复验的权威输入是 reviewed commit、清单、测试、validator 与本记录。

## 诚实边界

- 首页视频仍按要求保持未配置；未来配置只证明本地 MP4/VTT 接线、native controls、caption 与 fullscreen 合同，不声称已有真实产品录屏。
- GitHub、Release、README、在线指南与安装包链接需要联网；站点自身阅读、导航与本地指南不依赖网络。
- `local-first` 指项目治理与证据留在项目内，不表示远程 AI Provider 可离线推理。
- PNG 是 exact-hash receipt；重复截图前必须冻结 hash、focus 与 scenario `aria-current`。本轮通用 capture 曾暴露两个非产品性的截图状态时序差异；强化状态断言后指南重新得到 tracked SHA，且唯一受修正页面的 Loop 截图 fresh SHA 与 tracked SHA 完全一致。tracked 11 图未被替换。

最终产品官网表面未出现赛事叙述、评委脚本、虚构指标、客户 Logo 或 unsupported claim。
