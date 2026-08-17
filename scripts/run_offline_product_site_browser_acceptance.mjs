#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const VIEWPORTS = [
  { width: 1440, height: 900 },
  { width: 1366, height: 768 },
  { width: 1280, height: 800 },
  { width: 1024, height: 768 },
  { width: 390, height: 844 },
];

const SURFACES = [
  { key: "home", file: "index.html", states: [null] },
  {
    key: "loop",
    file: "loop-engineering.html",
    states: ["requirement", "design-contract", "implementation", "frontend-evidence"],
  },
  {
    key: "expert",
    file: "dynamic-expert-review.html",
    states: [
      "review-requirement",
      "review-design",
      "review-implementation",
      "review-frontend",
      "review-pr",
    ],
  },
  {
    key: "platform",
    file: "platform-capabilities.html",
    states: ["tool-governance", "continuity", "frontend-delivery", "engineering-controls"],
  },
  { key: "downloads", file: "downloads-docs.html", states: [null] },
  {
    key: "guide",
    file: "docs/USER_GUIDE.zh-CN.html",
    states: [
      "path-1a", "path-1b", "path-1c", "path-2a", "path-2b", "path-2c",
      "path-3a", "path-3b", "path-3c", "path-4a", "path-4b", "path-4c",
    ],
  },
];

const INTERACTION_SURFACES = SURFACES.filter((surface) =>
  ["loop", "expert", "platform", "guide"].includes(surface.key));
const INTERACTION_VIEWPORTS = [
  { width: 1366, height: 768 },
  { width: 390, height: 844 },
];
const GUIDE_SCENARIOS = Object.freeze({
  "existing-offline": "path-1a",
  "existing-online": "path-2a",
  "new-offline": "path-3a",
  "new-online": "path-4a",
});
const INTERACTIVE_AUDIT_DEFINITION =
  "visible key controls within a shared interaction region; text-only lines excluded";

const EXPECTED_SUMMARY = Object.freeze({
  stateCount: 135,
  stateFailures: 0,
  stateGeometryCheckCount: 135,
  viewportClippingFailures: 0,
  ancestorClippingFailures: 0,
  controlOverlapFailures: 0,
  mobileMenuCount: 1,
  mobileMenuFailures: 0,
  historyCount: 4,
  historyFailures: 0,
  tabKeyboardCount: 8,
  tabKeyboardFailures: 0,
  skipLinkCount: 5,
  skipLinkFailures: 0,
  guideScenarioCount: 8,
  guideScenarioFailures: 0,
  copyCount: 240,
  copyFailures: 0,
  noJsGroupCount: 12,
  noJsFailures: 0,
  configuredVideoFailures: 0,
  accessibilityFailures: 0,
  runtimeFailures: 0,
});

const resultFailureCount = (results) => results?.filter((item) => item.failures.length).length ?? -1;

const deriveSummary = (receipt) => ({
  stateCount: receipt.stateResults?.length ?? -1,
  stateFailures: resultFailureCount(receipt.stateResults),
  stateGeometryCheckCount: receipt.stateResults?.filter((item) => item.interactiveAudit).length ?? -1,
  viewportClippingFailures: receipt.stateResults?.filter(
    (item) => item.interactiveAudit?.viewportClipped?.length,
  ).length ?? -1,
  ancestorClippingFailures: receipt.stateResults?.filter(
    (item) => item.interactiveAudit?.ancestorClipped?.length,
  ).length ?? -1,
  controlOverlapFailures: receipt.stateResults?.filter(
    (item) => item.interactiveAudit?.overlaps?.length,
  ).length ?? -1,
  mobileMenuCount: receipt.mobileMenuResults?.length ?? -1,
  mobileMenuFailures: resultFailureCount(receipt.mobileMenuResults),
  historyCount: receipt.historyResults?.length ?? -1,
  historyFailures: resultFailureCount(receipt.historyResults),
  tabKeyboardCount: receipt.tabKeyboardResults?.length ?? -1,
  tabKeyboardFailures: resultFailureCount(receipt.tabKeyboardResults),
  skipLinkCount: receipt.skipLinkResults?.length ?? -1,
  skipLinkFailures: resultFailureCount(receipt.skipLinkResults),
  guideScenarioCount: receipt.guideScenarioResults?.length ?? -1,
  guideScenarioFailures: resultFailureCount(receipt.guideScenarioResults),
  copyCount: receipt.copyResults?.reduce((total, item) => total + item.results.length, 0) ?? -1,
  copyFailures: receipt.copyResults?.reduce(
    (total, item) => total + item.results.filter((result) => !result.passed).length,
    0,
  ) ?? -1,
  noJsGroupCount: receipt.noJsResults?.length ?? -1,
  noJsFailures: resultFailureCount(receipt.noJsResults),
  configuredVideoFailures: receipt.configuredVideo?.failures?.length ?? -1,
  accessibilityFailures: receipt.accessibilityResults?.reduce(
    (total, item) => total + item.failures.length,
    0,
  ) ?? -1,
  runtimeFailures: receipt.runtimeResults?.reduce(
    (total, item) => total + item.consoleErrors.length + item.pageErrors.length + item.failedRequests.length,
    0,
  ) ?? -1,
});

const parseArgs = (argv) => {
  const options = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith("--")) throw new Error(`Unexpected argument: ${key}`);
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`Missing value for ${key}`);
    options[key.slice(2)] = value;
    index += 1;
  }
  return options;
};

const requireOption = (options, key) => {
  const value = options[key];
  if (!value) throw new Error(`Missing --${key}`);
  return path.resolve(value);
};

const sha256 = (contents) => createHash("sha256").update(contents).digest("hex");
const sha256File = async (file) => sha256(await fs.readFile(file));

const collectFiles = async (root, relative = "") => {
  const directory = path.join(root, relative);
  const entries = await fs.readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const child = path.posix.join(relative.split(path.sep).join(path.posix.sep), entry.name);
    if (entry.isDirectory()) files.push(...await collectFiles(root, child));
    else if (entry.isFile()) files.push(child);
  }
  return files;
};

const buildManifest = async (root) => {
  const files = (await collectFiles(root)).sort();
  const lines = [];
  for (const relative of files) {
    lines.push(`${await sha256File(path.join(root, relative))}  ${relative}`);
  }
  return `${lines.join("\n")}${lines.length ? "\n" : ""}`;
};

const isWithin = (root, candidate) => {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== "..");
};

const git = (args) => execFileSync("git", args, { encoding: "utf8" }).trim();

