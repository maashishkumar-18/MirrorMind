# Phase 3 Step 3.3 — Feature Views (Reminders, Todos, Meetings, Schedule)

**Status:** Step 3.3 is **COMPLETE** — 5 commits (`f6474c5` 3.3a, `ad3d9ad` 3.3b,
`5de2234` 3.3c, `53dd07d` 3.3d, `93d560c` 3.3e). Next: Step 3.4 (Settings &
Diagnostics — includes the Phase-2-deferred Backup & Recovery panel, export
badge, full-wipe confirm).

The 3.1d **OPEN ITEM** (`ScheduleConflict` surfaced but overwrite/keep resolution
unwired, no overwrite helper on `ScheduleHandler`) is **now closed** — see 3.3a
(`overwrite_ids`) + 3.3e (the inline alert).

Settled decisions built to (Q1/Q2/Q3 + this session's AskUserQuestion):
- **Q1** — feature-CRUD IPC lands as a preliminary backend sub-step 3.3a,
  schema-first, all `worker=True`. Inline NLP create in every view = `chat.send`
  (not a form), **except Meetings** which uses a dedicated `meetings.capture`.

  > **Deliberate deviation from the Q1 pre-planning answer** (confirmed via
  > AskUserQuestion during planning). The Q1 note said "inline NLP create in
  > every view goes through `chat.send`". Meetings capture does **not** — it
  > calls `meetings.capture` (→ `MeetingNoteHandler.capture_meeting_note`)
  > directly. Rationale: a dedicated "Capture" button on a paste-a-transcript
  > text area is a decision the user has *already made explicit* — routing a
  > multi-hundred-line transcript through the four-tier agentic classifier is
  > unreliable, would only avoid a conversational reply at Tier-1
  > `meeting_note`, and the spec requires "no conversational response during
  > capture". The other three views keep `chat.send` (the utterance genuinely
  > is natural language that must be classified). So there are two create paths
  > by design: `chat.send` for reminders/todos/schedule, `meetings.capture` for
  > meetings.
- **Q2** — schedule conflict resolution = `schedule.create_item` with
  `overwrite_ids: list[str] = []`. Non-empty → each id (must be a *current*
  conflict) is soft-deleted, then the item is created, one transaction. Keep =
  dismiss, nothing created. Resolution lives **only** in the Schedule view; no
  chat-side resolution, no `_pending_action` coupling.
- **Q3** — no `reminders.acknowledge_reconciliation`. `reminders.complete/
  dismiss/reschedule` (and `delete`) prune the id from `useReminderStore`
  locally **and** the worker prunes its cached `self._reconciliation`.
- Nav shell = persistent left sidebar. Inline-create Tier-2/3 fallback handled
  inline in the feature view (answer text + option buttons → `chat.confirm_action`).

---

## 3.3a — feature-view CRUD IPC surface (`f6474c5`)

**19 new methods**, all `worker=True` + `degraded_ok=False` (the session DB
connection is thread-affine on `SessionWorker`, same reason `chat.*` /
`health.check` are):

| Namespace | Methods |
|---|---|
| reminders | `list` `complete` `dismiss` `reschedule` `update` `delete` |
| todos | `list` `complete` `update` `delete` |
| meetings | `list` `get` `capture` `delete` |
| schedule | `day` `week` `create_item` `update` `delete` |

- **`src/common/ipc/methods.py`** — `TodoWire` / `ActionItemWire` /
  `MeetingNoteWire` / `ScheduleItemWire` / `ScheduleConflictWire` + params/
  results; `ReminderWire` (from 3.1c) reused verbatim for `reminders.list` /
  the lifecycle results. `ScheduleItemResult = { item | null, conflict | null }`.
- **`src/backend/feature_wire.py`** (new) — entity → wire-dict mappers, sibling
  of `reminders_wire.py`.
- **`src/backend/session_worker.py`** — thin pass-throughs on the worker thread
  (`list_reminders` / `complete_reminder` / … / `create_schedule_item` /
  `schedule_week` / …). Each builds the handler on `self._conn` and returns the
  dataclass(es). `ReminderHandler` is built with `NoOpToastBridge()` — real
  WinRT toast registration is a later step, so create/reschedule via these
  paths do **not** yet register OS toasts (same as `action_dispatch` today).
  `complete/dismiss/reschedule/delete` also call `_prune_reconciliation(id)`
  (Q3). `schedule_week` = one `get_range_schedule` query, grouped into 7 keys.
