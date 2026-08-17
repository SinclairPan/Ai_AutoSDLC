# AI-SDLC 2.0 Offline Product Site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a polished, dependency-free offline AI-SDLC 2.0 product website that opens from `index.html`, presents five focused product pages, includes the self-contained Chinese new-user guide, and remains usable without a backend or network connection.

**Architecture:** The deliverable is a multi-page static site under one closed directory. Five top-level HTML files share local CSS and classic JavaScript; the nested user guide is a standalone document page. A Python standard-library validator and pytest contract tests enforce relative paths, offline runtime rules, page structure, product-truth boundaries, version identity, accessibility hooks, and supported download assets. Visual QA uses the approved homepage direction and browser captures at fixed laptop and mobile viewports.

**Tech Stack:** HTML5, CSS custom properties, classic ES2020 JavaScript without modules or framework runtime, Python 3.11 standard library, pytest, `playwright-cli` for visual verification, local raster assets generated with the ImageGen workflow.

## Global Constraints

- Implement against `docs/product-site/design/offline-product-site-visual-design-spec-v1.md` and `docs/product-site/content/offline-product-site-copy-v1.md`.
- Freeze `docs/product-site/content/USER_GUIDE.zh-CN.md` at SHA256 `8466b8535cea8f0a17e15181060b954ad84a815be96c7e2b269f84cfce054d67`; any source-guide change requires a new parity baseline and a fresh review.
- Treat `docs/product-site/design/homepage-direction-v2-approved.png` as the only approved homepage visual reference; do not use the earlier `selected-homepage-direction.png` in the deliverable.
- The evaluator must be able to double-click `index.html`; no server, package install, build command, CDN, web font, remote script, or remote stylesheet is allowed at runtime.
- External network addresses are allowed only as allowlisted visible `<a>` links and as inert command text inside source-bound `<pre><code data-guide-command>` blocks in the local user guide. Runtime attributes, CSS, JavaScript, non-allowlisted anchors, and unbound prose/code must reject external URLs.
- Main navigation text is exactly `AI-SDLC 2.0`, `Loop Engineering`, `Dynamic Expert Review`, `Platform Capabilities`, `Downloads & Docs`.
- Product values and explanatory content are Chinese; do not add competition labels, evaluator scripts, “30 秒看懂”, fake metrics, customer logos, or unsupported claims.
- The homepage video remains unconfigured until the user supplies a real local recording. The empty state must not display a fake duration, timeline, checksum, chapter, or successful playback state.
- The configured video path, poster path, caption path, and title live in one local JavaScript configuration file; valid local MP4 playback must use native controls and support fullscreen.
- Bounded Dynamic Expert Review Graph is an explanatory information-flow concept, not a graph database, durable runtime, voting system, Veto/Quorum model, or second state machine.
- The expert contract is one Primary Expert plus at most one Cross-risk Expert, read-only input, Findings returned to the original Writer, at most one rereview, and expert failure remaining `needs_review`.
- Local PR Review is a conditional cross-stage final review, not a fifth always-running Loop identical to Requirement, Design Contract, Implementation, and Frontend Evidence.
- Platform content must not claim multi-Agent orchestration, complete E2E platform ownership, WCAG certification, bundled private Vue 2 packages, or completely offline remote-provider inference.
- Supported v2.0.0 offline assets are exactly Windows AMD64, macOS ARM64, and Linux AMD64. Windows ARM, macOS Intel, and Linux ARM are not supported assets.
- `Downloads & Docs` contains no v2 migration guide, product-contract entry, or duplicated installation walkthrough. The four scenario families live only in `docs/USER_GUIDE.zh-CN.html`.
- Acceptance viewports are 1440×900, 1366×768, 1280×800, 1024×768, and 390×844. There must be no horizontal scrolling.
- Every material generated during implementation—copy change, image asset, page screenshot, validation report, and package—must pass the assigned adversarial review gate before it becomes the next task's baseline.
- Execute this plan in an isolated worktree created with `superpowers:using-git-worktrees`; do not implement on the evidence/design branch directly.

---

## File Structure

### Deliverable

```text
deliverables/ai-sdlc-2.0-offline-product-site/
├── index.html
├── loop-engineering.html
├── dynamic-expert-review.html
├── platform-capabilities.html
├── downloads-docs.html
├── docs/
│   └── USER_GUIDE.zh-CN.html
└── assets/
    ├── css/
    │   ├── tokens.css
    │   ├── site.css
    │   └── pages.css
    ├── images/
    │   ├── hero-layers.png
    │   └── video-poster.png
    └── js/
        ├── video-config.js
        └── site.js
```

### Verification and review evidence

```text
scripts/validate_offline_product_site.py
tests/unit/test_offline_product_site.py
docs/product-site/design/qa/
├── home-1440x900.png
├── home-1366x768.png
├── home-1280x800.png
├── home-1024x768.png
├── home-390x844.png
├── loop-1366x768.png
├── expert-review-1366x768.png
├── platform-1366x768.png
├── downloads-1366x768.png
├── guide-1366x768.png
└── guide-390x844.png
```

### File responsibilities

- `tokens.css`: colors, typography scale, spacing, breakpoints-as-comments, radii, focus ring, motion duration.
- `site.css`: reset, body, navigation, buttons, video shell, value navigation, tabs, common responsive behavior, accessibility utilities.
- `pages.css`: Loop workspace, expert graph, platform capability explorer, download resources, and guide-specific layouts.
- `video-config.js`: one immutable `window.AISDLC_VIDEO` object; empty `src` means the verified “即将加入” state.
- `site.js`: mobile navigation, URL-hash tabs, keyboard tab behavior, external-link safety, and video configuration. It does not fetch JSON or import modules.
- `validate_offline_product_site.py`: standard-library-only contract validator callable from tests and CLI.
- `test_offline_product_site.py`: unit tests for validator failure modes plus the final closed-world site contract.

## Approved-content coverage matrix

| Approved content | Product page | Required structure | Test anchor |
|---|---|---|---|
| Product positioning, product video, three value routes | Home | Hero, honest video state, three value links | `test_homepage_contract` |
| Formal WorkItem path and lifecycle | Loop Engineering | Explicit formal-path qualifier and lifecycle rail from Init/Adopt through Close | `test_loop_page_covers_formal_workitem_lifecycle` |
| Seven-step minimum Loop protocol | Loop Engineering | Expandable operating-model section with all seven numbered steps | `test_loop_page_covers_minimum_protocol` |
| Four Loop types plus cross-stage Local PR Review | Loop Engineering | Four stage tabs and a separate Local PR Review rail | `test_loop_page_keeps_pr_review_cross_stage` |
| State meaning, drift refusal, failure/fix/recovery chain | Loop Engineering | State table, input-digest drift rule, recover/reconcile flow | `test_loop_page_covers_state_and_recovery` |
| Bounded Dynamic Expert Review Graph | Dynamic Expert Review | Writer, frozen snapshot, one required expert, optional second expert, Findings, Writer fix, one rereview | `test_expert_page_truth_contract` |
| Cross-tool governance, continuity, frontend delivery, engineering control | Platform Capabilities | Exactly four main tabs with named sub-capabilities and boundaries | `test_platform_page_truth_contract` |
| v2.0.0 identity, official resources, installer links | Downloads & Docs | Version identity, approved link allowlist, three platform asset rows | `test_downloads_resource_contract` |
| Four scenarios × three operating systems × four steps | Local user guide | 12 path sections, 48 step sections, six required content parts per step, source command parity | `test_user_guide_source_parity` |

---

### Task 1: Build the offline-site contract validator

**Files:**
- Create: `scripts/validate_offline_product_site.py`
- Create: `tests/unit/test_offline_product_site.py`

