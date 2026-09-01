# Personal AI Companion — Project Logic

**Version:** 1.1
**Status:** Final — read before the Production Roadmap
**Audience:** Engineering team

**Changelog:**
- v1.0 — Initial draft from Decision Lock
- v1.1 — Incorporates: Decision Lock 4-issue amendment (`memories` table dropped, `schedule_items` added to export, `session_chunks` exclusion rationale stated, encryption unrecoverability risk elevated with proactive export prompting, structured-table retrieval mechanism defined with FTS5 and three routing paths, single-instance enforcement and uninstall data-retention policy added); roadmap 7-item review (refusal-rate gate corrected to two-sided band with explicit floor for hallucination regression, second-reviewer gate added for golden eval set, model download resumability defined with three-state guarantee, phase numbering collision resolved — Phase 1.5 → Phase 2, subsequent phases shifted); final grep verification pass confirming zero stray references

---

## 1. What the System Is

Personal AI Companion is a single-user, fully local Windows desktop application that functions as a persistent, intelligent memory layer for one person's daily life. It captures conversations, reminders, meeting notes, schedules, and todos through natural language — no mode-switching, no forms, no manual categorization. Everything the user tells it is remembered and retrievable. It runs entirely on the user's machine. Nothing leaves the device at runtime.

The system is not a search engine over files the user uploads. It is not a chatbot that forgets each conversation. It is not a task manager the user manually curates. It is a reasoning layer that sits between the user's natural-language input and a structured, searchable, locally-persisted record of their life — and it handles the translation automatically.

---

## 2. Who Uses It and How

There is one user. That user types into a chat interface. They do not select modes, choose features from menus, or decide whether their input is a "reminder" or a "conversation." They just type. The system decides what to do with it.

**Example — reminder capture:**
> "Remind me to send the invoice to Priya on Thursday at 3pm."

The system creates a reminder, confirms it, and fires a Windows notification at Thursday 3pm — whether the app is open or not.

**Example — memory retrieval:**
> "What did Sarah and I agree to in last week's Q4 meeting?"

The system searches meeting notes and conversation history, merges the results, and generates a grounded answer with a session-timestamp reference.

**Example — conversation:**
> "I'm feeling overwhelmed, I have too much going on."

The system responds conversationally. It may offer to show the user's todo list or today's schedule. It does not force a task-management flow.

The same input box handles all of this. The intelligence is in the system, not in the user's choice of interface.

---

## 3. The Four-Tier Agentic Reasoning Layer

Every user message passes through the agentic reasoning layer before anything else happens. This is a **single Ollama LLM call** that returns a structured JSON object containing:

| Field | Type | Description |
|---|---|---|
| `action_type` | enum | What the user intends: `conversation`, `reminder`, `todo`, `meeting_note`, `schedule`, `summary_request`, `retrieval_query`, or `none` |
| `confidence` | float (0.0–1.0) | How certain the model is about the classification |
| `retrieve_needed` | bool | Whether memory retrieval should run before generating a response |
| `retrieval_route` | enum | Which path to query: `semantic`, `structured`, or `hybrid` |
| `search_query` | str (optional) | The normalized query string for retrieval |
| `response` | str | The natural-language response to the user, generated in the same call |

### Tier Behavior

| Tier | Confidence | Behavior |
|---|---|---|
| 1 | ≥ 0.85 | Auto-execute. Action taken immediately. User sees the result. No confirmation prompt. |
| 2 | 0.70–0.85 | Disambiguation popup. User taps one option to confirm. |
| 3 | 0.50–0.70 | Clarification prompt with syntax examples. Conversation continues until confidence rises or falls. |
| 4 | < 0.50 | Treated as conversation. No action taken. Zero friction. |

All tier resolution — routing, dispatching, and response generation — happens within one local LLM inference call plus under 150ms of surrounding compute (DB reads for context, routing logic, DB writes for the result). No sequential model calls. No separate classifier.

---

## 4. End-to-End Message Flow

