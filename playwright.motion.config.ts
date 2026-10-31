import base from "./playwright.config";
import { defineConfig } from "@playwright/test";
export default defineConfig({
  ...base,
  workers: 2,
  webServer: [
    {
      command:
        "VITE_DEMO_MODE=true node node_modules/vite/bin/vite.js --config tests/vite.demo.config.ts",
      url: "http://127.0.0.1:5177",
      reuseExistingServer: true,
    },
    {
      command:
        "VITE_DEMO_MODE=false node node_modules/vite/bin/vite.js --config tests/vite.real.config.ts",
      url: "http://127.0.0.1:5175",
      reuseExistingServer: true,
    },
  ],
  outputDir: "/tmp/dormprint-motion-playwright",
  reporter: [
    ["list"],
    ["json", { outputFile: "output/motion-qa/e2e-results.json" }],
  ],
  use: {
    ...base.use,
    channel: "chrome",
    baseURL: process.env.MOTION_BASE_URL || "http://127.0.0.1:5177",
  },
});
