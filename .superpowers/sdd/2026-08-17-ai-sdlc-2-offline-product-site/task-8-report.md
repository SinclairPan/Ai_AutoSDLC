# Task 8 Report: Downloads & Docs and Beginner Guide

## Outcome

Implemented the Task 8 resource center and a source-bound, standalone Chinese
beginner guide. The Downloads & Docs page stays intentionally compact: it
contains official GitHub/Release/README resources, exact Release 2.0.0
identity, the local guide entry, and exactly three installer/SHA pairs. The
four installation paths live only in the guide.

The guide contains all 12 scenario/OS paths in source order for no-JavaScript
use. With JavaScript enabled, the reader chooses one of four mutually exclusive
scenarios and then one of its three operating systems. Every path repeats its
own Install, Verify, Initialize, and Start sequence, including purpose,
location, PowerShell command, expected result, troubleshooting, and next step.

Planned single commit subject: `feat: add offline downloads and beginner guide`.

## Release truth and boundaries

- Release identity: `v2.0.0`.
- Annotated tag commit: `737bda39e05c53450e180a20581b7b7a70db9cf0`.
- Tag tree: `3db58121e228a7a1c4c6b760c535d6df1ffdbe84`.
- Frozen guide source SHA-256:
  `8466b8535cea8f0a17e15181060b954ad84a815be96c7e2b269f84cfce054d67`.
- The immutable, non-draft, non-prerelease GitHub release exposes exactly the
  Windows AMD64 ZIP, macOS ARM64 TGZ, and Linux AMD64 TGZ, each with its
  matching `.sha256` asset.
- The site contains no installer archive. External resources are ordinary
  links visibly marked `需要联网`; the local guide remains available from
  `file://`.
- No v2 migration guide, package payload, unsupported platform, or invented
  install route was added.

## Changed files

- `deliverables/ai-sdlc-2.0-offline-product-site/downloads-docs.html`
  - Adds the exact release identity and official GitHub, Release, README, and
    local/online guide resources.
  - Adds exactly three compact installer/SHA rows and keeps walkthroughs out of
    the product page.
- `deliverables/ai-sdlc-2.0-offline-product-site/docs/USER_GUIDE.zh-CN.html`
  - Renders the frozen guide into 12 complete paths, 48 steps, 288 labelled
    step parts, and 48 source-parity command blocks.
  - Uses only nested local CSS/JS references and a relative return link.
- `deliverables/ai-sdlc-2.0-offline-product-site/assets/css/pages.css`
  - Adds responsive resource-table, guide-selector, step, diagnostic, safety,
    and command treatments.
  - Prevents grid children and long inline paths from causing mobile overflow;
    explicitly hides inactive scenario tabs after progressive enhancement.
- `deliverables/ai-sdlc-2.0-offline-product-site/assets/js/site.js`
  - Scopes visible OS tabs and keyboard movement to the selected guide
    scenario while preserving all source content for no-JavaScript use.
- `scripts/validate_offline_product_site.py`
  - Corrects the focus-visible contract to require one shared site rule rather
    than duplicating the same rule in every stylesheet.
- `tests/unit/test_offline_product_site.py`
  - Adds exact resource, allowlist-mutation, frozen-source parity, missing-path,
    missing-part, changed-command, tab-visibility, and final closed-world tests.

## TDD evidence

### RED

The initial Task 8 contract run, before implementing the resource page and
guide, reported:

```text
6 failed, 4 passed, 66 deselected
```

The failures covered the old Downloads heading, missing local guide, missing
12-path guide structure, and the resulting closed-world failure. Host, tag,
filename, and SHA-link mutations were already rejected by the new tests.

Visual verification later exposed that a grid declaration overrode the native
`hidden` presentation of inactive OS tabs. Its focused regression test first
reported:

```text
1 failed, 77 deselected in 0.36s
```