**Interfaces:**
- Produces: `SiteIssue(code: str, path: Path, detail: str)`.
- Produces: `validate_site(root: Path) -> list[SiteIssue]`.
- Produces: `validate_video_config(root: Path) -> list[SiteIssue]` for non-empty `src`, `poster`, and `captions` paths in `assets/js/video-config.js`.
- Produces: `validate_guide_parity(source_markdown: Path, rendered_html: Path) -> list[SiteIssue]` for the frozen 12-path guide inventory.
- Produces: `build_manifest(root: Path) -> str` with sorted `<sha256>  <relative/path>` lines and no absolute paths.
- Produces: CLI `uv run python scripts/validate_offline_product_site.py --root deliverables/ai-sdlc-2.0-offline-product-site` with exit `0` on no issues and exit `1` otherwise.
- Produces: optional CLI `--write-manifest docs/product-site/design/qa/package-manifest.sha256` that writes the deliverable-only manifest after successful validation.
- Produces: optional CLI `--guide-source docs/product-site/content/USER_GUIDE.zh-CN.md`; when present, guide-source SHA and rendered parity are required before success.
- Consumes later: the final site root from Tasks 2–8.

- [ ] **Step 1: Write validator unit tests for remote runtime assets, missing local assets, forbidden copy, and required pages**

```python
from pathlib import Path

from scripts.validate_offline_product_site import (
    build_manifest,
    validate_guide_parity,
    validate_site,
    validate_video_config,
)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_remote_runtime_asset_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "index.html", '<script src="https://cdn.example/app.js"></script>')
    issues = validate_site(tmp_path)
    assert "remote_runtime_asset" in {issue.code for issue in issues}


def test_missing_local_asset_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "index.html", '<img src="assets/images/missing.webp" alt="">')
    issues = validate_site(tmp_path)
    assert "missing_local_asset" in {issue.code for issue in issues}


def test_competition_copy_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "index.html", "<main>课题一赛事要求</main>")
    issues = validate_site(tmp_path)
    assert "forbidden_public_copy" in {issue.code for issue in issues}


def test_unknown_external_anchor_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "index.html", '<a href="https://example.com">unexpected</a>')
    issues = validate_site(tmp_path)
    assert "external_url_not_allowed" in {issue.code for issue in issues}


def test_external_url_in_unbound_text_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "index.html", "<main><p>https://example.com/install</p></main>")
    issues = validate_site(tmp_path)
    assert "external_url_in_unbound_text" in {issue.code for issue in issues}


def test_source_bound_guide_command_url_is_inert(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "docs/USER_GUIDE.zh-CN.html",
        '<main><pre><code data-guide-command="path-2a-install-1">'
        "git clone https://github.com/SinclairPan/Ai_AutoSDLC.git"
        "</code></pre></main>",
    )
    issues = validate_site(tmp_path)
    assert "external_url_in_unbound_text" not in {issue.code for issue in issues}


def test_missing_configured_video_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "assets/js/video-config.js",
        'window.AISDLC_VIDEO = {src:"assets/video/missing.mp4",poster:"",captions:""};',
    )
    issues = validate_video_config(tmp_path)
    assert "missing_video_asset" in {issue.code for issue in issues}


def test_empty_video_with_existing_poster_is_valid(tmp_path: Path) -> None:
    _write(tmp_path, "assets/images/video-poster.png", "poster")
    _write(
        tmp_path,
        "assets/js/video-config.js",
        'window.AISDLC_VIDEO = {src:"",poster:"assets/images/video-poster.png",captions:""};',
    )
    assert validate_video_config(tmp_path) == []


def test_configured_video_path_cannot_escape_site_root(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "assets/js/video-config.js",
        'window.AISDLC_VIDEO = {src:"../outside.mp4",poster:"",captions:""};',
    )
    issues = validate_video_config(tmp_path)
    assert "video_path_escape" in {issue.code for issue in issues}


def test_manifest_uses_sorted_relative_paths(tmp_path: Path) -> None:
    _write(tmp_path, "z.txt", "z")
    _write(tmp_path, "a.txt", "a")
    lines = build_manifest(tmp_path).splitlines()
    assert lines == sorted(lines, key=lambda line: line.split("  ", 1)[1])
    assert str(tmp_path) not in "\n".join(lines)
```

- [ ] **Step 2: Run the focused tests and confirm the missing module failure**

Run:

```powershell
uv run pytest -q tests/unit/test_offline_product_site.py
```

Expected: FAIL during import because `scripts.validate_offline_product_site` does not exist.

- [ ] **Step 3: Implement the validator with standard-library parsing**

Use these constants and signatures exactly:

```python
REQUIRED_PAGES = (
    "index.html",
    "loop-engineering.html",
    "dynamic-expert-review.html",
    "platform-capabilities.html",
    "downloads-docs.html",
    "docs/USER_GUIDE.zh-CN.html",
)

FORBIDDEN_PUBLIC_COPY = (
    "赛事要求",
    "课题一",
    "课题二",
    "30 秒看懂",
    "v2 迁移指南",
    "产品契约",
)

@dataclass(frozen=True)
class SiteIssue:
    code: str
    path: Path
    detail: str

RUNTIME_REF_ATTRS = {
    "script": "src",
    "link": "href",
    "img": "src",
    "source": "src",
    "track": "src",
}

ALLOWED_EXTERNAL_URLS = frozenset({
    "https://github.com/SinclairPan/Ai_AutoSDLC",
    "https://github.com/SinclairPan/Ai_AutoSDLC/releases/tag/v2.0.0",
    "https://github.com/SinclairPan/Ai_AutoSDLC/blob/v2.0.0/README.md",
    "https://github.com/SinclairPan/Ai_AutoSDLC/blob/v2.0.0/USER_GUIDE.zh-CN.md",
    "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-windows-amd64.zip",
    "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-windows-amd64.zip.sha256",
    "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-macos-arm64.tar.gz",
    "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-macos-arm64.tar.gz.sha256",
    "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-linux-amd64.tar.gz",
    "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-linux-amd64.tar.gz.sha256",
})
```

Implement `validate_site` with an `HTMLParser` subclass that records runtime references, anchors, visible text, ancestor context, one `main`, one `h1`, skip links, nav state, and tab attributes. Inspect `script[src]`, `link[href]`, `img[src]`, `source[src]`, `track[src]`, and local anchors. Reject `http://` or `https://` runtime assets. Accept an HTTP(S) anchor only when its normalized URL belongs to `ALLOWED_EXTERNAL_URLS`; reject every other external anchor. Reject URL literals in visible HTML text unless the text is inside `docs/USER_GUIDE.zh-CN.html` under `<pre><code data-guide-command>`; the final `--guide-source` parity gate proves those inert command URLs came from the frozen guide. Strip fragments and query strings before checking local file existence, reject local paths that escape the site root, and scan CSS `url(...)` values for remote or missing files. Scan local JavaScript for remote URL literals, module imports, and `fetch(`.

Implement `validate_video_config` by parsing the three quoted string fields from `video-config.js`. Resolve every non-empty media path from the site root, reject root escape, and require the referenced file to exist. The default non-empty poster must therefore pass, an empty `src`/`captions` must pass, and a future non-empty MP4 or VTT must exist.

Implement `validate_guide_parity` with this fixed inventory:

```python
GUIDE_SOURCE_SHA256 = "8466b8535cea8f0a17e15181060b954ad84a815be96c7e2b269f84cfce054d67"
GUIDE_PATH_IDS = tuple(f"path-{group}{platform}" for group in range(1, 5) for platform in "abc")
GUIDE_STEPS = ("install", "verify", "initialize", "start")
GUIDE_PARTS = ("purpose", "location", "command", "expected", "troubleshoot", "next")
```

Require 12 path nodes, four step nodes under each path, and six labelled part nodes under each step. Normalize CRLF/LF and trailing whitespace, then require every source fenced command block to equal the corresponding rendered `<pre><code>` text. Reject a source SHA mismatch before comparing HTML.

