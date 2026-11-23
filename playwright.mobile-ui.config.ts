import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "tests/e2e",
  testMatch: [
    "admin-notifications.spec.ts",
    "preview-pagination.spec.ts",
    "guest-order-access.spec.ts",
    "guest-order-access-admin.spec.ts",
    "delivery-campus-admin.spec.ts",
    "delivery-campus-customer.spec.ts",
    "slot-date-filter.spec.ts",
    "native-month.spec.ts",
    "operation-feedback.spec.ts",
    "delivery-proof-details.spec.ts",
    "service-prices.spec.ts",
    "print-task-flip.spec.ts",
    "payment-variance.spec.ts",
    "v1-courier.spec.ts",
    "v1-customer.spec.ts",
    "upload-picker.spec.ts",
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