After that rule was fixed, a browser keyboard assertion showed
`keyboardWrapsWithinScenario=false` at all five viewports because ArrowRight
could cross into a hidden scenario.

### GREEN

The final fresh unit run reported:

```text
78 passed in 0.58s
```

The hidden-tab regression test passed, and the final browser run reported
`keyboardWrapsWithinScenario=true` at all five viewports.

## Browser verification

The in-app Browser bridge could not import its required `node:process` module,
and `playwright-cli` explicitly rejected direct `file:` navigation. The same
installed Playwright Chromium runtime was therefore invoked by a temporary
local verification script with file access enabled. No temporary script,
screenshot, browser profile, or generated report was added to the repository.

The fresh run covered `1440x900`, `1366x768`, `1280x800`, `1024x768`, and
`390x844`. At every viewport:

- Downloads and the guide each had `scrollWidth - clientWidth == 0`.
- All 12 hash-addressed path states selected the matching panel, exposed four
  steps and 24 labelled parts, and kept only three OS tabs visible.
- ArrowRight from the last visible OS tab wrapped to the first OS tab in the
  same scenario.
- Downloads opened the nested local guide, and the return control opened
  Downloads again.
- Console errors and page errors were both `0`.

With JavaScript disabled at `390x844`, all 12 path panels, all 48 steps, and
all source content remained visible; horizontal overflow stayed `0`, and all
three stylesheets resolved to local `file://` URLs.

Visual inspection of desktop, 1024px, and mobile screenshots found readable
hierarchy, copyable command blocks, no clipped controls, no overlapping text,
and no accidental four-path walkthrough on the Downloads page.

## Final verification

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run pytest -q tests/unit/test_offline_product_site.py
# 78 passed in 0.58s

uv run python scripts/validate_offline_product_site.py --root deliverables/ai-sdlc-2.0-offline-product-site --guide-source docs/product-site/content/USER_GUIDE.zh-CN.md
# OFFLINE_PRODUCT_SITE_VALID

uv run ruff check scripts/validate_offline_product_site.py tests/unit/test_offline_product_site.py
# All checks passed!

node --check deliverables/ai-sdlc-2.0-offline-product-site/assets/js/site.js
node --check deliverables/ai-sdlc-2.0-offline-product-site/assets/js/video-config.js
git diff --check
# all exited 0 with no output
```

## Self-review

- Scope is limited to Task 8 content, its shared presentation/interaction
  support, validator correction, tests, and this report.
- The rendered guide SHA and all path/step/part/command mappings are enforced;
  negative fixtures prove plausible omissions and command drift fail.
- Every runnable command is in a `<pre><code>` block, preserving the frozen
  PowerShell text after newline/trailing-whitespace normalization.
- Each path is end-to-end and does not send a beginner to another section to
  complete the current step.
- The guide includes the approved public context, diagnostics, safety
  boundaries, GitHub, README, and guide resources without adding migration
  content.
- The deliverable contains zero ZIP, TGZ, TAR.GZ, or wheel payloads.
- No external runtime dependency is required to read or navigate the two local
  pages; JavaScript is progressive enhancement only.

## Fix Round 1/5 — three Important findings

Planned fix commit subject: `fix: harden beginner guide copy and parity`.

### Changes

- Added one native, keyboard-focusable `复制命令` button and one polite status
  region beside each of the 48 source-bound command blocks. The handler reads
  only the matching `[data-guide-command].textContent`; it first attempts the
  Clipboard API in a secure context, then falls back to a temporary selected
  textarea and `document.execCommand("copy")` for direct `file://` use. Success
  changes the control to `再次复制` and announces `已复制完整命令`; failure changes
  it to `重试复制` and announces `复制失败，请手动选择命令`. Focus returns to the
  triggering button after either branch.
- Corrected all 12 operating-system labels by scenario. Offline paths now use
  `Windows AMD64 / macOS Apple Silicon / Linux AMD64`; online paths use
  `Windows / macOS / Linux`. The Tab, path index, and visible current-path note
  agree for every path.