Implement `build_manifest` by iterating `sorted(path for path in root.rglob("*") if path.is_file())`, hashing file bytes with `hashlib.sha256`, and joining lines as `f"{digest}  {path.relative_to(root).as_posix()}"` with one trailing newline.

- [ ] **Step 4: Implement CLI output and exit behavior**

```python
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--write-manifest", type=Path)
    parser.add_argument("--guide-source", type=Path)
    args = parser.parse_args()
    issues = validate_site(args.root.resolve())
    issues.extend(validate_video_config(args.root.resolve()))
    if args.guide_source:
        issues.extend(
            validate_guide_parity(
                args.guide_source.resolve(),
                args.root.resolve() / "docs/USER_GUIDE.zh-CN.html",
            )
        )
    if issues:
        for issue in issues:
            print(f"{issue.code}: {issue.path}: {issue.detail}")
        return 1
    if args.write_manifest:
        args.write_manifest.write_text(build_manifest(args.root.resolve()), encoding="utf-8")
    print(f"OFFLINE_PRODUCT_SITE_VALID root={args.root.resolve()}")
    return 0
```

- [ ] **Step 5: Run the tests and lint**

Run:

```powershell
uv run pytest -q tests/unit/test_offline_product_site.py
uv run ruff check scripts/validate_offline_product_site.py tests/unit/test_offline_product_site.py
```

Expected: all tests pass; Ruff exits `0`.

- [ ] **Step 6: Commit the validator**

```powershell
git add scripts/validate_offline_product_site.py tests/unit/test_offline_product_site.py
git commit -m "test: add offline product site contract validator"
```

---

### Task 2: Create the static shell, navigation, and design tokens

**Files:**
- Create: `deliverables/ai-sdlc-2.0-offline-product-site/index.html`
- Create: `deliverables/ai-sdlc-2.0-offline-product-site/loop-engineering.html`
- Create: `deliverables/ai-sdlc-2.0-offline-product-site/dynamic-expert-review.html`
- Create: `deliverables/ai-sdlc-2.0-offline-product-site/platform-capabilities.html`
- Create: `deliverables/ai-sdlc-2.0-offline-product-site/downloads-docs.html`
- Create: `deliverables/ai-sdlc-2.0-offline-product-site/assets/css/tokens.css`
- Create: `deliverables/ai-sdlc-2.0-offline-product-site/assets/css/site.css`
- Create: `deliverables/ai-sdlc-2.0-offline-product-site/assets/css/pages.css`
- Create: `deliverables/ai-sdlc-2.0-offline-product-site/assets/js/site.js`
- Modify: `tests/unit/test_offline_product_site.py`

**Interfaces:**
- Produces: `body[data-page]` values `home`, `loop`, `expert`, `platform`, `downloads`.
- Produces: shared nav `.site-nav`, mobile toggle `[data-nav-toggle]`, menu `[data-nav-menu]`.
- Produces: tab containers `[data-tabs]`, tab buttons `[data-tab]`, panels `[data-tab-panel]`.
- Consumes: CSS variables from `tokens.css`; page-specific rules from `pages.css`.

- [ ] **Step 1: Add a failing top-level shell test**

```python
TOP_LEVEL_PAGES = (
    "index.html",
    "loop-engineering.html",
    "dynamic-expert-review.html",
    "platform-capabilities.html",
    "downloads-docs.html",
)


def test_top_level_page_shells_exist() -> None:
    root = Path("deliverables/ai-sdlc-2.0-offline-product-site")
    assert [name for name in TOP_LEVEL_PAGES if not (root / name).is_file()] == []
```

- [ ] **Step 2: Run the structure test and confirm required-page failures**

Run:

```powershell
uv run pytest -q tests/unit/test_offline_product_site.py::test_top_level_page_shells_exist
```

Expected: FAIL because the five top-level HTML files do not exist.

- [ ] **Step 3: Create five semantic HTML shells**

Each top-level page must include this structure, with page-specific `data-page`, title, `aria-current="page"`, `h1`, and main content:

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI-SDLC 2.0</title>
  <link rel="stylesheet" href="assets/css/tokens.css">
  <link rel="stylesheet" href="assets/css/site.css">
  <link rel="stylesheet" href="assets/css/pages.css">
  <script src="assets/js/site.js" defer></script>
</head>
<body data-page="home">
  <a class="skip-link" href="#main">跳到主要内容</a>
  <header class="site-header">
    <a class="brand" href="index.html">AI-SDLC 2.0</a>
    <button type="button" data-nav-toggle aria-expanded="false" aria-controls="site-nav">菜单</button>
    <nav id="site-nav" class="site-nav" data-nav-menu aria-label="主导航">
      <a href="index.html" aria-current="page">AI-SDLC 2.0</a>
      <a href="loop-engineering.html">Loop Engineering</a>
      <a href="dynamic-expert-review.html">Dynamic Expert Review</a>
      <a href="platform-capabilities.html">Platform Capabilities</a>
      <a href="downloads-docs.html">Downloads &amp; Docs</a>
    </nav>
    <a href="https://github.com/SinclairPan/Ai_AutoSDLC">GitHub</a>
  </header>
  <main id="main">
    <h1>把不确定的 AI 生成，变成可验证的工程交付</h1>
  </main>
