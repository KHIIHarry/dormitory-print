import { defineConfig, devices } from "@playwright/test";

const realUrl = "http://127.0.0.1:5241";
const demoUrl = "http://127.0.0.1:5243";

export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "./output/config-draft-qa/results",
  use: { channel: "chrome", trace: "off" },
  webServer: [
    {
      command: "npx vite --host 127.0.0.1 --port 5241 --strictPort",
      env: { VITE_DEMO_MODE: "false" },
      url: realUrl,
      reuseExistingServer: false,
    },
    {
      command: "npx vite --host 127.0.0.1 --port 5243 --strictPort",
      env: { VITE_DEMO_MODE: "true" },
      url: demoUrl,
      reuseExistingServer: false,
    },
  ],
  projects: [
    {
      name: "smtp-desktop",
      testMatch: "mail-draft.spec.ts",
      use: { ...devices["Desktop Chrome"], baseURL: realUrl },
    },
    {
      name: "smtp-mobile",
      testMatch: "mail-draft.spec.ts",
      use: {
        ...devices["iPhone 13"],
        defaultBrowserType: "chromium",
        baseURL: realUrl,
      },
    },
    {
      name: "preview-desktop",
      testMatch: "settings-preview.spec.ts",
      use: { ...devices["Desktop Chrome"], baseURL: demoUrl },
    },
    {
      name: "preview-mobile",
      testMatch: "settings-preview.spec.ts",
      use: {
        ...devices["iPhone 13"],
        defaultBrowserType: "chromium",
        baseURL: demoUrl,
      },
    },
  ],
});
