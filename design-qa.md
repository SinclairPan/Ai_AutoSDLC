# AI-SDLC v3.0.1 产品站数据融入 Design QA

## Comparison target

- Source visual truth: Git object `6a714b9481f7dc17484c7b4a24d8eac1427c7ab6:docs/product-site/design/qa/home-1440x900.png`, extracted read-only to `/private/tmp/ai-sdlc-product-design-source/docs/product-site/design/qa/home-1440x900.png` for the comparison.
- Secondary art-direction reference: `docs/product-site/design/selected-homepage-direction.png`. This user-owned v2.0 concept is a palette/composition reference only; its v2 identity and Web-PPT structure are intentionally not copied.
- Rendered implementation: `docs/product-site/design/qa/home-1440x900.png` plus the focused screenshots listed below.
- State: offline static product site, v3.0.1 identity, reduced motion, JavaScript enabled unless explicitly noted.
- Normalization: source and desktop implementation are both `1440 × 900` pixels at `1440 × 900` CSS px and device scale factor 1. The focused desktop captures are `1366 × 768`; the mobile viewport is `390 × 844` at device scale factor 1. `home-390x844.png` is a full-page `390 × 2568` capture of the same `390 × 844` viewport.

## Full-view comparison evidence

The pre-integration visual and the v3.0.1 implementation were opened together in one comparison input. The implementation preserves the existing white / cobalt / warm-gold palette, bold Chinese display hierarchy, blue primary action, glass-like product imagery, generous whitespace, and thin structural dividers. The intentional changes are product facts rather than design drift: `AI-SDLC 2.0` becomes `AI-SDLC v3.0.1`, the hero promise changes to “让 AI 开发从会生成，走到可交付”, and the former generic value section becomes a three-track evidence rail.

The secondary `selected-homepage-direction.png` was also opened with the implementation in one comparison input. Its white/blue engineering visual language remains recognizable, while its outdated version identity, full-width video-first composition, and mock dashboard block are not treated as literal targets.

## Focused region comparison evidence

- Homepage desktop: `docs/product-site/design/qa/home-1440x900.png`
- Homepage mobile: `docs/product-site/design/qa/home-390x844.png`
- Loop evidence fold: `docs/product-site/design/qa/loop-1366x768.png`
- Expert evidence fold: `docs/product-site/design/qa/expert-review-1366x768.png`
- Platform evidence fold: `docs/product-site/design/qa/platform-1366x768.png`
- Downloads identity and guide entry: `docs/product-site/design/qa/downloads-1366x768.png`
- Guide desktop/mobile: `docs/product-site/design/qa/guide-1366x768.png`, `docs/product-site/design/qa/guide-390x844.png`

Focused views were necessary because the evidence figures, mobile wrapping, guide controls, and version/asset identity are not readable enough in a homepage-only comparison.

## Required fidelity surfaces

- Fonts and typography: passed. The implementation keeps the existing system sans/mono pairing, display/body optical separation, weight hierarchy, line height, and letter spacing. Chinese hero lines wrap cleanly at desktop and mobile sizes; metric labels remain subordinate to values.
- Spacing and layout rhythm: passed after one P2 iteration. Homepage evidence aligns to the existing shell and divider rhythm. Loop, Expert, and Platform now expose the evidence heading and first metric row inside a 1366 × 768 viewport without crowding the hero.
- Colors and visual tokens: passed. Brand blue, ink, surface, line, and warm accent tokens remain consistent with the source visual language. Evidence panels use restrained tint and borders rather than introducing a new card system.
- Image quality and asset fidelity: passed. Existing raster hero layers and poster assets remain sharp, correctly cropped, and integrated with the white/blue composition. No source imagery was replaced by CSS art, emoji, or handcrafted SVG.
- Copy and content: passed. All current product claims use v3.0.1 identity. Benefit numbers are labeled as “证据锚定合成评估 / 50 个优势导向场景 / 非生产统计”; details expose methodology and raw JSON rather than presenting the directional data as production telemetry.
- Responsiveness and accessibility: passed. Final browser evidence reports zero clipping, overlap, horizontal overflow, accessibility, console, page, request, or no-JS failures. Route navigation transfers focus to the selected route and all 78 command controls preserve exact-copy behavior.

## Findings and iteration history

### Iteration 1 — blocked

- [P1] Guide copy controls could not find their command node because the renderer omitted the shared `data-guide-part="command"` wrapper.
  - Fix: restored the site-script command container contract and added a focused regression test.
  - Post-fix evidence: 390/390 browser copy assertions passed.
- [P2] Route keyboard activation changed the hash but dropped focus to `BODY`.
  - Fix: route links now move focus to the selected route section while preserving native history and scrolling.
  - Post-fix evidence: 24/24 desktop/mobile route interactions passed.
- [P2] Long commands expanded the guide's implicit grid track and caused viewport overflow.
  - Fix: command blocks scroll internally and `.guide-main` uses an explicit `minmax(0, 1fr)` track.
  - Post-fix evidence: desktop/tablet overflow failures fell to zero.

### Iteration 2 — blocked

- [P2] Six complete Release URLs still overflowed at 390 px because an `inline-flex` link could not wrap.
  - Fix: guide links now retain the 44 px target while allowing their label and network badge to wrap.
  - Post-fix evidence: mobile horizontal overflow and clipped-control failures are zero; no-JS mobile also passes.
- [P2] Loop, Expert, and Platform evidence started below the common 768 px fold.
  - Fix: removed duplicate benchmark outer margins and reduced the three detail-page hero/grid spacing without changing typography or content.
  - Post-fix evidence: the evidence heading and first metric row are visible in all three `1366 × 768` focused screenshots.

### Final pass

No actionable P0, P1, or P2 visual differences remain. The final browser run records:

- 80 page/state checks, 0 failures
- 24 route interactions, 0 failures
- 390 copy checks, 0 failures
- 12 no-JS groups, 0 failures
- 0 clipping, overlap, accessibility, runtime, remote-resource, or repository-back-reference failures

## Open questions

- None blocking. The video panel intentionally remains an honest “产品实录即将加入” state until a real v3.0.1 recording is supplied.

## Follow-up polish

- [P3] A real v3.0.1 product video can replace the current poster state later without changing the validated layout.

## Implementation checklist

- [x] Bind product facts and evidence to v3.0.1.
- [x] Preserve the established visual system and responsive shell.
- [x] Put the homepage evidence rail above the fold.
- [x] Put the first detail-page evidence row inside the common desktop fold.
- [x] Validate desktop, tablet, mobile, keyboard, no-JS, exact copy, offline ownership, and runtime behavior.

final result: passed