</body>
</html>
```

The navigation link targets are exactly:

```text
index.html
loop-engineering.html
dynamic-expert-review.html
platform-capabilities.html
downloads-docs.html
https://github.com/SinclairPan/Ai_AutoSDLC
```

- [ ] **Step 4: Implement the token file**

Define at least these exact custom properties:

```css
:root {
  --ink-950: #111827;
  --ink-700: #52627a;
  --brand-900: #0b1f5e;
  --brand-600: #1548d8;
  --brand-050: #eef4ff;
  --line-200: #dce3ee;
  --surface: #ffffff;
  --warm-light: #ffcf9f;
  --content-max: 77.5rem;
  --focus-ring: 0 0 0 3px rgba(21, 72, 216, 0.28);
  --radius-button: 0.625rem;
  --radius-media: 1.25rem;
  --motion-fast: 180ms;
}
```

- [ ] **Step 5: Implement common CSS without a card-grid default**

Include system font stacks, 44×44px minimum interactive targets, `.site-shell`, sticky header, text links, two button styles, thin dividers, responsive nav, skip link, focus-visible ring, and `prefers-reduced-motion`. Do not give every `section`, article, list item, or tab panel a background, border, radius, or shadow. Without the root `.js` class, keep the full navigation and every core tab panel visible in document order. Only `.js`-enhanced pages may collapse the mobile menu or hide inactive panels.

- [ ] **Step 6: Implement common JavaScript**

Use an IIFE with `setupMobileNavigation`, `setupTabs`, and `setupExternalLinks`. Add the `.js` class immediately, then initialize on `DOMContentLoaded`. The tab core follows this contract:

```javascript
(() => {
  "use strict";
  document.documentElement.classList.add("js");

  const setupMobileNavigation = (root = document) => {
    const toggle = root.querySelector("[data-nav-toggle]");
    const menu = root.querySelector("[data-nav-menu]");
    if (!toggle || !menu) return;
    const setOpen = (open) => {
      toggle.setAttribute("aria-expanded", String(open));
      menu.dataset.open = String(open);
    };
    toggle.addEventListener("click", () => {
      setOpen(toggle.getAttribute("aria-expanded") !== "true");
    });
    root.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || toggle.getAttribute("aria-expanded") !== "true") return;
      setOpen(false);
      toggle.focus();
    });
  };

  const setupExternalLinks = (root = document) => {
    root.querySelectorAll('a[href^="http://"], a[href^="https://"]').forEach((link) => {
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    });
  };

  const setupTabs = (root = document) => {
    root.querySelectorAll("[data-tabs]").forEach((group) => {
      const tabs = [...group.querySelectorAll("[data-tab]")];
      const panels = [...group.querySelectorAll("[data-tab-panel]")];
      const activate = (id, push) => {
        const selected = tabs.find((tab) => tab.dataset.tab === id) || tabs[0];
        if (!selected) return;
        tabs.forEach((tab) => {
          const active = tab === selected;
          tab.setAttribute("aria-selected", String(active));
          tab.tabIndex = active ? 0 : -1;
        });
        panels.forEach((panel) => {
          panel.hidden = panel.id !== selected.getAttribute("aria-controls");
        });
        if (push && location.hash !== `#${selected.dataset.tab}`) {
          history.pushState(null, "", `#${selected.dataset.tab}`);
        }
      };
      const restore = () => activate(location.hash.slice(1), false);
      tabs.forEach((tab, index) => {
        tab.addEventListener("click", () => activate(tab.dataset.tab, true));
        tab.addEventListener("keydown", (event) => {
          const keyMoves = {
            ArrowLeft: (index - 1 + tabs.length) % tabs.length,
            ArrowRight: (index + 1) % tabs.length,
            Home: 0,
            End: tabs.length - 1,
          };
          if (!(event.key in keyMoves)) return;
          event.preventDefault();
          tabs[keyMoves[event.key]].click();
          tabs[keyMoves[event.key]].focus();
        });
      });
      window.addEventListener("popstate", restore);
      window.addEventListener("hashchange", restore);
      restore();
    });
  };

  document.addEventListener("DOMContentLoaded", () => {
    setupMobileNavigation();
    setupTabs();
    setupExternalLinks();
  });
})();
```

`setupMobileNavigation` changes `aria-expanded`, toggles only the enhanced mobile menu, closes on Escape, and restores focus to the menu button. `setupExternalLinks` adds `target="_blank"` and `rel="noopener noreferrer"` only to HTTP(S) anchors. Initialization reads the current hash without creating history; user changes use `pushState`; `popstate` and `hashchange` restore the corresponding panel. If the hash is absent or invalid, activate the first tab and leave the URL unchanged.

- [ ] **Step 7: Run the shell tests**

```powershell
uv run pytest -q tests/unit/test_offline_product_site.py
```

Expected: all fixture-based validator tests and the top-level shell test pass. The full closed-world validator is intentionally deferred until the nested guide and all assets exist in Task 8.

- [ ] **Step 8: Commit the shell**

```powershell
git add deliverables/ai-sdlc-2.0-offline-product-site tests/unit/test_offline_product_site.py
git commit -m "feat: add offline product site shell"
```

---

### Task 3: Produce and review the local visual assets

**Files:**
- Create: `deliverables/ai-sdlc-2.0-offline-product-site/assets/images/hero-layers.png`
- Create: `deliverables/ai-sdlc-2.0-offline-product-site/assets/images/video-poster.png`
- Create: `docs/product-site/design/qa/asset-review.md`

**Interfaces:**
- `hero-layers.png`: decorative right-side glass-layer image, no words, controls, logo, or fake interface.
- `video-poster.png`: 16:9 glass-layer scene with no embedded words, controls, logo, duration, or player chrome. The exact product title is rendered as accessible HTML above the poster.
- Both assets are local and referenced by CSS/HTML.

- [ ] **Step 1: Generate the hero asset from the approved visual reference**

Use the ImageGen skill with `homepage-direction-v2-approved.png` attached as the reference. Preserve transparent layered blue glass, warm point light, white-to-pale-blue surrounding tone, and premium enterprise engineering mood. Remove all page text, navigation, buttons, video controls, fake application UI, and watermarks. Target a landscape image suitable for a 48% desktop hero slot.

- [ ] **Step 2: Generate the 16:9 video poster**

Use the same reference and visual language. Render no text or play icon into the bitmap; the exact title and real control remain separate accessible HTML.

- [ ] **Step 3: Save final selected PNG assets and record hashes**

Copy the selected built-in ImageGen PNG outputs to the two exact deliverable paths without overwriting a previously reviewed version. Preserve aspect ratio; do not stretch. Record dimensions, byte size, and SHA256 with:

```powershell
sips -g pixelWidth -g pixelHeight deliverables/ai-sdlc-2.0-offline-product-site/assets/images/hero-layers.png
sips -g pixelWidth -g pixelHeight deliverables/ai-sdlc-2.0-offline-product-site/assets/images/video-poster.png
Get-Item deliverables/ai-sdlc-2.0-offline-product-site/assets/images/hero-layers.png,deliverables/ai-sdlc-2.0-offline-product-site/assets/images/video-poster.png | Select-Object Name,Length
Get-FileHash deliverables/ai-sdlc-2.0-offline-product-site/assets/images/hero-layers.png,deliverables/ai-sdlc-2.0-offline-product-site/assets/images/video-poster.png -Algorithm SHA256
```

Write those exact results to `asset-review.md`.

- [ ] **Step 4: Run a three-expert asset review on the same hashes**

Review roles:

1. AI-SDLC practice expert: product meaning and factual neutrality.
2. AI Coding industry expert: premium developer-product positioning and differentiation.
3. Technical evaluator: first impression, readability, small-screen crop, and absence of Web-PPT cues.

Each reviewer returns `PASS` or at most three P0/P1 findings. Apply one consolidated correction batch, recompute hashes, and require final `PASS / PASS / PASS`. Record the final verdicts in `asset-review.md`.

- [ ] **Step 5: Verify the two selected asset files**

```powershell
Get-FileHash deliverables/ai-sdlc-2.0-offline-product-site/assets/images/hero-layers.png -Algorithm SHA256
Get-FileHash deliverables/ai-sdlc-2.0-offline-product-site/assets/images/video-poster.png -Algorithm SHA256
uv run pytest -q tests/unit/test_offline_product_site.py
```

Expected: both hashes match the final `asset-review.md` entries and all current unit tests pass.

- [ ] **Step 6: Commit reviewed assets**

```powershell
git add deliverables/ai-sdlc-2.0-offline-product-site/assets/images docs/product-site/design/qa/asset-review.md
git commit -m "design: add reviewed offline site visuals"
```

---

### Task 4: Implement the homepage and video contract

**Files:**
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/index.html`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/assets/css/site.css`
- Create: `deliverables/ai-sdlc-2.0-offline-product-site/assets/js/video-config.js`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/assets/js/site.js`
- Modify: `tests/unit/test_offline_product_site.py`

**Interfaces:**
- Produces: `window.AISDLC_VIDEO`.
- Produces: `[data-video-empty]`, `[data-video-empty-poster]`, `[data-video-player]`, `[data-video-title]`.
- Produces: homepage value anchors for Loop, expert review, and platform pages.

- [ ] **Step 1: Add failing homepage contract tests**

Assert that `index.html` contains the approved hero title, approved three value titles, three matching relative hrefs, `data-video-empty`, `data-video-empty-poster`, `data-video-player`, and a local `video-config.js` script. Assert that `video-config.js` has an empty `src`, the empty-state poster points to the reviewed local PNG, and the homepage contains no fake duration text. Assert `site.js` calls `setupVideo()` inside its existing `DOMContentLoaded` initializer. Add temporary-site tests where empty `src` plus an existing poster passes, existing MP4/poster/VTT files pass, and any configured media file that is missing or escapes the site root fails.

- [ ] **Step 2: Create the immutable video configuration**

```javascript
window.AISDLC_VIDEO = Object.freeze({
  src: "",
  type: "video/mp4",
  captions: "",
  poster: "assets/images/video-poster.png",
  title: "AI-SDLC 2.0 产品实录"
});
```

- [ ] **Step 3: Implement the homepage Hero**

