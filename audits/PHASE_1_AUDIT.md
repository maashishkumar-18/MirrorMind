# Phase 1 Comprehensive Audit

**Scope:** All of Phase 1 (Steps 1.1–1.6) of the Personal AI Companion migration, per
`production_roadmap.md` — local infrastructure, the ingestion / retrieval / generation
rewrites, the feature handlers, and dynamic model management. Commits `0d2909f` →
`c708cf9` (8 commits).

**Method:** Six independent audit agents (one per step, fresh context, no access to the
reasoning behind the original implementation) re-verified code, tests, and documentation
against the live repository — running the test suites, driving the download state machine
with hostile scripted fakes, re-deriving fixture values (cosine similarities, sliding-window
index ranges, progress math, golden-set hashes), fuzzing the FTS5 path with injection
strings, monkeypatching every agent failure mode, and smoke-testing against the live Ollama
daemon. A seventh cross-cutting pass (git history, secret scan, deleted-type greps, CI
config, roadmap consistency, lint-gate scope) was done directly. Every finding below was
independently reproduced before being accepted.

**Outcome:** All six steps meet their spec and acceptance criteria. No HIGH-severity issue.
**One MEDIUM** (Step 1.6: a raising `progress_callback` bypasses download cleanup — a
literal breach of the three-state guarantee). The rest are LOW: a handful of spec-compliance
gaps (an unimplemented "verify integrity" line, dead token-bound config, a missing
`compute_ms` span field), inaccurate type annotations, and hardening opportunities for
concurrency the single-connection runtime does not yet create. The clear, low-risk findings
were fixed with regression tests in the remediation commit; the judgment calls and
larger items are documented with rationale.

The test suite is green on `main` (485 before this audit, 500 after remediation);
`black --check`, `ruff check`, and `mypy src observability db` are all clean.

---

## How to read this document

- **PASS** — verified correct, no action needed.
- **CONCERN** — technically correct but fragile, imprecise, or worth improving later.
- **FINDING** — a real bug, gap, or inaccuracy. Each tagged `[FIXED]` or `[DOCUMENTED — not fixed, with rationale]`.

Severity: **HIGH** (undermines a core safety/correctness guarantee) · **MEDIUM** (real gap,
contained blast radius) · **LOW** (cosmetic/documentation accuracy, no functional impact).

---

## Cross-Cutting Checks (repo-wide)

**PASS** — Git history: all 8 Phase 1 commits present, messages detailed and accurate
(each agent checked its step's commit body against `--stat` + content; no overclaiming
found). The migration is genuinely reductive — `0d2909f` alone is +2,729 / −8,899 lines.

**PASS** — `RetrievedChunk` and `CitationLocationType` (the document-era shared contract):
**zero live references** anywhere in `src/ tests/ eval/ observability/ db/ dashboard/
scripts/` — only deletion-explaining comments remain. Both types are gone from
`src/common/types.py`. This is Phase 1's headline exit criterion and it is met.

**PASS** — No `pinecone` import anywhere in `src/`. No `google` / `openai` / `genai` /
`tiktoken` import in `src/ingestion/` (the cloud adapter classes survive as lazily-imported
unregistered dead code in `llm_client.py`, exactly as the roadmap specifies for v1.1).

**PASS** — Secret scan clean (pattern scan over all tracked non-doc files). No stray
`.db` / `.bak` / `__pycache__` / coverage artifacts committed. Git LFS intact for the
~90 MB `all-MiniLM-L6-v2` weights (`model.safetensors` materialized, not a pointer).

**PASS** — `.env.example`: all forbidden vars removed (`PINECONE_*` ×4, `GEMINI_API_KEY`,
`OPENAI_API_KEY`, `DEEPSEEK_API_KEY`); all required additions present; all pre-existing
`RAGPIPE_*` names preserved. Old refusal phrase fully gone.