- **`src/backend/handlers.py`** — 19 thin adapters. `ValueError` →
  `invalid_params`, `KeyError` → `not_found` (via `_feature_call`).
- **`src/features/schedule_handler.py`** — `create_schedule_item` gains
  `overwrite_ids` (Q2): a stray id (not in the current conflict set) →
  `ValueError`; partial coverage → `ScheduleConflict` with only the unresolved
  items; full coverage → soft-delete those rows + insert, one `with self._conn:`
  block (soft-delete only, project_logic §5). New `get_range_schedule(start,
  end)` — one JOIN query backing `schedule.week`. `update_schedule_item` is
  **unchanged** — still returns the conflict, no overwrite path (documented
  limitation; the roadmap conflict-alert acceptance is about *new* items).
- **`ipc/schema/methods.ts` + `ipc/fixtures/methods_examples.json`** — zod
  mirror `.strict()` + one params/result fixture per method (incl. a
  `schedule.create_item` `overwrite_ids` params + a conflict result). The
  cross-language round-trip (`tests/common/test_ipc_methods_roundtrip.py`) and
  `ipc/schema/methods.test.ts` coverage check gate it.

`todos.list` is capped server-side at the newest 500 non-deleted rows (an
internal constant, like `history()`'s 200 — no param).

New/changed tests: `tests/backend/test_ipc_methods_contract.py` (worker set),
`tests/backend/test_handlers.py` (+5, fake worker), `tests/backend/
test_session_worker.py` (+7, real DB + injected worker — pass-throughs +
`_prune_reconciliation` on a seeded overdue reminder + `overwrite_ids` +
`schedule_week` bad-date), `tests/features/test_schedule_handler.py` (+4).
**775 → 866 py** (+91: +16 real, +75 parametrized across the two contract
tests). `black` / `ruff` / `mypy src observability db` clean. **No Rust change.**

---

## 3.3b — nav shell + Reminders view (`ad3d9ad`)

- **`desktop/src/ui/NavRail.tsx`** (new) — persistent left `<nav>`, `NavLink`
  per destination (Chat / Reminders / To-dos / Meetings / Schedule),
  active-link highlight via `aria-current`. Emoji glyphs (no SVG assets).
  Returns `null` on `/` and `/first-run` and while `phase` is `starting` /
  `exited`.
- **`RootLayout.tsx`** — `<NavRail/>` + `<main>` wrapped in a
  `<div class="root-body">` flex row below `<Banner>`.
- **`App.tsx`** — `/reminders` `/todos` `/meetings` `/schedule` routes inside
  the existing `<RequireModel>` wrapper. Todos/Meetings/Schedule shipped as
  "Coming soon" placeholders (filled in by 3.3c/d/e).
- **`desktop/src/routes/featureCreate.ts`** (new, pure) —
  `interpretCreateResult(ChatSendResult)` → `created` | `disambiguation` |
  `conflict` | `message`. The shared inline-NLP-create interpretation for all
  four views. `actionLabel(option)`.
- **`desktop/src/store/reminders.ts`** — extends `useReminderStore` with the
  authoritative `active` list (from `reminders.list`) + `patchReminder`
  (drops a now-completed/dismissed row) / `removeReminder` /
  `pruneReconciliation`. `overdue` / `pendingAcknowledgment` (from the
  `app.reminders_pending` startup event) are **id-set overlays only** — never
  separate rows.
- **`desktop/src/routes/remindersView.ts`** (new, pure) —
  `groupReminders(active, overdueIds, now)` → `{ overdue, today, upcoming }`
  (each reminder once; overdue if its id ∈ `overdueIds` **or**
  `scheduled_time < now`); `datetimeLocalToIso` / `isoToDatetimeLocal` /
  `formatWhen`.
- **`desktop/src/routes/Reminders.tsx`** — grouped list, the Overdue group
  always rendered with a distinct `.reminder-overdue` style (never hidden),
  complete checkbox / delete / reschedule (`datetime-local`), inline NLP create
  (`chat.send` → `interpretCreateResult`) with the Tier-2 option buttons wired
  to `chat.confirm_action`.
- **`desktop/src/ipc/client.ts`** — per-method timeouts for the 19 new methods
  (`meetings.capture` 120 s — one local-LLM pass; the rest 10 s).
- **`styles.css`** — `--- feature views + nav rail (3.3) ---` block.

New tests: `featureCreate.test.ts` (6), `remindersView.test.ts` (6),
`store/reminders.test.ts` (+5). **90 → 107 vitest.**

---

## 3.3c — To-dos view (`5de2234`)

`store/todos.ts` (`useTodoStore` — all non-deleted rows), `routes/todosView.ts`
(`splitTodos` → `{ active, completed }`; active by priority then age, completed
newest-first; `priorityRank` / `priorityLabel`), `routes/Todos.tsx` (priority +
category chips, complete checkbox, inline title edit + priority `<select>` →
`todos.update`, soft-delete, a local "Completed" tab; shared `featureCreate`
inline create). `styles.css` `.todo-tabs` / `.todo-priority[data-level]` /
`.todo-category`. **+9 vitest → 116.**

---

## 3.3d — Meetings view (`53dd07d`)

`store/meetings.ts` (`useMeetingStore` — list + capture lifecycle; a captured
note is prepended), `routes/meetingsView.ts` (`summarizeMeeting` collapsed-row
preview, `actionItemLine`, `formatMeetingDate`), `routes/Meetings.tsx`
(expandable rows — attendees / topics / decisions / action items / follow-ups +
a collapsible transcript, "Review needed" badge when `needs_review`, inline
capture via the dedicated **`meetings.capture`** method — Q1, no `chat.send`, no
conversational response — delete). `styles.css` `.meeting-capture` /
`.needs-review-badge` / `.meeting-detail`. **+9 vitest → 125.**

---

## 3.3e — Schedule view + conflict resolution (`93d560c`)

`store/schedule.ts` (`useScheduleStore` — day = 1 group / week = 7, anchor date,
patch/remove), `routes/scheduleView.ts` (`weekStart` / `addDays` — **UTC**
date-key math so a non-UTC runner/viewer never rolls a day; `layoutDay` →
ordered slots + an overlap flag; `overwriteParams(conflict)` →
`schedule.create_item` params with `overwrite_ids = every conflicting id`),
`routes/Schedule.tsx`:
- Day (default) / Week toggle, prev / next / Today nav, a vertical timeline
  (time gutter + slots; overlapping items flagged red).
- NL edit `<input>` → `chat.send` → shared `featureCreate` helper.
- An **inline** (non-modal) conflict alert with **Overwrite** / **Keep
  existing**, seeded on mount from `useSessionStore.getState().pendingConflict`
  (a conflict raised in the chat view lands here — the only resolution surface)
  **and** from this view's own `chat.send` result. Overwrite →
  `schedule.create_item` with `overwriteParams(conflict)` → refresh + clear both
  the local state and the session-store mirror. Keep → clear both, nothing
  created.
- Per-item delete (`schedule.delete`).

`styles.css` `.schedule-toggle` / `.schedule-nav` / `.schedule-conflict` /
`.schedule-timeline` / `.schedule-slot[data-overlaps]`. **+8 vitest → 133.**

---

## Verification

Per commit: `desktop` `npm typecheck` / `lint` / `test` / `build` green;
`ipc` 136 vitest; Python (3.3a) `black` / `ruff` / `mypy` + `pytest` **866
passed, 1 skipped**. No Rust change.

**E2e** via `tauri dev` (WebView2 `--remote-debugging-port=9222`, CDP driver;
`active_model = llama3.1:8b` in the dev `app_config.json` so the app is past
`<RequireModel>`). The dev `session.db` was seeded with 3 reminders / 3 todos /
2 schedule items / 1 meeting note via the handlers (key from Credential
Manager):
- nav rail renders 5 links; click-navigation switches views; rail hidden on
  `/first-run`.
- **Reminders** — Overdue / Today / Upcoming groups; completing the overdue
  reminder drops it (3 → 2 rows) and the Overdue group disappears (local prune
  + list refresh).
- **To-dos** — Active (2) / Completed (1) tabs; priority + category shown;
  deleting an active todo (2 → 1).
- **Meetings** — the captured note renders; expanding shows the detail
  sections.
- **Schedule** — Day/Week toggle (week = 7 day columns); timeline with the
  seeded items; the NL "apply" field degrades gracefully (this box can't load
  `llama3.1:8b`, so `chat.send` returns the safe `CONVERSATION` fallback and
  nothing is created — no crash).
- **No console errors / warnings** across all five views.

**Not exercised live** (needs a Tier-1 agentic classification, which needs a
loaded model): inline NLP create actually creating an entity, the Tier-2
option-button path, and the Schedule conflict Overwrite/Keep flow. Covered by
`featureCreate.test.ts` + `scheduleView.test.ts` + `tests/features/
test_schedule_handler.py` (`overwrite_ids`) + `tests/backend/
test_session_worker.py` + the `ipc` `schedule.create_item` conflict fixture.