Use the exact title `把不确定的 AI 生成，变成可验证的工程交付`. The left column contains the approved explanation, the `观看产品实录` action, the `从 Loop Engineering 开始` link, and the version/tool line. The right column contains the 16:9 video shell. Its no-video HTML includes `<img data-video-empty-poster src="assets/images/video-poster.png" alt="">` beneath the exact product title and empty-state message, so the same-size poster remains visible without JavaScript. The Hero must remain one two-column surface at desktop widths, not two cards.

- [ ] **Step 4: Implement the video state transition**

Add this function to the existing IIFE:

```javascript
const setupVideo = (root = document, config = window.AISDLC_VIDEO) => {
  const empty = root.querySelector("[data-video-empty]");
  const emptyPoster = root.querySelector("[data-video-empty-poster]");
  const video = root.querySelector("[data-video-player]");
  if (!empty || !video || !config) return;
  if (emptyPoster && config.poster) emptyPoster.src = config.poster;
  if (!config.src) return;
  const source = document.createElement("source");
  source.src = config.src;
  source.type = config.type;
  video.append(source);
  if (config.captions) {
    const track = document.createElement("track");
    track.kind = "captions";
    track.srclang = "zh-CN";
    track.label = "中文字幕";
    track.src = config.captions;
    video.append(track);
  }
  video.poster = config.poster;
  video.hidden = false;
  empty.hidden = true;
};
```

Update the existing initializer rather than adding a second listener:

```javascript
document.addEventListener("DOMContentLoaded", () => {
  setupMobileNavigation();
  setupTabs();
  setupExternalLinks();
  setupVideo();
});
```

Use native `<video controls preload="metadata" playsinline>` so fullscreen remains browser-native. In the empty state, the action opens or focuses the video region and announces `产品实录即将加入` instead of pretending playback started.

- [ ] **Step 5: Exercise the configured-video DOM transition in an isolated browser copy**

Copy the current site to a fresh temporary directory. In that copy only, create local `assets/video/smoke.mp4` and `assets/video/smoke.vtt` files, set `src`, `poster`, and `captions` in `video-config.js`, then open the copied `index.html` via `file://`. The media bytes do not need to prove playback quality; this acceptance proves the configuration-to-DOM contract. Assert the player is visible, the empty state is hidden, one `<source>` has the configured local path/type, one captions `<track>` has the configured local VTT, the local poster is bound, native `controls` remains present, and there is no JavaScript console error. Delete the temporary copy after recording the result; do not add smoke media to the deliverable.

- [ ] **Step 6: Implement the approved three-value navigation**

Use the section statement `真正的差距，不在生成速度，而在交付能力。` and these exact titles:

```text
从一次生成，到持续完成
从模型自审，到专家对抗
从零散 Skills，到项目级工程系统
```

The CTA under each value links directly to its matching page. Use thin vertical dividers at wide widths and a single vertical list with horizontal dividers at narrow widths.

- [ ] **Step 7: Run tests and commit**

```powershell
uv run pytest -q tests/unit/test_offline_product_site.py
git add deliverables/ai-sdlc-2.0-offline-product-site tests/unit/test_offline_product_site.py
git commit -m "feat: build AI-SDLC product homepage"
```

---

### Task 5: Implement the Loop Engineering workspace

**Files:**
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/loop-engineering.html`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/assets/css/pages.css`
- Modify: `tests/unit/test_offline_product_site.py`

**Interfaces:**
- Tab ids: `requirement`, `design-contract`, `implementation`, `frontend-evidence`.
- Cross-stage rail id: `local-pr-review`.
- Formal lifecycle region: `[data-workitem-lifecycle]`.
- Minimum operating model: `[data-loop-protocol]` with exactly seven numbered steps.
- State reference: `[data-loop-state-table]`.
- Failure and recovery chain: `[data-loop-recovery]`.
- Each tab panel contains `[data-loop-input]`, `[data-loop-state]`, `[data-loop-feedback]`, `[data-loop-close]`, and `[data-loop-evidence]`.

- [ ] **Step 1: Add failing Loop page tests**

Add these exact contract tests:

- `test_loop_page_covers_formal_workitem_lifecycle`: assert the page states that arbitrary chat input does not automatically become a complete WorkItem, and contains the ordered lifecycle `Init / Adopt → WorkItem → Requirement → Design Contract → Implementation → Frontend Evidence（按需） → Local PR Review（按需） → Close` inside `[data-workitem-lifecycle]`.
- `test_loop_page_covers_minimum_protocol`: assert `[data-loop-protocol]` contains exactly seven numbered items covering Loop identity/input, artifact-derived state, gaps/stop/next action, frozen `input_digest`, Findings returned to the original Writer, at most one rereview, and close/freeze drift rejection.
- `test_loop_page_keeps_pr_review_cross_stage`: assert four Loop tab ids, one separately labelled Local PR Review rail, and that `local-pr-review` is not inside the four-tab list.
- `test_loop_page_covers_state_and_recovery`: assert `needs_user`, `needs_fix`, `needs_review`, and `closed`, plus the ordered recovery chain `gap → state → user / Writer / reviewer action → recompute → close or remain open` and an explicit refusal to reuse stale evidence after input drift.

Also assert one `h1` and the CTA to the expert page.

- [ ] **Step 2: Build the Hero and formal WorkItem lifecycle**

Use title `让 AI 开发任务，从明确需求走到可验证完成`. Directly below it, render a compact lifecycle rail with the exact ordered path from Step 1. Label it `正式 WorkItem 路径`; explain that ordinary conversation can start clarification, but only the explicit WorkItem path establishes governed lifecycle, identity, state, and closure evidence. Do not imply that every chat message is already a complete WorkItem.

- [ ] **Step 3: Add the seven-step operating model and recovery contract**

Use a semantic ordered list inside `[data-loop-protocol]`:

```text
1. Receive an explicit objective/input and assign Loop identity.
2. Recompute current state from project artifacts instead of trusting chat memory.
3. Output missing information, stop reason, and the single next action.
4. Freeze input_digest and the read-only review snapshot before execution or review.
5. Return Findings to the original Writer; reviewers do not modify the candidate.
6. After the targeted fix, allow at most one rereview against the rebound input.
7. Before Freeze/Close, rebuild the input and refuse closure when any bound input drifts.
```

Render the visitor-facing copy in Chinese, while keeping `input_digest`, `Findings`, `Writer`, `Freeze`, and `Close` as product terms. Add `[data-loop-state-table]` defining `needs_user` (missing decision/input), `needs_fix` (candidate must change), `needs_review` (independent verification is pending or inconclusive), and `closed` (fresh bound evidence satisfies the close contract). Add `[data-loop-recovery]` as a compact process strip: detected gap → explicit state → responsible human/Writer/reviewer action → artifact/state recomputation → close or remain open. State that changed inputs invalidate stale evidence and require rebinding.

- [ ] **Step 4: Build the four-tab Loop workspace and evidence band**

The active panel displays four labelled columns: current input, current state, feedback action, and close/freeze condition. Use product-language examples from the approved copy; do not invent runtime statistics or an operational dashboard.

For each tab, display the relevant artifact types:

```text
Requirement: objective, acceptance criteria, input digest, review snapshot
Design Contract: spec.md, plan.md, tasks.md, coverage/report
Implementation: required tasks, verification-evidence.json, test/lint/build
Frontend Evidence: browser entry, interactions, console/page errors, screenshots
```

Render Local PR Review as a separate horizontal band labelled `提交前跨阶段复核（按需）` with Review Pack, Findings, fix/rerun, and final report.

- [ ] **Step 5: Verify hash navigation and narrow layout**

Open `loop-engineering.html#implementation`, reload, and confirm Implementation remains active. Resize to 1024×768 and confirm the four-column panel becomes one readable column without a compressed workflow diagram.

- [ ] **Step 6: Run tests and commit**

