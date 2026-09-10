import { defineConfig } from "@playwright/test";

/**
 * Phase 4 Step 4.1 end-to-end flows. Each spec drives the built React frontend
 * (served by `vite preview`) against a real Python backend with the LLM stubbed
 * — see `e2e/support/harness.ts`. The Tauri Rust shell is NOT in the loop
 * (fe.7 + Step 4.4 cover the supervisor).
 */
export default defineConfig({
  testDir: "./e2e/flows",
  fullyParallel: false,
  workers: 1, // each test spawns a Python sidecar — keep memory bounded
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 150_000,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://localhost:4173",
    headless: true,
    trace: "on-first-retry",
  },
  webServer: {
    command: "npm run build && npm run preview -- --port 4173 --strictPort",
    url: "http://localhost:4173",
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
