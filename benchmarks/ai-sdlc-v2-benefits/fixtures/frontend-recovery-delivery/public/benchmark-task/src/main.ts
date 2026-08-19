import { createApp } from "vue";
import PrimeVue from "primevue/config";

import ReleaseRiskWorkbench from "./pages/ReleaseRiskWorkbench.vue";
import { aiSdlcTheme, themeOptions } from "./theme";

createApp(ReleaseRiskWorkbench)
  .use(PrimeVue, { theme: { preset: aiSdlcTheme, options: themeOptions } })
  .mount("#app");
