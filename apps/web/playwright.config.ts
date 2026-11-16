import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  globalSetup: "./tests/setup.ts",
  fullyParallel: false,
  use: {
    baseURL: "http://127.0.0.1:3000",
    headless: true,
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 1000 },
  },
  // These scenarios create global standard and subsidy policy versions; run it after readers of the shared demo catalog.
  projects: [
    {
      name: "core",
      testIgnore:
        /(rating-(fields|standard-drafts|indicator-status)|subsidy-policies)\.spec\.ts/,
    },
    {
      name: "configured-standards",
      testMatch:
        /(rating-(fields|standard-drafts|indicator-status)|subsidy-policies)\.spec\.ts/,
      dependencies: ["core"],
      workers: 1,
    },
  ],
  reporter: "list",
});
