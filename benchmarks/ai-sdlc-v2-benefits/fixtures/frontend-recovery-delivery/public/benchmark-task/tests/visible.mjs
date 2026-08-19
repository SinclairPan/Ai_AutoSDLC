import { createRiskController } from "../src/release-state.mjs";

const controller = createRiskController(
  async () => {
    throw new Error("API_UNAVAILABLE");
  },
  async () => undefined,
);
await controller.load();
if (controller.state.error !== "加载失败" || typeof controller.retry !== "function") {
  process.stderr.write("VISIBLE_RED: recoverable failure state is absent\n");
  process.exit(1);
}
process.stdout.write("VISIBLE_GREEN: recoverable failure state is present\n");