```powershell
uv run pytest -q tests/unit/test_offline_product_site.py
git add deliverables/ai-sdlc-2.0-offline-product-site/loop-engineering.html deliverables/ai-sdlc-2.0-offline-product-site/assets/css/pages.css tests/unit/test_offline_product_site.py
git commit -m "feat: add Loop Engineering product workspace"
```

---

### Task 6: Implement Bounded Dynamic Expert Review Graph

**Files:**
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/dynamic-expert-review.html`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/assets/css/pages.css`
- Modify: `tests/unit/test_offline_product_site.py`

**Interfaces:**
- Risk-view tab ids: `review-requirement`, `review-design`, `review-implementation`, `review-frontend`, `review-pr`.
- Graph nodes: Writer, frozen input/digest, Primary Expert, conditional Cross-risk Expert, Findings, Writer fix, one rereview, Close/needs_review.
- Boundary strip: read-only experts, maximum two experts, maximum one rereview, fail to `needs_review`.

- [ ] **Step 1: Add failing expert-page truth tests**

Assert presence of `Primary Expert`, `Cross-risk Expert`, `input_digest`, `Findings`, `原 Writer`, `最多一次复审`, and `needs_review`. Assert absence of `Veto`, `Quorum`, `投票`, `图数据库`, and `自主多 Agent Runtime` in positive claims.

- [ ] **Step 2: Build the page Hero and semantic graph**

Use title `让关键结果先经独立挑战，再进入下一步`. Build the graph from ordered HTML lists and labelled regions so screen readers receive the same order as sighted users. CSS may draw connecting lines with borders, but must not replace node labels with a bitmap or compress the graph into unreadable text.

- [ ] **Step 3: Implement five risk-view tabs**

Each tab changes only four values: primary risk, selected Primary Expert role, optional second risk, and a concrete example Finding. It must not change the global expert-count or rereview limits.

Use these role examples:

```text
Requirement → scope and acceptance expert
Design Contract → interface and boundary expert
Implementation → behavior and regression expert
Frontend Evidence → interaction and evidence-identity expert
Local PR Review → cross-stage regression expert
```

- [ ] **Step 4: Add the responsibility boundary and final CTA**

Display the exact closing statement `专家负责把问题说清楚，原 Writer 负责把结果修好，Loop 负责决定能不能关闭。` Link the CTA to `platform-capabilities.html`.

- [ ] **Step 5: Run tests and commit**

```powershell
uv run pytest -q tests/unit/test_offline_product_site.py
git add deliverables/ai-sdlc-2.0-offline-product-site/dynamic-expert-review.html deliverables/ai-sdlc-2.0-offline-product-site/assets/css/pages.css tests/unit/test_offline_product_site.py
git commit -m "feat: explain bounded dynamic expert review"
```

---

### Task 7: Implement Platform Capabilities explorer

**Files:**
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/platform-capabilities.html`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/assets/css/pages.css`
- Modify: `tests/unit/test_offline_product_site.py`

**Interfaces:**
- Main tab ids: `tool-governance`, `continuity`, `frontend-delivery`, `engineering-controls`.
- Each panel contains exactly one problem statement, one mechanism view, one inspectable-result list, and explicit boundary copy.

- [ ] **Step 1: Add failing platform-page tests**

Assert exactly four main tabs and required visible terms: Codex, Claude Code, Cursor, Copilot, checkpoint, handoff, recover, PrimeVue, enterprise-vue2, Browser Gate, code simplification, local-first. Assert that `macOS Intel`, `完整 E2E 平台`, `附送私有组件包`, and `完全离线 AI 推理` do not appear as positive claims.

- [ ] **Step 2: Build the capability explorer**

Use title `把零散 Skills，变成留在项目里的工程系统`. The four tabs map exactly to the four groups in the approved product copy:

```text
跨 AI 工具治理
断点续作
前端工程与验收
工程控制边界
```

- [ ] **Step 3: Implement each panel's inspectable results**

Use the following artifacts and boundaries:

```text
Tool governance → canonical adapter files and project-side Loop artifacts; not live multi-Agent orchestration
Continuity → checkpoint, status, handoff, recover, reconcile; not hidden-chain-of-thought recovery
Frontend delivery → solution confirmation, public-primevue, enterprise-vue2 profile, Style Pack, Browser Gate; not bundled private packages or WCAG certification
Engineering controls → fail-closed identity/evidence/security plus non-blocking simplification advice; not hard line-count gating or offline remote inference
```

- [ ] **Step 4: Add the comparison sentence and download CTA**

Use exact closing copy: `Prompt 和 Skills 告诉 AI 怎么做；AI-SDLC 继续管理它做到哪一步、凭什么继续，以及何时可以结束。` Link to `downloads-docs.html`.

- [ ] **Step 5: Run tests and commit**

```powershell
uv run pytest -q tests/unit/test_offline_product_site.py
git add deliverables/ai-sdlc-2.0-offline-product-site/platform-capabilities.html deliverables/ai-sdlc-2.0-offline-product-site/assets/css/pages.css tests/unit/test_offline_product_site.py
git commit -m "feat: add project-level platform capability explorer"
```

---

### Task 8: Implement Downloads & Docs and the self-contained user guide

**Files:**
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/downloads-docs.html`
- Create: `deliverables/ai-sdlc-2.0-offline-product-site/docs/USER_GUIDE.zh-CN.html`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/assets/css/pages.css`
- Modify: `tests/unit/test_offline_product_site.py`

**Interfaces:**
- Downloads page exposes v2.0.0 identity, GitHub, Release, README, local user guide, and exactly three installer/SHA pairs.
- Guide scenario ids: `existing-offline`, `existing-online`, `new-offline`, `new-online`.
- Guide operating-system anchors: `windows`, `macos`, `linux` within each scenario family.
- Frozen guide source SHA256: `8466b8535cea8f0a17e15181060b954ad84a815be96c7e2b269f84cfce054d67`.
- Rendered path ids: `path-1a` through `path-4c`.
- Every path contains four `article[data-guide-step]` nodes: `install`, `verify`, `initialize`, `start`.
- Every step contains six labelled nodes: `[data-guide-part="purpose"]`, `[data-guide-part="location"]`, `[data-guide-part="command"]`, `[data-guide-part="expected"]`, `[data-guide-part="troubleshoot"]`, and `[data-guide-part="next"]`.

- [ ] **Step 1: Add failing resource, guide, and final closed-world tests**

Assert exact version `v2.0.0`, tag commit `737bda39e05c53450e180a20581b7b7a70db9cf0`, tag tree `3db58121e228a7a1c4c6b760c535d6df1ffdbe84`, and the three official asset filename/SHA links from the approved copy. Assert every HTTP(S) link belongs to `ALLOWED_EXTERNAL_URLS`; a single changed host, tag, filename, or SHA URL must fail validation.

Add `test_user_guide_source_parity` that checks the frozen Markdown SHA, all 12 `path-*` sections, all 48 `data-guide-step` articles, all six required parts per step, and exact normalized parity between each Markdown fenced command block and its corresponding rendered `<pre><code data-guide-command="path-1a-install-1">` block. Assert the local guide contains no instruction that sends a beginner to another directory section to complete a current step. Add negative fixtures for one missing path, one missing part, and one altered command. Add the final contract test only now:

```python
def test_built_site_has_no_contract_issues() -> None:
    root = Path("deliverables/ai-sdlc-2.0-offline-product-site")
    assert validate_site(root) == []
```

- [ ] **Step 2: Build the resource page**

First screen: version identity and official repository resources on the left; Chinese new-user guide entry on the right. Mark every external link with visible `需要联网`; keep the local guide unmarked and usable offline.

Second layer: compact three-row installer table for Windows AMD64, macOS ARM64, Linux AMD64. Do not include binaries in the site directory.

- [ ] **Step 3: Convert the approved guide into semantic standalone HTML**

