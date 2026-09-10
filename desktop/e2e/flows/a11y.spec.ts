import AxeBuilder from "@axe-core/playwright";

import { test, expect } from "../support/harness";

/**
 * WCAG 2.1 AA automated pass (Phase 4 Step 4.5).
 *
 * axe-core over every route + interactive surface, against the real backend
 * with seeded data. The manual Narrator / keyboard walk is
 * docs/accessibility_review.md.
 */
test.use({ activeModel: "llama3.1:8b", fakeLlmFixture: "reminder" });

async function scan(page: import("@playwright/test").Page, context?: string) {
  const builder = new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]);
  const results = await builder.analyze();
  const violations = results.violations.map((v) => ({
    id: v.id,
    impact: v.impact,
    help: v.help,
    nodes: v.nodes.map((n) => n.target).flat(),
  }));
  expect(violations, `${context ?? page.url()}\n${JSON.stringify(violations, null, 2)}`).toEqual([]);
}

test("first-run has no axe violations", async ({ page, backend }) => {
  backend.start();
  await page.goto("/first-run");
  await backend.waitReady();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await scan(page, "/first-run");
});

const GATED = ["/chat", "/reminders", "/todos", "/meetings", "/schedule"] as const;

for (const route of GATED) {
  test(`${route} has no axe violations`, async ({ page, backend }) => {
    await backend.seed({
      reminders: [
        {
          id: "r-a11y",
          title: "Water the plants",
          scheduled_time: "2027-03-01T09:00:00+00:00",
          notes: "every few days",
          fired_at: null,
        },
      ],
    });
    backend.start();
    await page.goto(route);
    await backend.waitReady();
    await page.waitForLoadState("networkidle");
    await scan(page, route);
  });
}

const SETTINGS_TABS = ["general", "models", "data", "backup", "diagnostics"] as const;

for (const tab of SETTINGS_TABS) {
  test(`settings/${tab} has no axe violations`, async ({ page, backend }) => {
    backend.start();
    await page.goto(`/settings?tab=${tab}`);
    await backend.waitReady();
    await page.waitForLoadState("networkidle");
    await scan(page, `settings/${tab}`);
  });
}

test("forced-colors (Windows high-contrast) — first-run + chat stay usable", async ({
  page,
  backend,
}) => {
  await page.emulateMedia({ forcedColors: "active", colorScheme: "dark" });
  backend.start();
  await page.goto("/chat");
  await backend.waitReady();
  await scan(page, "/chat (forced-colors)");
});
