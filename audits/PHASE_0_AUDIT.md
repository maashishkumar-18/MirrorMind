# Phase 0 Comprehensive Audit

**Scope:** All of Phase 0 (Steps 0.1–0.4) of the Personal AI Companion migration, per `production_roadmap.md`.
**Method:** Four independent audit agents (one per step, fresh context, no access to the reasoning behind the original implementation) re-verified code, tests, and documentation against the live repository — running commands, re-deriving fixture values, and attempting to break things, not reading claims and trusting them. A fifth cross-cutting pass (git history, secret scanning, plan/roadmap consistency) was done directly. Every finding below was independently reproduced before being accepted; nothing here is asserted on an agent's word alone.
**Outcome:** One HIGH severity bug found and fixed (migration runner atomicity), one MEDIUM severity spec-compliance gap found and fixed (missing debug-server comment), two MEDIUM severity test-coverage gaps found and fixed (untested fusion-function branches), one LOW/MEDIUM finding found and fixed with a genuine secondary discovery along the way (dead IPC fixture → wiring it up surfaced a real Pydantic/zod validation asymmetry, also fixed). All fixes are committed with regression tests. Remaining findings are documented, not code defects, and are listed with rationale for why each was left as-is.

---

## How to read this document

- **PASS** — verified correct, no action needed.
- **CONCERN** — technically correct but fragile, imprecise, or worth improving later.
- **FINDING** — a real bug, gap, or inaccuracy. Each is tagged `[FIXED]` or `[DOCUMENTED — not fixed, with rationale]`.

Severity: **HIGH** (undermines a core safety/correctness guarantee) · **MEDIUM** (real gap, contained blast radius) · **LOW** (cosmetic/documentation accuracy, no functional impact).

---

## Cross-Cutting Checks (repo-wide)

**PASS** — `git status`/`git diff` clean at every checkpoint; no secrets found via pattern scan across tracked files; all 8 commits (`Baseline` → `Format` → `Step 0.1` → `Step 0.2` → `Step 0.3` → `Step 0.4` → `audit remediation`) have intact, accurate messages (one backtick-related corruption from a shell quoting mistake was caught and amended immediately after the Step 0.4 commit).

**PASS** — `production_roadmap.md`'s Step 0.2 verification note ("only `_handle_decomposed`'s import remains broken") confirmed via direct grep: exactly one match, in the deliberately-untouched method.

**CONCERN** — The approved implementation plan's own verification section says `mypy src observability db tools`, but the actual CI/pre-commit config correctly omits `tools/` (it's 100% mypy-excluded, so passing it as an explicit target errors — `"no .py[i] files"`). This is a deliberate, correctly-documented deviation discovered during implementation, not a defect — flagged here only because the plan's wording itself is now imprecise relative to what was actually built.

**FINDING — mid-audit file-change race between concurrent audit agents** `[DOCUMENTED — not a defect]`
The Step 0.1 audit agent observed `src/retrieval/orchestrator.py` transiently modified mid-session, with no command of its own targeting that file. Investigation: four audit agents ran in parallel against the **same, non-isolated working directory**, and the Step 0.2 agent was independently doing mutation-testing on that exact file (temporarily stripping and restoring an inline `# type: ignore` comment) at the same time. This is the almost-certain explanation — one agent observed another agent's file mid-edit before it reverted. Confirmed harmless: `git status`/`git diff` were clean both at the time and at every check since. **Lesson for future audits of this kind:** run agents that do mutation-testing on the same files either sequentially or in isolated worktrees, not concurrently against a shared directory — this time it was a red herring, but the setup made a real collision possible.

---

## Step 0.1 — Repository Preparation and Tooling

