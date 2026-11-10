import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "tests/e2e",
  testMatch: [
    "delivery-campus-admin.spec.ts",
    "slot-date-filter.spec.ts",
    "native-month.spec.ts",
    "delivery-proof-details.spec.ts",
    "service-prices.spec.ts",
  ],
  use: {
    baseURL: "http://localhost:5173",
    reducedMotion: "reduce",
    locale: "zh-CN",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: true,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], channel: "chrome" },
    },
    { name: "webkit", use: { ...devices["iPhone 13"], browserName: "webkit" } },
  ],
});
