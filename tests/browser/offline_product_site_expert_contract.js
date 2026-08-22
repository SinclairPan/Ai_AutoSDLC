async (page) => {
  const expect = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  const tabIds = [
    "review-requirement",
    "review-design",
    "review-implementation",
    "review-frontend",
    "review-pr",
  ];
  const graphOrder = [
    "risk",
    "capability",
    "expert-routing",
    "isolation",
    "findings",
    "writer-fix",
    "rereview",
    "outcomes",
  ];
  const state = async () =>
    page.evaluate(() => ({
      hash: location.hash,
      selected: document.querySelector('[role="tab"][aria-selected="true"]')?.id,
      visiblePanels: [...document.querySelectorAll('[role="tabpanel"]')]
        .filter((panel) => getComputedStyle(panel).display !== "none")
        .map((panel) => panel.id),
    }));
  const assertSelected = async (tabId) => {
    const current = await state();
    expect(current.selected === tabId, `expected ${tabId}, got ${current.selected}`);
    expect(current.hash === `#${tabId}`, `hash did not follow ${tabId}`);
    expect(
      JSON.stringify(current.visiblePanels) === JSON.stringify([`${tabId}-panel`]),
      `${tabId} did not own the only visible panel`,
    );
  };

  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto(page.url().split("#", 1)[0]);
  const defaultState = await state();
  expect(defaultState.selected === tabIds[0], "default tab is not Requirement");
  const desktopTopology = await page.evaluate(() => {
    const center = (name) => {
      const rect = document
        .querySelector(`[data-graph-node="${name}"]`)
        .getBoundingClientRect();
      return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
    };
    return {
      routing: center("expert-routing"),
      isolation: center("isolation"),
      findings: center("findings"),
      writer: center("writer-fix"),
      rereview: center("rereview"),
    };
  });
  expect(
    Math.abs(desktopTopology.routing.x - desktopTopology.isolation.x) < 2,
    "Panel does not flow vertically into Isolation",
  );
  expect(
    Math.abs(desktopTopology.isolation.y - desktopTopology.findings.y) < 2,
    "Isolation does not flow horizontally to Findings",
  );
  expect(
    Math.abs(desktopTopology.findings.x - desktopTopology.writer.x) < 2,
    "Findings does not flow vertically to Original Writer",
  );
  expect(
    Math.abs(desktopTopology.writer.x - desktopTopology.rereview.x) < 2,
    "Original Writer does not flow vertically to Re-review",
  );
  for (const tabId of tabIds) {
    await page.locator(`#${tabId}`).click();
    await assertSelected(tabId);
  }

  await page.locator("#review-implementation").click();
  await page.locator("#review-implementation").focus();
  await page.keyboard.press("ArrowRight");
  await assertSelected("review-frontend");
  await page.keyboard.press("Home");
  await assertSelected("review-requirement");
  await page.keyboard.press("End");
  await assertSelected("review-pr");
  await page.keyboard.press("ArrowLeft");
  await assertSelected("review-frontend");

  await page.locator("#review-requirement").click();
  await page.locator("#review-design").click();
  await page.goBack();
  await assertSelected("review-requirement");
  await page.goForward();
  await assertSelected("review-design");
  await page.reload();
  await assertSelected("review-design");

  const responsive = {};
  for (const [width, height] of [
    [1024, 768],
    [390, 844],
  ]) {
    await page.setViewportSize({ width, height });
    responsive[`${width}x${height}`] = await page.evaluate((expectedOrder) => {
      const graph = document.querySelector("[data-expert-graph]");
      return {
        clientWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
        graphDisplay: getComputedStyle(graph).display,
        graphDirection: getComputedStyle(graph).flexDirection,
        graphOrder: [...graph.children].map((node) => node.dataset.graphNode),
        branches: [...document.querySelectorAll("[data-expert-branch]")].map(
          (node) => `${node.dataset.expertBranch}:${node.dataset.route}`,
        ),
        outcomes: [...document.querySelectorAll("[data-review-outcome]")].map(
          (node) => `${node.dataset.reviewOutcome}:${node.dataset.route}`,
        ),
        expectedOrder,
      };
    }, graphOrder);
    const observed = responsive[`${width}x${height}`];
    expect(observed.scrollWidth === observed.clientWidth, `${width}px overflows`);
    expect(observed.graphDisplay === "flex", `${width}px graph is not vertical`);
    expect(observed.graphDirection === "column", `${width}px graph order is not column`);
    expect(
      JSON.stringify(observed.graphOrder) === JSON.stringify(graphOrder),
      `${width}px graph source order drifted`,
    );
    expect(
      JSON.stringify(observed.branches) ===
        JSON.stringify(["primary:required", "cross-risk:conditional"]),
      `${width}px branch semantics drifted`,
    );
    expect(
      JSON.stringify(observed.outcomes) ===
        JSON.stringify(["close:conditions-met", "needs-review:expert-failure"]),
      `${width}px exit semantics drifted`,
    );
  }

  await page.context().route("**/*.js", async (route) => {
    await route.fulfill({
      body: "",
      contentType: "application/javascript",
      status: 200,
    });
  });
  await page.reload();
  const noJs = await page.evaluate(() => ({
    hasJsClass: document.documentElement.classList.contains("js"),
    panelCount: document.querySelectorAll('[role="tabpanel"]').length,
    visiblePanels: [...document.querySelectorAll('[role="tabpanel"]')]
      .filter((panel) => getComputedStyle(panel).display !== "none")
      .map((panel) => panel.id),
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(!noJs.hasJsClass, "no-JS document retained the js class");
  expect(noJs.panelCount === 5, "no-JS document lost a risk panel");
  expect(noJs.visiblePanels.length === 5, "no-JS document hid a risk panel");
  expect(noJs.scrollWidth === noJs.clientWidth, "no-JS 390px document overflows");
  await page.context().unroute("**/*.js");
  await page.reload();

  return {
    clicks: tabIds.length,
    desktopTopology,
    keyboard: ["ArrowRight", "Home", "End", "ArrowLeft"],
    history: ["Back", "Forward", "Reload"],
    responsive,
    noJs,
  };
}
