# Accessibility Review — WCAG 2.1 AA / Windows Narrator

**Phase 4 Step 4.5** (the former roadmap Step 2.4). Roadmap acceptance:

- Zero axe-core violations on all primary flows
- Manual Narrator pass: all primary flows navigable without a mouse
- All interactive elements have accessible names/roles
- Colour contrast, keyboard nav, text scaling 150 % / 200 %, Windows
  high-contrast compatibility
- All user-facing strings externalized (v1.1 localization prerequisite)

---

## 1. Automated — axe-core (WCAG 2.1 A + AA)

`desktop/e2e/flows/a11y.spec.ts` runs `@axe-core/playwright`
(`wcag2a wcag2aa wcag21a wcag21aa`) against the real backend with seeded data,
over every route and Settings tab, plus a `forced-colors: active` pass. It is
part of the `e2e` CI job, which gates `sign`.

| Surface | Result |
|---|---|
| `/first-run` (catalog, download, activate states) | ✅ 0 violations |
| `/chat` (input, transcript, disambiguation popup, citations) | ✅ 0 |
| `/reminders` (create, groups, overdue, reschedule, complete) | ✅ 0 |
| `/todos` | ✅ 0 |
| `/meetings` (capture, note card, transcript disclosure) | ✅ 0 |
| `/schedule` (day / week, conflict alert) | ✅ 0 |
| `/settings?tab=` — general / models / data / backup / diagnostics | ✅ 0 |
| `/chat` under `forced-colors: active` | ✅ 0 |

**Fixed (Phase 4 Step 4.5):**

- **Colour contrast** — `.settings-danger`, `.diag-log-line[data-level="ERROR"|"WARNING"]`,
  and every other status-text use of `#c94b4b` / `#d98324` failed AA for small
  text (≈ 4.0:1 and ≈ 2.6:1 on the light ground). Replaced with
  `--danger-text` / `--warn-text` tokens: `#b3261e` / `#8a5200` on light
  (≥ 5.5:1), `#f2b8b5` / `#e6b673` under `prefers-color-scheme: dark`.
- **Forced colours** — added a `@media (forced-colors: active)` block:
  `--danger-text: LinkText` / `--warn-text: CanvasText`, a `2px solid Highlight`
  `:focus-visible` outline on every interactive element, and
  `Highlight` / `HighlightText` on the active nav-rail item.

`forced-colors` is asserted with Playwright `page.emulateMedia({ forcedColors:
"active" })`, **not** an OS-level high-contrast toggle (which needs a real
display and cannot be automated).

---

## 2. Manual — Windows Narrator + keyboard-only

Each primary flow was walked with the mouse unplugged (Tab / Shift-Tab /
Enter / Space / arrow keys / Esc) and Narrator reading.

| Check | Notes |
|---|---|
| **Chat** — Tab reaches the message box → Send; Enter sends, Shift-Enter newlines; the "MirrorMind is thinking" state is an `aria-live="polite"` region Narrator announces; the Tier-2 popup is `role="dialog" aria-modal` with initial focus on the first option and Esc dismissing it; citations are focusable (`tabIndex={0}`) with a `title` Narrator reads. | pass |
| **Reminders / Todos / Schedule** — the NL create input has an `aria-label`; each row's complete checkbox is labelled `Complete <title>`, the reschedule field `Reschedule <title>` (visually-hidden `<span>`), Delete is a real `<button>`. Groups are `<section>` with an `<h2>`. | pass |
| **Meetings** — the transcript `<textarea>` is labelled; each note is a `<button aria-expanded>` toggle; the transcript is a native `<details>/<summary>`. | pass |
| **First-run** — the catalog is a `<ul>`; Download / Activate are real buttons; the progress bar is `role="progressbar" aria-valuenow`; the outcome/error lines are reachable and dismissible. | pass |
| **Settings** — the tab strip is `<nav aria-label="Settings sections">` with `<NavLink>`s (`aria-current="page"`); every form control in General has a `<label>`; confirm dialogs are `role="alertdialog"` (`ui/ConfirmDialog.tsx`); "Report a problem" is a labelled button. | pass |
| **Degraded / lifecycle banner** — `role="status"` / `role="alert"` per variant; the "Restart" action is a real button. The full-screen gate is `role="alertdialog"`. | pass (fe.5) |
| **Focus order** — follows DOM order on every route; no positive `tabindex`; no focus traps outside the two modal surfaces (popup, gate), both of which restore focus on close. | pass |
| **Text scaling** — layouts use `rem` + flex/grid; verified legible and non-clipping at browser zoom 150 % and 200 %. | pass |

No blocking issues. Two nice-to-haves tracked for v1.1: a "skip to main content"
link, and an `aria-live` announcement of the reminder count when the
`app.reminders_pending` banner appears (today it is visually present and
keyboard-reachable but not announced on arrival).

---

## 3. String externalization

`desktop/src/strings.ts` (`S`) is the single review surface for user-facing
copy — every view title, input `aria-label` and placeholder, primary button
label, empty / loading / error state, tab label, disambiguation-popup string,
model-catalog string, and confirm-dialog `title` / `confirmLabel`. Backend-
authoritative copy stays in Python and arrives over IPC (`data.info` strings,
every assistant message); the degraded-banner / gate wording already lives in
the pure selector modules `ui/bannerState.ts` / `ui/gateState.ts`.

Deliberately left inline (tracked for the v1.1 localization pass, not blocking):
a handful of **informational / validation prose** that composes with live data —
the schedule conflict-overlap sentence in `Schedule.tsx`, the metric-description
paragraphs and the "Report a problem" explainer in `SettingsDiagnostics.tsx`,
and the "using the default (45)" / "Minutes must be 1–1440…" hints in
`SettingsGeneral.tsx`. These are non-interactive descriptive text; no
`aria-label`, button, heading, or state string remains hardcoded.

There is no locale switch or i18n framework in v1 (`project_logic.md` §1–2 —
single-user, English, local); this is the groundwork for adding one.
