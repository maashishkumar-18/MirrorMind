# Phase 3 Step 3.2 — Chat Interface

**Status:** Step 3.2 is **COMPLETE** — one commit. Next: Step 3.3 (feature views —
Reminders / Todos / Meetings / Schedule; the `ScheduleConflict` overwrite/keep
resolution OPEN ITEM from 3.1d still stands).

Roadmap Step 3.2 = the primary chat surface on top of the fe.1–fe.7 scaffold: the
`/chat` view, `useSessionStore` (deferred from fe.4), the Tier-2 disambiguation popup
resolved via `chat.confirm_action`, the Tier-3 clarification (a normal assistant
message, no modal), and the schedule-conflict notice surfaced inline.

Settled design decisions built to:
- **Schedule conflict** — 3.2 only *surfaces* `ChatSendResult.conflict` as an inline
  block under the assistant turn. The overwrite/keep flow + backend resolution +
  `ScheduleHandler` overwrite helper are Step 3.3.
- **Non-streaming** — `chat.send` resolves with the full answer; the typing indicator
  is a plain "awaiting response" state (client timeout already 120 000 ms). No
  `chat.send.progress` event.
- **No virtualization** — the full transcript for the current mount is rendered.
- **Citations** — inline `[Session <short> · approx. <ts>]` markers with a
  hover/focus tooltip built from the `ChatCitation` payload. No
  `chat.citation_context` backend method in 3.2 (a real "jump to session context"
  click-through waits for a later step).

---

## What landed

### Backend — `src/backend/session_repository.py`

`SessionRepository.history()` now returns the **newest `_HISTORY_MAX` (200) turns**,
oldest-first (`ORDER BY turn_index DESC LIMIT 200` then `reversed`). A defensive
ceiling on the un-virtualized transcript — sessions are already bounded by
idle-close + `chat.new`, so this rarely bites. `all_messages()` (the re-ingest
input) is **untouched and uncapped**. No IPC contract / `methods.py` / `methods.ts`
change — an internal server-side constant, not a parameter. `session_worker.history()`
and `_chat_history` pass the list straight through, unchanged.

`tests/backend/test_session_repository.py` +1
(`test_history_caps_at_the_newest_history_max_turns`: seed 250, assert 200 returned
oldest-first starting at `turn_index == 50`, `all_messages` still 250). **774 → 775
py**, black / ruff / mypy clean.

### Frontend — `desktop/`

Vitest runs in the `node` environment — **no RTL** (fe.5/fe.6 settled decision).
All logic is a Zustand store + pure helpers, unit-tested directly; the `.tsx`
components are thin wrappers (untested, like `FirstRun.tsx`).

**`desktop/src/store/session.ts` — `useSessionStore`** (new; deferred from fe.4).
Client-owned, driven by `Chat.tsx`. `messages` is **append-only for the lifetime of
a `/chat` mount** — each `SessionMessage` carries its own `sessionId`, and the render
layer draws a boundary wherever two adjacent turns disagree. State:
`sessionId` / `messages` / `sending` / `historyLoading` / `hydrated` /
`pendingDisambiguation` (bound to the assistant message id via `forMessageId`) /
`pendingConflict` (a mirror of the latest turn's conflict — the per-message
`conflict` field is the render source) / `error`. Actions:
`startHistoryLoad` / `hydrate` / `failHistoryLoad`; `startSend` (optimistic pending
user turn, clears a stale popup, returns a temp id) → `completeSend` (reconciles the
user turn, appends the assistant turn with citations / tier / conflict, clears
`sending`, stashes `pendingDisambiguation` when the result carries one) /
`failSend` (marks the user turn failed, clears `sending`, sets `error`);
`removeMessage` (retry); `applyConfirmResult` (append the `chat.confirm_action`
assistant turn, clear the popup); `dismissDisambiguation` (visual-only);
`reset` (`chat.new` — swap `sessionId`, **keep** the transcript so a boundary shows
on the next turn); `clearError`.

**`desktop/src/routes/chatView.ts`** (new; pure) —
`withBoundaries(messages)` interleaves `{ kind: "boundary" }` markers (walks from
index 1, so it structurally cannot emit one before the first turn — covers `chat.new`
*and* a transparent idle auto-close, detected purely from the id);
`shortId` (trailing 6 chars of `session_<hex>`); `formatTimestamp` (locale
date+time, raw string back on a parse failure); `formatCitationLabel`
(`[Session <short> · approx. <ts>]`); `disambigLabel` (action-type → button copy,
raw value fallback).

**`desktop/src/ui/DisambiguationPopup.tsx`** (new; presentational) — a card over the
transcript (not a full-screen modal): one button per backend option via
`disambigLabel`, a × and a backdrop that both dismiss, `role="dialog"`
`aria-modal="true"`, first option focused on mount, `Esc` → dismiss, buttons
disabled while the `chat.confirm_action` round-trip is in flight.