const verifyReceipt = async (options) => {
  const receiptPath = requireOption(options, "verify-receipt");
  const siteRoot = requireOption(options, "site-root");
  const manifestPath = requireOption(options, "manifest");
  const runnerPath = fileURLToPath(import.meta.url);
  const receipt = JSON.parse(await fs.readFile(receiptPath, "utf8"));
  const errors = [];
  const check = (condition, message) => {
    if (!condition) errors.push(message);
  };

  check(receipt.schemaVersion === 3, "schemaVersion must be 3");
  check(receipt.inputs?.manifestSha256 === await sha256File(manifestPath), "manifest SHA drifted");
  check(
    receipt.inputs?.copiedManifestSha256 === receipt.inputs?.manifestSha256,
    "fresh-copy manifest is not bound to the committed manifest",
  );
  if (receipt.schemaVersion >= 3) {
    check(receipt.inputs?.runnerSha256 === await sha256File(runnerPath), "runner SHA drifted");
  }
  check(
    (await fs.readFile(manifestPath, "utf8")) === await buildManifest(siteRoot),
    "site bytes do not match the committed manifest",
  );
  check(receipt.inputs?.copyRootKind === "fresh-external-copy", "copy root provenance missing");
  check(/^[0-9a-f]{40}$/.test(receipt.inputs?.inputCommit || ""), "input commit is invalid");
  try {
    execFileSync(
      "git",
      ["merge-base", "--is-ancestor", receipt.inputs.inputCommit, "HEAD"],
      { stdio: "ignore" },
    );
  } catch {
    errors.push("input commit is not reachable from HEAD");
  }
  if (receipt.schemaVersion >= 3) {
    try {
      const committedManifest = execFileSync(
        "git",
        ["show", `${receipt.inputs.inputCommit}:docs/product-site/design/qa/package-manifest.sha256`],
      );
      check(sha256(committedManifest) === receipt.inputs.manifestSha256, "input commit manifest SHA drifted");
    } catch {
      errors.push("input commit does not contain the bound manifest");
    }
    try {
      const committedRunner = execFileSync(
        "git",
        ["show", `${receipt.inputs.inputCommit}:scripts/run_offline_product_site_browser_acceptance.mjs`],
      );
      check(sha256(committedRunner) === receipt.inputs.runnerSha256, "input commit runner SHA drifted");
    } catch {
      errors.push("input commit does not contain the bound runner");
    }
    const boundPaths = [
      "deliverables/ai-sdlc-2.0-offline-product-site",
      "scripts/run_offline_product_site_browser_acceptance.mjs",
      "scripts/validate_offline_product_site.py",
      "docs/product-site/design/qa/package-manifest.sha256",
      "docs/product-site/design/qa/home-1440x900.png",
      "docs/product-site/design/qa/home-1366x768.png",
      "docs/product-site/design/qa/home-1280x800.png",
      "docs/product-site/design/qa/home-1024x768.png",
      "docs/product-site/design/qa/home-390x844.png",
      "docs/product-site/design/qa/loop-1366x768.png",
      "docs/product-site/design/qa/expert-review-1366x768.png",
      "docs/product-site/design/qa/platform-1366x768.png",
      "docs/product-site/design/qa/downloads-1366x768.png",
      "docs/product-site/design/qa/guide-1366x768.png",
      "docs/product-site/design/qa/guide-390x844.png",
    ];
    try {
      execFileSync(
        "git",
        ["diff", "--quiet", receipt.inputs.inputCommit, "HEAD", "--", ...boundPaths],
        { stdio: "ignore" },
      );
    } catch {
      errors.push("reviewed product or evidence inputs drifted after input commit");
    }
  }

  const derivedSummary = deriveSummary(receipt);
  check(JSON.stringify(receipt.summary) === JSON.stringify(EXPECTED_SUMMARY), "summary contract drifted");
  check(JSON.stringify(derivedSummary) === JSON.stringify(EXPECTED_SUMMARY), "summary is not reproducible");

  const expectedHistorySurfaces = new Set(INTERACTION_SURFACES.map((surface) => surface.key));
  check(
    receipt.historyResults?.length === expectedHistorySurfaces.size
      && new Set(receipt.historyResults.map((item) => item.surface)).size === expectedHistorySurfaces.size
      && receipt.historyResults.every((item) => expectedHistorySurfaces.has(item.surface)),
    "history surface coverage drifted",
  );
  const expectedKeyboardPairs = new Set(INTERACTION_VIEWPORTS.flatMap((viewport) =>
    INTERACTION_SURFACES.map((surface) => `${surface.key}:${viewport.width}x${viewport.height}`)));
  const keyboardPairs = new Set(receipt.tabKeyboardResults?.map(
    (item) => `${item.surface}:${item.viewport.width}x${item.viewport.height}`,
  ));
  check(
    keyboardPairs.size === expectedKeyboardPairs.size
      && [...expectedKeyboardPairs].every((pair) => keyboardPairs.has(pair)),
    "tab keyboard surface/viewport coverage drifted",
  );
  const expectedScenarioPairs = new Set(INTERACTION_VIEWPORTS.flatMap((viewport) =>
    Object.keys(GUIDE_SCENARIOS).map((scenario) => `${scenario}:${viewport.width}x${viewport.height}`)));
  const scenarioPairs = new Set(receipt.guideScenarioResults?.map(
    (item) => `${item.scenario}:${item.viewport.width}x${item.viewport.height}`,
  ));
  check(
    scenarioPairs.size === expectedScenarioPairs.size
      && [...expectedScenarioPairs].every((pair) => scenarioPairs.has(pair)),
    "guide scenario coverage drifted",
  );
  const expectedSkipViewports = new Set(VIEWPORTS.map((viewport) => `${viewport.width}x${viewport.height}`));
  const skipViewports = new Set(receipt.skipLinkResults?.map(
    (item) => `${item.viewport.width}x${item.viewport.height}`,
  ));
  check(
    skipViewports.size === expectedSkipViewports.size
      && [...expectedSkipViewports].every((viewport) => skipViewports.has(viewport)),
    "skip-link viewport coverage drifted",
  );
  check(
    receipt.mobileMenuResults?.length === 1
      && receipt.mobileMenuResults[0]?.viewport?.width === 390
      && receipt.mobileMenuResults[0]?.opened?.expanded === "true"
      && receipt.mobileMenuResults[0]?.opened?.menuOpen === "true"
      && receipt.mobileMenuResults[0]?.closed?.expanded === "false"
      && receipt.mobileMenuResults[0]?.closed?.menuOpen === "false"
      && receipt.mobileMenuResults[0]?.closed?.focusedToggle === true,
    "mobile menu Escape/focus evidence drifted",
  );
  for (const item of receipt.historyResults || []) {
    const surface = INTERACTION_SURFACES.find((candidate) => candidate.key === item.surface);
    const first = surface?.states[0];
    const second = surface?.states[1];
    check(
      item.snapshots?.selected?.selected === second
        && item.snapshots?.selected?.hash === `#${second}`
        && item.snapshots?.selected?.focused === second
        && item.snapshots?.back?.selected === first
        && item.snapshots?.back?.hash === ""
        && item.snapshots?.back?.focused === first
        && item.snapshots?.forward?.selected === second
        && item.snapshots?.forward?.hash === `#${second}`
        && item.snapshots?.forward?.focused === second
        && item.snapshots?.reload?.selected === second
        && item.snapshots?.reload?.hash === `#${second}`
        && item.snapshots?.reload?.focused === "BODY",
      `history observations drifted for ${item.surface}`,
    );
  }
  for (const item of receipt.tabKeyboardResults || []) {
    const surface = INTERACTION_SURFACES.find((candidate) => candidate.key === item.surface);
    const visibleStates = item.surface === "guide" ? surface?.states.slice(0, 3) : surface?.states;
    check(
      item.steps?.start === visibleStates?.[0]
        && item.steps?.ArrowRight === visibleStates?.[1]
        && item.steps?.End === visibleStates?.at(-1)
        && item.steps?.Home === visibleStates?.[0]
        && item.steps?.ArrowLeft === visibleStates?.at(-1),
      `tab keyboard observations drifted for ${item.surface}`,
    );
  }
  for (const item of receipt.skipLinkResults || []) {
    check(
      item.beforeActivation?.focusedClass?.split(/\s+/).includes("skip-link")
        && item.beforeActivation?.visible === true
        && item.afterActivation?.focusedId === "main"
        && item.afterActivation?.focusedTag === "MAIN",
      `skip-link activation observations drifted at ${item.viewport?.width}`,
    );
  }
  for (const item of receipt.guideScenarioResults || []) {
    const expected = GUIDE_SCENARIOS[item.scenario];
    check(
      item.expectedState === expected
        && item.selected === expected
        && item.hash === `#${expected}`
        && item.focused === expected
        && item.ariaCurrent === "true",
      `guide scenario observations drifted for ${item.scenario}`,
    );
  }
  for (const state of receipt.stateResults || []) {
    const audit = state.interactiveAudit;
    const controls = audit?.controls || [];
    const viewportClipped = controls.filter((control) => control.viewportClipReasons.length).map((control) => control.id);
    const ancestorClipped = controls.filter((control) => control.ancestorClipReasons.length).map((control) => control.id);
    const overlaps = [];
    for (let index = 0; index < controls.length; index += 1) {
      for (let next = index + 1; next < controls.length; next += 1) {
        const first = controls[index];
        const second = controls[next];
        if (first.region !== second.region || first.contains?.includes(second.id) || second.contains?.includes(first.id)) continue;
        const horizontal = Math.min(first.documentRect.right, second.documentRect.right)
          - Math.max(first.documentRect.left, second.documentRect.left);
        const vertical = Math.min(first.documentRect.bottom, second.documentRect.bottom)
          - Math.max(first.documentRect.top, second.documentRect.top);
        if (horizontal > 1 && vertical > 1) overlaps.push({ region: first.region, first: first.id, second: second.id });
      }
    }
    check(audit?.definition === INTERACTIVE_AUDIT_DEFINITION, "interactive audit definition drifted");
    check(audit?.keyControlCount === controls.length, "interactive key-control count drifted");
    check(JSON.stringify(audit?.viewportClipped) === JSON.stringify(viewportClipped), "viewport clipping evidence drifted");
    check(JSON.stringify(audit?.ancestorClipped) === JSON.stringify(ancestorClipped), "ancestor clipping evidence drifted");
    check(JSON.stringify(audit?.overlaps) === JSON.stringify(overlaps), "control overlap evidence drifted");
  }

  const ownership = receipt.requestOwnership || {};
  const requests = ownership.requests || [];
  const uniqueUrls = new Set(requests.map((request) => request.url));
  check(ownership.requestCount === requests.length && requests.length === 33, "request count drifted");
  check(ownership.uniqueUrlCount === uniqueUrls.size && uniqueUrls.size === 13, "unique URL count drifted");
  check(
    ownership.remoteCount === requests.filter((request) => request.protocol !== "file:").length
      && ownership.remoteCount === 0,
    "remote request ownership drifted",
  );
  check(
    ownership.siteRootEscapeCount === requests.filter((request) => request.ownership !== "copied-site-root").length
      && ownership.siteRootEscapeCount === 0,
    "site-root ownership drifted",
  );
  check(
    ownership.repositoryBackReferenceCount === requests.filter((request) => request.repositoryBackReference).length
      && ownership.repositoryBackReferenceCount === 0,
    "repository back-reference ownership drifted",
  );

  const captures = receipt.expertScreenshot?.captures || [];
  check(captures.length >= 2, "expert screenshot needs at least two captures");
  check(new Set(captures.map((capture) => capture.sha256)).size === 1, "expert screenshots are not byte-identical");
  for (const capture of captures) {
    check(capture.assertions?.unclipped === true, "expert screenshot is clipped");
    check(capture.assertions?.tablistScrollLeft === 0, "expert tablist scroll is not frozen");
    check(capture.assertions?.pageScrollX === 0 && capture.assertions?.pageScrollY === 0, "expert page scroll is not frozen");
  }
  if (receipt.expertScreenshot?.trackedPath) {
    const tracked = path.resolve(receipt.expertScreenshot.trackedPath);
    check(await sha256File(tracked) === captures[0]?.sha256, "tracked expert screenshot hash drifted");
  }

  if (errors.length) {
    for (const error of errors) console.error(`RECEIPT_INVALID ${error}`);
    process.exitCode = 1;
    return;
  }
  console.log(`BROWSER_ACCEPTANCE_RECEIPT_VALID ${receiptPath}`);
};

