<!-- Title: Session Schema Cross-Pipeline Review -->

# Session Schema Cross-Pipeline Review

**Production Roadmap Phase 0 Step 0.4 deliverable.** This document is the cross-pipeline review committed alongside the frozen schema and shared type contracts. Read it alongside `db/migrations/0001_initial_schema.sql`, `db/migration_runner.py`, and the session-era additions to `src/common/types.py` — not as a substitute for reading that code.

**Status: frozen.** No pipeline rewrite in Phase 1 begins until this document has no unresolved gaps, per Step 0.4's exit criterion. This is the single highest-leverage design decision in the project (`production_roadmap.md` Step 0.4 objective) — a schema change mid-migration causes coordinated breakage across all three pipelines, per `RAG_MIGRATION_AUDIT.md`'s top risk finding.

---

## 1. Overview

Phase 0 Step 0.4 designs and freezes, before any Phase 1 pipeline code is written:

- The complete SQLite schema for the session database (10 substantive tables + `schema_migrations`), in `db/migrations/0001_initial_schema.sql`.
- An idempotent, forward-only migration runner, in `db/migration_runner.py`.
- The session-era shared type contracts — `SessionRetrievedChunk`, `SessionCitationFormat`, `AgenticActionType`, `RetrievalRoute`, `AgenticOutput` — introduced in `src/common/types.py` **alongside**, not replacing, the existing `RetrievedChunk`/`CitationLocationType`. Those old types are deleted only in Phase 1 Step 1.4, once both retrieval (Step 1.3) and generation (Step 1.4) have migrated off them.
- The sliding-window sub-chunking algorithm (§3 below), pinned in enough precision that Phase 1 Step 1.2's unit tests are written against this document, not free to redefine it.

---

## 2. Full DDL Reference

The authoritative schema is `db/migrations/0001_initial_schema.sql`. Summary:

| Table | Purpose | FTS5 |
|---|---|---|
| `sessions` | Conversation windows, bounded by idle timeout or explicit close | — |
| `messages` | Individual turns within a session | — |
| `session_chunks` | Vector store — one primary chunk per session (enforced by a partial unique index) plus overlapping sub-chunks for sessions > 20 messages | — |
| `reminders` | scheduled_time / fired_at / completed_at / dismissed_at / toast_id | ✓ |
| `todos` | priority / category / completed_at | ✓ |
| `meeting_notes` | attendees / topics / decisions / action_items / follow_ups / needs_review | ✓ |
| `schedules` | Schedule containers (e.g. "Monday plan") | — |
| `schedule_items` | Time-slotted items within a schedule | ✓ |
| `summaries` | Daily/weekly, idempotent by `(summary_type, period_start)` | — |
| `sync_metadata` | Present-but-inert in v1, reserved for v2 cloud sync | — |

Every substantive table has `id`, `created_at`, `updated_at`, `deleted_at`, and a nullable `sync_metadata` JSON column — except `sync_metadata` itself (§6, item 7). All soft-delete only; no hard deletes in v1.

FTS5 tables use the standard external-content pattern (`content=`, `content_rowid='rowid'`) with `AFTER INSERT`/`AFTER DELETE`/`AFTER UPDATE` triggers keeping each index in sync with its base table. FTS5 has no concept of `deleted_at` — a query against an FTS5 table must be combined with a `JOIN ... WHERE deleted_at IS NULL` against the base table (demonstrated explicitly in `tests/common/db/test_schema_ddl.py::TestFts5Queryable::test_soft_deleted_row_still_matches_fts_but_app_query_pattern_excludes_it`), which is exactly the pattern Phase 1 Step 1.3's `StructuredTableSearch` must use.

---

## 3. Chunking Algorithm

Transcribed verbatim from `production_roadmap.md` v1.2, Step 0.4's task text — this is the literal specification Phase 1 Step 1.2's unit tests are written against, not re-derived there:

> **Pin the sliding-window sub-chunking algorithm.** Specify the exact algorithm in pseudocode, not just the two config numbers (`window_size`, `stride`). Canonical definition: given messages `M[0..N-1]`, window size `W`, and stride `S`, sub-chunks are generated only when `N > threshold` (default 20). Window start indices are `0, S, 2S, 3S, ...` for as long as `start + W ≤ N` — a window is created only if it is full (exactly `W` messages); a trailing window that would be undersized (`start + W > N`) is dropped, not padded and not included truncated. Worked example at `W=10, S=5, N=25`: starts `0, 5, 10, 15` are each full windows (`15 + 10 = 25 ≤ 25`), `start=20` is dropped (`20 + 10 = 30 > 25`) — 4 sub-chunks, not 3. Rationale for dropping rather than padding/truncating: the embedding model is not calibrated on variable-length conversational inputs, so an undersized window would sit in a different density region of the vector space than full-window chunks; the primary chunk (always present, always covering the full session including the tail) is the better home for tail content than a degraded partial sub-chunk.

In pseudocode:

```
def sub_chunk_windows(N, W=10, S=5, threshold=20):
    if N <= threshold:
        return []  # primary chunk only -- no sub-chunks
    windows = []
    start = 0
    while start + W <= N:
        windows.append((start, start + W - 1))  # inclusive message-index range
        start += S
    return windows
```

Worked example, `N=25, W=10, S=5`:

| start | window (message indices) | included? |
|---|---|---|
| 0 | 0–9 | ✓ |
| 5 | 5–14 | ✓ |
| 10 | 10–19 | ✓ |
| 15 | 15–24 | ✓ (15+10=25 ≤ 25) |
| 20 | would be 20–29 | ✗ dropped (20+10=30 > 25) |

Result: **primary + 4 sub-chunks**, not 3 — this corrected a genuine error in an earlier draft of the roadmap's own acceptance criterion (see `production_roadmap.md` v1.2 changelog), verified by direct computation, not assumption.

`window_size` (`W`), `stride` (`S`), and the sub-chunking `threshold` (message count above which sub-chunks are generated at all) are config values (`config/ingestion/chunker.yaml`, created in Phase 1 Step 1.2), not hardcoded constants — this document fixes the *algorithm*, not the specific numbers, though `W=10, S=5, threshold=20` are the defaults used throughout `project_logic.md` and the worked example above.

---

## 4. `SessionRetrievedChunk` Field-by-Field Consumer Map

| Field | Persisted column? | Written by | Read by |
|---|---|---|---|
| `chunk_id` | `session_chunks.id` | Ingestion (chunker/enricher) | Retrieval, generation |
| `session_id` | `session_chunks.session_id` | Ingestion | Retrieval (session-scoped filtering), generation (citations) |
| `content` | `session_chunks.content` | Ingestion | Retrieval, generation |
| `raw_content` | *(not separately persisted — see note)* | — | — |
| `topics` | `session_chunks.topics` (JSON array) | Ingestion (metadata extraction) | Retrieval (metadata filtering), generation |
| `action_types` | `session_chunks.action_types` (JSON array) | Ingestion | Retrieval Router (Phase 1 Step 1.3), generation |
| `entities` | `session_chunks.entities` (JSON array) | Ingestion | Generation |
| `sentiment` | `session_chunks.sentiment` | Ingestion | Generation |
| `message_roles` | `session_chunks.message_roles` (JSON array) | Ingestion | Generation (role-aware prompt assembly) |
| `chunk_type` | `session_chunks.chunk_type` (`primary`\|`sub_chunk`) or synthesized `structured_record` for `StructuredTableSearch` results | Ingestion; Retrieval Router for structured results | Retrieval, generation |
| `token_count` | `session_chunks.token_count` | Ingestion | Retrieval (context budget) |
| `score`, `semantic_score`, `keyword_score`, `metadata_score` | *(not persisted — computed per-query)* | Retrieval (`HybridSearch`) | Generation, confidence scoring |
| `metadata` | *(free-form, assembled at query time from the above persisted columns)* | Retrieval | Generation |
| `timestamp` | *(derived — see §6, item 4)* | Retrieval, from `session_chunks.created_at`/`updated_at` | Generation (citations) |
| `parent_chunk_id` | *(derived — see §6, item 3)* | Retrieval, from `session_id` + `chunk_type='primary'` lookup | Retrieval dedup logic (Phase 1 Step 1.3) |
| `source_prefix` | *(derived — see §6, item 4)* | Retrieval, assembled at query time | Generation |