Source only from `docs/product-site/content/USER_GUIDE.zh-CN.md`. Preserve each command, expected CLI result, error treatment, and next action inside the selected scenario/OS sequence. The guide header provides `返回 Downloads & Docs` linking to `../downloads-docs.html`; it is not a sixth main product page.

The nested guide loads `../assets/css/tokens.css`, `../assets/css/site.css`, `../assets/css/pages.css`, and `../assets/js/site.js`. It must not use root-absolute `/assets/...` paths, because those break when opened from `file://`.

Use four scenario selectors:

```text
已有项目 + 离线安装包
已有项目 + 在线安装
全新项目 + 离线安装包
全新项目 + 在线安装
```

Within the chosen scenario, present Windows, macOS, and Linux paths. Do not write “请查看另一章后继续”; repeat the command and expectation needed for that path.

Use these exact source-to-HTML mappings so the validator can prove the rendered guide is complete and source-bound:

```python
PATH_HEADING_TO_ID = {
    "路径 1A": ("path-1a", "existing-offline", "windows"),
    "路径 1B": ("path-1b", "existing-offline", "macos"),
    "路径 1C": ("path-1c", "existing-offline", "linux"),
    "路径 2A": ("path-2a", "existing-online", "windows"),
    "路径 2B": ("path-2b", "existing-online", "macos"),
    "路径 2C": ("path-2c", "existing-online", "linux"),
    "路径 3A": ("path-3a", "new-offline", "windows"),
    "路径 3B": ("path-3b", "new-offline", "macos"),
    "路径 3C": ("path-3c", "new-offline", "linux"),
    "路径 4A": ("path-4a", "new-online", "windows"),
    "路径 4B": ("path-4b", "new-online", "macos"),
    "路径 4C": ("path-4c", "new-online", "linux"),
}
STEP_TITLE_TO_ID = {
    "Install": "install",
    "Verify": "verify",
    "Initialize": "initialize",
    "Start": "start",
}
PART_HEADING_TO_ID = {
    "本步要完成什么": "purpose",
    "在哪里执行": "location",
    "复制并运行": "command",
    "你应该看到": "expected",
    "如果结果不同": "troubleshoot",
    "下一步": "next",
}
```

For example, the `路径 1A` section becomes `<section id="path-1a" data-guide-path="path-1a" data-guide-scenario="existing-offline" data-guide-os="windows">`; its `Install` subsection becomes `<article data-guide-step="install">`; and its six source headings become the six exact `data-guide-part` values above. Each fenced block under `复制并运行` becomes `<pre><code data-guide-command="path-1a-install-1">` with byte-equivalent command text after CRLF/LF and trailing-whitespace normalization.

Repeat this complete structure for `path-1a` through `path-4c`. When one step has multiple fenced command blocks, suffix their ids in source order (`-1`, `-2`, and so on). The visual selector may show one enhanced path at a time, but the initial HTML must contain all 12 complete paths in document order for no-JavaScript use and source-parity validation.

- [ ] **Step 4: Verify local navigation from both directory levels**

Resolve and open these two file URLs directly, then follow their links:

```powershell
$siteRoot = (Resolve-Path 'deliverables/ai-sdlc-2.0-offline-product-site').Path
$downloadsUri = [Uri]::new((Join-Path $siteRoot 'downloads-docs.html')).AbsoluteUri
$guideUri = [Uri]::new((Join-Path $siteRoot 'docs/USER_GUIDE.zh-CN.html')).AbsoluteUri
playwright-cli -s=ai-sdlc-offline-site open $downloadsUri
playwright-cli -s=ai-sdlc-offline-site goto $guideUri
```

Expected: guide opens from Downloads; return link opens Downloads; all CSS is local; external resources remain ordinary clickable URLs.

- [ ] **Step 5: Run tests and commit**

```powershell
uv run pytest -q tests/unit/test_offline_product_site.py
uv run python scripts/validate_offline_product_site.py --root deliverables/ai-sdlc-2.0-offline-product-site --guide-source docs/product-site/content/USER_GUIDE.zh-CN.md
git add deliverables/ai-sdlc-2.0-offline-product-site tests/unit/test_offline_product_site.py
git commit -m "feat: add offline downloads and beginner guide"
```

---

### Task 9: Complete responsive, accessibility, and file-URL verification

