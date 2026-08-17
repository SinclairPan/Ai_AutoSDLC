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
