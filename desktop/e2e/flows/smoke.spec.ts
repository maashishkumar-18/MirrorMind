import { test, expect } from "../support/harness";

/**
 * Harness smoke (Step 4.1a): proves the whole bridge works end to end —
 * `vite preview` build + injected Tauri shim + real spawned Python backend +
 * SSE event stream + zod IPC client. No LLM fixture needed (the first-run
 * screen does not call the model).
 */
test("boots against the real backend and lands on first-run with no model", async ({
  page,
  backend,
}) => {
  backend.start();
  await page.goto("/");
  await backend.waitReady();

  // Loading (`/`) redirects to `/first-run` because no model is active.
  await expect(page).toHaveURL(/\/first-run$/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});
