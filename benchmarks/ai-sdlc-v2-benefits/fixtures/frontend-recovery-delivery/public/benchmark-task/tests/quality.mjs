import { readFile } from "node:fs/promises";

const manifest = JSON.parse(await readFile("program-manifest.json", "utf8"));
const environment = JSON.parse(await readFile("environment-lock.json", "utf8"));
const page = await readFile("src/pages/ReleaseRiskWorkbench.vue", "utf8");
const theme = await readFile("src/theme.ts", "utf8");
for (const arm of ["A00", "A10", "A11"]) {
  if (manifest.arm_confirmation_state[arm] !== "pending") throw new Error(`${arm} confirmation must start pending`);
}
if (!environment.node.binary_sha256 || !environment.browser.binary_sha256) throw new Error("runtime identity is not frozen");
if (!page.includes("发布风险工作台")) throw new Error("page identity is missing");
for (const token of ["primary", "surface", "highlight", "#1770e6", "darkModeSelector"]) {
  if (!theme.includes(token)) throw new Error(`theme token is missing: ${token}`);
}
process.stdout.write("QUALITY_OK: public project and solution target are valid\n");