const runAcceptance = async (options) => {
  const sourceSiteRoot = requireOption(options, "site-root");
  const manifestPath = requireOption(options, "manifest");
  const receiptPath = requireOption(options, "receipt");
  const copyRoot = requireOption(options, "copy-root");
  const screenshotRoot = requireOption(options, "screenshot-root");
  const playwrightModule = requireOption(options, "playwright-module");
  const browserExecutable = requireOption(options, "browser-executable");
  const inputCommit = options["input-commit"];
  if (!/^[0-9a-f]{40}$/.test(inputCommit || "")) throw new Error("--input-commit must be a full commit SHA");
  git(["cat-file", "-e", `${inputCommit}^{commit}`]);
  const repositoryRoot = path.resolve(git(["rev-parse", "--show-toplevel"]));

  const manifest = await fs.readFile(manifestPath, "utf8");
  if (manifest !== await buildManifest(sourceSiteRoot)) throw new Error("Source site does not match manifest");
  await fs.mkdir(copyRoot, { recursive: true });
  if ((await fs.readdir(copyRoot)).length) throw new Error(`--copy-root must be empty: ${copyRoot}`);
  const copiedSiteRoot = path.join(copyRoot, "site");
  await fs.cp(sourceSiteRoot, copiedSiteRoot, { recursive: true });
  if (manifest !== await buildManifest(copiedSiteRoot)) throw new Error("Fresh copy does not match manifest");
  await fs.mkdir(screenshotRoot, { recursive: true });

  const { chromium } = await import(pathToFileURL(playwrightModule).href);
  const browser = await chromium.launch({
    headless: true,
    executablePath: browserExecutable,
    args: ["--allow-file-access-from-files"],
  });
  const urlFor = (file, hash = "") => `${pathToFileURL(path.join(copiedSiteRoot, file)).href}${hash ? `#${hash}` : ""}`;
  const stateResults = [];
  const runtimeResults = [];
  const accessibilityResults = [];
  const copyResults = [];
  const noJsResults = [];
  const mobileMenuResults = [];
  const historyResults = [];
  const tabKeyboardResults = [];
  const skipLinkResults = [];
  const guideScenarioResults = [];

  for (const viewport of VIEWPORTS) {
    const context = await browser.newContext({ viewport, reducedMotion: "reduce" });
    await context.setOffline(true);
    const page = await context.newPage();
    const consoleErrors = [];
    const pageErrors = [];
    const failedRequests = [];
    page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    page.on("requestfailed", (request) => { if (!request.url().startsWith("file:")) failedRequests.push(request.url()); });

    for (const surface of SURFACES) {
      await page.goto(urlFor(surface.file), { waitUntil: "load" });
      for (const state of surface.states) {
        if (state) {
          await page.evaluate((id) => { location.hash = id; }, state);
          await page.waitForFunction(
            (id) => document.querySelector(`[data-tab="${id}"]`)?.getAttribute("aria-selected") === "true",
            state,
          );
        }
        const observed = await page.evaluate(async ({ key, state, auditDefinition }) => {
          const visible = (node) => {
            if (!node) return false;
            const style = getComputedStyle(node);
            const rect = node.getBoundingClientRect();
            return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
          };
          const selected = document.querySelector('[role="tab"][aria-selected="true"]');
          const panel = selected ? document.getElementById(selected.getAttribute("aria-controls")) : null;
          const controlNodes = [...document.querySelectorAll('a[href], button, [role="tab"], video[controls]')].filter(visible);
          const rectObject = (rect, documentSpace = false) => ({
            left: rect.left + (documentSpace ? scrollX : 0),
            right: rect.right + (documentSpace ? scrollX : 0),
            top: rect.top + (documentSpace ? scrollY : 0),
            bottom: rect.bottom + (documentSpace ? scrollY : 0),
            width: rect.width,
            height: rect.height,
          });
          const controlIds = new Map(controlNodes.map((node, index) => [node,
            node.dataset.tab
              || node.dataset.copyCommand
              || node.id
              || node.getAttribute("aria-label")
              || `${node.tagName.toLowerCase()}:${index}:${node.textContent.trim().replace(/\s+/g, " ").slice(0, 48)}`]));
          const regionFor = (node) => {
            const region = node.closest(".site-header")
              || node.closest('[role="tablist"]')
              || node.closest(".guide-scenario-links")
              || node.closest(".guide-copy-row")
              || node.closest("nav, section, article, aside, footer, main")
              || document.body;
            const peers = [...document.querySelectorAll(
              '.site-header, [role="tablist"], .guide-scenario-links, .guide-copy-row, nav, section, article, aside, footer, main, body',
            )];
            return region.id
              || region.getAttribute("aria-label")
              || `${region.tagName.toLowerCase()}:${peers.indexOf(region)}`;
          };
          const scrollAncestors = new Set();
          for (const node of controlNodes) {
            for (let parent = node.parentElement; parent; parent = parent.parentElement) scrollAncestors.add(parent);
          }
          const savedScroll = [...scrollAncestors].map((node) => ({ node, left: node.scrollLeft, top: node.scrollTop }));
          const originalWindowScroll = { x: scrollX, y: scrollY };
          const controls = controlNodes.map((node) => ({
            id: controlIds.get(node),
            tag: node.tagName,
            label: node.getAttribute("aria-label") || node.textContent.trim().replace(/\s+/g, " ").slice(0, 80),
            region: regionFor(node),
            documentRect: rectObject(node.getBoundingClientRect(), true),
            contains: controlNodes.filter((candidate) => candidate !== node && node.contains(candidate)).map((candidate) => controlIds.get(candidate)),
            viewportRect: null,
            viewportClipReasons: [],
            ancestorChecks: [],
            ancestorClipReasons: [],
          }));
          for (let index = 0; index < controlNodes.length; index += 1) {
            const node = controlNodes[index];
            node.scrollIntoView({ block: "center", inline: "center" });
            await new Promise((resolve) => requestAnimationFrame(resolve));
            const rect = node.getBoundingClientRect();
            controls[index].viewportRect = rectObject(rect);
            if (rect.left < -0.5) controls[index].viewportClipReasons.push("left");
            if (rect.right > innerWidth + 0.5) controls[index].viewportClipReasons.push("right");
            if (rect.top < -0.5) controls[index].viewportClipReasons.push("top");
            if (rect.bottom > innerHeight + 0.5) controls[index].viewportClipReasons.push("bottom");
            for (let parent = node.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
              const style = getComputedStyle(parent);
              const axes = [];
              if (["hidden", "clip", "auto", "scroll"].includes(style.overflowX)) axes.push("x");
              if (["hidden", "clip", "auto", "scroll"].includes(style.overflowY)) axes.push("y");
              if (!axes.length) continue;
              const parentRect = parent.getBoundingClientRect();
              const reasons = [];
              if (axes.includes("x") && rect.left < parentRect.left - 0.5) reasons.push("left");
              if (axes.includes("x") && rect.right > parentRect.right + 0.5) reasons.push("right");
              if (axes.includes("y") && rect.top < parentRect.top - 0.5) reasons.push("top");
              if (axes.includes("y") && rect.bottom > parentRect.bottom + 0.5) reasons.push("bottom");
              const check = {
                ancestor: parent.id || parent.getAttribute("aria-label") || parent.className || parent.tagName,
                overflowX: style.overflowX,
                overflowY: style.overflowY,
                reasons,
              };
              controls[index].ancestorChecks.push(check);
              controls[index].ancestorClipReasons.push(...reasons.map((reason) => `${check.ancestor}:${reason}`));
            }
          }
          for (const saved of savedScroll) {
            saved.node.scrollLeft = saved.left;
            saved.node.scrollTop = saved.top;
          }
          scrollTo(originalWindowScroll.x, originalWindowScroll.y);
          const overlaps = [];
          for (let index = 0; index < controls.length; index += 1) {
            for (let next = index + 1; next < controls.length; next += 1) {
              const first = controls[index];
              const second = controls[next];
              if (first.region !== second.region || first.contains.includes(second.id) || second.contains.includes(first.id)) continue;
              const horizontal = Math.min(first.documentRect.right, second.documentRect.right)
                - Math.max(first.documentRect.left, second.documentRect.left);
              const vertical = Math.min(first.documentRect.bottom, second.documentRect.bottom)
                - Math.max(first.documentRect.top, second.documentRect.top);
              if (horizontal > 1 && vertical > 1) overlaps.push({ region: first.region, first: first.id, second: second.id });
            }
          }
          const interactiveAudit = {
            definition: auditDefinition,
            keyControlCount: controls.length,
            controls,
            viewportClipped: controls.filter((control) => control.viewportClipReasons.length).map((control) => control.id),
            ancestorClipped: controls.filter((control) => control.ancestorClipReasons.length).map((control) => control.id),
            overlaps,
          };
          const clippedControls = [...interactiveAudit.viewportClipped, ...interactiveAudit.ancestorClipped];
          const undersizedControls = innerWidth <= 390
            ? controlNodes.flatMap((node) => {
              const rect = node.getBoundingClientRect();
              return rect.width < 44 || rect.height < 44
                ? [node.getAttribute("aria-label") || node.textContent.trim().replace(/\s+/g, " ").slice(0, 60)]
                : [];
            })
            : [];
          const externalLinks = [...document.querySelectorAll('a[href^="http://"], a[href^="https://"]')]
            .filter(visible);
          const externalFailures = externalLinks.flatMap((link) => {
            const label = [...link.querySelectorAll(".network-label")].find((node) => visible(node) && node.textContent.trim() === "需要联网");
            const rel = new Set((link.getAttribute("rel") || "").split(/\s+/).filter(Boolean));
            return link.target === "_blank" && rel.size === 2 && rel.has("noopener") && rel.has("noreferrer") && label
              ? [] : [link.href];
          });
          const failures = [];
          if (location.protocol !== "file:") failures.push("not-file-protocol");
          if (document.documentElement.scrollWidth > document.documentElement.clientWidth) failures.push("horizontal-overflow");
          if (interactiveAudit.viewportClipped.length) failures.push("viewport-clipped-controls");
          if (interactiveAudit.ancestorClipped.length) failures.push("ancestor-clipped-controls");
          if (interactiveAudit.overlaps.length) failures.push("overlapping-controls");
          if (undersizedControls.length) failures.push("undersized-controls");
          if (state && selected?.dataset.tab !== state) failures.push("wrong-selected-tab");
          if (state && (!panel || panel.hidden || !visible(panel))) failures.push("hidden-selected-panel");
          if (document.querySelectorAll("main").length !== 1) failures.push("main-count");
          if (document.querySelectorAll("h1").length !== 1) failures.push("h1-count");
          if (!document.querySelector("nav")?.getAttribute("aria-label")) failures.push("nav-label");
          if (externalFailures.length) failures.push("external-link-contract");
          return {
            key,
            state,
            protocol: location.protocol,
            selected: selected?.dataset.tab || null,
            hash: location.hash,
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth,
            clippedControls,
            interactiveAudit,
            undersizedControls,
            externalLinkCount: externalLinks.length,
            externalFailures,
            failures,
          };
        }, { key: surface.key, state, auditDefinition: INTERACTIVE_AUDIT_DEFINITION });
        stateResults.push({ viewport, ...observed });
      }
    }

    await page.goto(urlFor("index.html"), { waitUntil: "load" });
    await page.keyboard.press("Tab");
    await page.waitForTimeout(250);
    const beforeActivation = await page.evaluate(() => {
      const active = document.activeElement;
      const failures = [];
      if (!active?.classList.contains("skip-link") || active.getBoundingClientRect().top < 0) failures.push("skip-link-focus");
      if (document.querySelectorAll("main").length !== 1) failures.push("main-count");
      if (!document.querySelector("nav")?.getAttribute("aria-label")) failures.push("nav-label");
      if (getComputedStyle(document.documentElement).scrollBehavior !== "auto") failures.push("reduced-motion");
      return {
        focusedClass: active?.className || "",
        visible: Boolean(active && active.getBoundingClientRect().top >= 0),
        failures,
      };
    });
    await page.keyboard.press("Enter");
    const afterActivation = await page.evaluate(() => ({
      focusedId: document.activeElement?.id || "",
      focusedTag: document.activeElement?.tagName || "",
    }));
    const skipFailures = [...beforeActivation.failures];
    if (afterActivation.focusedId !== "main" || afterActivation.focusedTag !== "MAIN") {
      skipFailures.push("skip-link-activation-focus");
    }
    const skipResult = { viewport, beforeActivation, afterActivation, failures: skipFailures };
    skipLinkResults.push(skipResult);
    accessibilityResults.push(skipResult);
    if (viewport.width === 390) {
      await page.goto(urlFor("index.html"), { waitUntil: "load" });
      const toggle = page.locator("[data-nav-toggle]");
      await toggle.focus();
      await page.keyboard.press("Enter");
      const opened = await page.evaluate(() => {
        const button = document.querySelector("[data-nav-toggle]");
        const menu = document.querySelector("[data-nav-menu]");
        return {
          expanded: button?.getAttribute("aria-expanded"),
          menuOpen: menu?.dataset.open,
          focusedToggle: document.activeElement === button,
        };
      });
      await page.keyboard.press("Escape");
      const closed = await page.evaluate(() => {
        const button = document.querySelector("[data-nav-toggle]");
        const menu = document.querySelector("[data-nav-menu]");
        return {
          expanded: button?.getAttribute("aria-expanded"),
          menuOpen: menu?.dataset.open,
          focusedToggle: document.activeElement === button,
        };
      });
      const failures = [];
      if (opened.expanded !== "true" || opened.menuOpen !== "true") failures.push("menu-not-opened");
      if (closed.expanded !== "false" || closed.menuOpen !== "false") failures.push("menu-not-closed");
      if (!closed.focusedToggle) failures.push("menu-focus-not-returned");
      mobileMenuResults.push({ viewport, opened, closed, failures });
    }
    runtimeResults.push({ viewport, consoleErrors, pageErrors, failedRequests });
    await context.close();
  }

  const tabSnapshot = (page) => page.evaluate(() => {
    const selected = document.querySelector('[role="tab"][aria-selected="true"]');
    const panel = selected ? document.getElementById(selected.getAttribute("aria-controls")) : null;
    return {
      selected: selected?.dataset.tab || null,
      hash: location.hash,
      focused: document.activeElement?.dataset?.tab || document.activeElement?.tagName || null,
      panelVisible: Boolean(panel && !panel.hidden && panel.getBoundingClientRect().width > 0),
    };
  });

  for (const surface of INTERACTION_SURFACES) {
    const context = await browser.newContext({ viewport: INTERACTION_VIEWPORTS[0], reducedMotion: "reduce" });
    await context.setOffline(true);
    const page = await context.newPage();
    const [first, second] = surface.states;
    await page.goto(urlFor(surface.file), { waitUntil: "load" });
    await page.locator(`[data-tab="${first}"]`).focus();
    await page.locator(`[data-tab="${second}"]`).click();
    const selected = await tabSnapshot(page);
    await page.goBack({ waitUntil: "load" });
    await page.waitForFunction((id) => document.querySelector(`[data-tab="${id}"]`)?.getAttribute("aria-selected") === "true", first);
    const back = await tabSnapshot(page);
    await page.goForward({ waitUntil: "load" });
    await page.waitForFunction((id) => document.querySelector(`[data-tab="${id}"]`)?.getAttribute("aria-selected") === "true", second);
    const forward = await tabSnapshot(page);
    await page.reload({ waitUntil: "load" });
    await page.waitForFunction((id) => document.querySelector(`[data-tab="${id}"]`)?.getAttribute("aria-selected") === "true", second);
    const reload = await tabSnapshot(page);
    const failures = [];
    if (selected.selected !== second || selected.hash !== `#${second}` || selected.focused !== second || !selected.panelVisible) failures.push("selected-state");
    if (back.selected !== first || back.hash !== "" || back.focused !== first || !back.panelVisible) failures.push("back-state");
    if (forward.selected !== second || forward.hash !== `#${second}` || forward.focused !== second || !forward.panelVisible) failures.push("forward-state");
    if (reload.selected !== second || reload.hash !== `#${second}` || reload.focused !== "BODY" || !reload.panelVisible) failures.push("reload-state");
    historyResults.push({
      surface: surface.key,
      focusContract: "Back and Forward follow the selected tab; Reload preserves hash/selection and restarts document focus at BODY",
      snapshots: { selected, back, forward, reload },
      failures,
    });
    await context.close();
  }

  for (const viewport of INTERACTION_VIEWPORTS) {
    for (const surface of INTERACTION_SURFACES) {
      const context = await browser.newContext({ viewport, reducedMotion: "reduce" });
      await context.setOffline(true);
      const page = await context.newPage();
      const first = surface.states[0];
      const visibleStates = surface.key === "guide" ? surface.states.slice(0, 3) : surface.states;
      await page.goto(urlFor(surface.file), { waitUntil: "load" });
      await page.locator(`[data-tab="${first}"]`).focus();
      const steps = { start: await page.evaluate(() => document.activeElement?.dataset?.tab || null) };
      for (const key of ["ArrowRight", "End", "Home", "ArrowLeft"]) {
        await page.keyboard.press(key);
        steps[key] = await page.evaluate(() => document.activeElement?.dataset?.tab || null);
      }
      const expected = {
        start: visibleStates[0],
        ArrowRight: visibleStates[1],
        End: visibleStates.at(-1),
        Home: visibleStates[0],
        ArrowLeft: visibleStates.at(-1),
      };
      const failures = Object.keys(expected).filter((key) => steps[key] !== expected[key]).map((key) => `keyboard-${key}`);
      tabKeyboardResults.push({ surface: surface.key, viewport, visibleStates, steps, failures });
      await context.close();
    }
  }

  for (const viewport of INTERACTION_VIEWPORTS) {
    const context = await browser.newContext({ viewport, reducedMotion: "reduce" });
    await context.setOffline(true);
    const page = await context.newPage();
    await page.goto(urlFor("docs/USER_GUIDE.zh-CN.html"), { waitUntil: "load" });
    for (const [scenario, expectedState] of Object.entries(GUIDE_SCENARIOS)) {
      const selector = page.locator(`[data-guide-scenario-selector="${scenario}"]`);
      await selector.focus();
      await page.keyboard.press("Enter");
      await page.waitForFunction((id) => document.querySelector(`[data-tab="${id}"]`)?.getAttribute("aria-selected") === "true", expectedState);
      const observed = await page.evaluate(({ scenarioId, expected }) => {
        const selected = document.querySelector('[role="tab"][aria-selected="true"]');
        const scenarioNode = document.querySelector(`[data-guide-scenario-selector="${scenarioId}"]`);
        return {
          scenario: scenarioId,
          expectedState: expected,
          selected: selected?.dataset.tab || null,
          hash: location.hash,
          focused: document.activeElement?.dataset?.tab || document.activeElement?.tagName || null,
          ariaCurrent: scenarioNode?.getAttribute("aria-current"),
          visibleTabCount: [...document.querySelectorAll('[role="tab"]')].filter((tab) => !tab.hidden).length,
        };
      }, { scenarioId: scenario, expected: expectedState });
      const failures = [];
      if (observed.selected !== expectedState || observed.hash !== `#${expectedState}` || observed.focused !== expectedState) failures.push("scenario-state");
      if (observed.ariaCurrent !== "true" || observed.visibleTabCount !== 3) failures.push("scenario-contract");
      guideScenarioResults.push({ viewport, ...observed, failures });
    }
    await context.close();
  }

  for (const viewport of VIEWPORTS) {
    const context = await browser.newContext({ viewport });
    await context.setOffline(true);
    await context.addInitScript(() => {
      Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
      Object.defineProperty(Navigator.prototype, "clipboard", {
        configurable: true,
        get() { return { writeText: async () => { throw new Error("force file fallback"); } }; },
      });
    });
    const page = await context.newPage();
    await page.goto(urlFor("docs/USER_GUIDE.zh-CN.html"), { waitUntil: "load" });
    await page.evaluate(() => {
      window.__acceptanceCopies = [];
      document.execCommand = (command) => {
        const active = document.activeElement;
        const selectedText = active instanceof HTMLTextAreaElement
          ? active.value.slice(active.selectionStart, active.selectionEnd) : null;
        const clipboardData = new DataTransfer();
        const event = new ClipboardEvent("copy", { clipboardData, cancelable: true });
        document.dispatchEvent(event);
        window.__acceptanceCopies.push({ command, selectedText, payload: clipboardData.getData("text/plain") });
        return true;
      };
    });
    const commandIds = await page.locator("code[data-guide-command]").evaluateAll((nodes) => nodes.map((node) => node.dataset.guideCommand));
    const results = [];
    for (const commandId of commandIds) {
      const pathId = commandId.match(/^path-[1-4][a-c]/)?.[0];
      await page.evaluate((id) => { location.hash = id; }, pathId);
      await page.waitForFunction((id) => !document.getElementById(id)?.hidden, pathId);
      const button = page.locator(`[data-copy-command="${commandId}"]`);
      await button.focus();
      await page.keyboard.press("Enter");
      await page.waitForFunction((id) => document.querySelector(`[data-copy-command="${id}"]`)?.dataset.copyState === "success", commandId);
      results.push(await page.evaluate((id) => {
        const buttonNode = document.querySelector(`[data-copy-command="${id}"]`);
        const commandNode = document.querySelector(`[data-guide-command="${id}"]`);
        const capture = window.__acceptanceCopies.at(-1);
        const passed = capture?.selectedText === commandNode?.textContent
          && capture?.payload === commandNode?.textContent
          && document.activeElement === buttonNode
          && buttonNode?.dataset.copyState === "success"
          && document.querySelector(`[data-copy-status="${id}"]`)?.textContent === "已复制完整命令";
        return { commandId: id, passed };
      }, commandId));
    }
    copyResults.push({ viewport, results });
    await context.close();
  }

  for (const viewport of [{ width: 1366, height: 768 }, { width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport, javaScriptEnabled: false });
    await context.setOffline(true);
    for (const surface of SURFACES) {
      const page = await context.newPage();
      await page.goto(urlFor(surface.file), { waitUntil: "load" });
      const result = await page.evaluate(({ key, expectedPanels }) => {
        const visible = (node) => {
          if (!node) return false;
          const style = getComputedStyle(node);
          const rect = node.getBoundingClientRect();
          return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
        };
        const visiblePanels = [...document.querySelectorAll("[data-tab-panel]")].filter(visible).length;
        const failures = [];
        if (visiblePanels !== expectedPanels) failures.push("hidden-panels");
        if (document.documentElement.scrollWidth > document.documentElement.clientWidth) failures.push("horizontal-overflow");
        if ([...document.styleSheets].some((sheet) => new URL(sheet.href).protocol !== "file:")) failures.push("remote-style");
        if (key === "home" && !visible(document.querySelector("[data-video-empty-poster]"))) failures.push("poster-hidden");
        return { key, expectedPanels, visiblePanels, failures };
      }, { key: surface.key, expectedPanels: surface.states.filter(Boolean).length });
      noJsResults.push({ viewport, ...result });
      await page.close();
    }
    await context.close();
  }

  let configuredVideo;
  {
    const fixtureRoot = path.join(copyRoot, "configured-video-site");
    await fs.cp(copiedSiteRoot, fixtureRoot, { recursive: true });
    await fs.mkdir(path.join(fixtureRoot, "assets", "video"), { recursive: true });
    await fs.writeFile(path.join(fixtureRoot, "assets", "video", "demo.mp4"), "configured-video-fixture");
    await fs.writeFile(path.join(fixtureRoot, "assets", "video", "demo.vtt"), "WEBVTT\n\n00:00.000 --> 00:01.000\nAI-SDLC\n");
    await fs.writeFile(
      path.join(fixtureRoot, "assets", "js", "video-config.js"),
      'window.AISDLC_VIDEO = Object.freeze({src:"assets/video/demo.mp4",type:"video/mp4",captions:"assets/video/demo.vtt",poster:"assets/images/video-poster.png",title:"已配置产品实录"});\n',
    );
    const context = await browser.newContext({ viewport: { width: 1366, height: 768 } });
    await context.setOffline(true);
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    await page.goto(pathToFileURL(path.join(fixtureRoot, "index.html")).href, { waitUntil: "load" });
    configuredVideo = await page.evaluate(() => {
      const video = document.querySelector("video[data-video-player]");
      const source = video?.querySelector("source");
      const track = video?.querySelector("track");
      const failures = [];
      if (!video || video.hidden || !video.controls) failures.push("video-not-visible-with-controls");
      if (!source || new URL(source.src).protocol !== "file:") failures.push("video-source-not-local");
      if (!track || new URL(track.src).protocol !== "file:" || track.kind !== "captions") failures.push("caption-not-local");
      if (!(typeof video?.requestFullscreen === "function" || typeof video?.webkitEnterFullscreen === "function")) failures.push("fullscreen-entry-missing");
      return { failures };
    });
    configuredVideo.failures.push(...pageErrors.map((error) => `page-error:${error}`));
    await context.close();
  }

  const requestOwnership = await collectRequestOwnership(browser, copiedSiteRoot, repositoryRoot, urlFor);
  const screenshotEvidence = await captureScreenshots(browser, screenshotRoot, repositoryRoot, urlFor);
  await browser.close();

  const resultBundle = {
    stateResults,
    mobileMenuResults,
    historyResults,
    tabKeyboardResults,
    skipLinkResults,
    guideScenarioResults,
    accessibilityResults,
    copyResults,
    noJsResults,
    configuredVideo,
    runtimeResults,
  };
  const summary = deriveSummary(resultBundle);
  const receipt = {
    schemaVersion: 3,
    inputs: {
      inputCommit,
      manifestSha256: await sha256File(manifestPath),
      manifestEntryCount: manifest.trim().split("\n").length,
      sourceSiteRoot,
      copiedSiteRoot,
      copyRootKind: "fresh-external-copy",
      copiedManifestSha256: sha256(await buildManifest(copiedSiteRoot)),
      runnerSha256: await sha256File(fileURLToPath(import.meta.url)),
      playwrightModule,
      browserExecutable,
    },
    summary,
    ...resultBundle,
    requestOwnership,
    representativeScreenshots: screenshotEvidence.representativeScreenshots,
    expertScreenshot: screenshotEvidence.expertScreenshot,
  };
  await fs.mkdir(path.dirname(receiptPath), { recursive: true });
  await fs.writeFile(receiptPath, `${JSON.stringify(receipt, null, 2)}\n`);
  console.log(JSON.stringify({ summary, requestOwnership: {
    requestCount: requestOwnership.requestCount,
    uniqueUrlCount: requestOwnership.uniqueUrlCount,
    remoteCount: requestOwnership.remoteCount,
    siteRootEscapeCount: requestOwnership.siteRootEscapeCount,
    repositoryBackReferenceCount: requestOwnership.repositoryBackReferenceCount,
  }, expertScreenshot: screenshotEvidence.expertScreenshot }, null, 2));
  if (
    JSON.stringify(summary) !== JSON.stringify(EXPECTED_SUMMARY)
    || requestOwnership.requestCount !== 33
    || requestOwnership.uniqueUrlCount !== 13
    || requestOwnership.remoteCount !== 0
    || requestOwnership.siteRootEscapeCount !== 0
    || requestOwnership.repositoryBackReferenceCount !== 0
    || new Set(screenshotEvidence.expertScreenshot.captures.map((capture) => capture.sha256)).size !== 1
    || screenshotEvidence.expertScreenshot.captures.some((capture) => !capture.assertions.unclipped)
  ) process.exitCode = 1;
};