**Note on `raw_content`:** unlike `RetrievedChunk` (where `content` carries source-prefix enrichment and `raw_content` is the pre-enrichment original), `session_chunks.content` is the raw session text — enrichment (the `[Session: ... | Topic: ...]` prefix, Phase 1 Step 1.2) is applied at embedding time to a *copy* used only for the embedding input, not persisted as a second column. `SessionRetrievedChunk.raw_content` and `.content` are expected to be equal in the common case for session chunks; Phase 1 Step 1.2 has final say on whether a distinct enriched-vs-raw persisted pair turns out to be needed once the enrichment prefix design is implemented — flagged here as a decision Phase 1 owns, not a gap in this schema (the column exists and is sufficient either way; only *whether a second column is added* is open, and that's an additive, non-breaking change if it turns out to be needed).

---

## 5. Agentic Output → Retrieval Router Mapping

`AgenticOutput` (`src/common/types.py`) is the sole interface between the agentic reasoning layer and the Retrieval Router (Phase 1 Step 1.3):

| `AgenticOutput` field | Retrieval Router behavior |
|---|---|
| `retrieve_needed = False` | Short-circuit: `response` is returned directly to the frontend, no retrieval runs at all (`project_logic.md` §4's `retrieve_needed = false` branch). Verified by `tests/common/test_agentic_output.py` accepting this combination as valid input. |
| `retrieve_needed = True`, `retrieval_route = SEMANTIC` | Dispatch to `HybridSearch` (session_chunks vector + BM25 + rerank) |
| `retrieve_needed = True`, `retrieval_route = STRUCTURED` | Dispatch to `StructuredTableSearch` (FTS5 over `meeting_notes`/`todos`/`reminders`/`schedule_items`, filtered by `deleted_at IS NULL`) |
| `retrieve_needed = True`, `retrieval_route = HYBRID` | Both paths execute, results merged before generation |
| `search_query` | Passed to whichever path(s) above; `None` is valid (e.g. the semantic path can fall back to the raw user message) |
| `action_type` | Not consumed by the Retrieval Router directly — consumed downstream by the feature handlers (Phase 1 Step 1.5) that write to `reminders`/`todos`/`meeting_notes`/`schedule_items` when a structured action is detected |
| `confidence` | Drives the four-tier agentic reasoning behavior (`project_logic.md` §3) — auto-execute / disambiguate / clarify / conversation — orthogonal to which retrieval path runs |

All three `retrieval_route` values and the `retrieve_needed=False` short-circuit are confirmed covered by `AgenticOutput`'s validation surface (`tests/common/test_agentic_output.py`); the *dispatch logic itself* (the Retrieval Router class) is Phase 1 Step 1.3's deliverable, not this step's — this document confirms the contract gives it everything it needs, per the Step 0.4 cross-pipeline review task ("the agentic output contract gives the Retrieval Router everything it needs to dispatch").

---

## 6. Resolved Gaps

Deliberate deviations between `production_roadmap.md`'s literal per-table column lists and the DDL actually committed, each resolved and recorded here rather than left ambiguous for a Phase 1 implementer to rediscover:

1. **`session_chunks.message_roles`** is added even though the roadmap's explicit column list for `session_chunks` omits it. Every other `SessionRetrievedChunk`-derived field (`topics`/`action_types`/`entities`/`sentiment`) is persisted as a column rather than recomputed via a `messages` join on every retrieval — the <200ms vector-search budget (`production_roadmap.md` Phase 2 Step 2.5) can't afford that join at scale. `message_roles` gets the same treatment for consistency.

2. **`session_chunks.sync_metadata`** is included even though the roadmap's per-table column list for `session_chunks` doesn't spell it out explicitly. The universal-constraint sentence ("every table must have a `sync_metadata` column") is read as governing, not contradicted by, the per-table list — the two statements in the roadmap are consistent, not conflicting.

3. **`SessionRetrievedChunk.parent_chunk_id` is not a persisted column.** With exactly one primary chunk per session enforced by `idx_session_chunks_primary` (a partial unique index on `session_id WHERE chunk_type='primary' AND deleted_at IS NULL`), "the parent of a sub-chunk" is always derivable via `session_id` + `chunk_type='primary'` — an explicit FK column would be redundant. Populated by the retrieval layer at query time instead (`None` for primary chunks, the primary chunk's `id` for sub-chunks and structured records).

4. **`SessionRetrievedChunk.timestamp` and `.source_prefix` are not persisted columns.** `timestamp` derives from `session_chunks.created_at`/`updated_at` directly — sufficient given citations are explicitly "approximate" per `project_logic.md` §12 ("session ID + approximate timestamp references"). `source_prefix` is assembled at retrieval time from `session_id` + `timestamp` + `topics[0]`, mirroring how `RetrievedChunk.source_prefix` is also not a raw storage field today (per `RAG_MIGRATION_AUDIT.md`'s cross-reference notes on the generation pipeline).

5. **`meeting_notes.searchable_text` population ownership.** This column is written by the **application layer** — specifically Phase 1 Step 1.5's `MeetingNoteHandler.capture_meeting_note()` — never by a trigger or a SQLite generated column. The `meeting_notes_ai`/`meeting_notes_au` FTS5 triggers only ever read `searchable_text` as already-flattened text; they never derive it from the JSON array columns (`attendees`/`topics`/`decisions`/`action_items`/`follow_ups`). This avoids doing JSON-array flattening inside a SQL trigger, which would be unnecessary complexity for Phase 0. Recorded explicitly so a Phase 1 implementer doesn't look at the schema and wonder why a flat text column sits alongside JSON columns with no visible producer.

6. **`summaries` idempotency uses a partial unique index, not a plain `UNIQUE` constraint.** `idx_summaries_period` is `UNIQUE(summary_type, period_start) WHERE deleted_at IS NULL`. A plain `UNIQUE` constraint would make a soft-deleted historical summary permanently block regeneration for that period, contradicting the soft-delete-only, no-hard-deletes design (`project_logic.md` §5).

7. **`sync_metadata` (the table) has no `sync_metadata` column of its own.** A self-referential JSON column on the table that *is* the sync bookkeeping mechanism is nonsensical. Documented exception to the universal-constraint rule, confirmed by `tests/common/db/test_schema_ddl.py::TestUniversalColumns::test_sync_metadata_table_has_no_sync_metadata_column_of_its_own`.

**No unresolved gaps remain** as of this document's commit — every deviation from a literal reading of the roadmap's per-table column lists is accounted for above, and the cross-pipeline review (ingestion writes / retrieval reads-and-routes / generation reads) confirmed every `SessionRetrievedChunk` consumer has the fields it needs (§4) and the Retrieval Router has what it needs to dispatch (§5).

---

## 7. Sign-Off Checklist

Matching Step 0.4's acceptance criteria verbatim:

- [x] **All schema tests pass.** `tests/common/db/test_schema_ddl.py` (22 tests), `tests/common/db/test_migration_runner.py` (17 tests), `tests/common/test_types_session.py` (9 tests), `tests/common/test_agentic_output.py` (28 tests) — all passing as of this commit, alongside the full Phase 0 suite.
- [x] **Cross-pipeline review document has no unresolved gaps.** §6 above enumerates every deviation from the roadmap's literal text, each resolved with a stated rationale; §4 and §5 confirm every consumer's field needs are met.
- [x] **The existing `RetrievedChunk` is untouched — old and new types coexist.** `RetrievedChunk`/`CitationLocationType` in `src/common/types.py` are unmodified; `SessionRetrievedChunk`/`SessionCitationFormat`/`AgenticActionType`/`RetrievalRoute`/`AgenticOutput` are appended after them in the same module. Confirmed by `tests/common/test_types_session.py::TestOldRetrievedChunkUntouched`.
- [x] **CI is green.** `black --check .`, `ruff check .`, `mypy src observability db tests`, and the full pytest suite all pass clean as of this commit.

**Exit criterion met:** schema design reviewed and frozen. This is the gate to Phase 1 — no pipeline rewrite begins until this document's sign-off, which is now complete.