**PASS** — `eval/gates.json` has 4 real numeric gate values (no placeholders);
`_meta.golden_set_sha256` matches the current `golden_qa_set.json` byte-for-byte (the
harness's startup drift-guard currently passes); `_meta.model` (`llama3.1:8b`) matches the
CI `eval` job's `OLLAMA_DEFAULT_MODEL`.

**PASS** — CI workflow: `lint-python` / `lint-ipc-schema` / `test` gate on push + PR;
`eval` (heavy, installs Ollama) is `workflow_dispatch`-only; `sign` / `wack-precheck`
structurally unreachable from `pull_request`. `src/` is free of `TODO` / `FIXME` /
`NotImplementedError` markers.

**PASS** — `production_roadmap.md` / `project_logic.md`: no stray `Phase 1.5` labels,
no live `memories`-table references (the v1.1 changelog's own grep-pass claim holds).

**CONCERN** — `pyproject.toml` grandfathered lint/type suppressions are stale for the
retrieval layer. The block's own policy is *"clear an entry when its file is next
rewritten/touched by a migration step."* Yet after Phase 1:
- `src/retrieval/hybrid_search.py` (substantially rewritten in 1.3b) is still wholesale
  under `[[tool.mypy.overrides]] ignore_errors = true` plus a `B904` ruff ignore.
- `src/retrieval/reranker.py` (slimmed in 1.3a) still under `ignore_errors` + `B904,F401,F841`.
- `src/ingestion/embedder.py` (cloud providers deleted in 1.2, defaults changed in 1.1)
  still under `ignore_errors`.
- `src/retrieval/context_builder.py` (fully rewritten in 1.3c) still carries a ruff
  `F841` ignore — **this one is load-bearing** and correctly kept (see 1.3-C1: the audit
  agent's "stale, masks nothing" claim was disproved by a full `ruff check .` run).

Net effect: the `mypy src observability db` gate is weaker than it appears for the
retrieval layer — real type debt in newly-written code inside those three modules would
not be caught. Only the genuinely-pinned Phase 0 fusion helpers in `hybrid_search.py`
justify a suppression. **Recommendation:** narrow the `ignore_errors` overrides to the
specific pinned functions (or add real annotations) when retrieval typing is next
touched — tracked, not fixed now, because doing it properly is an unscoped
typing-remediation project on Any-typed metadata-boost vectors.

**CONCERN** — `README.md` describes the entire pre-migration document-RAG architecture
(Pinecone, cloud LLMs, `RetrievedChunk` as the live contract, `sample_data/`, the
retrieval orchestrator, `run_ragas_eval.py`). Stale since Phase 0, not a Phase 1
regression. A full rewrite is a Phase 5 (Store-listing) deliverable; remediation added a
prominent out-of-date banner pointing to the `audits/` docs so a reader is not
misinformed (see 1.4-C8).

---

## Step 1.1 — Local Infrastructure (VectorStoreInterface, SQLiteVectorStore, LocalEmbeddingProvider, OllamaAdapter)

### PASS
- **Interface boundary is real and enforced** — `grep` shows zero imports of
  `sqlite_vector_store` in `src/ingestion` or `src/retrieval`; the concrete store is
  constructed only in tests / the (not-yet-existing) composition root. All four
  `VectorStoreInterface` methods match spec signatures.
- **`delete()` genuinely evicts** — re-derived: `_matrix` shrinks `(4,384)→(3,384)`,
  `_ids` / `_id_pos` / `_norms` rebuilt and re-aligned, a deleted chunk cannot resurface
  in `query()`; idempotent.
- **`LocalEmbeddingProvider`** — re-derived `embed_batch → (N,384)` float32,
  `embed_query → (384,)`; cosine similarity of the test's dentist-sentence pair =
  **0.8139 > 0.7** (unrelated pair 0.12); registered as `"local"` in `ProviderFactory`;
  token counting is HF `AutoTokenizer` (`count_tokens("hello") == 3`), not tiktoken.
- **PyInstaller path resolution** — precedence `sys._MEIPASS` → `RAGPIPE_EMBEDDING_MODEL_PATH`
  → repo `models/…` (path math correct); all four branches have tests.
- **`OllamaAdapter`** — 3 error classes with user-readable messages, each tested. The
  **404-vs-model-missing disambiguation does NOT reproduce as a bug** — a bare nginx/route
  404 correctly falls through to a generic `ProviderAPIError`; only `"model" + "not found"`
  maps to `ModelNotDownloadedError`.
- **`ProviderRegistry().list_providers() == ["ollama"]`** (verified live); cloud adapters
  retained unregistered. `simple_generate` defaults `provider="ollama"`, resolves via the
  registry (no hardcoded adapter).
- **`EmbeddingGenerator`** — default `"local"` (diffed against `1555b32`), `estimate_cost`
  removed (test pins absence), 429/rate/quota retry conditions stripped,
  `embed_chunks` / `embed_query` / `embed_queries` **signatures unchanged** → `hybrid_search.py`
  needed no changes. The old buggy `if embeddings:` ndarray-truthiness is gone; callers
  use `is None` / `len()` / `.size`.

### CONCERN
- **`SQLiteVectorStore` has no concurrency guard.** `upsert` reassigns `self._matrix`
  (via `np.vstack`) then `self._norms` in a separate call; a concurrent `query()` on
  another thread can see an `(n+1,384)` matrix against an `(n,)` norms vector → broadcasting
  error, or a stale read. Contained (crash / stale read, never DB corruption). Fine under
  the roadmap's single-instance + process-mutex assumption, but nothing in the class
  enforces it — confirm when the composition root wires ingestion + retrieval together.
- **Multi-instance in-memory staleness** — two stores on one DB file: a `delete()` through
  one does not touch the other's numpy matrix; `delete()` of an already-soft-deleted row
  returns `False` and leaves the stale row in this instance's matrix, so `query()` keeps
  returning it. Re-derived with two instances. Same single-instance assumption.
- `resolve_model_path`'s `RAGPIPE_EMBEDDING_MODEL_PATH` override is unreachable in a frozen
  build (`sys._MEIPASS` is checked first) — a packaged `.exe` cannot be pointed at an
  alternate model dir. Likely intentional; the documented override has a silent carve-out.
- `ModelNotLoadedError` triggers on any non-200 body containing the substring `"loading"`
  — an unrelated `"error loading adapter"` would be reported as "the model is still loading."

### FINDING
1. **Spec line "verify integrity" is not implemented.** `[FIXED]` — Severity: LOW.
   The Step 1.1 spec for `SQLiteVectorStore.__init__` says "register WAL mode, verify
   integrity." WAL is set; the only construction-time check is `_table_exists("session_chunks")`.
   Neither `open_session_db` nor the store runs `PRAGMA integrity_check` / `quick_check`.
   **Fix:** `SQLiteVectorStore.__init__` now runs `PRAGMA quick_check` once and raises
   `RuntimeError` if the result is not `ok`; regression test added.
2. **`EmbeddingGenerator.embed_query` / `embed_queries` return-type annotation is
   inaccurate.** `[FIXED]` — Severity: LOW.
   Annotated `list[float] | None` / `list[list[float] | None]`; actually returns
   `np.ndarray` rows (`LocalEmbeddingProvider.embed_batch` returns an ndarray). The sole
   live caller (`hybrid_search._semantic_search`) is ndarray-safe, so no runtime break, but
   any code trusting the annotation (`if vec:`, `json.dumps(vec)`) would fail. **Fix:**
   annotations corrected to `np.ndarray | None` / `list[np.ndarray | None]`; a test pins
   the runtime return type.
3. **No test pins `simple_generate`'s `"llama3.1:8b"` / `OLLAMA_DEFAULT_MODEL` fallback.**
   `[FIXED]` — Severity: LOW.
   The acceptance criterion "`simple_generate()` routes through `OllamaAdapter` by default
   (verified by unit test)" is met for the *provider* but the *model* fallback path is only
   exercised implicitly. **Fix:** a test now asserts the resolved `model_name` is
   `"llama3.1:8b"` with the env unset, and the env value when set.

---

## Step 1.2 — Ingestion Pipeline Rewrite

### PASS
- **`parser.py` deleted**; every doc-era symbol gone (`CleanedDocument`, `_chunk_by_slides`,
  `_merge_small_chunks`, `generate_namespace_from_document`, `sanitize_namespace`,
  `GoogleProvider`, `OpenAIProvider`, `DocumentMetadata`) — repo-wide grep finds only
  absence-assertions in tests. `unstructured` + `python-magic-bin` removed from
  `requirements.txt`.
- **The sliding-window sub-chunker is provably correct.** Running the *real* `SessionChunker`
  on synthetic sessions of n = 19/20/21/22/25/26/30/35 reproduces the frozen
  `docs/schema_review.md` §3 algorithm exactly: **n=25 → primary + exactly 4 sub-chunks
  `[0-9] [5-14] [10-19] [15-24]`, no trailing window** (the acceptance criterion); n=20/19 →
  primary only (strict `>` threshold); n=21 → 3 sub-chunks (window `[15-24]` correctly
  dropped). `sub_chunk_windows()` is byte-identical to the pinned pseudocode. The unit
  tests assert **exact per-chunk `message_indices` ranges**, not just counts.
- **End-to-end verified** — `test_pipeline_session.py` ingests a 25-message session through
  the real pipeline with real all-MiniLM embeddings into a real migrated SQLite DB and
  asserts 1 primary + 4 sub rows in `session_chunks` with the right window indices.
- `tiktoken` gone from ingestion (`tokenizer.py` uses `transformers.AutoTokenizer`);
  `SessionMetadata` fields exact; `_call_llm` prompt session-framed; `_prepare_sample`
  uses `[User]:` / `[Assistant]:`; `_build_source_prefix` produces the exact spec shape
  `[Session: {id} | {ts} | Topic: {topic}]`; ingestion imports only `VectorStoreInterface`;
  `.env.example` and `requirements.txt` diffs match the spec item-for-item.

### CONCERN
- The spec's "Preserve `extract_with_retry` unchanged" is not literally true — signature and
  body were necessarily rewritten for `SessionMetadata` params; the *retry structure* (attempt
  loop, try/except, final-failure fallback) is preserved. Spec wording issue, not a code defect.
- `Chunk` gained a `topics` field and `SessionChunkerConfig` a `sub_chunk_threshold` key
  beyond the spec's explicit enumerations — both defensible (topics feeds `_build_source_prefix`;
  the threshold key means "20" is no longer hardcoded).
- The spec's "all-MiniLM-L6-v2's 384-token max" is wrong (384 is the embedding *dimension*);
  the implementation correctly uses the model's real `max_seq_length` of **256**.
- Stale transitional alias `SemanticChunker = SessionChunker` at `chunker.py:264` — nothing
  imports it post-1.6. Dead code, a 1.3/1.4 cleanup miss.

### FINDING
1. **`max_chunk_tokens` / `min_chunk_tokens` in `chunker.yaml` are dead config.**
   `[DOCUMENTED — not fixed, with rationale]` — Severity: LOW-MEDIUM.
   Nothing in `SessionChunker.chunk` / `_build_chunk` / the enricher / the pipeline ever
   reads them — chunking is purely message-count based. The embedder's 256-token truncation
   guard is a *separate* hardcoded literal in `src/ingestion/embedder.py::MODEL_CONFIGS`,
   not sourced from the yaml. The spec asked for chunk-size bounds "from config, not
   hardcoded" **and** a "token-count boundary" unit test — both the wiring and that test
   are absent. **Not fixed here** because the right fix is a design decision the step owner
   should make: either (a) make `embedder.MODEL_CONFIGS["local"].max_tokens` read from the
   yaml so there is one source of truth and add a boundary guard + test, or (b) delete the
   two keys and strike the "bounds from config" clause from the roadmap. Recommend (a).
2. **Empty timestamp yields a malformed source prefix** `[Session: x |  | Topic: y]`
   (double space, empty field). `[FIXED]` — Severity: LOW. Not hit in practice (the
   pipeline always passes a timestamp). **Fix:** `ts = chunk.timestamp or "unknown"` before
   formatting; test added.
3. **`scripts/simulate_traffic.py` import is broken** (`from eval.run_ragas_eval import …`
   — module renamed to `run_eval` in 1.4). `[DOCUMENTED]` — Severity: LOW. Explicitly
   deferred in the 1.2 and 1.4 commit messages; not in any CI job (not collected by pytest,
   not in mypy scope). A dev-only portfolio load-generator. Fix belongs to whichever step
   rewrites it.
4. **Re-ingesting a shrunk session orphans sub-chunk rows** (upsert-only, no stale
   cleanup). `[DOCUMENTED]` — Severity: LOW. A pre-disclosed deferral (the "after every
   message" trigger wiring). `test_reingest_is_idempotent` only covers same-size re-ingest,
   so its name over-claims — a code comment was added noting the limitation.

---

## Step 1.3 — Retrieval Pipeline Rewrite (1.3a demolition, 1.3b core, 1.3c assembly)

### PASS
- **Demolition complete.** `orchestrator.py`, `intent_router.py`, `query_rewriter.py`,
  `query_expander.py`, `metadata_filter.py`, both dead yamls, and `src/retrieval/pipelines/`
  are all gone; grep for every SCRAP symbol (`_fallback_search`, `_handle_decomposed`,
  `_elasticsearch_search`, `colbert`, `WeightOptimizer`, `INTENT_TEMPLATES`, …) finds only
  removal-explaining comments. `legacy_vector_store.py` (Pinecone) deleted; `HybridSearch`
  imports only `VectorStoreInterface`.
- **The `key_topics` silent-mismatch bug is provably not repeated.** All 5
  `hybrid_search.yaml` metadata-boost fields (`topics`, `action_types`, `entities`,
  `sentiment`, `message_roles`) are real `session_chunks` columns **and** populated on the
  `SessionRetrievedChunk` by `_to_retrieved_chunk` — verified with a live round-trip
  assertion.
- **Pinned fusion/dedup helpers byte-identical to Phase 0** — AST-level comparison of
  `_merge_candidates`, `_weighted_merge`, `_reciprocal_rank_fusion`, `_deduplicate`,
  `_normalize_scores`, `BM25Index` at `0d2909f~1` vs HEAD: all identical. The
  BM25-over-dense-pool deviation is a conscious, doc-string-documented v1.1 follow-up.
- **Router tests exercise all 3 routes end-to-end** — real ingested 25-message session,
  real all-MiniLM embeddings, seeded structured rows, all 3 routes + the `retrieve_needed=false`
  short-circuit + a `route()→ContextBuilder.build()` chain asserting no `course/chapter/slide`
  leakage.
- **`RetrievalAgent` degrades safely on every failure mode** — monkeypatch fuzz (truncated
  JSON, empty, whitespace, JSON array, prose-with-braces, `TimeoutError`, `RuntimeError`)
  → every case returns `CONVERSATION` / `retrieve_needed=False` / safe default route.
  Timeout is 120 s (generous for CPU 7–8B).
- **`StructuredTableSearch`** — real FTS5, soft-deleted rows excluded via a base-table JOIN,
  `chunk_type="structured_record"`, injection fuzz (`"; DROP TABLE`, unbalanced quotes,
  `(((`, `NEAR`, `^$`) → no crash, no injection (tokens quoted, params bound).
- **Timing** — `router.yaml` carries both budgets; the 15,000 ms `orchestrator.yaml`
  timeout is genuinely deleted (comments only). Measured on a 300-chunk corpus:
  `vector_store.query` **0.16 ms** (budget 200), `route()` **~25–33 ms** (budget 3,500).
- Lint + full suite green; commit messages accurate.

### CONCERN
1. **`pyproject.toml` ruff ignore `"src/retrieval/context_builder.py" = ["F841"]` —
   audit agent claimed stale; DISPROVED.** `[DOCUMENTED — no change; finding rejected]`.
   The agent's evidence (`ruff check --select F841 src/retrieval/context_builder.py` →
   clean) was invalid: per-file-ignores still apply under `--select`, so the check was
   masking the very finding it was meant to test. A full `ruff check .` with the line
   removed *does* fire — `TokenCounter.count_tokens_batch`'s dead `all_tokens` accumulator
   is char-pinned by `tests/retrieval/test_context_builder_token_counter.py` (Phase 0).
   The ignore is correct; the `pyproject.toml` comment was updated to record why it must
   stay. **Lesson:** verify a "stale suppression" claim by running the *full* configured
   lint, not a scoped `--select` (which the per-file-ignore still filters).
2. **`hybrid_search.py` / `reranker.py` grandfathered wholesale under `mypy ignore_errors`
   despite a substantial rewrite** — see the cross-cutting CONCERN. `[DOCUMENTED]`.
3. **HYBRID `_merge` over-corrects for a single structured hit.** `[DOCUMENTED — not fixed,
   with rationale]` — Severity: LOW-MEDIUM. `_normalize` on a 1-element list hits the
   `hi == lo` branch → `score = 1.0`, so *any* single structured match (even a 1-token FTS
   hit) normalizes to 1.0 and, via the structured-first tie-break, **always ranks #1 in
   HYBRID mode regardless of match quality**. Symmetrically, with ≥2 structured hits the
   weakest normalizes to 0.0. The spec's intent ("ties to the structured record") is
   honored but the fix trades "structured always buried" for "structured always on top."
   **Not fixed here** because the right correction (blend raw + normalized score, or gate
   the structured tie-break on a raw-score floor) is a retrieval-quality change that should
   be made against the Phase 4 eval harness, not blind. Tracked for Phase 4 /
   retrieval-quality tuning.
4. **`RetrievalAgent._fallback` can echo model garbage to the user** — the scrub only
   rewrites responses that `startswith("{")` or `"```"`; a JSON array or brace-containing
   prose leaks the raw string as the user-facing `response` (routing stays safe).
   `[FIXED]` — added `startswith("[")` and a length/shape sanity check.
5. **The "3.5 s end-to-end" budget is asserted against a `route()`-only measurement** that
   stubs the cross-encoder and omits the agentic Ollama call (the two dominant CPU costs).
   `[DOCUMENTED]` — the test file is transparent that the authoritative 10k-chunk benchmark
   is Phase 2 Step 2.5; a labeling/scope issue, not a regression. The `<200 ms` vector-search
   target *is* meaningfully validated.
6. **No production composition root yet** wires `agent → router → context → generation`;
   the end-to-end path lives only in tests. `[DOCUMENTED]` — consistent with the
   router-centric lean architecture; assembly is a later phase.
7. Field named `topics`; spec task text says `extracted_topics`. Internally consistent,
   just a naming deviation.

### FINDING
None rising to blocking severity — every Step 1.3 acceptance criterion is met and
independently reproduced.

---

## Step 1.4 — Generation Pipeline Rewrite + Eval Set + CI Gates (1.4a code, 1.4b eval)

### PASS
- **`RetrievedChunk` / `CitationLocationType` fully deleted** — zero live references
  (hard acceptance criterion). Every other scrapped symbol (`estimate_deepseek_cost`,
  `AssemblyStrategySelector`, `TOPIC_EXTRACTION`, `QUIZ_GENERATION`, `FOOTNOTE`,
  `APPENDED`, `include_source_prefix`, …) has zero live references.
- **Refusal-phrase / `REFUSAL_PATTERNS` coupling is atomic** — the new phrase
  (*"I don't have anything about that in our past conversations or your notes."*) and the
  regex change land in the **same commit** (`12327fb`); `re.search` against the literal
  phrase matches.
- **`conversation_history` is a real, tested prompt section** — `_format_history()`,
  oldest-first, `[User]:` / `[Assistant]:` labels, `## RECENT CONVERSATION` heading; the
  empty-vs-populated unit test is non-superficial.
- **`GroundingValidator.llm_check` implemented** — one `simple_generate` call, JSON-with-
  regex fallback, returns `None` (→ keyword-overlap fallback) on any exception. Default
  method is `keyword_overlap` (llm_check opt-in — a disclosed deviation that keeps tests
  offline).
- **`MetricsStore` migration executed** against a synthetic `cost_usd` DB: `compute_ms`
  added, `cost_usd` dropped (SQLite 3.38.4), rows preserved, idempotent on 2nd init.
  In-`__init__` `ALTER TABLE`, not a numbered migration, per spec.
- **`eval/gates.json` — 4 real gates**, hash matches the golden set, `_meta.model` matches
  CI. **`golden_qa_set.json`** — 86 items (≥50), all 5 action types, all 4 disambiguation
  tiers, 11 paraphrase-recall (≥10), 20 unanswerable (≥20), 15 temporal-answerable (≥10);
  `_meta.counts` verified by independent item-by-item count.
- **`run_eval.py` scorers each compute what their name says** — none stubbed, none
  hard-coded to 1.0. `bootstrap_pipeline()` seeds from `seed_corpus.json` with
  `simple_generate` monkeypatched — no Ollama.
- **`docs/eval_review.md` is a genuine adversarial second-review** — 35-item ground-truth
  sample, per-item seed table, hand-recomputed calibration numbers, 8 tracked non-blocking
  follow-ups. "Sign-off with follow-ups," not a rubber stamp.
- `sample_lecture.pptx` removed in the prompt-rewrite commit; end-to-end generation test
  produces a `GenerationResponse` with a `[Session s7 · approx. …]` citation; full suite +
  lint green.

### CONCERN
1. **`post_processor.py` still defines `CitationExtractor` and
   `PostProcessor._match_citation_to_chunk`** — the 1.4a task list says "Delete SCRAP: …
   CitationExtractor, _match_citation_to_chunk," but they were rewritten in place to
   session-temporal semantics rather than deleted. Coherent (Step 2 still needs
   extract-then-match, which the same task list also mandates), and the commit message never
   claims deletion — but it leaves scrapped class *names* that will mislead a future
   grep-based audit. `[FIXED]` — renamed to `SessionCitationExtractor` /
   `_match_citation_to_session` with a one-line "renamed-from" note.
2. **`MetadataBasedClassifier` was deleted, not "rewritten … update `section_headers`"** as
   the roadmap task literally says. `[DOCUMENTED]` — a consistent extension of the
   pre-blessed assembly-strategy deletion (no assembly layer → nowhere for a chunk-ordering
   classifier to live); disclosed in the commit message; zero code references the deleted
   names.
3. **The 5 new action-type prompt templates are authored and loadable but not wired by any
   production caller.** `[DOCUMENTED]` — Severity: LOW-MEDIUM. `reminder_confirmation` etc.
   are valid YAML, load via `ModeConfig(prompt_id=…)`, but the already-committed Step 1.5
   handlers use their own inline `simple_generate` prompts and never touch these files, so
   their "consumed by Step 1.5 <Handler>" header comments are not (yet) true. The 1.4 spec
   only requires *authoring* them — not a criterion breach. **Recommendation:** wire them in
   when the Phase 3 orchestrator routes action-type responses, or soften the headers to
   "intended for." Header comments softened in remediation.
4. **`observability/tracing.py` never got the `compute_ms` span field.** `[FIXED]` —
   Severity: LOW. The spec task says "`observability/tracing.py` Langfuse span records
   `compute_ms` instead of `cost_usd`." `12327fb` removed the orchestrator's `cost_details`
   span payload and did not substitute `{"compute_ms": …}`. `compute_ms` *is* persisted via
   `MetricsStore` (the important path); only the Langfuse span lost the field. **Fix:**
   `GenerationOrchestrator._generate_impl` now calls `span.update(metadata={"compute_ms": …})`.
5. `scripts/simulate_traffic.py` import-broken (`eval.run_ragas_eval`, `src.common.pricing`).
   `[DOCUMENTED]` — same disclosed deferral as 1.2-F3.
6. **"Enforced from Phase 4" for `faithfulness` / `agentic_routing` is prose-only.**
   `[DOCUMENTED]` — Severity: LOW. `check_gates` evaluates all 4 gates unconditionally; a
   manual `eval` job run today would report FAIL (routing ~0.61 < 0.9). Safe only because
   that job is `workflow_dispatch`-only and the gap is thoroughly disclosed in
   `gates.json._meta` + `eval_review.md`. **Recommendation:** an `"enforced_from_phase"`
   field per gate that `check_gates` honors (deferred to the Phase 4 gate-enforcement step).
7. **`paraphrase_recall` "shares no keywords with the stored value" is over-stated for
   ~5–6 of 11 items** (q044 "trip", q045 "Portugal", q050 "plants", …). `[DOCUMENTED]` —
   independently confirmed; already disclosed as Follow-up 1 in `eval_review.md`. The items
   remain valid paraphrase tests (the *operative* term is disjoint).
8. **Cosmetic:** mojibake (`â€”`) in `golden_qa_set.json._meta.description`;
   `README.md` wholesale stale. `[FIXED]` — a top-of-file "out of date, see audits/"
   banner added to `README.md` (a full rewrite is a Phase 5 deliverable). The JSON
   mojibake is `[DOCUMENTED — not fixed]`: correcting it changes the file's bytes and
   therefore its sha256, which would fail the harness's drift-guard until `gates.json`
   is re-stamped — not worth a recalibration entry for a description string.

### FINDING
None above CONCERN. No hard acceptance criterion is breached.

---

## Step 1.5 — Feature Handlers (1.5a synchronous, 1.5b reminder + scheduler + Toast)

### PASS
- **On-launch reconciliation** — two independent SELECTs; **separate single-mode tests**
  (missed-fire-only, fired-unacked-only). Falsification from freshly-built DBs: missed-only
  → `overdue` only; fired-only → `pending` only; fired-then-dismissed → neither. The
  `dismissed_at IS NULL` guard beyond the spec's literal query is *required* by the spec
  prose ("leaves either state only through complete / dismiss / reschedule").
- **Schedule conflict detection** — half-open-interval overlap; 9 geometries falsified
  (exact / partial-start / partial-end / contains / inside / zero-duration → `ScheduleConflict`
  returned, **zero rows written**; adjacent end==start → correctly no conflict). Global
  (not per-date), deliberate and documented. `update` path identical.
- **MeetingNote validation** — monkeypatched `simple_generate`: decisions present →
  `needs_review=False`; empty decisions **and** action_items → `needs_review=True` + raw
  transcript stored; malformed JSON → 3 calls then `needs_review=True`. No `response`/`reply`
  field on the dataclass. `searchable_text` flattened + written by the handler (proven via
  FTS5 MATCH).
- **Summary idempotency + timestamp** — regenerating keeps the same `id`, 1 active row,
  `scheduled_at` unchanged (the intended time), `generated_at` advanced. Backed by a real
  partial unique index `idx_summaries_period … WHERE deleted_at IS NULL` in the DDL.
- **Toast bridge** — `create_reminder`'s bridge call is *outside* the DB transaction; a
  bridge failure leaves the reminder with `toast_id=NULL` (documented benign, covered by a
  reconcile test that keys on time not toast_id). `InMemoryToastBridge` records every call
  (the "mock WinRT" double). `update` / `delete` / `reschedule` cancel + re-register.
- **Scheduler thread** — connection opened *inside* `run()` (thread-affine); stop is
  `_stop_event` (no `Thread._stop` shadow); each `_tick` half wrapped separately; `fire_due`
  gates on `fired_at IS NULL` (idempotent). Real-thread test at 0.05 s poll fires once and
  joins cleanly.
- **Scoped-out deferrals genuine** — no `AgenticOutput` / slot-extraction / dispatcher code
  in `src/features/` (only the `__init__.py` docstring deferring it to Phase 3);
  `toast_bridge.py` is ABC + 2 test doubles, no half-built WinRT; `src/common/ipc/` is
  schema-only (no transport). Historical `448 passed` count verified in a worktree.

### CONCERN
1. **Schedule conflict check is check-then-act, not one transaction** — `_overlapping()`
   runs its SELECT before the `with self._conn:` write block. Harmless under the actual
   runtime (one backend connection, handlers single-threaded, scheduler thread never writes
   `schedule_items`); a real TOCTOU only if two writers hit `schedule_items` concurrently.
   `[FIXED]` — a one-line comment noting the single-writer assumption was added.
2. **`fire_due` fires toasts before its single post-loop commit** — a crash or a raising
   `fire_toast` mid-loop leaves some `fired_at` writes uncommitted while their toasts
   already showed → those reminders re-fire on the next tick. Fallback path (the OS toast is
   primary), low harm. `[DOCUMENTED]` — recommend committing per-row or before `fire_toast`
   when this is next touched.
3. **Empty-day summary behaviour differs by entry point** — `generate_daily_summary`
   writes a row for an empty day; the scheduler's `generate_due_summaries` skips empty days.
   Both defensible; slightly inconsistent and undocumented. `[DOCUMENTED]`.
4. **"Atomic single transaction" wording** — `create_reminder` uses two transactions (row
   INSERT+commit, then `toast_id` UPDATE+commit) around the external bridge call. The row
   *creation* is a single atomic statement and the ordering holds — a faithful
   interpretation. `[DOCUMENTED]`.

### FINDING
No correctness findings. Nothing above LOW-severity concern.

---

## Step 1.6 — Dynamic Model Management

### PASS
- **Three-state interruption guarantee holds under adversarial pull scripts.** Drove
  `_run_download` with a scripted `PullStreamer` on cases the committed tests don't cover:
  verify-always-fails → terminates after `_MAX_RESTARTS` with a specific `ModelDownloadError`
  after `_safe_delete`; never-recovers → hits `_MAX_ATTEMPTS`, cleans up, raises an
  actionable error; alternating `_LayerInconsistent` / `PullInterrupted` → the **single
  `attempts` counter** bounds the whole loop (the claimed fix is real). All four
  `raise ModelDownloadError` sites are preceded by `_safe_delete` (traced).
- **`switch_active_model` takes effect on a live router with no restart** — `active_model()`
  re-reads `app_config.json` every call, no caching. Verified: set A → `generate()` routes to
  A; `switch_active_model("B")` → same instance routes to B.
- **App-config is authoritative over `$OLLAMA_DEFAULT_MODEL`** — with the config absent and
  `OLLAMA_DEFAULT_MODEL` exported, `generate()` returns `error_code="no_model_active"` and
  the adapter is never called; `model_setup_required()` → True.
- **`AppConfig.load()` never raises** — fed `{{{`, `[]`, `""`, `12345`, a bare string, an
  unexpected object, a directory path — every one falls back to first-launch defaults with
  a logged warning. `save()` is atomic (`tmp` + `os.replace`), no `.tmp` residue.
- **Router error mapping** — every provider exception (`OllamaNotRunningError`,
  `ModelNotDownloadedError`, `ModelNotLoadedError`, `ProviderTimeoutError`, `ProviderAPIError`,
  bare `Exception`) → a structured `InferenceResult(ok=False, error_code=…, message=…)`,
  never a raised exception; `KeyboardInterrupt` correctly propagates.
- **Standalone scope intact** — `resolve_default_model()` is referenced nowhere but its own
  definition; `simple_generate` and `src/generation/config.py` still read
  `$OLLAMA_DEFAULT_MODEL` directly.
- **Catalog** — valid JSON, 6 well-formed entries, no network imports, committed, not
  gitignored. `.gitignore` `/data/` and `.gitattributes` `models/**` LFS rule don't catch
  anything they shouldn't (`src/models/` filter → `unspecified`). Live-Ollama read-only
  smoke passes. Lint clean, no new `pyproject.toml` suppressions. 37 new / 485 total
  verified via `--collect-only`.

### CONCERN
1. **`ProviderTimeoutError` maps to the generic `inference_failed` message** ("Something
   went wrong generating a response. Please try again."). The team explicitly chose this,
   and the message is user-readable (not a stack trace), so it's not a spec violation — but
   on CPU inference a timeout is the single most likely failure and the message gives no
   actionable hint. `[DOCUMENTED]` — recommend a timeout-specific message ("the model is
   taking too long — try a smaller model") when the router is wired to callers in Phase 3.
2. **Incoherent message sequence on one mixed-failure path** — a pull script that resumes
   3× then hits a bad digest emits `progress_callback(phase="restarting", …)` and then
   immediately raises "Download interrupted and couldn't be resumed." Both terminal states
   are *clean* (`_safe_delete` runs; no leftover artifacts), so the on-disk guarantee is
   intact — only the message the user sees is contradictory. `[FIXED]` — the post-loop arm
   now distinguishes "was mid-restart" from "was mid-resume" and emits a matching message.

### FINDING
1. **A raising `progress_callback` bypasses cleanup and surfaces a raw exception.**
   `[FIXED]` — **Severity: MEDIUM** (LOW real-world likelihood — the callback is supplied by
   the Phase 3 orchestrator/IPC layer and a raise there is a programming error — but a
   literal breach of "NEVER an ambiguous partial state").
   **Repro:** `download_model(m, cb)` where `cb` raises on the first `downloading` event →
   the exception propagates unmodified out of `download_model` (no `except` clause matches),
   `_safe_delete` is never called, and a partially-pulled model can be left in Ollama's
   store while the caller sees a bare `RuntimeError`. `active_downloads` *is* cleaned (the
   `try/finally`). **Fix:** `_run_download`'s `progress_callback` invocations are wrapped;
   a callback exception now triggers `_safe_delete` and is re-raised as a `ModelDownloadError`
   after cleanup, so the terminal state is always one of the three clean outcomes.
   Regression test added.
2. **`download_model` adds the un-normalized model name to `active_downloads`.**
   `[FIXED]` — Severity: LOW-MEDIUM. A tagless download (`"mistral"`) + a tagged status
   query (`"mistral:latest"`) misses `DOWNLOADING` (reports `NOT_INSTALLED` / `AVAILABLE`).
   Low real-world impact (catalog entries are all fully tagged). **Fix:**
   `add`/`discard` now use `normalize_model_name(model_name)`; test added.
3. **Disk-full detection is a fragile substring match.** `[FIXED]` — Severity: LOW. The 3
   spec-listed variants ("no space left on device", "ENOSPC", "not enough space…") are all
   caught, but e.g. `"not enough disk space available"` (word `disk` between) is classified
   as `_LayerInconsistent` → a spurious clean-restart. **Fix:** the disk-full branch now
   matches a `not enough .*space` regex plus `"no space left"` / `"disk full"`.
4. **`get_model_status` reports `DOWNLOADING` for an already-installed model mid-re-pull.**
   `[DOCUMENTED — not fixed]` — Severity: LOW. The `active_downloads` check is first,
   before the installed check. Only reachable during a re-pull of that exact name; the
   report is defensible (the model *is* being updated). No fix — flagged for completeness.

---

## Summary

| Step | PASS themes | Findings (fixed) | Findings (documented) | Concerns |
|---|---|---|---|---|
| 1.1 | interface boundary, `delete()` eviction, local embedder, Ollama errors, registry | 3 (integrity check; return annotations; model-default test) | — | 4 (concurrency, multi-instance staleness, frozen override, over-match) |
| 1.2 | `parser.py` gone, sliding-window provably correct, end-to-end ingest | 1 (empty-timestamp prefix) | 3 (dead token config; broken script; shrunk-session orphans) | 4 (spec-wording, extra fields, "384" error, dead alias) |
| 1.3 | demolition complete, `key_topics` bug not repeated, pinned helpers byte-identical, FTS5 injection-safe | 1 (`_fallback` scrub) | 1 (HYBRID over-correction) + 1 rejected (ruff ignore is load-bearing) | 6 (mypy suppressions, timing scope, no composition root, …) |
| 1.4 | doc-era types deleted, refusal coupling atomic, gates real + hash-matched, genuine 2nd review | 3 (scrapped names renamed; `compute_ms` span; README) | 4 (classifier deletion; templates unwired; Phase-4 prose gate; paraphrase overstatement; JSON mojibake) | — |
| 1.5 | reconciliation independence, conflict geometry, meeting-note validation, summary idempotency | 1 (TOCTOU comment) | 2 (fire-before-commit; empty-day inconsistency) | 4 |
| 1.6 | three-state guarantee under attack, no-restart switch, app-config authoritative, crash-safe config | 3 (callback cleanup — MEDIUM; name normalization; disk-full regex) + 1 concern (message coherence) | 1 (re-pull status) | 2 |

**Net result:** Phase 1 is re-verified, not just originally-verified. All six steps meet
their spec and acceptance criteria; the document-era shared type contract is permanently
and completely deleted; the sliding-window chunker, the FTS5 structured path, the agent
fallback, the three-state download guarantee, and reminder reconciliation all hold under
adversarial testing. One MEDIUM finding (download cleanup on a raising callback) and a set
of LOW findings were fixed with regression tests; the judgment calls (HYBRID merge tuning,
the dead chunk-token config decision, the retrieval-layer mypy suppressions) are documented
with rationale for the step owner. Post-remediation the suite is at **500 tests passing**
(485 + 15 audit regression tests), all lint / type / format gates clean.

All remediation is in the "Phase 1 audit + remediation" commit, on top of the eight
Phase 1 commits. Fixed: 1.1-F1/F2/F3, 1.2-F2, 1.3-C4, 1.4-C1/C4/C8, 1.5-C1, 1.6-F1/F2/F3/C2,
plus comments for the documented deferrals (1.2-F4, 1.4-C3, 1.5-C1). One audit finding was
investigated and **rejected** (1.3-C1 — the ruff ignore is load-bearing).
