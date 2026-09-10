import { test, expect } from "../support/harness";

/**
 * Roadmap Step 4.1 flow: meeting-note capture.
 *
 * Meetings view → paste a synthetic transcript → Capture → the note appears
 * with the extracted decisions and action items.
 */
test.use({ activeModel: "llama3.1:8b", fakeLlmFixture: "meeting", stubIngest: true });

test("capture a transcript and see the extracted decisions + action items", async ({
  page,
  backend,
}) => {
  backend.start();
  await page.goto("/meetings");
  await backend.waitReady();
  await backend.warmup();

  await page
    .getByRole("textbox", { name: "Meeting transcript" })
    .fill(
      "Priya: launch is moving to Friday. Sam: I'll own the checklist. Action: Priya sends the updated launch checklist by Wednesday.",
    );
  await page.getByRole("button", { name: "Capture" }).click();

  const detail = page.locator(".meeting-detail");
  await expect(detail).toBeVisible({ timeout: 30_000 });
  await expect(detail).toContainText("Launch moves to Friday");
  await expect(detail).toContainText("Send the updated launch checklist");
});