```
User types a message
        │
        ▼
IPC Bridge (zod-validated JSON envelope, version-checked)
        │
        ▼
Python Backend receives message
        │
        ▼
Conversation history loaded from DB (last N turns of current session)
        │
        ▼
Agentic Reasoning Layer
  → Single Ollama call: structured JSON output
    (action_type, confidence, retrieve_needed,
     retrieval_route, search_query, response)
        │
        ├─── retrieve_needed = false
        │         │
        │         ▼
        │    Response returned directly to frontend
        │
        └─── retrieve_needed = true
                  │
                  ▼
          Retrieval Router (first-class, tested component)
                  │
           ┌──────┴────────────┐
        semantic          structured          hybrid
           │                   │                │
    session_chunks        FTS5 query      both paths
    hybrid search        on structured   executed and
    (vector + BM25       tables (FTS5    merged before
     + local rerank)     index, no ext)  generation
           │                   │                │
           └──────┬────────────┘                │
                  ▼                              │
          Retrieved context ←──────────────────┘
                  │
                  ▼
        Generation Pipeline
        (session-framed prompt template,
         conversation_history injected as first-class input,
         retrieved context injected)
                  │
                  ▼
        Response + session/timestamp citations
                  │
                  ▼
        Post-processing
        (answer cleaning, grounding validation,
         citation formatting)
                  │
                  ▼
IPC Bridge → Frontend renders response
                  │
                  ▼
Session persistence
  → Message written to `messages` table
  → If session > 20 messages: sliding window
    sub-chunks generated and embedded
  → Primary session chunk updated
  → Structured entity (if action_type matched)
    written to its dedicated table
    (reminders / todos / meeting_notes / schedules)
  → If reminder: ScheduledToastNotification
    registered with Windows
  → Metrics written to MetricsStore
```

---

## 5. How Data Is Organized

All data lives in a **single SQLite file**, encrypted at rest with SQLCipher. The encryption key is randomly generated on first launch and stored in Windows Credential Manager (DPAPI-protected). The file uses WAL mode. A named mutex (`Global\PersonalAICompanion_v1_SingleInstance`) prevents more than one app instance from opening it.

### Tables (10 total)

| Table | Purpose |
|---|---|
| `sessions` | Conversation windows. Bounded by idle timeout (~30–60 min, configurable) or explicit user action. |
| `messages` | Individual turns within a session. Role (user/assistant), content, timestamp. |
| `session_chunks` | Vector store. One primary chunk per session (full session text). Overlapping sub-chunks for sessions > 20 messages (window=10, stride=5). 384-dim float32 embeddings from bundled `all-MiniLM-L6-v2`. |
| `reminders` | Reminder entities with scheduled_time, fired_at, completed_at. FTS5 indexed. |
| `todos` | Todo entities with priority, category, completed_at. FTS5 indexed. |
| `meeting_notes` | Meeting captures with attendees, topics, decisions, action_items, follow_ups. FTS5 indexed. |
| `schedules` | Schedule containers (e.g., "Monday plan"). |
| `schedule_items` | Time-slotted items within a schedule. Conflict-validated before commit. FTS5 indexed. |
| `summaries` | Daily and weekly summaries. Idempotent, regenerable on demand. |
| `sync_metadata` | Present-but-inert in v1. Reserved for v2 cloud sync schema compatibility. |

**Universal constraints:** Every table has `id`, `created_at`, `updated_at`, `deleted_at`. Soft-delete only — no hard deletes anywhere in v1. No row is ever physically removed.

### Vector Search Mechanism

Embeddings are loaded from `session_chunks` into a numpy array in memory at startup. Query-time search is brute-force cosine similarity against this in-memory array. At personal-use scale (thousands of sessions over years), this stays comfortably under the 200ms search target on the reference hardware. No native SQLite extension (sqlite-vec, hnswlib) is required in v1.

### Structured Table Search Mechanism

`meeting_notes`, `todos`, `reminders`, and `schedule_items` each have a SQLite FTS5 virtual table index over their text-content columns. FTS5 is a SQLite built-in — no extension required. Queries apply `WHERE deleted_at IS NULL` before FTS ranking. This is keyword/phrase match, not semantic search.

**Known limitation:** FTS5 will miss paraphrased queries where the user's phrasing does not share keywords with stored content (e.g., "what did we decide about the budget" may not match a `decisions` field containing "Q4 spending plan"). This is a documented, measured limitation. The golden eval set includes a "structured-data paraphrase recall" category specifically to track it. Improvement path (semantic search over structured table embeddings, or LLM query expansion before FTS) is a v1.1 candidate pending eval results.

---

## 6. How the RAG Pipeline Works

The RAG pipeline is not a standalone service. It is a set of modules within the Python backend that execute as part of the message-processing flow.

### Ingestion

Ingestion happens in two contexts:

**After every message is stored:** the session's embedding representation is updated.
- Sessions ≤ 20 messages: primary chunk only (full session text embedded as one unit).
- Sessions > 20 messages: primary chunk updated + overlapping sub-chunks generated or extended. Window size = 10 messages, stride = 5 messages. Both values are externalized config, not hardcoded constants.

**When a meeting note is captured:** the transcript is separately chunked and embedded if long enough to warrant sub-chunking.

The embedding model is `all-MiniLM-L6-v2` (384-dim), bundled directly in the MSIX installer. No first-run download required. Token counting uses `AutoTokenizer` from `sentence-transformers` — not tiktoken. Chunk-size bounds are derived from this model's 384-token max sequence length and stored in `config/ingestion/chunker.yaml`.

### Retrieval

