import Aura from "@primeuix/themes/aura";
import { definePreset } from "@primeuix/themes";

export const aiSdlcTheme = definePreset(Aura, {
  semantic: {
    primary: { 500: "#1770e6" },
    colorScheme: {
      light: {
        surface: { 0: "#ffffff", 50: "#f7f9fc", 900: "#172033" },
        highlight: { background: "#e8f2ff", color: "#124ba3" },
      },
    },
  },
});

export const themeOptions = { darkModeSelector: false };
