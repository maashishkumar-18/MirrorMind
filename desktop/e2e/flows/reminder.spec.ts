import { test, expect } from "../support/harness";

/**
 * Roadmap Step 4.1 flow: reminder, end to end.
 *
 * Type it in chat → Tier-2 disambiguation → confirm → reminder created (+ toast
 * registered) → appears in the Reminders view → mock the clock past its time →
 * relaunch → it shows as overdue → complete → it leaves the active list.
 */
test.use({ activeModel: "llama3.1:8b", fakeLlmFixture: "reminder", stubIngest: true });

test("natural-language reminder → disambiguate → confirm → overdue → complete", async ({
  page,
  backend,
}) => {
  backend.start();
  await page.goto("/chat");
  await backend.waitReady();
  await backend.warmup();

  await page.getByRole("textbox", { name: "Message" }).fill("Remind me to call the dentist");
  await page.getByRole("button", { name: "Send message" }).click();

  const popup = page.getByRole("dialog", { name: "Confirm what you meant" });
  await expect(popup).toBeVisible({ timeout: 30_000 });
  await popup.getByRole("button", { name: "Set a reminder" }).click();
  await expect(popup).toBeHidden({ timeout: 60_000 });

  // The toast was registered on create.
  await expect
    .poll(() => backend.toastCalls().filter((c) => c.op === "register").length, { timeout: 15_000 })
    .toBeGreaterThan(0);

  await page.goto("/reminders");
  await expect(page.locator(".feature-row-title", { hasText: "Call the dentist" })).toBeVisible({
    timeout: 15_000,
  });

  // Mock the clock: push the reminder into the past, relaunch → reconciliation
  // surfaces it as overdue.
  await backend.stopChild();
  await backend.seed({
    sql: ["UPDATE reminders SET scheduled_time = '2020-01-02T09:00:00+00:00'"],
  });
  backend.start();
  await page.reload();
  await backend.waitReady();

  const overdueRow = page.locator("li[data-overdue] .feature-row-title", {
    hasText: "Call the dentist",
  });
  await expect(overdueRow).toBeVisible({ timeout: 20_000 });

  // The checkbox is controlled + always unchecked; onChange fires the complete call.
  await page.getByRole("checkbox", { name: "Complete Call the dentist" }).click();
  await expect(page.locator(".feature-row-title", { hasText: "Call the dentist" })).toBeHidden({
    timeout: 15_000,
  });
});