### PASS
- `--strict-markers` genuinely enforced (verified by planting an unregistered marker and watching collection fail correctly).
- Ruff per-file-ignores table: exhaustively checked against all 12 listed files with an isolated ruff run — every declared ignore matches a real, current violation, file-for-file, code-for-code. No stale or incomplete entries.
- mypy overrides: confirmed genuinely necessary (temporarily disabled, reran, real errors reappeared) — not blanket suppressions of nothing.
- `requirements-dev.txt` installs cleanly; installed versions match pins exactly.
- CI job graph verified correct: `sign`/`wack-precheck` structurally unreachable from `pull_request` events; only `workflow_dispatch` + `release` branch triggers them.
- Pre-commit hook revisions verified to exist upstream (`git ls-remote --tags`).
- `conftest.py`'s Langfuse-leak guard genuinely works (tested by injecting a fake key and watching it fail with the documented message).
- IPC round-trip test is real, not a false positive — confirmed by renaming a field in the TS schema and watching the test correctly fail via a real Node subprocess call, then reverting.
- Full lint/type/test gate (`black --check`, `ruff check`, `mypy`, `pytest`) all pass exactly as CI would run them.

### FINDINGS
1. **`ipc/fixtures/envelope_invalid.json` was dead — committed but never consumed by any test.** `[FIXED]`
   Five well-formed negative-path fixtures sat unreferenced since the Step 0.1 commit; both the Python and TS test suites duplicated the same *intent* with hardcoded inline cases instead. **Fix:** wired the fixture into a new `test_invalid_envelope_rejected_by_both_pydantic_and_zod` test using the same Node-subprocess mechanism as the round-trip proof. **This immediately surfaced a real, separate bug** (see next item) — justifying wiring it up rather than the cheaper option of just deleting it.
2. **Pydantic/zod validation asymmetry on the IPC envelope's `version` field.** `[FIXED]` — Severity: Medium.
   Once the fixture above was wired up, one case failed: `{"version": "1", ...}`. Pydantic v2's default lax mode silently coerces the numeric string `"1"` to `int(1)` and accepts it; zod's plain `z.number()` correctly rejects a string. `version` is the version-check middleware's discriminator field — the entire design point of this schema is "fail loudly on mismatch," so silent type coercion here is exactly the wrong behavior. **Fix:** added `Field(strict=True)` to `IPCEnvelope.version`, confirmed it now matches zod (rejects both numeric strings and `bool`, which is an `int` subclass in Python), and added two regression tests pinning both cases.
3. **Stale error count in a `pyproject.toml` comment.** `[FIXED]` — Severity: Low.
   The mypy-override rationale comment cited "62 errors across these 13 modules," dating from before Step 0.2 removed two modules from that list. Re-verified the current real count (55 errors, 11 modules) and corrected the comment.
4. **`production_roadmap.md`'s own Step 0.1 acceptance criteria are self-contradictory.** `[DOCUMENTED — not fixed]` — Severity: Low, spec wording only.
   The spec lists both "pytest discovers and runs zero tests" and "IPC envelope schema round-trips... in a standalone test" as sibling acceptance criteria for the same step — these cannot both be literally true, since the round-trip criterion requires a real passing test to exist. The implementation correctly favored the substantive criterion (a working round-trip proof) over the literal one. Left as a note for whoever next revises the roadmap; not a code change.
5. **Commit-message inaccuracies in the already-landed Step 0.1 commit.** `[DOCUMENTED — not fixed]` — Severity: Low.
   The commit message claims a fuller `tests/` skeleton was created than actually was (only `tests/common/` + `conftest.py` landed in that commit; the other subdirectories were populated by later steps). Historical commit messages are not being rewritten for text-only inaccuracies — noted here for the record instead.

---

## Step 0.2 — Pre-existing Bug Fixes

