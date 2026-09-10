import { test, expect } from "../support/harness";

/**
 * Roadmap Step 4.1 flow: memory retrieval.
 *
 * A synthetic session is ingested (first chat turn → re-ingest embeds it), then
 * a question is asked that should retrieve from it. The response must cite the
 * session with its ID and timestamp.
 */
test.use({ activeModel: "llama3.1:8b", fakeLlmFixture: "retrieval" });

test("ask about a past conversation → grounded answer with a session citation", async ({
  page,
  backend,
}) => {
  backend.start();
  await page.goto("/chat");
  await backend.waitReady();

  const message = page.getByRole("textbox", { name: "Message" });
  const send = page.getByRole("button", { name: "Send message" });

  // Turn 1: give the companion something to remember. Re-ingest embeds it.
  await message.fill(
    "On the Q4 planning call we agreed to ship on Friday and Priya owns the checklist.",
  );
  await send.click();
  await expect(page.locator(".chat-msg[data-role='assistant']")).toBeVisible({ timeout: 30_000 });

  // Turn 2: ask about it.
  await message.fill("What did we agree on the Q4 call?");
  await send.click();

  const citation = page.locator(".chat-citation").last();
  await expect(citation).toBeVisible({ timeout: 30_000 });
  await expect(citation).toContainText("Session");
  await expect(citation).toContainText("approx.");
  // the tooltip/title carries the full session id
  await expect(citation).toHaveAttribute("title", /session_[0-9a-f]+/);
});
