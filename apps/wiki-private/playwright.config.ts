import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests/e2e",
  fullyParallel: false,
  projects: [{ name: "chromium-private", use: { browserName: "chromium" } }],
  workers: 1,
  timeout: 30_000,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5184/wiki/",
    browserName: "chromium",
    launchOptions: process.env.WIKI_BROWSER_EXECUTABLE
      ? { executablePath: process.env.WIKI_BROWSER_EXECUTABLE }
      : {},
    viewport: { width: 1586, height: 992 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:5184/wiki/",
    reuseExistingServer: !process.env.GITHUB_ACTIONS,
  },
});