### PASS
- Every field in every reconstructed dataclass (`SearchCandidate`, `HybridSearchResult`, `RerankedChunk`, `RerankerResult`, `ConfidenceResult`) verified correct field-by-field against the real source.
- `VectorStore.query()`'s real signature (`vector: list[float]`, not the old broken `query: str`) and real return shape (`list[dict]`, not `.matches`) both confirmed matching in the fixed `_fallback_search`.
- Bare `except:` fully eliminated (`grep` confirms zero remaining in the file).
- `_handle_decomposed` confirmed still deliberately broken/untouched, exactly as scoped.
- `simple_generate()` genuinely resolves via `ProviderRegistry.get()`, not a hardcoded adapter; the roadmap's own acceptance-criteria text naming a nonexistent `get_provider()` method is confirmed a spec error, correctly worked around in the real implementation and test.
- All 5 real call sites of `simple_generate()` confirmed compatible with the new signature.
- Two of three inline `# type: ignore` comments confirmed to suppress exactly the error they claim (verified by removing each and re-running mypy).
- **Mutation testing confirmed the characterization tests are not false-positive-prone**: reverting either fix back to its broken form made the corresponding test fail immediately, as it should.

### FINDINGS
1. **Required deliverable never completed: the debug-server documentation comment.** `[FIXED]` — Severity: Medium.
   Step 0.2's spec explicitly required adding a specific forward-reference comment to `tools/retrieval_debug_server.py` at the `c.page_start`/`c.page_end` lines (documenting, not fixing, that known bug). This was silently never done — confirmed absent via `git log` (the file was untouched by the Step 0.2 commit) and by reading the file at HEAD. **Fix:** added the exact comment text the spec specifies.
2. **One inline `# type: ignore[import-not-found]` is redundant, not load-bearing.** `[DOCUMENTED — not fixed]` — Severity: Low.
   `pyproject.toml` sets `ignore_missing_imports = true` globally, so this specific ignore comment suppresses nothing that wasn't already suppressed. Harmless (no `warn_unused_ignores` lint gate to trip), but the commit message's claim that all three ignores are "precise, load-bearing" suppressions is inaccurate for this one. Left in place rather than removed, since removing it risks nothing but fixing it gains nothing either — noted for accuracy only.
3. **Two minor commit-message inaccuracies** (call-site count says "four," real count is five; an unexplained `pytest-asyncio` config addition). `[DOCUMENTED — not fixed]` — Severity: Low, no functional impact.
4. **Unguarded exception paths in `_fallback_reranker`/`_fallback_confidence_scorer`** (unlike `_fallback_search`, which wraps its real work in `try/except`). `[DOCUMENTED — not fixed]` — Severity: Low.
   Confirmed genuinely pre-existing (untouched by the Step 0.2 diff) and out of that step's stated scope. Worth hardening whenever this code is next touched, but not a regression to fix now.

---

## Step 0.3 — Characterization Tests

### PASS
- All 11 test files independently re-verified against the real source modules they target, line by line.
- Every JSON fixture (BM25, all 5 hybrid-search fusion fixtures, reranker, confidence ×2) independently re-derived from live code via throwaway scripts — all matched.
- **The mutate-before-snapshot fixture bug (found and fixed during original implementation) was confirmed genuinely fixed across all five affected fixture files**, not just the one where it first surfaced as a visible test failure.
- Reranker softmax math hand-verified: `softmax([5,2,1])` → only the top score clears the default `min_score_threshold=0.1`, exactly matching the fixture — a real, non-tautological interaction, not a coincidence.
- `GenerationOrchestrator` mocking confirmed to be at a legitimate DI seam, not internal monkeypatching; traced through whether a subtly-introduced bug (wrong warning text, wrong branch, wrong field) would actually be caught — yes, in all three cases tried.
- Full 176-test Step 0.3 suite passes individually and together; zero skips, zero warnings, zero accidental network/model calls.
- Both `TokenCounter` classes' claimed differences (method names, exception-handling depth, dead code in one's batch method) all confirmed accurate by reading the real source side-by-side.

