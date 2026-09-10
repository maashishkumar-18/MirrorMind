import { test, expect } from "../support/harness";

/**
 * Roadmap Step 4.1 flow: first-launch model download.
 *
 * No active model → `/first-run` → pick a model → mock download (completes
 * instantly via RAGPIPE_FAKE_MODELS) → activate → chat becomes reachable.
 */
test.use({ fakeModels: true });

test("first-run: download a model, activate it, land on chat", async ({ page, backend }) => {
  backend.start();
  await page.goto("/");
  await backend.waitReady();
  await expect(page).toHaveURL(/\/first-run$/);

  const firstRow = page.locator("li.model-row").first();
  await expect(firstRow).toContainText("Llama 3.1 8B");

  await firstRow.getByRole("button", { name: "Download" }).click();
  await expect(page.locator(".first-run-outcome")).toContainText("Downloaded", { timeout: 15_000 });

  await firstRow.getByRole("button", { name: "Activate" }).click();

  await expect(page).toHaveURL(/\/chat$/, { timeout: 15_000 });
  await expect(page.getByRole("textbox", { name: "Message" })).toBeVisible();
});