const collectRequestOwnership = async (browser, copiedSiteRoot, repositoryRoot, urlFor) => {
  const context = await browser.newContext({ viewport: { width: 1366, height: 768 } });
  await context.setOffline(true);
  const page = await context.newPage();
  const requests = [];
  let activePage = "";
  page.on("request", (request) => {
    const url = request.url();
    const parsed = new URL(url);
    let resolvedPath = null;
    let ownership = "remote";
    let repositoryBackReference = false;
    if (parsed.protocol === "file:") {
      resolvedPath = fileURLToPath(parsed);
      ownership = isWithin(copiedSiteRoot, resolvedPath) ? "copied-site-root" : "site-root-escape";
      repositoryBackReference = isWithin(repositoryRoot, resolvedPath);
    }
    requests.push({ page: activePage, url, protocol: parsed.protocol, resolvedPath, ownership, repositoryBackReference });
  });
  for (const surface of SURFACES) {
    activePage = surface.file;
    await page.goto(urlFor(surface.file), { waitUntil: "load" });
  }
  await context.close();
  return {
    requestCount: requests.length,
    uniqueUrlCount: new Set(requests.map((request) => request.url)).size,
    remoteCount: requests.filter((request) => request.protocol !== "file:").length,
    siteRootEscapeCount: requests.filter((request) => request.ownership !== "copied-site-root").length,
    repositoryBackReferenceCount: requests.filter((request) => request.repositoryBackReference).length,
    requests,
  };
};