### CONCERNS
- `test_assembly_strategy_is_selected_and_applied_before_building`'s name promises call-*ordering* verification but only asserts the call happened, not its position relative to `build()`. A bug that reordered these two calls wouldn't be caught.
- The reranker's cross-encoder model is necessarily fully mocked (a live model would break hermeticity) — the suite pins the deterministic sort/normalize/threshold logic around it, not any property of a real cross-encoder's output. Disclosed honestly in the test file; a reasonable trade-off, not a defect.

### FINDINGS
1. **Untested `dedup_strategy != "score"` branch in `_deduplicate`.** `[FIXED]` — Severity: Medium.
   All 4 fixture cases used `dedup_strategy="score"`. The real, distinct behavior for any other value (silently keeping whichever candidate was seen first, ignoring score) was never exercised — confirmed via coverage data showing the branch was never taken. `_deduplicate` is one of the fusion functions the roadmap itself calls "the most critical to pin." **Fix:** added a dedicated test constructing two same-parent candidates under a non-`"score"` strategy, asserting the higher-scored one is correctly *not* selected.
2. **Untested unrecognized-`normalize_method` branch in `_normalize_scores`.** `[FIXED]` — Severity: Medium.
   Only `"minmax"` and `"zscore"` were exercised. Any other value hits neither `if` nor `elif` and returns candidates completely **unnormalized** — a silent no-op with no error. The suite already tested the analogous "typo falls back to weighted" case for `merge_strategy`; the same diligence wasn't applied here. **Fix:** added a test asserting an unrecognized method leaves raw scores untouched, pinning the current (silent) behavior so a future change to this is deliberate, not accidental.

---

## Step 0.4 — Session Schema and Cross-Pipeline Review

