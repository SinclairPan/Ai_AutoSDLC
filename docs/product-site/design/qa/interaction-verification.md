# Task 9 交互与可访问性验证

验证日期：2026-08-17

验证入口：`file:///.../deliverables/ai-sdlc-2.0-offline-product-site/index.html`

浏览器：本机 Chromium headless shell（Playwright 直接驱动；仅访问本地文件）

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
- 每页恰有一个 `main` 和一个 `h1`，主导航具备名称；Tab 语义、`aria-selected`、面板关联、链接/按钮标签通过静态与浏览器检查。
- `prefers-reduced-motion: reduce` 下计算样式为 `scroll-behavior: auto`；`:focus-visible` 在键盘路径中有可见轮廓。
- 主要正文配色实测对比度：`#111827/#fff=17.74:1`、`#52627a/#fff=6.20:1`、`#0b1f5e/#fff=15.31:1`、`#1548d8/#fff=7.12:1`、`#0b1f5e/#eef4ff=13.86:1`。

## 代表截图

| 文件 | SHA-256 |
| --- | --- |
| `home-1440x900.png` | `147852FD02E52E026085D41DA50D65161F5869FADE71884BCC1643DEBE978C44` |
| `home-1366x768.png` | `13F4248831A0D9A0A853351EEE1EAF7B49FA25A8D9DCB84A37C961893415FC8C` |
| `home-1280x800.png` | `9195F963783C0E2EA39157FFE9C396B80CC09F3FD3337208ECA548EEA04DC3C9` |
| `home-1024x768.png` | `4AF1240CFA8823A45DD5E43FFF786E2E93C28BBFEB525B6B5B0B3FE3BEAC1F65` |
| `home-390x844.png` | `F37F65CE23C209127F3D1FE8EF7A40673A84D5EAD8D40A1D3B2DDDCDF2F71E75` |
| `loop-1366x768.png` | `452B18E9F2593C6E5701E5F205AF1853AF771A419579F0585BBFEC2C0E9857B1` |
| `expert-review-1366x768.png` | `4ED3B00F1F83FEF29D625906BB5F6B21CCC811272C88E6B9A6BDE4FBF13207EF` |
| `platform-1366x768.png` | `D30C8A61C121CCED95CE5D74CBEF63D84C4048A340CBD4492300D0859BCA135B` |
| `downloads-1366x768.png` | `D28F6296BBFC366FA83387944FEC9E14BFF3034C44C60A6D2A520737E856DBD6` |
| `guide-1366x768.png` | `9C3ADE75D4C9C046CD368651EF3DF9A0C47B8AD772194135D78B6A936834391F` |
| `guide-390x844.png` | `3E863B2BB83DAA618211C9A6E1FACC2072DDDD4D78E607334DD70636025F8DB0` |

## 视觉并排复核

批准参考图 `homepage-direction-v2-approved.png`（SHA-256 `0526F97DF004537C3D3C758FE22127EBABE524965BA9143FE5A5523D72FB206D`）先与 RED 首页截图在同一个 2880×900 输入中比较；修复后再与 `home-1440x900.png` 以相同方式比较。最终首页保持参考图的双栏主视觉、短促层级、16:9 视频比例、细边框和正文留白；标题由三行回到两行，首屏可见价值区入口。五个视口均未出现 Web-PPT 式满屏分镜或长扁卡片堆叠。

并排复核的临时文件为 `/private/tmp/task9-home-reference-red-comparison.png` 与 `/private/tmp/task9-home-reference-green-comparison.png`；提交内保留参考图和最终同视口截图作为可复核源。