Retrieval is triggered only when the agentic layer sets `retrieve_needed = true`. The Retrieval Router reads `retrieval_route` from the agentic output and dispatches to one of three paths:

**Semantic path:** hybrid search — cosine similarity over the in-memory numpy embedding array + BM25 sparse keyword search, results merged via reciprocal rank fusion, then reranked by the local CrossEncoder (`cross-encoder/ms-marco-MiniLM-L-6-v2`).

**Structured path:** FTS5 query against one or more structured tables, filtered by `deleted_at IS NULL`. Returns results as `SessionRetrievedChunk` instances with `chunk_type = "structured_record"`.

**Hybrid path:** both paths execute. Results are merged before being passed to generation.

The Retrieval Router is a named, tested component — not an implicit side effect of prompt design.

### Generation

Generation receives: the retrieved context (if any), the full conversation history for the current session (injected as a first-class prompt input), and the user's message. Prompt templates are session/memory-framed — no document, page, slide, or course language anywhere in any template. Citations reference session ID and approximate timestamp only.

The generation response is the `response` field of the same structured JSON output produced by the agentic reasoning LLM call — one call, not two sequential calls.

---

## 7. How the Frontend and Backend Communicate

The React/TypeScript frontend never talks directly to any database or model. It sends and receives **versioned JSON envelopes** over the Tauri IPC bridge.

- Every message type has an explicit `version` field.
- All messages are validated by zod schemas (TypeScript) and Pydantic models (Python).
- Version mismatch fails loudly: the user sees a "please restart the app" notice, not silent misbehavior.
- All IPC calls have explicit timeouts. A timeout produces a "temporarily unavailable" UI state — never an infinite spinner.

The **Tauri Rust shell** is responsible for:
- Launching the Python backend sidecar at startup
- Monitoring the backend and restarting on crash (exponential backoff, max 3 attempts)
- Surfacing the degraded-mode banner if restart fails
- Enforcing single-instance via the named mutex
- Registering and cancelling Windows Toast Notifications (WinRT API, called from Rust)

The **Python backend** is responsible for:
- All business logic
- All database access
- All model inference coordination (via Ollama HTTP API)
- All IPC message handling and routing

The **frontend** is responsible for:
- Rendering all views
- User interaction
- IPC message construction and schema validation

---

## 8. How Models Are Managed

The app ships with **zero generative models**. The bundled Ollama binary (pinned version, sidecar) is started by the Tauri shell alongside the Python backend.

**On first launch:** the app detects that no model is active and forces the user through the model selection flow — choose a model → download with visible progress → verify integrity → activate. No feature requiring inference is available until this completes.

**The bundled `all-MiniLM-L6-v2` embedding model** (~90MB, included in the MSIX) requires no download. It is available immediately on first launch. Retrieval and ingestion work from the first session.

**Model switching:** users can switch the active generative model from Settings → Models at any time without restarting the app. The `OllamaAdapter` handles switching via Ollama's HTTP API. A "model not downloaded" or "model not loaded" error produces a specific, user-readable message — never a generic failure.

**The model catalog** (curated list of recommended models with name, size, description, minimum RAM) is bundled as a JSON file in the app. It is not fetched from the network.

---

## 9. How Reminders Work End-to-End

1. Agentic layer classifies input as `reminder` with confidence ≥ 0.85.
2. Reminder written atomically to the `reminders` table (single transaction — no partial writes possible).
3. A `ScheduledToastNotification` is registered with Windows via the Tauri Rust shell at the reminder's `scheduled_time`. This registration persists independently of whether the app is running.
4. At `scheduled_time`, Windows fires the Toast notification regardless of app state.
5. If the app is open: the backend receives the notification callback, sets `fired_at = NOW()`, presents completion UI.
6. If the app is closed: `fired_at` remains NULL until the next launch.
7. On every launch, **reconciliation runs** — two independent conditions checked:
   - `scheduled_time < NOW() AND fired_at IS NULL` → reminder **never fired** (OS dropped it, Focus Assist swallowed it, system was rebooting). Surfaced as **"overdue."**
   - `fired_at IS NOT NULL AND completed_at IS NULL` → reminder **fired but unacknowledged** (app was closed when the user tapped the Toast). Surfaced as **"pending acknowledgment."**
8. A reminder leaves either state only through explicit user action: complete, dismiss, or reschedule.

---

## 10. How the System Handles Failure