**`desktop/src/routes/Chat.tsx`** (rewrite from the placeholder) —
- **Mount**: `startHistoryLoad()` → `call("chat.history", {})` → `hydrate` /
  `failHistoryLoad`. `.chat-log-loading` while loading (no empty-content flash), an
  empty-state line for a hydrated 0-message session, a retry link on load failure.
- **Unmount cleanup**: `dismissDisambiguation()` — a Tier-2 popup never survives
  leaving `/chat` (visual-only; the backend `_pending_action` self-discards on the
  next `chat.send` / `chat.new`).
- **Transcript**: `withBoundaries(messages).map(...)` — right-aligned user bubbles,
  left-aligned assistant bubbles, a `New conversation` `role="separator"` at each
  boundary, per-message timestamp, a "Retry" button on a `failed` user turn, an
  inline `.chat-conflict` block under an assistant turn that reported a schedule
  overlap ("⚠️ That overlaps with **{title}** ({start}–{end}). Open the Schedule view
  to overwrite or keep it." — no action wired). Tier 3 needs no special case (the
  backend returns it as a normal `answer`).
- **Typing indicator**: while `sending`, a left-aligned `.chat-typing` bubble with an
  animated ellipsis, `role="status"` `aria-live="polite"`; fine for ~120 s.
- **Composer**: full-width `<textarea aria-label="Message">` + a
  `<button aria-label="Send message">`. `Enter` submits, `Shift+Enter` inserts a
  newline. Disabled while `sending` or `phase` is `degraded` / `exited`. Optimistic
  send: `startSend` → `call("chat.send")` → `completeSend` / `failSend`.
- **Auto-scroll**: a bottom sentinel `ref` + `scrollIntoView` keyed on
  `messages.length` / `sending`, suppressed for the one-shot `hydrate()` via a
  `didHydrate` ref (honours `prefers-reduced-motion`).
- **"New conversation"**: `call("chat.new", {})` → `reset(session_id)`; disabled while
  `sending`.
- **Disambiguation**: `<DisambiguationPopup>` when `pendingDisambiguation` is set.
  A choice → `call("chat.confirm_action", { pending_action_id, choice })` →
  `applyConfirmResult`; a `no_pending_action` error → close + "That prompt expired".
  Dismiss (× / backdrop / `Esc`) → close immediately, then
  `chat.confirm_action(..., choice: "conversation")` to append the Tier-4 reply.
- **Degraded/exited**: a `.chat-offline` line + disabled composer (the fe.5 banner
  carries the primary messaging).

**`desktop/src/styles.css`** — a `--- chat interface (3.2) ---` block:
`.chat-view` / `.chat-header` / `.chat-log` (`flex:1; min-height:0` so it scrolls,
not the page) / `.chat-msg[data-role]` / `.chat-typing` / `.chat-boundary` /
`.chat-citation` + `.chat-citation-tip` (CSS popover) / `.chat-conflict` /
`.chat-composer` / `.chat-offline` / `.disambig-*`, plus a
`prefers-reduced-motion` rule for the typing ellipsis.

**No `ipc/` change** — the contract is untouched; the 79-test `ipc` suite and the
existing `chat.send` tier/conflict fixtures already cover the shapes the store
consumes. **No Rust change.**

New tests: `desktop/src/store/session.test.ts` (10) +
`desktop/src/routes/chatView.test.ts` (9). **71 → 90 vitest (desktop).**

### Verification

`desktop`: `npm typecheck` / `lint` / `test` (90) / `build` green.
`ipc`: 79. Python: `black` / `ruff` / `mypy src observability db` clean;
`pytest` **775 passed, 1 skipped**. Rust: unchanged (7).

E2e via `tauri dev` (model `llama3.1:8b` active, driven over a WebView2
`--remote-debugging-port=9222` CDP seam; `Page.captureScreenshot`):
- lands on `/chat`; empty session hydrated ("Start a conversation…").
- type + `Enter` → optimistic user bubble (with timestamp) → `.chat-typing` →
  assistant reply appended, `sending` cleared.
- `Shift+Enter` keeps the draft and does **not** send.
- "New conversation" → `chat.new` (backend returns a new `session_id`) → the next
  turn renders below a single "New conversation" separator.
- window close → backend exits code 0, zero orphan `python.exe`.
- **Not exercised live** (this box has only ~4 GB free RAM, so `llama3.1:8b` can't
  actually load and every agent call returns the safe `CONVERSATION` fallback):
  the Tier-2 popup, the Tier-3 clarification copy, grounded citations, and the
  inline conflict block — all covered by the store / helper unit tests and the
  `ipc` fixtures.
