import { readFile } from "node:fs/promises";

const manifest = JSON.parse(await readFile("program-manifest.json", "utf8"));
const environment = JSON.parse(await readFile("environment-lock.json", "utf8"));
const page = await readFile("src/pages/ReleaseRiskWorkbench.vue", "utf8");
const theme = await readFile("src/theme.ts", "utf8");
if ("applies_to_arms" in manifest || "arm_confirmation_state" in manifest)
  throw new Error("public target must be treatment-neutral");
if (!environment.node.binary_sha256 || !environment.browser.binary_sha256)
  throw new Error("runtime identity is not frozen");
if (!environment.preinstalled_dependency_tree_sha256)
  throw new Error("dependency tree is not frozen");
if (!page.includes("发布风险工作台"))
  throw new Error("page identity is missing");
for (const token of [
  "primary",
  "surface",
  "highlight",
  "#1770e6",
  "darkModeSelector",
]) {
  if (!theme.includes(token))
    throw new Error(`theme token is missing: ${token}`);
}
process.stdout.write(
  "QUALITY_OK: public project and solution target are valid\n",
);
