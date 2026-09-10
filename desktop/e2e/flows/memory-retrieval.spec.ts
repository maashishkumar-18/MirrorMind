import { test, expect } from "../support/harness";

/**
 * Roadmap Step 4.1 flow: memory retrieval.
 *
 * A synthetic session is ingested (first chat turn → re-ingest embeds it), then
 * a question is asked that should retrieve from it. The response must cite the
 * session with its ID and timestamp.
 */
// stubEmbed + stubRerank: the dense/cross-encoder models never load (30-60s cold
// on CI). The SEMANTIC route still runs the real vector store + BM25 + router +
// context builder + generation; with a single seeded chunk the fake unit-vector
// provider is enough to surface it. Ranking quality lives in the eval + unit suites.
test.use({
  activeModel: "llama3.1:8b",
  fakeLlmFixture: "retrieval",
  stubEmbed: true,
  stubRerank: true,
});

test("ask about a past conversation → grounded answer with a session citation", async ({
  page,
  backend,
}) => {
  // This is the one flow that runs the real ingestion + retrieval pipeline
  // (chunk → tokenize → store → semantic route → context build → generate);
  // give a cold, 2-core CI runner generous headroom.
  test.setTimeout(240_000);
  backend.start();
  await page.goto("/chat");
  await backend.waitReady();
  await backend.warmup();

  const message = page.getByRole("textbox", { name: "Message" });
  const send = page.getByRole("button", { name: "Send message" });

  // Turn 1: give the companion something to remember. Re-ingest embeds it.
  await message.fill(
    "On the Q4 planning call we agreed to ship on Friday and Priya owns the checklist.",
  );
  await send.click();
  await expect(page.locator(".chat-msg[data-role='assistant']")).toBeVisible({ timeout: 90_000 });

  // The re-ingest of turn 1 is an async worker followup; a second chat.history
  // round trip only returns once that followup (metadata + embed) has drained,
  // so the chunk is queryable before turn 2 retrieves.
  await backend.warmup();

  // Turn 2: ask about it.
  await message.fill("What did we agree on the Q4 call?");
  await send.click();

  const citation = page.locator(".chat-citation").last();
  await expect(citation).toBeVisible({ timeout: 90_000 });
  await expect(citation).toContainText("Session");
  await expect(citation).toContainText("approx.");
  // the tooltip/title carries the full session id
  await expect(citation).toHaveAttribute("title", /session_[0-9a-f]+/);
});
