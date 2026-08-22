# AI-SDLC v3.0.1 Product Site Data Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the offline product site to the `v3.0.1` competition-final product truth and integrate the Loop, dynamic-expert, and overall comparison datasets into one coherent site journey.

**Architecture:** Keep the existing static HTML/CSS/JS delivery. Add a closed release-baseline input to the deterministic benefit-data builder, regenerate three datasets, then render shared evidence components with page-specific visual hierarchy. Preserve offline behavior and existing accessibility semantics.

**Tech Stack:** HTML5, CSS, vanilla JavaScript, Python dataset builder, pytest, Node browser acceptance.

**Spec:** `docs/product-site/design/2026-08-21-v3-product-site-data-integration.md`

## Global Constraints

- Product truth is tag `v3.0.1`, commit `9a59a3edd483b0e6526b67b03fbfcac3ba48d2e4`.
- Do not present synthetic scores as production observations or statistically significant results.
- Preserve the user-owned untracked `docs/product-site/design/selected-homepage-direction.png`.
- Do not modify directional benchmark WIP in the build worktree.
- Use the existing static offline site; do not introduce a framework or network dependency.

---

### Task 1: Freeze v3.0.1 release and dataset contracts

**Files:**
- Create: `benchmarks/ai-sdlc-product-value/release-baseline.json`
- Modify: `benchmarks/ai-sdlc-product-value/capabilities.json`
- Modify: `scripts/build_product_site_benefit_data.py`
- Test: `tests/unit/test_product_site_benefit_builder.py`

**Interfaces:**
- Consumes: release tag, commit, tree and product-contract digests.
- Produces: three datasets with a closed `product_release` object and v3.0.1 source identity.

- [ ] Add a failing builder test that rejects missing, extra, or mismatched release-baseline fields and asserts literal v3.0.1 output metadata.
- [ ] Run `UV_CACHE_DIR=.uv-cache uv run pytest -q tests/unit/test_product_site_benefit_builder.py` and confirm RED with the missing release-baseline contract.
- [ ] Add the closed baseline loader and validate v3.0.1 capability evidence paths.
- [ ] Regenerate the three JSON datasets and confirm the builder tests pass.
- [ ] Commit the data-contract batch.

### Task 2: Define the site-level v3 and evidence behavior

**Files:**
- Modify: `tests/unit/test_offline_product_site.py`

**Interfaces:**
- Consumes: the five HTML pages and three generated datasets.
- Produces: observable contracts for version identity, evidence navigation, disclosure layers, page-specific metrics, downloads and mobile-safe structure.

- [ ] Add failing tests for non-historical v2 copy, missing v3 identity, incorrect Loop semantics, missing homepage evidence rail and old download assets.
- [ ] Add failing tests for page-specific data placement and the three disclosure levels.
- [ ] Run the focused tests and confirm each failure is caused by the unchanged v2 markup.

### Task 3: Rebuild the five-page information architecture

**Files:**
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/index.html`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/loop-engineering.html`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/dynamic-expert-review.html`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/platform-capabilities.html`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/downloads-docs.html`

**Interfaces:**
- Consumes: generated data and the shared site styles/scripts.
- Produces: homepage evidence navigation, Loop dumbbells, expert deltas, grouped overall metrics and v3 downloads.

- [ ] Implement the minimum semantic markup required by Task 2 tests.
- [ ] Preserve navigation, tabs, CTAs, no-JS content and network labels.
- [ ] Run focused site tests until GREEN.
- [ ] Commit the information-architecture batch.

### Task 4: Implement shared data visuals and responsive behavior

**Files:**
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/assets/css/pages.css`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/assets/css/site.css`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/assets/js/site.js`

**Interfaces:**
- Consumes: semantic `data-*` attributes from Task 3.
- Produces: paired dumbbells, expert comparison bars, grouped metric disclosure and responsive evidence rows without JavaScript-only meaning.

- [ ] Add failing DOM/contract assertions for responsive and no-JS readable states.
- [ ] Implement the shared visual grammar while preserving focus and reduced-motion behavior.
- [ ] Run focused tests and the offline validator until GREEN.
- [ ] Commit the visual-system batch.

### Task 5: Refresh the v3.0.1 guide and delivery evidence

**Files:**
- Modify: `docs/product-site/content/USER_GUIDE.zh-CN.md`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/docs/USER_GUIDE.zh-CN.html`
- Modify: `docs/product-site/design/qa/package-manifest.sha256`
- Modify: `docs/product-site/design/qa/browser-acceptance-receipt.json`
- Modify: `docs/product-site/design/qa/*.png`
- Create: `docs/product-site/design/qa/v3-data-integration-design-qa.md`

**Interfaces:**
- Consumes: tag v3.0.1 user guide and final page tree.
- Produces: offline guide, package manifest, fresh screenshots, browser receipt and visual QA report.

- [ ] Replace the embedded guide source with the v3.0.1 12-route guide.
- [ ] Run the 116+ focused tests and the offline validator.
- [ ] Capture 390/1024/1366 screenshots and run browser interaction, accessibility, no-JS and offline checks.
- [ ] Compare the selected visual target with the final homepage and record P0/P1/P2/P3 findings.
- [ ] Fix P0/P1/P2 findings, rerun acceptance, and set `final result: passed` only with fresh evidence.
- [ ] Update continuity handoff and commit the final evidence batch.