| Failure | Behavior |
|---|---|
| Python backend crash | Tauri restarts with exponential backoff (1s, 2s, 4s). After 3 failed attempts, degraded-mode banner shown with "Restart app" action. UI never hangs silently. |
| Database corruption | On-launch `PRAGMA integrity_check`. Corruption detected → offer restore from most recent backup snapshot before failing to start. |
| Model inference failure | `OllamaAdapter` classifies errors: `model_not_downloaded`, `model_not_loaded`, `ollama_not_running`. Each produces a specific user-visible message. Never a generic error. |
| Missed reminder | Caught by on-launch reconciliation (`scheduled_time < NOW() AND fired_at IS NULL`). Surfaced as "overdue." Never silently dropped. |
| Unacknowledged reminder | Caught by on-launch reconciliation (`fired_at IS NOT NULL AND completed_at IS NULL`). Surfaced as "pending acknowledgment." |
| Missed summary generation | If app was closed at the scheduled time, summary is generated on next launch and stamped with the original scheduled timestamp, not the late generation time. |
| Disk full during model download | Caught. Specific message: "Not enough disk space — free X GB and try again." |
| Network loss during model download | Caught. Resume from last successfully pulled layer if Ollama supports it; otherwise restart cleanly with a clear message. Never an ambiguous partial state. |
| Second app instance launched | Named mutex detected. First instance brought to foreground. Second instance exits immediately without opening the database. |

---

## 11. How Packaging and Distribution Work

The Python backend is frozen by PyInstaller into a self-contained executable — no system Python required on the user's machine. The Ollama binary is pinned and bundled as a second Tauri sidecar. The `all-MiniLM-L6-v2` model weights are bundled inside the MSIX package. The Tauri bundler packages the Rust shell, the compiled React frontend (static assets), and both sidecars into a signed `.msix` file.

**MSIX capabilities declared:**
- Internet access (client) — for Ollama model downloads and Store update checks only
- `ToastNotifications` — for reminder triggering

No microphone, camera, location, or contacts access is declared. No other network capabilities exist.

Automatic updates are managed by the Microsoft Store (standard MSIX update flow). On update:
1. Schema migrations run automatically on first post-update launch.
2. A pre-migration database snapshot is taken automatically before any migration runs.
3. Migrations are forward-only and idempotent — running twice produces the same result as running once.

---

## 12. Encryption and Data Recovery

**At rest:** the SQLite database is encrypted with SQLCipher. The encryption key is randomly generated on first launch and stored in Windows Credential Manager via DPAPI. The key is machine+user-account-bound.

**Key loss:** if a user reinstalls Windows, the DPAPI key is gone and the encrypted database is unrecoverable. The app displays a specific, non-alarming message: *"Your previous data is protected by your Windows account and cannot be recovered after a Windows reinstall. Starting fresh."* It does not crash.

**Mitigation — proactive export prompting:**
- Settings → Data & Privacy always shows: "Last exported: [date]" or "Never — your data cannot be recovered if Windows is reinstalled without an export."
- If `last_exported_at` is null or > 30 days ago, a non-blocking badge appears on the Settings nav item.
- Permanent static text in Settings → Data & Privacy: *"Uninstalling this app will permanently delete your encrypted database. Export your data first."*
- The data export (JSON, user-chosen location) is available at any time from Settings.
- Export copy is plaintext JSON. Re-import is a v2 feature. The Settings copy states: *"Export your data — saves a backup copy of everything for safekeeping. Note: re-importing into the app is not yet supported; this export preserves your data for a future update."*

**What the export contains:** `sessions`, `messages`, `reminders`, `todos`, `meeting_notes`, `schedules`, `schedule_items`, `summaries`. `session_chunks` (embeddings) are excluded — they are fully derivable from `messages` and will be regenerated automatically on re-import in v2. Including raw float32 embedding BLOBs in a user-facing JSON export would produce unreadable output with no recovery value.

**Automatic local backup:** a rolling daily snapshot (last 7 retained) is stored in a separate directory from the live database. User-accessible restore is available from Settings → Backup & Recovery. A pre-migration snapshot is taken automatically before every schema migration.

**On uninstall:** MSIX uninstall removes the app package and its sandbox directory, including the encrypted database and all backup snapshots. Uninstall is a permanent, irrecoverable data deletion event unless the user has previously exported. This is stated explicitly in the Store listing and in Settings.

---

## 13. Session Lifecycle

A session is a bounded conversation window. It closes automatically after a configurable idle timeout (default 30–60 minutes of no messages). Users can also close a session explicitly via the "New conversation" button.

Structured captures — reminders, todos, meeting notes, schedule items — are **atomic standalone extractions**. They are never folded into the conversation session. A reminder created during a session is stored in the `reminders` table, not in `messages`. The session's conversation record does not contain the reminder data — only the conversational turn that triggered the extraction.

Sessions ≤ 20 messages receive a single primary chunk embedding. Sessions > 20 messages also receive overlapping sub-chunks. The 20-message threshold, window size (10), and stride (5) are all externalized in `config/ingestion/chunker.yaml` — not hardcoded.

---

*This is how the system works. The Production Roadmap specifies how it will be built, in what order, and to what acceptance criteria.*