- Extended `validate_guide_parity()` to parse the frozen Markdown into ordered
  semantic blocks for every `path × step × part`. Paragraphs, list items, and
  fenced command blocks are normalized independently, retained in order, and
  compared with the matching rendered HTML part. The validator also rejects a
  changed six-part order.
- Updated the final closed-world test so it executes both the complete-site
  validator and frozen-guide parity rather than relying on a separate parity
  test alone.

### TDD evidence

The focused review suite was added before implementation and produced:

```text
6 failed, 78 deselected in 0.43s
```

The failures were the incorrect scenario labels, zero copy controls, altered
expected output not detected, empty troubleshooting not detected, wrong next
action not detected, and swapped parts not detected.

After implementing each contract, the focused suite reported:

```text
7 passed, 77 deselected in 0.41s
```

The seventh assertion is the combined final closed-world gate. The four
semantic mutation fixtures now all produce `guide_part_content_mismatch`; the
swapped-parts fixture additionally produces `guide_part_order_mismatch`.

### Five-viewport file verification

The existing in-app Browser / `playwright-cli` direct-file limitations remain
environmental, so the same installed Playwright Chromium runtime was invoked
by a temporary script against the real nested `file://` guide. Clipboard API
was explicitly unavailable so every success exercised the fallback branch.

```text
viewport   protocol commands controls statuses validCopies overflow console page
1440x900   file:    48       48       48       48          0        0       0
1366x768   file:    48       48       48       48          0        0       0
1280x800   file:    48       48       48       48          0        0       0
1024x768   file:    48       48       48       48          0        0       0
390x844    file:    48       48       48       48          0        0       0
```

For every one of the 240 keyboard activations, the DOM command text matched
the corresponding command parsed independently from the frozen Markdown, the
fallback textarea selection exactly matched the DOM `textContent`, focus
returned to the control, and the success state was announced. A separate
forced failure returned:

```text
state=error label=重试复制 status=复制失败，请手动选择命令
```

At `390x844` with JavaScript disabled, all 48 commands and all 12 paths remained
visible, all 48 inert copy controls had no layout box, and document overflow
was `0`. Visual inspection at 1440px, 1024px, and 390px confirmed the copy row,
status, and internally scrolling command block remained readable without page
overflow.

### Final fix-round verification

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run pytest -q tests/unit/test_offline_product_site.py
# 84 passed in 0.75s

uv run python scripts/validate_offline_product_site.py --root deliverables/ai-sdlc-2.0-offline-product-site --guide-source docs/product-site/content/USER_GUIDE.zh-CN.md
# OFFLINE_PRODUCT_SITE_VALID

uv run ruff check scripts/validate_offline_product_site.py tests/unit/test_offline_product_site.py
# All checks passed!

node --check deliverables/ai-sdlc-2.0-offline-product-site/assets/js/site.js
node --check deliverables/ai-sdlc-2.0-offline-product-site/assets/js/video-config.js
git diff --check
# all exited 0 with no output
```

### Fix-round self-review

- The copy path never reconstructs, trims, or interpolates a command; both API
  branches receive the exact source-bound code node `textContent`.
- Buttons remain native controls with at least 44px height, visible focus from
  the shared stylesheet, focus restoration, success/error state, and an
  `aria-live` status. With JavaScript disabled, only the enhancement controls
  disappear; commands remain readable.
- Architecture labels are now a scenario relation, not one global three-item
  list, so online availability is not incorrectly restricted to release-asset
  architectures.
- Full part parity is semantic rather than raw-HTML equality: it tolerates
  HTML presentation but rejects changed content, missing recovery guidance,
  wrong continuation actions, block reordering, and part swaps.
- No frozen Markdown, release URL, installer asset, main product page, or
  non-Task-8 feature changed. No unresolved Task 8 issue remains.