const captureScreenshots = async (browser, screenshotRoot, repositoryRoot, urlFor) => {
  const representativeScreenshots = [];
  const portablePath = (target) => path.relative(repositoryRoot, target).split(path.sep).join("/");
  const capture = async (page, name, fullPage = false) => {
    const target = path.join(screenshotRoot, name);
    await page.screenshot({ path: target, fullPage });
    const evidence = { name, path: portablePath(target), sha256: await sha256File(target) };
    representativeScreenshots.push(evidence);
    return evidence;
  };
  for (const viewport of VIEWPORTS) {
    const context = await browser.newContext({ viewport, reducedMotion: "reduce" });
    await context.setOffline(true);
    const page = await context.newPage();
    await page.goto(urlFor("index.html"), { waitUntil: "load" });
    await settlePage(page, null, false);
    await capture(page, `home-${viewport.width}x${viewport.height}.png`, viewport.width === 390);
    await context.close();
  }

  const cases = [
    ["loop-engineering.html", "design-contract", "loop-1366x768.png"],
    ["platform-capabilities.html", "continuity", "platform-1366x768.png"],
    ["downloads-docs.html", null, "downloads-1366x768.png"],
    ["docs/USER_GUIDE.zh-CN.html", "path-1b", "guide-1366x768.png"],
  ];
  for (const [file, state, name] of cases) {
    const context = await browser.newContext({ viewport: { width: 1366, height: 768 }, reducedMotion: "reduce" });
    await context.setOffline(true);
    const page = await context.newPage();
    await page.goto(urlFor(file, state || ""), { waitUntil: "load" });
    await settlePage(page, state, false);
    await capture(page, name);
    await context.close();
  }
  {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: "reduce" });
    await context.setOffline(true);
    const page = await context.newPage();
    await page.goto(urlFor("docs/USER_GUIDE.zh-CN.html", "path-1b"), { waitUntil: "load" });
    await settlePage(page, "path-1b", false);
    await capture(page, "guide-390x844.png");
    await context.close();
  }

  const expertCaptures = [];
  {
    const context = await browser.newContext({ viewport: { width: 1366, height: 768 }, reducedMotion: "reduce" });
    await context.setOffline(true);
    const page = await context.newPage();
    await page.goto(urlFor("dynamic-expert-review.html", "review-design"), { waitUntil: "load" });
    for (let index = 1; index <= 2; index += 1) {
      const assertions = await settlePage(page, "review-design", true);
      const name = index === 1 ? "expert-review-1366x768.png" : "expert-review-1366x768-repeat.png";
      const target = path.join(screenshotRoot, name);
      await page.screenshot({ path: target, fullPage: false });
      expertCaptures.push({ index, path: portablePath(target), sha256: await sha256File(target), assertions });
    }
    await context.close();
  }
  const repeatPath = path.join(screenshotRoot, "expert-review-1366x768-repeat.png");
  await fs.rm(repeatPath);
  expertCaptures[1].path = expertCaptures[0].path;
  return {
    representativeScreenshots,
    expertScreenshot: {
      trackedPath: portablePath(path.join(screenshotRoot, "expert-review-1366x768.png")),
      state: "review-design",
      captures: expertCaptures,
    },
  };
};

