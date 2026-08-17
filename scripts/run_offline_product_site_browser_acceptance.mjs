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

const EXPECTED_SUMMARY = Object.freeze({
  stateCount: 135,
  stateFailures: 0,
  copyCount: 240,
  copyFailures: 0,
  noJsGroupCount: 12,
  noJsFailures: 0,
  configuredVideoFailures: 0,
  accessibilityFailures: 0,
  runtimeFailures: 0,
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

  check([1, 2].includes(receipt.schemaVersion), "schemaVersion must be 1 or 2");
  check(receipt.inputs?.manifestSha256 === await sha256File(manifestPath), "manifest SHA drifted");
  check(
    receipt.inputs?.copiedManifestSha256 === receipt.inputs?.manifestSha256,
    "fresh-copy manifest is not bound to the committed manifest",
  );
  if (receipt.schemaVersion >= 2) {
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
  if (receipt.schemaVersion >= 2) {
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

  const stateFailures = receipt.stateResults?.filter((item) => item.failures.length).length ?? -1;
  const copyCount = receipt.copyResults?.reduce((total, item) => total + item.results.length, 0) ?? -1;
  const copyFailures = receipt.copyResults?.reduce(
    (total, item) => total + item.results.filter((result) => !result.passed).length,
    0,
  ) ?? -1;
  const noJsFailures = receipt.noJsResults?.filter((item) => item.failures.length).length ?? -1;
  const runtimeFailures = receipt.runtimeResults?.reduce(
    (total, item) => total + item.consoleErrors.length + item.pageErrors.length + item.failedRequests.length,
    0,
  ) ?? -1;
  const derivedSummary = {
    stateCount: receipt.stateResults?.length ?? -1,
    stateFailures,
    copyCount,
    copyFailures,
    noJsGroupCount: receipt.noJsResults?.length ?? -1,
    noJsFailures,
    configuredVideoFailures: receipt.configuredVideo?.failures?.length ?? -1,
    accessibilityFailures: receipt.accessibilityResults?.reduce(
      (total, item) => total + item.failures.length,
      0,
    ) ?? -1,
    runtimeFailures,
  };
  check(JSON.stringify(receipt.summary) === JSON.stringify(EXPECTED_SUMMARY), "summary contract drifted");
  check(JSON.stringify(derivedSummary) === JSON.stringify(EXPECTED_SUMMARY), "summary is not reproducible");

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
        const observed = await page.evaluate(({ key, state }) => {
          const visible = (node) => {
            if (!node) return false;
            const style = getComputedStyle(node);
            const rect = node.getBoundingClientRect();
            return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
          };
          const selected = document.querySelector('[role="tab"][aria-selected="true"]');
          const panel = selected ? document.getElementById(selected.getAttribute("aria-controls")) : null;
          const controls = [...document.querySelectorAll('a[href], button, [role="tab"], video[controls]')].filter(visible);
          const clippedControls = controls.flatMap((node) => {
            const rect = node.getBoundingClientRect();
            let scrollContainer = false;
            for (let parent = node.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
              const style = getComputedStyle(parent);
              if (["auto", "scroll"].includes(style.overflowX) && parent.scrollWidth > parent.clientWidth) scrollContainer = true;
            }
            return !scrollContainer && (rect.left < -0.5 || rect.right > innerWidth + 0.5)
              ? [node.getAttribute("aria-label") || node.textContent.trim().replace(/\s+/g, " ").slice(0, 60)]
              : [];
          });
          const undersizedControls = innerWidth <= 390
            ? controls.flatMap((node) => {
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
          if (clippedControls.length) failures.push("clipped-controls");
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
            undersizedControls,
            externalLinkCount: externalLinks.length,
            externalFailures,
            failures,
          };
        }, { key: surface.key, state });
        stateResults.push({ viewport, ...observed });
      }
    }

    await page.goto(urlFor("index.html"), { waitUntil: "load" });
    await page.keyboard.press("Tab");
    await page.waitForTimeout(250);
    const accessibility = await page.evaluate(() => {
      const active = document.activeElement;
      const failures = [];
      if (!active?.classList.contains("skip-link") || active.getBoundingClientRect().top < 0) failures.push("skip-link-focus");
      if (document.querySelectorAll("main").length !== 1) failures.push("main-count");
      if (!document.querySelector("nav")?.getAttribute("aria-label")) failures.push("nav-label");
      if (getComputedStyle(document.documentElement).scrollBehavior !== "auto") failures.push("reduced-motion");
      return { activeClass: active?.className || "", failures };
    });
    accessibilityResults.push({ viewport, ...accessibility });
    runtimeResults.push({ viewport, consoleErrors, pageErrors, failedRequests });
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

  const summary = {
    stateCount: stateResults.length,
    stateFailures: stateResults.filter((item) => item.failures.length).length,
    copyCount: copyResults.reduce((total, item) => total + item.results.length, 0),
    copyFailures: copyResults.reduce((total, item) => total + item.results.filter((result) => !result.passed).length, 0),
    noJsGroupCount: noJsResults.length,
    noJsFailures: noJsResults.filter((item) => item.failures.length).length,
    configuredVideoFailures: configuredVideo.failures.length,
    accessibilityFailures: accessibilityResults.reduce((total, item) => total + item.failures.length, 0),
    runtimeFailures: runtimeResults.reduce((total, item) => total + item.consoleErrors.length + item.pageErrors.length + item.failedRequests.length, 0),
  };
  const receipt = {
    schemaVersion: 2,
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
    stateResults,
    accessibilityResults,
    copyResults,
    noJsResults,
    configuredVideo,
    runtimeResults,
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
