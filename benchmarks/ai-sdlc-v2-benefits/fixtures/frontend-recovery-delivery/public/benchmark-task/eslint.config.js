import js from "@eslint/js";
import pluginVue from "eslint-plugin-vue";

export default [
  { ignores: ["dist/**"] },
  js.configs.recommended,
  ...pluginVue.configs["flat/essential"],
  {
    files: ["tests/*.mjs"],
    languageOptions: { globals: { process: "readonly" } },
  },
];
