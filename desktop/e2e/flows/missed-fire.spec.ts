import { test, expect } from "../support/harness";

/**
 * Roadmap Step 4.1 flow: missed-fire reconciliation.
 *
 * Seed a reminder with a past `scheduled_time` and `fired_at IS NULL`, launch
 * the app, and verify it surfaces as "overdue" in the Reminders view
 * (`ReminderHandler.reconcile_on_launch` → `app.reminders_pending`).
 */
test.use({ activeModel: "llama3.1:8b" });

test("a past-due unfired reminder shows as overdue on launch", async ({ page, backend }) => {
  await backend.seed({
    reminders: [
      {
        id: "r-missed",
        title: "Water the plants",
        scheduled_time: "2020-01-02T09:00:00+00:00",
        notes: "",
        fired_at: null,
      },
    ],
  });

  backend.start();
  await page.goto("/reminders");
  await backend.waitReady();

  const overdueRow = page.locator("li[data-overdue] .feature-row-title", {
    hasText: "Water the plants",
  });
  await expect(overdueRow).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("heading", { name: "Overdue" })).toBeVisible();
});