const settlePage = async (page, state, expert) => {
  if (state) {
    await page.waitForFunction(
      (id) => document.querySelector(`[data-tab="${id}"]`)?.getAttribute("aria-selected") === "true",
      state,
    );
  }
  return page.evaluate(async ({ stateId, isExpert }) => {
    const style = document.createElement("style");
    style.dataset.acceptanceFreeze = "true";
    style.textContent = "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}";
    document.head.append(style);
    document.documentElement.style.scrollBehavior = "auto";
    if (stateId) history.replaceState(null, "", `#${stateId}`);
    const tab = stateId ? document.querySelector(`[data-tab="${stateId}"]`) : null;
    if (tab) tab.focus({ preventScroll: true });
    const tablist = tab?.closest('[role="tablist"]');
    if (tablist) tablist.scrollLeft = 0;
    window.scrollTo(0, 0);
    await document.fonts.ready;
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    if (tablist) tablist.scrollLeft = 0;
    window.scrollTo(0, 0);
    const headerNodes = [...document.querySelectorAll(".site-header a, .site-header button")];
    const tabNodes = [...document.querySelectorAll('[role="tab"]')];
    const geometry = [...headerNodes, ...tabNodes].map((node) => {
      const rect = node.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(node);
      const contentRect = range.getBoundingClientRect();
      return {
        label: node.textContent.trim().replace(/\s+/g, " "),
        left: rect.left,
        right: rect.right,
        contentLeft: contentRect.left,
        contentRight: contentRect.right,
        scrollWidth: node.scrollWidth,
        clientWidth: node.clientWidth,
        clipped: rect.left < -0.5
        || rect.right > innerWidth + 0.5
        || node.scrollWidth > node.clientWidth + 0.5
        || contentRect.left < rect.left - 0.5
        || contentRect.right > rect.right + 0.5,
      };
    });
    const overlaps = [];
    for (let index = 0; index < headerNodes.length; index += 1) {
      const first = headerNodes[index].getBoundingClientRect();
      for (let next = index + 1; next < headerNodes.length; next += 1) {
        const second = headerNodes[next].getBoundingClientRect();
        if (Math.min(first.right, second.right) - Math.max(first.left, second.left) > 0.5) {
          overlaps.push(`${geometry[index].label} <> ${geometry[next].label}`);
        }
      }
    }
    const clipped = geometry.filter((item) => item.clipped).map((item) => item.label);
    const selected = document.querySelector('[role="tab"][aria-selected="true"]');
    return {
      hash: location.hash,
      selected: selected?.dataset.tab || null,
      focused: document.activeElement?.dataset?.tab || document.activeElement?.tagName || null,
      tablistScrollLeft: tablist?.scrollLeft ?? 0,
      pageScrollX: scrollX,
      pageScrollY: scrollY,
      documentScrollWidth: document.documentElement.scrollWidth,
      documentClientWidth: document.documentElement.clientWidth,
      geometry,
      overlaps,
      clipped,
      unclipped: clipped.length === 0
        && overlaps.length === 0
        && document.documentElement.scrollWidth === document.documentElement.clientWidth,
      animationSettled: document.querySelector('[data-acceptance-freeze="true"]') !== null,
      expert: isExpert,
    };
  }, { stateId: state, isExpert: expert });
};

const options = parseArgs(process.argv.slice(2));
if (options["verify-receipt"]) await verifyReceipt(options);
else await runAcceptance(options);
