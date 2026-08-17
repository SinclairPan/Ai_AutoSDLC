# Task 9 交互与可访问性验证

验证日期：2026-08-17

验证入口：`file:///.../deliverables/ai-sdlc-2.0-offline-product-site/index.html`

浏览器：本机 Chromium headless shell（Playwright 直接驱动；仅访问本地文件）

可执行 runner：`scripts/run_offline_product_site_browser_acceptance.mjs`

持久 receipt：`docs/product-site/design/qa/browser-acceptance-receipt.json`

## 覆盖矩阵

- 视口：`1440×900`、`1366×768`、`1280×800`、`1024×768`、`390×844`。
- 页面：首页、Loop Engineering、Dynamic Expert Review、Platform Capabilities、Downloads & Docs、中文用户指南。
- 页面状态：5 个页面/指南在各自 Hash/Tab 状态下共 135 个页面-状态-视口组合。
- 无 JavaScript：6 个页面 × 桌面/移动两种视口，共 12 组。
- 复制：指南 48 条命令 × 5 个视口，共 240 次复制回退验证。

最终矩阵结果：`actualStateCount=135`，`failures=0`，浏览器 console error、page error、远程失败请求均为 `0`。

## 交互结果

- 主导航：桌面链接、移动菜单、`Escape` 关闭及焦点回到菜单按钮通过。
- Tab/Hash/历史：Loop、专家复核、平台能力和指南均通过点击、Hash 深链、Back、Forward、Reload；Back/Forward 后焦点跟随恢复后的选中 Tab，Reload 保留 Hash 和选中面板。
- 键盘：Tab、方向键、Home、End、Enter、可见焦点环通过；跳转链接获得焦点时可见，激活后焦点落到 `main#main`。
- 指南场景选择：4 个场景在桌面和移动端共 8 次选择均定位到正确操作系统 Tab，焦点可见。
- 复制：每个视口识别 48 个复制按钮，240 次写入内容与对应代码块一致，无失败。
- 视频：未配置态保留可聚焦视频控件、海报、说明及全屏入口，真实播放器隐藏；临时本地 MP4/VTT 配置态显示原生 `controls`、本地 `file:` source/track、字幕和全屏入口，无页面错误。

## 响应式、离线与可访问性结果

- 135 个页面状态均满足 `scrollWidth == clientWidth`；没有文档级横向滚动、控件裁切、关键控件重叠或小于 44×44 px 的交互目标。
- 12 个无 JavaScript 页面保持主导航和正文可读；Loop/专家复核/平台能力的全部面板，以及指南全部 12 条路径均可见。
- 所有资源请求均为本地 `file:`；未发现远程依赖、console error 或 page error。
- 六个入口的完整请求清单为 33 requests / 13 unique URLs；每条记录都包含页面、完整 URL、协议、解析路径、复制根归属和仓库回指判断，remote、site-root escape、repository back-reference 均为 `0`。
- 全站 28 个 allowlisted `http(s)` 外链均静态显示“需要联网”，并带有 `target="_blank"` 与 `rel="noopener noreferrer"`；validator 与全站参数化合同共同约束该边界。
- 每页恰有一个 `main` 和一个 `h1`，主导航具备名称；Tab 语义、`aria-selected`、面板关联、链接/按钮标签通过静态与浏览器检查。
- `prefers-reduced-motion: reduce` 下计算样式为 `scroll-behavior: auto`；`:focus-visible` 在键盘路径中有可见轮廓。
- 主要正文配色实测对比度：`#111827/#fff=17.74:1`、`#52627a/#fff=6.20:1`、`#0b1f5e/#fff=15.31:1`、`#1548d8/#fff=7.12:1`、`#0b1f5e/#eef4ff=13.86:1`。

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

## Task 10 Fix Round 1：可移植证据与专家截图

runner 从空的 `/private/tmp` 根复制清单绑定的站点后运行，receipt 记录输入 commit、manifest SHA、复制根、runner SHA、完整结果数组与逐请求归属。本轮 receipt SHA-256 为 `cbeaad1d957789c745a2fbece5a9763ba6253d8cb4ff24e51780b33e4b92c1a9`；manifest SHA-256 为 `3910199d193bc3965092936784a1576f353b9939c83db92598dc947780dd122d`。

专家截图固定为 `#review-design` 与选中、聚焦的 Design Contract Tab；截图前强制 `tablist.scrollLeft=0`、`scrollX=0`、`scrollY=0`、双 `requestAnimationFrame` 与禁用 animation/transition。连续两次捕获均为 `2abf4273f4fb3aa771cdb5dcb76d9b6e4e8f0923cd7026055627cd7399018cb7`。receipt 同时保存 header/Tab 的元素矩形、文字矩形、`scrollWidth/clientWidth`、重叠与裁切数组；两次均为 `unclipped=true`、`clipped=[]`、`overlaps=[]`。

上表 11 张截图全部由同一 runner 重建。因全站页头增加可见联网边界，首页、Loop、专家与平台截图更新；Downloads 的像素保持不变；指南以冻结的 `#path-1b` 状态重建。

## 视觉并排复核

批准参考图 `homepage-direction-v2-approved.png`（SHA-256 `0526F97DF004537C3D3C758FE22127EBABE524965BA9143FE5A5523D72FB206D`）先与 RED 首页截图在同一个 2880×900 输入中比较；修复后再与 `home-1440x900.png` 以相同方式比较。最终首页保持参考图的双栏主视觉、短促层级、16:9 视频比例、细边框和正文留白；标题由三行回到两行，首屏可见价值区入口。五个视口均未出现 Web-PPT 式满屏分镜或长扁卡片堆叠。

并排复核的临时文件为 `/private/tmp/task9-home-reference-red-comparison.png` 与 `/private/tmp/task9-home-reference-green-comparison.png`；提交内保留参考图和最终同视口截图作为可复核源。