### PASS
- `0001_initial_schema.sql` applies cleanly; all 10 substantive tables + `schema_migrations` present with exactly the documented columns.
- Partial unique index correctly enforces one primary chunk per session (tested: second primary insert raises `IntegrityError`; a new primary after soft-deleting the old one is permitted).
- FTS5 triggers verified working end-to-end (insert → indexed and matchable; delete → removed from the index).
- Diffed the roadmap's literal `session_chunks` column list against the real DDL — no undocumented discrepancies beyond what `docs/schema_review.md` §6 already records.
- `_split_sql_statements`'s documented limitation (a `;` inside a string literal would split incorrectly) reproduced exactly as described; confirmed the real migration file contains no such literals, so the limitation is real but currently dormant.
- `db/connection.py`'s `key` parameter confirmed genuinely inert (grepped the function body — referenced only in the signature/docstring).
- `RetrievedChunk`/`CitationLocationType` confirmed byte-for-byte unchanged in substance from the pre-Phase-0 baseline (only whitespace/type-hint modernization from the repo-wide formatting pass).
- `docs/schema_review.md`'s Chunking Algorithm section (§3) confirmed **byte-identical** to its source paragraph in `production_roadmap.md`, via programmatic diff.
- §6 "Resolved Gaps" spot-checked item-by-item against the real DDL — every claim held up (e.g. `parent_chunk_id` genuinely absent as a column; `meeting_notes_ai`/`au` triggers genuinely read `searchable_text` as flat text, never derive it from JSON).
- Sign-off checklist's test counts (22/17/9/28 = 76) reconfirmed exactly accurate via a fresh `--collect-only` run.
- `.gitignore` coverage for scratch `db/*.db`/`.bak` files confirmed working (created real scratch artifacts, confirmed `git status` didn't surface them, cleaned up).

### FINDINGS
1. **`db/migration_runner.py`'s core atomicity guarantee was false for DDL statements — the only statement type real migrations contain.** `[FIXED]` — **Severity: HIGH.**
   The original fix for `executescript()`'s non-atomicity (switching to individual `conn.execute()` calls inside `with conn:`) was itself incomplete. Python's `sqlite3` module, in its default legacy isolation mode, only opens an implicit transaction before a **DML** statement (INSERT/UPDATE/DELETE) — DDL statements (CREATE TABLE/INDEX/TRIGGER, i.e. everything a schema migration is made of) autocommit **individually**, regardless of `with conn:`.

   This was confirmed by two independent reproductions:
   - Minimal repro: two `CREATE TABLE` statements inside `with conn:`, the second one failing (duplicate name) — the **first table survived on disk** after the exception, contradicting the module's own docstring and the original commit message's "confirmed empirically" claim (that testing had only exercised DML after the DDL, not DDL-only failure).
   - Full `MigrationRunner.run()` repro against a synthetic partially-failing migration: `run()` raised as expected, `schema_migrations` correctly showed no row for it, but the stray table from the first (successful) DDL statement was left behind — a genuinely inconsistent, hard-to-recover state that would hit "table already exists" on any naive retry.

   None of the 17 pre-existing tests in `test_migration_runner.py` exercised a genuinely multi-statement, partially-failing migration — every test used successful runs or single-statement failures caught before any DDL executed. The 76/76-green signal and the confident docstring gave false assurance here; this is exactly the failure mode a "gate to Phase 1" schema-review document should not have inherited unexamined.

   **Fix:** `run()` now opens its connection with `isolation_level=None` (so Python's driver manages no implicit transaction of its own) and issues explicit `BEGIN`/`COMMIT`/`ROLLBACK` around each migration's statements plus its bookkeeping insert. SQLite's engine *does* support fully transactional DDL when explicitly told to — the limitation was in the Python driver's automatic behavior, not SQLite itself. Verified the fix doesn't break trigger bodies (their internal `BEGIN...END` syntax is unrelated to and unaffected by the outer transaction's `BEGIN`/`COMMIT`). Added `TestAtomicity` (3 new tests) proving: a partially-failing migration leaves zero trace, `status()` correctly still shows it unapplied, and fixing + retrying the migration file succeeds cleanly afterward. Updated the module's docstrings to describe the real, now-correct mechanism instead of the disproven claim.

### CONCERNS
- The pre-migration snapshot is the only real safety net for a migration that fails mid-way in production; recovering from a mismatch between `schema_migrations` and the live schema still requires a human to notice and restore from the snapshot manually — not automated or tested. (This concern predates and is orthogonal to the atomicity fix above; the fix means this recovery path should now never actually be needed for a partial-DDL scenario, but it remains the fallback for other failure classes, e.g. disk corruption.)
- `docs/schema_review.md` is otherwise exceptionally well-verified against reality (no other factual inaccuracy found anywhere in it) — its one blind spot was taking `db/migration_runner.py`'s atomicity design on faith rather than re-deriving the claim independently, the way it re-derived everything else (test counts, DDL columns, chunking text). Worth remembering for future "frozen" documents: an audit should re-verify safety-critical mechanism claims specifically, not just data/count claims.

---

## Summary

| Step | PASS items | Findings (fixed) | Findings (documented, not fixed) |
|---|---|---|---|
| 0.1 | 9 | 3 (dead fixture → wired up; version-strictness bug; stale comment) | 2 (spec wording; commit-message accuracy) |
| 0.2 | 7 | 1 (missing debug-server comment) | 3 (redundant ignore; commit-message accuracy; unguarded exceptions, deferred) |
| 0.3 | 8 | 2 (both untested fusion-function branches) | — (2 minor concerns, no fix needed) |
| 0.4 | 10 | 1 (migration runner atomicity — HIGH) | — (1 concern, orthogonal to the fix) |

**Net result:** 295 tests passing (up from 283 before this audit), all lint/type/format gates clean, one HIGH-severity correctness bug closed with regression coverage, and every other real finding either fixed or explicitly recorded with rationale. Phase 0's schema and infrastructure are now re-verified, not just originally-verified — the distinction that matters for a milestone the roadmap itself calls "the gate to Phase 1."

All remediation is in commit `7f71c55` ("Phase 0 audit remediation..."), on top of the original six Phase 0 commits.