**Files:**
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/assets/css/site.css`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/assets/css/pages.css`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/assets/js/site.js`
- Modify: `tests/unit/test_offline_product_site.py`
- Create: `docs/product-site/design/qa/interaction-verification.md`

**Interfaces:**
- Verification session name: `ai-sdlc-offline-site`.
- Required viewports: 1440×900, 1366×768, 1280×800, 1024×768, 390×844.
- Required keyboard paths: skip link, main nav, mobile menu, each tab list, homepage actions, guide scenario selector.
- Enhanced-state inventory: Home `1`; Loop `4`; Expert `5`; Platform `4`; Downloads `1`; Guide `12`; total `27` states per viewport and `135` state/viewport results.
- History acceptance sequence on Loop, Expert, Platform, and Guide: default A → select B → Back returns A → Forward returns B → Reload keeps B.

- [ ] **Step 1: Extend validator tests for accessibility hooks**

Assert every page has one `main`, one `h1`, a skip link, viewport metadata, visible focus CSS, and the correct `aria-current`. Assert every tab button has `role="tab"`, `aria-controls`, and a matching `role="tabpanel"`. Assert every decorative image has empty alt and every meaningful image has non-empty alt.

- [ ] **Step 2: Open the site directly from the filesystem**

Run:

```powershell
$siteRoot = (Resolve-Path 'deliverables/ai-sdlc-2.0-offline-product-site').Path
$indexUri = [Uri]::new((Join-Path $siteRoot 'index.html')).AbsoluteUri
playwright-cli -s=ai-sdlc-offline-site open $indexUri
playwright-cli -s=ai-sdlc-offline-site network-state-set offline
```

Expected: the homepage loads with local styling and images. `playwright-cli -s=ai-sdlc-offline-site console error` reports no errors caused by missing runtime assets or blocked module requests.

- [ ] **Step 3: Capture the homepage at all five viewports**

For each size, run `resize`, then `screenshot`. Save the accepted output under the exact filenames listed in the file structure. Inspect every image with `view_image`; reject blank, clipped, horizontally scrolling, or loading captures.

The 1366×768 capture must show the full main headline, both primary actions, and the main body of the 16:9 video window. The 390×844 capture must show readable text and a single-column value list.

Put `homepage-direction-v2-approved.png` and `home-1440x900.png` into the same visual-comparison input. Check hierarchy, video proportion, title wrapping, color, spacing, radii, and the position of the value section. Fix visible drift, recapture, and repeat the comparison once before accepting the homepage screenshots.

- [ ] **Step 4: Verify tab history and capture representative detail states**

On Loop, Expert, Platform, and Guide, run the exact sequence default A → select B → browser Back → browser Forward → Reload. Confirm selected tab, `aria-selected`, panel visibility, focus target, and URL hash agree at every transition. Then capture one accepted non-default state for each detail page at 1366×768 under the exact QA filenames.

Capture the guide at 1366×768 and 390×844. Confirm the four scenario selectors remain visible or immediately reachable, command blocks do not overflow, and the current step contains its own command, expected result, error action, and next action.

- [ ] **Step 5: Check every enhanced state at every required viewport**

At each of the five viewport sizes, inspect all 27 states: Home; all four Loop tabs; all five Expert tabs; all four Platform tabs; Downloads; and all 12 guide paths. For each state, confirm its panel is visible and run:

```powershell
playwright-cli -s=ai-sdlc-offline-site eval "() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth })"
```

Expected: `scrollWidth` is not greater than `clientWidth`, no content is clipped, and controls do not overlap. Record all 135 state/viewport results in `interaction-verification.md`; representative screenshots remain the eleven named files above.

- [ ] **Step 6: Verify progressive enhancement with JavaScript unavailable**

Block the local JavaScript before reloading the site:

```powershell
playwright-cli -s=ai-sdlc-offline-site route "**/*.js" --body "" --content-type "application/javascript"
playwright-cli -s=ai-sdlc-offline-site reload
```

At 1366×768 and 390×844, open all five product pages and the local guide. Confirm the full main navigation is visible, every core tab/panel and all 12 guide paths remain readable in document order, the video poster and honest empty-state message remain visible, and ordinary local links still work. Record results, then restore JavaScript routing:

```powershell
playwright-cli -s=ai-sdlc-offline-site unroute "**/*.js"
playwright-cli -s=ai-sdlc-offline-site reload
```

- [ ] **Step 7: Run keyboard and reduced-motion checks**

Use `press Tab`, arrow keys, Home, End, Enter, and Escape. Confirm focus never disappears, the mobile menu returns focus to its toggle, and Tab activation matches ARIA state. Emulate or manually set reduced motion and confirm no essential information depends on animation.

- [ ] **Step 8: Record evidence and run the complete local checks**

`interaction-verification.md` records viewport, page, interaction, result, screenshot path, console result, and known limits. Then run:

```powershell
uv run pytest -q tests/unit/test_offline_product_site.py
uv run ruff check scripts/validate_offline_product_site.py tests/unit/test_offline_product_site.py
uv run python scripts/validate_offline_product_site.py --root deliverables/ai-sdlc-2.0-offline-product-site --guide-source docs/product-site/content/USER_GUIDE.zh-CN.md
git diff --check
```

Expected: all commands exit `0`.

- [ ] **Step 9: Commit responsive and accessibility work**

```powershell
git add deliverables/ai-sdlc-2.0-offline-product-site tests/unit/test_offline_product_site.py docs/product-site/design/qa/interaction-verification.md docs/product-site/design/qa/*.png
git commit -m "test: verify offline site across laptop viewports"
```

---

### Task 10: Run final adversarial acceptance and package the closed-world site

**Files:**
- Create: `docs/product-site/design/qa/final-adversarial-review.md`
- Create: `docs/product-site/design/qa/package-manifest.sha256`
- Modify only if a blocker is found: files under `deliverables/ai-sdlc-2.0-offline-product-site/`, validator, or tests.

**Interfaces:**
- Final review baseline: Git commit plus SHA256 for every deliverable, validator, test, approved copy/guide/visual source, QA document, and QA screenshot.
- Final package root: `deliverables/ai-sdlc-2.0-offline-product-site/`.
- Final result: `PASS / PASS / PASS`, empty validator result, and a closed-world manifest.

- [ ] **Step 1: Freeze one review baseline**

Require a clean worktree. Record `git rev-parse HEAD`, `git status --porcelain=v1`, and sorted SHA256 values for every file under the deliverable root, `scripts/validate_offline_product_site.py`, `tests/unit/test_offline_product_site.py`, `docs/product-site/content/offline-product-site-copy-v1.md`, `docs/product-site/content/USER_GUIDE.zh-CN.md`, `docs/product-site/design/offline-product-site-visual-design-spec-v1.md`, `docs/product-site/design/homepage-direction-v2-approved.png`, and `docs/product-site/design/qa/`.

- [ ] **Step 2: Dispatch three independent read-only reviews against the same baseline**

Review scopes:

1. AI-SDLC practice expert: every claim, boundary, command, platform asset, Loop state, and expert role.
2. AI Coding industry expert: value differentiation, mature-product positioning, feature hierarchy, and absence of generic Skills-site framing.
3. Technical evaluator: first five seconds, navigation, visual polish, laptop readability, offline use, accessibility, and absence of Web-PPT patterns.

Each reviewer returns `PASS` or at most five P0/P1 findings with page, screenshot, and minimal fix. No reviewer may edit the baseline.

- [ ] **Step 3: Apply one consolidated correction batch**

Accept only findings supported by the frozen page, screenshot, or repository fact. Reject false findings with exact evidence. Rerun the validator, unit tests, affected screenshots, and hash manifest after the correction batch.

- [ ] **Step 4: Generate and commit the package manifest**

Run:

```powershell
uv run python scripts/validate_offline_product_site.py --root deliverables/ai-sdlc-2.0-offline-product-site --guide-source docs/product-site/content/USER_GUIDE.zh-CN.md --write-manifest docs/product-site/design/qa/package-manifest.sha256
git add deliverables/ai-sdlc-2.0-offline-product-site docs/product-site/design/qa/package-manifest.sha256 docs/product-site/design/qa/*.png
git commit -m "chore: freeze offline product site review baseline"
```

The manifest hashes only files under the deliverable root, uses relative paths, and never includes itself.

- [ ] **Step 5: Require final exact-hash PASS**

Send the same baseline commit, deliverable manifest SHA256, validator/test hashes, approved copy/guide/visual-source hashes, QA-document hashes, and screenshot hashes to all three reviewers. Final acceptance requires `PASS / PASS / PASS`; do not continue on a conditional or stale PASS.

- [ ] **Step 6: Verify the evaluator path one last time**

Copy the deliverable directory to a fresh temporary directory outside the repository. Open the copied `index.html` via `file://`, set the browser offline, navigate all five pages and the local guide, and confirm no asset resolves back to the repository.

- [ ] **Step 7: Record the final review without changing the reviewed deliverable**

Write `final-adversarial-review.md` with the reviewed baseline commit, manifest hash, screenshot hashes, each reviewer verdict, accepted fixes, and rejected findings with evidence. Commit only the review record:

```powershell
git add docs/product-site/design/qa/final-adversarial-review.md
git commit -m "docs: record final offline site acceptance"
```

- [ ] **Step 8: Run final gates and prove no post-review drift**

```powershell
uv run pytest -q tests/unit/test_offline_product_site.py
uv run ruff check scripts/validate_offline_product_site.py tests/unit/test_offline_product_site.py
uv run python scripts/validate_offline_product_site.py --root deliverables/ai-sdlc-2.0-offline-product-site --guide-source docs/product-site/content/USER_GUIDE.zh-CN.md
git diff --check
$reviewedBaselineCommit = ((Select-String -Path 'docs/product-site/design/qa/final-adversarial-review.md' -Pattern '^Reviewed baseline commit:').Line -split ':', 2)[1].Trim()
git diff --exit-code "$reviewedBaselineCommit..HEAD" -- deliverables/ai-sdlc-2.0-offline-product-site scripts/validate_offline_product_site.py tests/unit/test_offline_product_site.py docs/product-site/content/offline-product-site-copy-v1.md docs/product-site/content/USER_GUIDE.zh-CN.md docs/product-site/design/offline-product-site-visual-design-spec-v1.md docs/product-site/design/homepage-direction-v2-approved.png docs/product-site/design/qa ':(exclude)docs/product-site/design/qa/final-adversarial-review.md'
git status --porcelain=v1
```

Expected: tests, Ruff, validator, diff check, and no-drift check exit `0`; status is empty. The only commit after the reviewed baseline contains the review record itself.

---

## Plan Self-Review Checklist

- [ ] Every visual-spec section maps to a numbered task.
- [ ] All five product pages and the nested guide have exact output paths.
- [ ] The homepage video has an honest empty state and one future configuration point.
- [ ] Runtime uses no external asset, module import, fetch, server, or package dependency.
- [ ] Platform, expert-review, Loop, browser, component-provider, and download boundaries match v2.0.0 evidence.
- [ ] The four beginner scenario families remain inside the guide.
- [ ] Laptop, mobile, keyboard, reduced-motion, offline, and file-URL checks are executable.
- [ ] Every generated artifact has an explicit adversarial review gate.
- [ ] No step contains prohibited placeholder language or an undefined interface.
- [ ] Final acceptance binds all three expert verdicts to the same commit and hashes.
