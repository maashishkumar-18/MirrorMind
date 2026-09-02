# Personal AI Companion — Production Roadmap

**Version:** 1.2
**Status:** Final Engineering Blueprint
**Prerequisite:** Read `project_logic.md` before this document.

**Changelog:**
- v1.0 — Initial draft from Decision Lock
- v1.1 — Incorporates: Decision Lock 4-issue amendment (`memories` table dropped from DDL, `schedule_items` added to data export, `session_chunks` exclusion from export stated with explicit rationale, proactive export prompting defined as v1 product requirement, structured-table retrieval mechanism defined with FTS5 three-path routing, single-instance mutex and uninstall data-retention policy locked); roadmap 7-item review (refusal-rate CI gate corrected from one-sided ceiling to two-sided band — floor explicitly protects against hallucination regression; second-reviewer sign-off gate added to Step 1.4 eval set authoring; model download interruption handling defined with three-state guarantee in Step 1.6; phase numbering collision eliminated — former Phase 1.5 Non-Functional Hardening promoted to Phase 2, all subsequent phases renumbered Phase 3–6); grep verification pass confirming zero stray phase labels, zero `memories` references, `schedule_items` present at all required locations, two-sided refusal-rate band consistent across Step 1.4 and Phase 4 gate table
- v1.2 — Resolves two implementation-spec ambiguities surfaced by code-level audit review: (1) sliding-window sub-chunking algorithm pinned explicitly — window starts at multiples of `stride` beginning at 0, full-window-only rule, trailing undersized windows dropped (primary chunk covers the tail instead); Step 0.4 deliverables gain a required "Chunking Algorithm" section in `docs/schema_review.md`; Step 1.2's acceptance criterion corrected from "primary + 3 sub-chunks" to "primary + 4 sub-chunks" for a 25-message session, with explicit per-chunk index assertions rather than a bare count assertion. (2) `MetricsStore` schema versioning decoupled from the session-DB migration sequence — `cost_usd` → `compute_ms` is now an idempotent in-process `ALTER TABLE` in `MetricsStore.__init__` (column-existence check, `DROP COLUMN` on SQLite ≥ 3.35.0 else left as nullable orphan), not a `0002_cost_to_latency.sql` file in the session-DB migration runner. The session-DB migration sequence remains `0001_initial_schema.sql` only as of this version.

---

## Phase and Milestone Overview

| Phase | Content | Gate to Proceed |
|---|---|---|
| Phase 0 | Bug fixes, characterization tests, schema design and freeze | Schema reviewed and frozen; all characterization tests passing; CI green |
| Phase 1 | Full pipeline migration (ingestion, retrieval, generation), feature handlers, model management | All pipeline tests passing; document-era types deleted; feature handlers integration-tested; golden eval seeded and reviewed |
| Phase 2 | Non-functional hardening: encryption, backup/restore, crash recovery, accessibility, performance benchmarking | WACK pre-check passing; accessibility audit clean; all performance ceilings met on reference hardware |
| Phase 3 | Frontend implementation (all views, IPC client, settings, diagnostics) | All frontend views functional; IPC contract verified; first-launch flow operational |
| Phase 4 | System integration, end-to-end tests, golden eval pass, reliability testing | E2E tests passing; RAGAS eval gates passed; 100/100 fuzzing cycles clean |
| Phase 5 | Packaging, signing, WACK pre-certification, Store submission | WACK clean pass; Store submission accepted; staged rollout live |
| Phase 6 | Monitoring, observability verification, production readiness review | Crash-free rate ≥ 99.5%; rollout expansion decision made |

---

## Phase 0 — Foundation, Bug Triage, and Schema Design

**Objective:** Establish the engineering foundation before any migration work begins. Fix the pre-existing bugs that would otherwise be mistaken for migration regressions. Write characterization tests that pin current behavior for every component that will be modified. Design and freeze the session schema and shared type contracts so that all three pipeline rewrites in Phase 1 build against a stable target.

**Why this phase must complete before Phase 1 begins:** The RAG audit identifies `RetrievedChunk` and the structured-table schema as the two highest-risk design decisions — changing either mid-migration causes coordinated breakage across all three pipelines. Designing them first, in isolation, with explicit cross-pipeline review, is the only way to guarantee Phase 1 can proceed incrementally rather than as a single giant coordinated commit.

---

### Step 0.1 — Repository Preparation and Tooling

**Tasks:**

Set up the Python testing infrastructure. Install `pytest`, `pytest-cov`, and `pytest-asyncio`. Create the `tests/` directory structure mirroring `src/` (currently empty — the entire test suite consists of unassertive `__main__` blocks). Configure `pytest.ini` with coverage thresholds, test discovery paths, and markers for `unit`, `integration`, and `characterization` test categories.

Set up the GitHub Actions CI workflow on a `windows-latest` runner for all test stages. Configure the protected-branch-only signing workflow (no signing secrets accessible to PR-level CI). Add the WACK pre-check as a named, manually-triggered job on the release branch — automated execution required before every Store submission, not on every commit.

Set up pre-commit hooks: `black`, `ruff`, `mypy` for the Python backend; `eslint` + `tsc --noEmit` for the TypeScript frontend. All linting gates must pass before a PR can merge.

Establish the versioned IPC envelope schema. Define the base Pydantic message model (Python) with an explicit `version` field and message type discriminator. Define the corresponding zod schema (TypeScript). Write the version-check middleware that fails loudly on mismatch. This schema is frozen before any frontend or backend IPC work begins in later phases.

**Deliverables:**
- `pytest` infrastructure with directory structure and configuration
- GitHub Actions CI workflow (`windows-latest`, test + lint gates)
- Protected-branch signing workflow (structure only, no secrets yet)
- Pre-commit hook configuration
- Base IPC envelope schema (Pydantic + zod) with version-check middleware

**Acceptance Criteria:**
- `pytest` discovers and runs zero tests without configuration errors
- CI workflow runs on push to main and on PRs; all lint checks pass on the existing unmodified codebase
- IPC envelope schema round-trips correctly between a Pydantic model and its zod equivalent in a standalone test

**Exit Criteria:** CI is green. IPC schema is merged and frozen.

---

### Step 0.2 — Pre-existing Bug Fixes

**Tasks:**

**Bug 1 — `_fallback_search`/`_fallback_reranker` broken imports and mismatched kwargs** (`src/retrieval/orchestrator.py:743-808`): Fix the import paths (`from hybrid_search import SearchResult` → `from src.retrieval.hybrid_search import HybridSearchResult`) and correct the `RerankerResult(...)` constructor kwargs to match the real dataclass fields. Write a unit test that instantiates both fallback paths and verifies they do not raise `ImportError` or `TypeError`. Mark this test `characterization`.

**Bug 2 — `simple_generate()` hardcodes `DeepSeekAdapter()` directly** (`src/common/llm_client.py:722-766`): Modify `simple_generate()` to accept an optional `provider` parameter and resolve it through `ProviderRegistry`. Default to the registered default provider (which will become `OllamaAdapter` in Phase 1; for now, keep `DeepSeekAdapter` as the registered default so behavior is unchanged). Write a unit test that mocks `ProviderRegistry` and verifies `simple_generate()` calls `registry.get_provider()` rather than directly instantiating `DeepSeekAdapter`. Mark this test `characterization`.

**Verification — debug server `AttributeError`** (`tools/retrieval_debug_server.py:101-119`): Verify by code inspection (not assumption) that the `c.page_start`/`c.page_end` references are in code that will be fully replaced by the `RetrievedChunk` schema change in Phase 1 Step 1.3. Document this with a code comment: `# BUG: references non-existent attributes. Will be resolved by RetrievedChunk schema migration in Phase 1 Step 1.3. Do not add a workaround here.` Do not add a workaround.

**Deliverables:**
- Fixed `orchestrator.py` fallback paths with passing characterization test
- Fixed `simple_generate()` with registry resolution and passing characterization test
- Documented (not fixed) debug server attribute bug with forward-reference comment

**Acceptance Criteria:**
- Both characterization tests pass
- No behavior change to any working code path — only broken dead-code paths are fixed
- CI is green

**Exit Criteria:** Both bugs fixed, both tests passing, CI green.

---

### Step 0.3 — Characterization Tests for KEEP-AS-IS and MODIFY Components

**Objective:** Pin the current behavior of every component that will be modified in Phase 1, before it is touched. These tests exist to catch regressions — they test "the output is what it currently is," not "the output is what it should be."

**Components Requiring Characterization Tests (priority order):**

`BM25Index` (`src/retrieval/hybrid_search.py:188-303`): Feed a fixed corpus of 10 documents and 5 queries. Assert exact rank order and BM25 scores match a pre-computed fixture.

`CrossEncoderReranker` (`src/retrieval/reranker.py:323-478`): Feed a fixed set of query-chunk pairs. Assert scores fall within a tolerance band of a pre-computed fixture. Assert rank order is stable.

`HybridSearch` score fusion functions — `_weighted_merge`, `_reciprocal_rank_fusion`, `_normalize_scores`, `_deduplicate` (`src/retrieval/hybrid_search.py:846-988`): Pure functions. Write input/output fixture tests. Assert exact outputs for known inputs. These are the most critical to pin — any unintended change in score fusion behavior would be silent.

`SemanticCoverageScorer` (`src/retrieval/confidence.py:410-571`): Feed fixed query/context pairs with `use_embeddings=False` (default). Assert score ranges and threshold classification outputs match fixtures.

`GenerationOrchestrator` request lifecycle (`src/generation/orchestrator.py:114-415`): Mock `LLMClient` and `PostProcessor`. Assert the orchestrator correctly sequences request ID generation, timing, tracing span creation, and error response construction.

`PromptBuilder._substitute_with_validation` (`src/generation/prompt_builder.py:815-847`): Pure function. Write fixture tests for all variable-present, variable-missing, and partial-missing cases.

`TemplateLoader` (`src/generation/prompt_builder.py:527-618`): Write fixture tests for YAML loading, caching, versioning, and `RAGPIPE_PROMPT_TEMPLATES_DIR` override behavior.

`AnswerCleaner` (`src/generation/post_processor.py:268-357`): Pure text function. Write fixture tests for `[HIDE]` stripping, `<THINK>` stripping, whitespace normalization, and truncation.

`MetricsStore` (`observability/metrics_store.py`): Write fixture tests for insert, query, and the dataclass-to-row mapping. Assert WAL mode is active. Assert schema columns match the expected set.

`TokenCounter` — both instances (`src/retrieval/context_builder.py:223-281` and `src/generation/prompt_builder.py:478-521`): Write fixture tests for known strings with known token counts. Assert char-based fallback activates correctly when tiktoken is unavailable.

**Deliverables:**
- Characterization test suite covering all components listed above
- All tests marked with `@pytest.mark.characterization`
- Fixture data stored in `tests/fixtures/` as JSON files (not hardcoded in test functions)

**Acceptance Criteria:**
- All characterization tests pass against the current unmodified codebase
- Coverage report shows each listed component is exercised
- No test asserts anything about document-era behavior that would fail after the migration

**Exit Criteria:** All characterization tests passing. CI green. This is the safety net for all of Phase 1.

---

### Step 0.4 — Session Schema Design and Cross-Pipeline Review

**Objective:** Design and freeze the complete database schema and shared type contracts before any pipeline code is written. This is the single highest-leverage design decision in the entire project.

**Tasks:**

**Design the full SQLite schema.** Write complete DDL for all 10 tables: `sessions`, `messages`, `session_chunks`, `reminders`, `meeting_notes`, `schedules`, `schedule_items`, `todos`, `summaries`, `sync_metadata`. Every table must have `id`, `created_at`, `updated_at`, `deleted_at`. Every table must have a `sync_metadata` column (nullable JSON, inert in v1). Include FTS5 virtual table definitions for `reminders`, `todos`, `meeting_notes`, `schedule_items`. Include a `schema_migrations` table and an idempotent, forward-only migration runner.

**Design `session_chunk` metadata schema.** Define the exact column set for `session_chunks`: `id`, `session_id` (FK), `chunk_type` (primary | sub_chunk), `window_start_message_idx`, `window_end_message_idx`, `content` (raw text), `embedding` (BLOB, raw float32 array), `token_count`, `topics` (JSON array), `action_types` (JSON array), `entities` (JSON array), `sentiment` (TEXT), `created_at`, `updated_at`, `deleted_at`.

**Pin the sliding-window sub-chunking algorithm.** Specify the exact algorithm in pseudocode, not just the two config numbers (`window_size`, `stride`). Canonical definition: given messages `M[0..N-1]`, window size `W`, and stride `S`, sub-chunks are generated only when `N > threshold` (default 20). Window start indices are `0, S, 2S, 3S, ...` for as long as `start + W ≤ N` — a window is created only if it is full (exactly `W` messages); a trailing window that would be undersized (`start + W > N`) is dropped, not padded and not included truncated. Worked example at `W=10, S=5, N=25`: starts `0, 5, 10, 15` are each full windows (`15 + 10 = 25 ≤ 25`), `start=20` is dropped (`20 + 10 = 30 > 25`) — 4 sub-chunks, not 3. Rationale for dropping rather than padding/truncating: the embedding model is not calibrated on variable-length conversational inputs, so an undersized window would sit in a different density region of the vector space than full-window chunks; the primary chunk (always present, always covering the full session including the tail) is the better home for tail content than a degraded partial sub-chunk.

**Design `SessionRetrievedChunk` dataclass.** Introduce in `src/common/types.py` alongside (not replacing) the existing `RetrievedChunk`. Fields: `chunk_id`, `session_id`, `content`, `raw_content`, `topics`, `action_types`, `entities`, `sentiment`, `timestamp`, `message_roles`, `chunk_type`, `parent_chunk_id`, `token_count`, `score`, `semantic_score`, `keyword_score`, `metadata_score`, `source_prefix`, `metadata`. Define `SessionCitationFormat` as a simple dataclass with `session_id` and `approximate_timestamp` — replaces `CitationLocationType`. No PAGE, SLIDE, SECTION, or CHAPTER enum variants.

**Design the agentic JSON output contract.** Define the Pydantic model for the structured JSON the Ollama LLM call must return: `action_type` (enum), `confidence` (float 0.0–1.0), `retrieve_needed` (bool), `retrieval_route` (enum: semantic | structured | hybrid), `search_query` (str, optional), `response` (str). This contract is the interface between the agentic reasoning layer and all downstream components.

**Cross-pipeline review.** With schema and shared types defined, walk through each pipeline (ingestion, retrieval, generation) and verify: every DB-reading and DB-writing component has what it needs; every `SessionRetrievedChunk` consumer has the required fields; the agentic output contract gives the Retrieval Router everything it needs to dispatch. Document any gaps and resolve them before declaring the schema frozen. Commit the review as `docs/schema_review.md`, including a dedicated "Chunking Algorithm" section containing the sliding-window pseudocode, the trailing-window drop rule, and the rationale above — this is the specification Step 1.2's unit tests are written against, not something Step 1.2 is free to redefine.

**Write schema migration infrastructure.** Implement the `schema_migrations` table and the migration runner. Migration `0001_initial_schema.sql` creates all 10 tables. The runner must: check which migrations have been applied, apply unapplied migrations in order, be idempotent, take a pre-migration snapshot before applying any migration.

**Write schema tests.** Test that: migration `0001` creates all expected tables with all expected columns; FTS5 virtual tables are queryable; the migration runner is idempotent; `SessionRetrievedChunk` can be constructed and serialized; the agentic output Pydantic model validates correctly and rejects malformed inputs.

**Deliverables:**
- Complete DDL for all 10 tables (`0001_initial_schema.sql`)
- Migration runner implementation
- `SessionRetrievedChunk` and `SessionCitationFormat` in `src/common/types.py` (alongside existing types — not replacing yet)
- Agentic output Pydantic model in `src/common/types.py`
- Schema tests (all passing)
- Cross-pipeline review document committed to `docs/schema_review.md`, including the "Chunking Algorithm" section (pseudocode, trailing-window drop rule, rationale)

**Acceptance Criteria:**
- All schema tests pass
- Cross-pipeline review document has no unresolved gaps
- The existing `RetrievedChunk` is untouched — old and new types coexist
- CI is green

**Exit Criteria:** Schema design reviewed and frozen. This is the gate to Phase 1. No pipeline rewrite begins until this step's exit criteria are met.

---

**Phase 0 Milestone:** Pre-existing bugs fixed. Characterization test safety net in place. Schema and shared contracts frozen. CI green. Engineering team is ready to begin migration work with a stable target and a regression detection system.

---

## Phase 1 — Backend Pipeline Migration

**Objective:** Rewrite all three RAG pipelines (ingestion, retrieval, generation) to operate on session-shaped data with local infrastructure (SQLite, `all-MiniLM-L6-v2`, Ollama). Every rewrite is incremental — no big-bang replacements. Characterization tests catch regressions. The old `RetrievedChunk` type is deleted only at the end of this phase, once all pipelines pass against the new type.

---

### Step 1.1 — Local Infrastructure: VectorStoreInterface, SQLite Implementation, Local Embedder

**Tasks:**

**Introduce `VectorStoreInterface` ABC** in `src/common/`. Define the abstract interface: `upsert(chunk: SessionChunkRecord) → UpsertResult`, `query(embedding: np.ndarray, top_k: int, filters: dict) → list[SessionRetrievedChunk]`, `delete(chunk_id: str) → bool`, `get_stats() → StoreStats`. Both ingestion (writes) and retrieval (reads) depend on this interface. Neither pipeline imports the concrete implementation directly.

**Implement `SQLiteVectorStore`** behind the interface. On initialization: open the SQLCipher-encrypted SQLite connection, load all embeddings from `session_chunks` into a numpy array in memory, register WAL mode, verify integrity. On `upsert`: write the chunk row to SQLite and append the embedding to the in-memory array. On `query`: compute cosine similarity between the query embedding and all in-memory rows using numpy, return top-k results as `SessionRetrievedChunk` instances. On `delete`: set `deleted_at` in SQLite, remove from the in-memory array, and rebuild the index mapping. Write unit tests using an in-memory SQLite database (not the encrypted production DB).

**Implement `LocalEmbeddingProvider`** — the `EmbeddingProvider` ABC implementation for `all-MiniLM-L6-v2`. Load the model from the bundled path (resolved relative to the PyInstaller frozen executable path). Implement `embed_batch(texts: list[str]) → np.ndarray` and `embed_query(text: str) → np.ndarray`. Token counting uses `AutoTokenizer` from `sentence-transformers` — not tiktoken. No retry/backoff (local in-process inference does not need it). No cost estimation. Register in `ProviderFactory` as `"local"`. Write unit tests: verify embedding shape is (N, 384), verify cosine similarity between semantically similar sentences is > 0.7, verify the model loads correctly from the bundled path under a mocked PyInstaller environment.

**Implement `OllamaAdapter`** in `src/common/llm_client.py`. Implement `ProviderAdapter` with local HTTP calls to the Ollama daemon (default `http://localhost:11434`). Handle new error semantics: `model_not_downloaded`, `model_not_loaded`, `ollama_not_running` — each produces a user-readable message, not a stack trace. Add `OllamaAdapter` to `ProviderRegistry._register_defaults()` as the default provider. Remove `DeepSeekAdapter`, `OpenAIAdapter`, and `GeminiAdapter` from the registered defaults (they remain as unregistered classes for v1.1). Write unit tests against a mock HTTP server. Write tests for each new error type.

**Update `EmbeddingGenerator`** default model from `"gemini-embedding-001"` to `"local"`. Simplify the retry/backoff wrapper — `"429"`, `"rate"`, `"quota"` retry conditions are irrelevant for local inference. Remove `estimate_cost`. Preserve the `embed_chunks` and `embed_query`/`embed_queries` interface unchanged so `hybrid_search.py` call sites require no modification.

**Deliverables:**
- `VectorStoreInterface` ABC in `src/common/`
- `SQLiteVectorStore` implementation with unit tests
- `LocalEmbeddingProvider` implementation with unit tests
- `OllamaAdapter` implementation with unit tests
- Updated `EmbeddingGenerator` with updated defaults and tests
- Updated `ProviderRegistry` with Ollama as default, cloud adapters unregistered

**Acceptance Criteria:**
- All new unit tests pass
- All Phase 0 characterization tests still pass
- `simple_generate()` routes through `OllamaAdapter` by default (verified by unit test)
- Local embedding produces correct shape and reasonable semantic similarity scores
- CI is green

**Exit Criteria:** Local infrastructure components implemented, tested, and integrated.

---

### Step 1.2 — Ingestion Pipeline Rewrite

**Tasks:**

**Remove SCRAP components.** Delete `src/ingestion/parser.py` entirely. Remove `CleanedDocument`, `_fix_line_breaks`, `get_cleaning_report` from `src/ingestion/cleaner.py`. Remove `_chunk_by_slides` and `_merge_small_chunks` from `src/ingestion/chunker.py`. Remove `unstructured` and `python-magic-bin` from `requirements.txt`. Verify via grep that no imports of these components remain anywhere in the codebase.

**Rewrite `DocumentMetadata` → `SessionMetadata`** in `src/ingestion/metadata_extractor.py`. Replace fields `filename, file_type, course_name, chapter_title, global_context, subject_area, key_topics, extraction_confidence` with `session_id, timestamp, action_types, entities, sentiment, topics, extraction_confidence`. Rewrite `_call_llm` prompt: academic document analyzer → session metadata extractor. Rewrite `_prepare_sample` to use message-role/turn boundaries (`[User]`/`[Assistant]`) instead of page/slide markers. Preserve `extract_with_retry` wrapper unchanged. Write unit tests with a mocked LLM call.

**Rewrite `Chunk` dataclass** in `src/ingestion/chunker.py`. Replace document fields with session fields: `session_id, message_roles, action_types, entities, sentiment, timestamp`. Replace `element_ids` with `message_indices` (list of message turn indices covered by this chunk). Keep generic fields unchanged: `chunk_id, content, chunk_type, parent_chunk_id, token_count`.

**Rewrite chunking strategy** in `src/ingestion/chunker.py`.
- Sessions ≤ 20 messages: one primary chunk (full session content).
- Sessions > 20 messages: primary chunk + overlapping sub-chunks (window=10, stride=5, from config), implementing the sliding-window algorithm pinned in Step 0.4's `docs/schema_review.md` exactly — window starts at multiples of `stride` beginning at 0, full-window-only, trailing undersized windows dropped.
- Replace `tiktoken.encoding_for_model("text-embedding-3-small")` with `AutoTokenizer` from `sentence-transformers`. Re-derive chunk-size bounds from `all-MiniLM-L6-v2`'s actual 384-token max sequence length. Bounds must come from config, not be hardcoded.
- Create `config/ingestion/chunker.yaml` with `window_size`, `stride`, `max_chunk_tokens`, `min_chunk_tokens` — applying the `from_yaml()` + `_apply_env_overrides()` pattern used throughout retrieval and generation config.
- Preserve sentence splitter (`_split_into_sentences`, `_fallback_sentence_split`, `_restore_abbreviations`) — domain-agnostic, reusable.
- Write unit tests for: primary-chunk-only path (≤ 20 messages), sub-chunk generation (> 20 messages) asserted by exact per-chunk message-index ranges (not just a count), overlap correctness (sub-chunk N and sub-chunk N+1 share `stride` messages), trailing-window drop behavior (a session length that leaves a final undersized window produces no chunk for it), token-count boundary behavior.

**Rewrite `EnrichedChunk`** in `src/ingestion/enricher.py`. Replace document metadata fields with session fields. Rewrite `_build_source_prefix` to produce: `[Session: {session_id} | {approximate_timestamp} | Topic: {primary_topic}]`. Rewrite `to_metadata()` to produce the session-shaped metadata dict. Preserve `get_validation_report`/`ValidationReport` pattern unchanged.

**Replace `VectorStore` with `VectorStoreInterface`** in ingestion. Import the interface, not the concrete implementation. Inject the concrete `SQLiteVectorStore` at the application composition root.

**Remove namespace strategy.** Delete `generate_namespace_from_document`/`sanitize_namespace`. Session isolation uses `session_id` as a direct filter column in the SQLite schema — no namespace indirection needed.

**Remove cloud embedding providers.** Delete `GoogleProvider` and `OpenAIProvider` from `src/ingestion/embedder.py`. `LocalEmbeddingProvider` is the only registered provider.

**Update `.env.example`.** Remove `PINECONE_*` (4 vars), `GEMINI_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`. Add `OLLAMA_HOST` (default `http://localhost:11434`), `OLLAMA_DEFAULT_MODEL`, `RAGPIPE_DB_PATH`, `RAGPIPE_EMBEDDING_MODEL_PATH`. Preserve all existing `RAGPIPE_*` env-override names.

**Deliverables:**
- Rewritten ingestion pipeline
- `config/ingestion/chunker.yaml`
- All ingestion-related unit tests passing
- Updated `requirements.txt` and `.env.example`

**Acceptance Criteria:**
- A synthetic session transcript can be ingested end-to-end: chunked, metadata-extracted, enriched, embedded, upserted to `SQLiteVectorStore`
- Primary chunk only for a 10-message session; primary + 4 sub-chunks for a 25-message session (window=10, stride=5): sub-chunk 1 covers messages 0–9, sub-chunk 2 covers messages 5–14, sub-chunk 3 covers messages 10–19, sub-chunk 4 covers messages 15–24, no trailing partial window is created — verified by explicit index assertions in the unit test, not just a count assertion
- All Phase 0 characterization tests still pass
- No imports of `pinecone`, `google.genai`, or `openai` remain in any ingestion file
- CI is green

**Exit Criteria:** Ingestion pipeline fully rewritten and tested. Session data can be ingested into local SQLite with local embeddings.

---

### Step 1.3 — Retrieval Pipeline Rewrite

**Tasks:**

**Migrate `RetrievedChunk` → `SessionRetrievedChunk` in retrieval.** Update all retrieval call sites to use `SessionRetrievedChunk`. The old `RetrievedChunk` remains in `src/common/types.py` — not deleted yet (generation still uses it). This step migrates retrieval only.

**Replace `VectorStore` import in `HybridSearch`** with `VectorStoreInterface`. Inject the concrete `SQLiteVectorStore` at the application composition root. Remove the direct `from src.ingestion.vector_store import VectorStore` import — this cross-pipeline coupling is eliminated here.

**Replace cloud `EmbeddingGenerator`** in `HybridSearch` with the updated `EmbeddingGenerator` defaulting to `LocalEmbeddingProvider`. No call site modification required.

**Replace `_semantic_search` in `HybridSearch`** with a `VectorStoreInterface.query()`-backed implementation. BM25, score fusion, reranking, and deduplication are unchanged.

**Remove SCRAP retrieval components:**
- Delete `pipelines/learning.py`, `planner.py`, `qa.py`, `quiz.py`, `revision.py` and the `PIPELINE_REGISTRY`
- Delete study-material `context_builder.yaml` templates and classification heuristics
- Remove `ContextBuilder._apply_diversity_filtering` (keys off `page_start`/`chapter_title`)
- Remove `ConceptRegistry._load_defaults` (CS-course vocabulary)
- Remove `QueryRewriter._load_technical_terms` and `INTENT_TEMPLATES` (academic intents)
- Remove `CitationLocationType` from the retrieval layer (replaced by `SessionCitationFormat`)

**Rewrite `RetrievalAgent`.** Replace the DeepSeek-hardcoded, academic-domain agent with a session-aware agent operating on the agentic output contract from Step 0.4. New decision space maps to the four confidence tiers and the three retrieval routes. Add the `retrieve_needed = false` path — the agent can now return the `response` field directly without running retrieval. Update prompt from academic framing to companion framing. All provider references route to Ollama via `simple_generate()`.

**Rewrite session-facing metadata fields throughout retrieval.** Replace all document-era field references (`course_name`, `chapter_title`, `topic`, `page_start`, `page_end`) with session-era fields (`session_id`, `timestamp`, `message_roles`, `action_types`, `entities`, `sentiment`, `extracted_topics`) in: `MetadataFilterConfig._default_config`, `metadata_filter.yaml`, `hybrid_search.yaml` metadata boost fields, `ContextBuilder._build_citation`/`_build_metadata_header`, `ContextBuilder.get_template()`, `ContextBuilder._determine_section_type()`. Verify every configured metadata boost field in `hybrid_search.yaml` actually survives to storage — the `key_topics` silent-mismatch bug must not be repeated.

**Introduce the Retrieval Router** as a named, tested component. Class with a `route(agentic_output: AgenticOutput, query: str) → list[SessionRetrievedChunk]` method. Reads `retrieval_route` from the agentic output. Dispatches to: semantic path (`HybridSearch`), structured path (`StructuredTableSearch`), or hybrid path (both, results merged).

**Implement `StructuredTableSearch`.** Query `meeting_notes`, `todos`, `reminders`, `schedule_items` via SQLite FTS5. Accept a table filter, a query string (from `agentic_output.search_query`), and a `deleted_at IS NULL` filter. Return results as `SessionRetrievedChunk` instances with `chunk_type = "structured_record"`. Write unit tests using in-memory SQLite with FTS5 virtual tables.

**Remove dead code:**
- `_fallback_search`/`_fallback_reranker` (fixed in Step 0.2; confirm zero remaining references)
- `_handle_decomposed` placeholder (never-implemented sub-query handler)
- `KeywordSearch._elasticsearch_search` (always returns `[]`)
- `colbert` backend references from `reranker.py` (no implementation exists)
- `WeightOptimizer` (unreachable — `add_sample()` never called)

**Re-validate `orchestrator.yaml` timing.** The 15,000ms timeout was calibrated for cloud embedding API latency. Measure against the local stack (local embedding + SQLite query + local reranker) and update accordingly. The <200ms vector search target and <3.5s end-to-end target are the authoritative benchmarks.

**Deliverables:**
- Retrieval pipeline fully rewritten: session-shaped, local-only, `VectorStoreInterface`-backed
- `StructuredTableSearch` with FTS5 and unit tests
- Retrieval Router as a named, tested component
- Rewritten `RetrievalAgent` (Ollama, session-aware, `retrieve_needed` branch)
- Updated `context_builder.yaml`, `metadata_filter.yaml`, `hybrid_search.yaml`
- All dead code removed
- `orchestrator.yaml` timing re-validated

**Acceptance Criteria:**
- A synthetic session-based query executes end-to-end through the retrieval pipeline and returns `SessionRetrievedChunk` instances
- Structured-only query against `meeting_notes` FTS5 returns correct results
- Hybrid query returns merged results from both paths
- `HybridSearch` no longer imports from `src.ingestion.vector_store` directly
- All Phase 0 characterization tests still pass
- The old `RetrievedChunk` is still present in `src/common/types.py` — not deleted yet
- CI is green

**Exit Criteria:** Retrieval pipeline fully migrated. The old `RetrievedChunk` is the only remaining document-era shared type still in use.

---

### Step 1.4 — Generation Pipeline Rewrite

**Tasks:**

**Remove SCRAP generation components:**
- Delete `CitationFormatter` (formats `[Course | Chapter | Slide N]`)
- Delete `CitationExtractor` (regex-parses bracket citations)
- Delete `PostProcessor._match_citation_to_chunk`
- Replace `Citation` dataclass document-location fields with session citation fields
- Rewrite `context_aware.yaml` system prompt rule 3 (citation format)
- Rewrite `PromptBuilder.build` context-header assembly (`course_name`/`chapter_title`)
- Delete `ExamAnswerContextAssembly`, `ExamAnswerOrderingClassifier`, `ChronologicalContextAssembly`, `ChronologicalOrderingClassifier` (confirmed dead in production)
- Delete `SemanticGroupingClassifier` (TODO stub, never instantiated)
- Delete `GenerationMode.TOPIC_EXTRACTION` and `QUIZ_GENERATION` (no YAML config, no templates, unreachable)
- Delete `CitationStyle.FOOTNOTE` and `APPENDED` (declared, no distinct behavior implemented)
- Delete `ModeConfig.include_source_prefix` and `ModeConfig.prompt_version_constraint` (parsed but never read)

**Wire `conversation_history` into prompt assembly.** `GenerationRequest.conversation_history` is declared but `PromptBuilder.build()` never reads it. Wire it fully: inject the current session's recent message history as a first-class prompt section (oldest first, with role labels). Write a unit test that verifies `PromptBuilder.build()` produces different outputs when `conversation_history` is empty vs. populated with N turns.

**Rewrite `MetadataBasedClassifier`.** Replace `chunk_type`/`topic` keyword rules (`"definition"`, `"example"`, `"formula"`) with session equivalents (`reminder`, `todo`, `meeting`, `schedule`, `sentiment`, `entities`). Update `section_headers` dict accordingly.

**Rewrite prompt templates and refusal phrase.** Rewrite `config/generation/prompts/context_aware.yaml` and `simple_explanation.yaml` with companion/memory framing. Replace `{course}` and `{chapter}` template variables with `{session_date_range}` and `{topics}`. The refusal phrase must change from `"The provided material does not cover this topic in sufficient detail"` to a session-appropriate equivalent. The new phrase and the updated `REFUSAL_PATTERNS` regex in `eval/run_ragas_eval.py` must be changed in the same commit — these two changes must never be separated.

**Write action-type-specific prompt templates (net-new files):**
- `config/generation/prompts/reminder_confirmation.yaml`
- `config/generation/prompts/todo_confirmation.yaml`
- `config/generation/prompts/meeting_summary.yaml`
- `config/generation/prompts/schedule_confirmation.yaml`
- `config/generation/prompts/conflict_alert.yaml`

**Rewrite `CitationFormatter`.** Replace document-location formatting with session-temporal formatting: `[Session: {session_id} | approx. {timestamp}]`. Update `PostProcessor.process` Step 2 accordingly.

**Implement `GroundingValidator` `llm_check`.** The `llm_check` method is currently documented as a valid config option but silently falls back to keyword overlap. Implement it: an Ollama call that verifies whether the answer's claims are grounded in the provided session context. The keyword-overlap fallback is retained if the LLM call fails.

**Update `GenerationOrchestrator._generate_impl`.** Remove `if model_config.provider == "deepseek": cost_details = {"total": estimate_deepseek_cost(...)}`. Replace with a latency-proxy estimate: `{"compute_ms": elapsed_ms}`. Update `observability/tracing.py` Langfuse span to record `compute_ms` instead of `cost_usd`. Update the `MetricsStore` `cost_usd` column → `compute_ms`. `MetricsStore` is a separate, unencrypted SQLite file from the session DB with its own schema and no existing migration runner (per the audit: KEEP-AS-IS, own WAL mode, own connection) — this is **not** a numbered migration in the session-DB sequence from Step 0.4. Instead, implement it as an idempotent in-process update in `MetricsStore.__init__`: on connection, check whether `compute_ms` exists; if not, run `ALTER TABLE pipeline_calls ADD COLUMN compute_ms REAL`, then drop the `cost_usd` column if the SQLite version supports `DROP COLUMN` (3.35.0+), otherwise leave it as a nullable orphan column.

**Remove hardcoded document-domain `__main__` fixtures** from `orchestrator.py`, `prompt_builder.py`, `post_processor.py`. Replace with proper pytest integration tests using the session-seeded in-memory DB.

**Author the golden eval set.** Write 50–100 synthetic session-based evaluation items covering: all action types (reminder, todo, meeting, schedule, summary), all three disambiguation tiers (Tier 1/2/3 ambiguity edge cases), and at least 10 items in the "structured-data paraphrase recall" category (queries where the user's phrasing does not share keywords with the stored field value). Commit as `eval/golden_qa_set.json`.

Set the two previously-placeholder CI gate thresholds during this step — not after:
- **Refusal rate baseline:** run the eval harness against a minimum of 20 out-of-domain questions with the new session-based prompt templates. Record the mean refusal rate. The CI gate is a **two-sided band**: `baseline − 5pp ≤ refusal_rate ≤ baseline + 5pp`. The floor catches hallucination regression (model stops refusing when it should — the safety-critical direction). The ceiling catches over-refusal regression. Commit both the baseline value and the band definition to `eval/gates.json`.
- **Temporal accuracy baseline:** a human reviewer assesses at least 10 time-reference questions to establish a reasonable pass rate. Record this as the floor threshold in `eval/gates.json`.

Commit all thresholds to `eval/gates.json` alongside the eval set. The CI gate must never be implicitly "whatever the first run happens to produce."

**Eval set second-reviewer gate.** Before the `eval/golden_qa_set.json` commit is merged, a second engineer (not the author of the prompt-template rewrite) must review a random sample of at least 20 eval items and confirm: (a) coverage — all action types, all three tiers, and the paraphrase recall category are represented; (b) ground truth accuracy — expected answers are actually correct; (c) question quality — questions reflect realistic user phrasing, not synthetic edge cases unlikely to occur in real usage. The review is documented as a checklist committed to `docs/eval_review.md` alongside the eval set. This directly implements the mitigation for product spec Risk #6 ("old eval set gives false confidence in release readiness").

**Migrate all generation call sites from `RetrievedChunk` to `SessionRetrievedChunk`.** Once all call sites in `CitationFormatter`, `PromptBuilder`, `StandardContextAssembly`, `MinimalContextAssembly`, and `PostProcessor` are migrated, **delete `RetrievedChunk` and `CitationLocationType` from `src/common/types.py`**. Verify via grep that zero references remain.

**Update the eval harness** in the same commit as the prompt template rewrite. Update `REFUSAL_PATTERNS` regex (must match the new refusal phrase exactly). Rewrite `bootstrap_pipeline()` to seed a session-based KB instead of ingesting `sample_data/sample_lecture.pptx`. Remove `sample_lecture.pptx` from the repo. Replace the old `golden_qa_set.json` with the new session-based eval set.

**Deliverables:**
- Generation pipeline fully rewritten: session-framed, `conversation_history` wired, no document-era language
- Five new action-type-specific prompt templates
- `GroundingValidator` `llm_check` implemented
- `RetrievedChunk` and `CitationLocationType` deleted from `src/common/types.py`
- Updated eval harness with new refusal regex and session-based bootstrap
- `eval/golden_qa_set.json` (50–100 items, second-reviewer sign-off)
- `eval/gates.json` with all CI gate thresholds defined (faithfulness ≥ 0.6, two-sided refusal rate band, temporal accuracy floor, agentic routing ≥ 90%)
- `docs/eval_review.md` (second-reviewer checklist)
- `MetricsStore` schema update: `compute_ms REAL` column added via `ALTER TABLE` in `MetricsStore.__init__`, with a column-existence check so the update is idempotent; `cost_usd` column retired (dropped if SQLite ≥ 3.35.0, left as nullable orphan on older versions). No change to the session-DB migration sequence (`0001_initial_schema.sql` remains the only migration as of this version).

**Acceptance Criteria:**
- `RetrievedChunk` has zero remaining references in the codebase (verified by grep)
- `CitationLocationType` has zero remaining references in the codebase (verified by grep)
- A synthetic session Q&A query executes end-to-end: retrieval → generation → response with session-timestamp citation
- `PromptBuilder.build()` produces distinct output when `conversation_history` is populated (verified by unit test)
- All Phase 0 characterization tests still pass
- The eval harness runs against the new session-based golden set with no import errors
- `eval/gates.json` has all four gate thresholds explicitly committed — no placeholder values
- `docs/eval_review.md` is committed with second-reviewer sign-off
- CI is green

**Exit Criteria:** All three pipelines fully migrated. Document-era shared type contract permanently deleted. Eval set authored, reviewed, and gated.

---

### Step 1.5 — Feature Handlers: Reminders, Todos, Meeting Notes, Schedule

**Tasks:**

**Implement `ReminderHandler`.** CRUD against the `reminders` table. `create_reminder`: atomic single transaction, then register `ScheduledToastNotification` with Windows via IPC to the Tauri Rust shell. `update_reminder`: update the row, cancel the old Toast registration, register a new Toast at the new time. `delete_reminder`: soft-delete (`deleted_at = NOW()`), cancel the Toast registration. On-launch reconciliation: query `scheduled_time < NOW() AND fired_at IS NULL AND deleted_at IS NULL` (missed fire → "overdue") and `fired_at IS NOT NULL AND completed_at IS NULL AND deleted_at IS NULL` (unacknowledged → "pending acknowledgment"). Surface both sets to the frontend with distinct states. A reminder leaves either state only through explicit user action: complete, dismiss, or reschedule.

**Implement Windows Toast notification integration** in the Tauri Rust shell (WinRT API via the `windows` crate). `register_toast(reminder_id, scheduled_time, body) → toast_id`. `cancel_toast(toast_id)`. `ToastActivationHandler`: on notification activation (user taps the Toast), send an IPC message to the Python backend to set `fired_at = NOW()`. The Python backend calls the Rust shell via IPC — it does not access WinRT directly.

**Implement `TodoHandler`.** CRUD against `todos`. NLP-extracted priority and category are always user-editable post-creation. Completed todos retained with `completed_at` — no deletion. `get_todos(filters)` supports filtering by `completed_at IS NULL` (active only) and `priority`.

**Implement `MeetingNoteHandler`.** `capture_meeting_note(raw_transcript: str) → MeetingNote`. LLM extraction pass produces: `attendees`, `topics`, `decisions`, `action_items` (task/owner/deadline), `follow_ups`. Validates that at least one of `{decisions, action_items}` is non-empty — if not, stores raw transcript with `needs_review = true`. No conversational response generated during meeting-note capture (spec requirement).

**Implement `ScheduleHandler`.** `create_schedule_item`: validates for time-slot conflicts before committing. Conflict detected → surface to the user, do not silently overwrite. `update_schedule_item`: same conflict validation. `get_day_schedule(date) → list[ScheduleItem]`.

**Implement `SummaryHandler`.** `generate_daily_summary(date) → Summary`: queries `todos`, `reminders`, `meeting_notes`, `schedule_items`, and `session_chunks` for the given date; LLM call to produce a natural-language summary; idempotent (overwrite if a summary already exists for this date, stamp with the original scheduled timestamp not the late generation time). `generate_weekly_summary(week_start) → Summary`: aggregates daily summaries.

**Implement the local scheduler.** A background thread in the Python backend. Polls the `reminders` table at ≤60s resolution for `scheduled_time ≤ NOW() AND fired_at IS NULL AND deleted_at IS NULL`. For matched reminders: set `fired_at = NOW()`, send the IPC trigger to the Tauri shell for Toast activation. Checks the summary schedule for daily/weekly generation. Starts with the Python backend, shuts down cleanly with it.

**Write integration tests** for each handler using in-memory SQLite. Test: create → retrieve → update → soft-delete lifecycle for each entity. Test reminder on-launch reconciliation with both missed-fire and unacknowledged-fire cases (each condition independently, not just together). Test meeting note extraction validation (empty decisions and action_items → `needs_review = true`). Test schedule conflict detection (overlapping items → conflict surfaced, not committed).

**Deliverables:**
- `ReminderHandler`, `TodoHandler`, `MeetingNoteHandler`, `ScheduleHandler`, `SummaryHandler`
- Windows Toast notification integration (Rust, in Tauri shell)
- Local scheduler thread
- Integration tests for all handlers

**Acceptance Criteria:**
- Reminder created → stored in DB → Toast registered (verified by mock WinRT call in test)
- On-launch reconciliation correctly surfaces `scheduled_time < NOW() AND fired_at IS NULL` as "overdue" and `fired_at IS NOT NULL AND completed_at IS NULL` as "pending acknowledgment" — both conditions independently verified in separate tests
- Meeting note without decisions or action_items stored with `needs_review = true`
- Schedule conflict detected and surfaced — not silently overwritten
- Daily summary generated from a pre-seeded synthetic dataset with correct scheduled timestamp
- All Phase 0 characterization tests still pass
- CI is green

**Exit Criteria:** All feature handlers implemented and tested. Reminder Toast integration verified. Scheduler thread operational.

---

### Step 1.6 — Dynamic Model Management

**Tasks:**

**Implement `OllamaManager`.** Wraps the bundled Ollama sidecar (started by the Tauri shell before the Python backend). `is_running() → bool` via `GET /api/tags`. `get_installed_models() → list[ModelInfo]`. `get_model_status(model_name) → ModelStatus` (not_installed | downloading | available | active). The Python backend communicates with Ollama via HTTP — it does not manage the Ollama process lifecycle directly.

**Implement `ModelManager`.** `download_model(model_name, progress_callback)`: streams download progress from Ollama's pull API; calls `progress_callback` with `{percent, speed_mbps, eta_seconds}`. Interruption handling: Ollama's pull API downloads models layer-by-layer — if an interruption occurs, attempt to resume from the last successfully pulled layer. If resumption is not possible (daemon was killed mid-pull and layer state is inconsistent), delete partial layer artifacts and restart from the beginning with the message: "Download interrupted — restarting from the beginning." Under no circumstances are partial download artifacts left on disk without a clear status. The user always sees either: (a) a complete, verified model, (b) a clean restart from zero, or (c) a specific error with an actionable message. Never an ambiguous partial state. Disk-full during download: caught and surfaced with "Not enough disk space — free X GB and try again." `verify_model_integrity(model_name) → bool`. `switch_active_model(model_name)`: updates the active model in app config without restarting the backend. `get_model_catalog() → list[ModelCatalogEntry]`: returns the curated list from the bundled JSON catalog file (not fetched from the network).

**Implement `ModelInferenceRouter`.** Routes generation requests to the currently active Ollama model. Handles `model_not_loaded` and `ollama_not_running` with specific user-readable messages. Never a generic error.

**First-launch model selection enforcement.** If `active_model` is null in app config, the backend returns a specific error code for "no model active." The frontend routes to the model setup screen. All inference-dependent features are blocked until a model is active.

**Write unit tests.** `OllamaManager` tested against a mock HTTP server. `ModelManager.download_model` tested against: a clean download (mock streaming response), a network-loss interruption (verify clean restart, no artifacts), a disk-full condition (verify specific error message). Resumption path tested separately from restart path.

**Deliverables:**
- `OllamaManager`, `ModelManager`, `ModelInferenceRouter`
- Bundled model catalog JSON (name, size, description, minimum RAM per model)
- First-launch enforcement logic in the backend
- Unit tests for all components including all interruption paths

**Acceptance Criteria:**
- `download_model` correctly streams progress
- Network-loss interruption produces either a clean resume or a clean restart with no partial artifacts on disk
- Disk-full produces the correct specific error message
- `switch_active_model` takes effect for the next inference call without restarting the backend
- First-launch flow blocks inference features when no model is active
- All Phase 0 characterization tests still pass
- CI is green

**Exit Criteria:** Dynamic model management fully implemented. All download interruption paths verified. First-launch flow operational.

---

**Phase 1 Milestone:** All three RAG pipelines migrated to session-based, local-only operation. All feature handlers implemented. Dynamic model management operational. Document-era shared types permanently deleted. Eval set authored, reviewed, and gated. The system can ingest session data, retrieve memory, generate responses, create reminders/todos/meetings/schedules, and manage Ollama models — entirely locally.

---

## Phase 2 — Non-Functional Hardening

**Objective:** Production-grade the system before packaging. Encryption, backup/restore, crash recovery, accessibility, and performance benchmarking are binding requirements for Store submission — not optional polish.

---

### Step 2.1 — SQLCipher Encryption and Key Management

**Tasks:**

Integrate SQLCipher into the PyInstaller build. Verify that `pysqlcipher3` (or `sqlcipher3`) can be frozen by PyInstaller on Windows without native DLL issues. Generate the database encryption key on first launch using `os.urandom(32)`. Store the key in Windows Credential Manager via the `keyring` library. Retrieve the key on all subsequent launches.

Handle key-not-found (e.g., Windows reinstall): display the message: *"Your previous data is protected by your Windows account and cannot be recovered after a Windows reinstall. Starting fresh."* Do not crash silently. Do not attempt to open the old encrypted file.

Implement the on-launch SQLite integrity check: `PRAGMA integrity_check`. If the result is not `"ok"`, offer the restore-from-backup flow before attempting any other operation.

**Deliverables:**
- SQLCipher integration in PyInstaller build
- Key generation, storage (Windows Credential Manager), and retrieval
- Key-not-found handling with correct user message
- On-launch integrity check with backup-restore offer

**Acceptance Criteria:**
- The encrypted SQLite file cannot be opened by a standard SQLite browser without the key
- Key is stored in and retrieved from Windows Credential Manager correctly
- Key-not-found path produces the specified user message, not a crash
- Integrity check detects a manually-corrupted database and offers the restore flow

---

### Step 2.2 — Backup, Restore, and Export

**Tasks:**

**Automatic backup.** Background job that runs daily (default 2am, configurable). Uses SQLite's online backup API (safe to run while the DB is in use). Stores snapshots in `%LocalAppData%\Packages\<PFN>\LocalState\backups\`. Retains the last 7 snapshots. Deletes older ones.

**Settings → Backup & Recovery panel.** Shows date/time of each available snapshot. "Restore from this backup" action: confirm dialog (warns current data will be replaced), copy snapshot over live DB, restart the Python backend subprocess.

**Data export.** Generates a JSON file containing all user data: `sessions`, `messages`, `reminders`, `todos`, `meeting_notes`, `schedules`, `schedule_items`, `summaries`. User chooses the save location via the system file picker. Export is plaintext JSON — not encrypted. `session_chunks` (embeddings) are excluded — they are fully derivable from `messages` and will be regenerated automatically on re-import in v2; including raw float32 embedding BLOBs in a user-facing JSON export would produce unreadable output with no recovery value. Settings copy reads: *"Export your data — saves a backup copy of everything for safekeeping. Note: re-importing into the app is not yet supported; this export preserves your data for a future update."*

**Proactive export badge.** If `last_exported_at` is null or > 30 days ago, add a badge indicator to the Settings nav item (not a modal — a badge). Settings → Data & Privacy always shows: "Last exported: [date]" or "Never — your data cannot be recovered if Windows is reinstalled without an export."

**Full wipe.** Settings → Data & Privacy → "Delete all my data." Confirm dialog. Soft-deletes all rows across all tables (`deleted_at = NOW()`). Clears all scheduled Toast registrations. Resets app config. Does not uninstall the app.

**Uninstall warning.** Permanent static text in Settings → Data & Privacy: *"Uninstalling this app will permanently delete your encrypted database. Export your data first."*

**Deliverables:**
- Automatic daily backup with 7-snapshot rolling retention
- Settings Backup & Recovery panel with restore action
- Data export (JSON, all 8 substantive tables, user-chosen location)
- Proactive export badge and Settings panel copy
- Full wipe action

**Acceptance Criteria:**
- Backup job runs and produces a valid SQLCipher-encrypted snapshot file
- Restore from snapshot produces a usable database (integrity check passes post-restore)
- Export JSON contains all 8 substantive tables: `sessions`, `messages`, `reminders`, `todos`, `meeting_notes`, `schedules`, `schedule_items`, `summaries`
- `session_chunks` is absent from the export JSON (verified)
- Settings panel shows correct last-exported date
- Badge appears after simulating 31 days without export (mock clock in test)
- Full wipe leaves all tables present but all rows soft-deleted

---

### Step 2.3 — Crash Recovery and Process Supervision

**Tasks:**

Implement the Tauri process supervisor: on Python backend crash, restart with exponential backoff (1s, 2s, 4s). After 3 failed attempts, show the degraded-mode banner with a "Restart app" button. All IPC calls must have explicit timeouts — a timeout produces a "temporarily unavailable" UI state, not an infinite spinner.

Implement the single-instance mutex: `Global\PersonalAICompanion_v1_SingleInstance`. On second-instance launch: bring the first instance's window to the foreground (via a named event or WM_COPYDATA signal), then exit immediately. Implement the first-instance focus handler on the receiving side.

Implement subprocess-kill fuzzing test: a test runner that kills the Python backend process at random points, verifies that the Tauri shell detects the crash, restarts the backend, the frontend shows the degraded-mode banner during restart, and the frontend recovers and becomes functional after successful restart. Run on `windows-latest` CI runner.

**Deliverables:**
- Process supervisor with exponential backoff and degraded-mode banner
- Single-instance mutex enforcement with focus-signal handler
- All IPC calls with explicit timeouts
- Subprocess-kill fuzzing test

**Acceptance Criteria:**
- Backend killed mid-operation → frontend shows degraded-mode banner → backend restarts → frontend recovers (verified by fuzzing test)
- Second-instance launch brings first instance to foreground and exits (verified by process test)
- IPC timeout produces "temporarily unavailable" state, not an infinite spinner

---

### Step 2.4 — Accessibility Pass

**Tasks:**

Audit every primary user flow against WCAG 2.1 AA: chat input/output, reminder creation/viewing, todo management, schedule view, meeting note capture, model download, Settings panels. Use `axe-core` (or Playwright + axe integration) for automated scanning. Conduct a manual Windows Narrator pass on each flow.

Fix all violations: color contrast, keyboard navigation (no mouse-only interactions), accessible names/roles for all interactive elements (no icon-only buttons without accessible labels), system text scaling compatibility (test at 150% and 200%), Windows high-contrast mode compatibility.

Verify all user-facing strings are externalized (no hardcoded UI text in React components). This is the prerequisite for v1.1 localization.

**Deliverables:**
- Accessibility audit report
- All WCAG 2.1 AA violations resolved
- All strings externalized
- Automated accessibility test integrated into CI (required gate before Store submission)

**Acceptance Criteria:**
- Zero axe-core violations on all primary flows
- Manual Narrator pass: all primary flows navigable without a mouse
- All interactive elements have accessible names/roles
- Layouts intact at 150% and 200% text scaling
- High-contrast mode produces no unreadable UI states

---

### Step 2.5 — Performance Benchmarking

**Tasks:**

Implement an automated performance benchmark suite against the reference hardware spec (8th-gen Intel i5 or AMD Ryzen 5 equivalent, 16GB RAM, SATA SSD, integrated GPU only, no discrete GPU).

Benchmark and assert against §9.1 targets:

| Metric | Target | Hard Ceiling | CI Behavior |
|---|---|---|---|
| Embedding generation | < 100ms/chunk | 250ms | Fail if above ceiling |
| Vector search | < 200ms | 500ms | Fail if above ceiling |
| LLM inference (7B class) | 2–3s | 5s | Warn only — model-dependent |
| End-to-end query → response | < 3.5s | 6s | Fail if above ceiling |
| App cold start | < 3s to interactive | 6s | Fail if above ceiling |
| Idle CPU | < 2% | 5% | Fail if above ceiling |
| Idle RAM | < 300MB | 500MB | Fail if above ceiling |

Vector search benchmark uses a pre-seeded database of 10,000 session chunks — approximately 3 years of heavy personal use. This is the scale at which brute-force cosine must remain within budget.

Benchmark results are committed as JSON artifacts per CI run for trend tracking across releases.

**Deliverables:**
- Automated benchmark suite
- Benchmark results artifact in CI
- All hard ceilings met on reference hardware

**Acceptance Criteria:**
- All hard ceilings met on the reference hardware spec
- Idle CPU and RAM targets met with the full stack running (Tauri shell, Ollama sidecar, Python backend, model loaded)
- Benchmark JSON artifact committed per CI run

---

**Phase 2 Milestone:** The application is production-hardened. Encryption at rest. Backup and restore operational. Crash recovery tested by fuzzing. Accessibility verified. Performance benchmarked against reference hardware.

---

## Phase 3 — Frontend Implementation

**Objective:** Build the React/TypeScript frontend. The Python backend APIs are stable. The IPC contract is frozen. The frontend consumes defined, tested endpoints — it does not drive backend design.

---

### Step 3.1 — Frontend Architecture and IPC Client

**Tasks:**

Establish the React + TypeScript project structure within the Tauri shell. Configure routing (React Router), state management (Zustand or equivalent — no Redux overhead for a single-user desktop app), and the IPC client layer.

Implement the typed IPC client: wraps `window.__TAURI__.invoke()` with zod schema validation. Every IPC call goes through this client — no component makes raw `invoke()` calls. The client handles: request serialization, response deserialization, version-check errors (route to "please restart" UI), timeout errors (route to "temporarily unavailable" UI), and backend degraded-mode events (show banner).

Implement the degraded-mode banner: persistent top-of-screen notification during backend restart. Shows "AI features temporarily unavailable — restarting..." with a progress indicator. Replaced by "Ready" confirmation when the backend reconnects. Never a silent spinner.

Implement the first-launch model selection flow: detect "no active model" error code from the backend, route to the model selection screen. Show bundled model catalog (name, size, description, minimum RAM), download progress bar (percent, speed, ETA), integrity verification confirmation, and "Activate" button. No other feature accessible until a model is active.

**Deliverables:**
- React + TypeScript project with routing and state management
- Typed IPC client with zod validation
- Degraded-mode banner
- First-launch model selection flow

**Acceptance Criteria:**
- IPC client correctly validates all message types against zod schemas
- Version mismatch routes to "please restart" UI (tested by injecting a mismatched version in a unit test)
- First-launch flow presents model catalog, downloads with progress, activates before allowing access to other features

---

### Step 3.2 — Chat Interface

**Tasks:**

Implement the primary chat view: message input (single text field, full-width, Enter to send, Shift+Enter for newline), message history display (user messages right-aligned, assistant messages left-aligned, timestamps), session boundary indicators (visual separator between sessions), typing indicator while the backend is generating.

Implement citation display: session-timestamp citations rendered as inline tooltips or footnotes. Clicking a citation shows the session context it references. No page/slide citations — only session ID + approximate timestamp references.

Implement the Tier 2 disambiguation popup: appears over the chat when confidence is 0.70–0.85, offers 2–4 option buttons to confirm intent. Dismissible (falls through to Tier 4 conversation if dismissed).

Implement the Tier 3 clarification prompt: appears as a follow-up assistant message with syntax examples. Not a modal — the conversation continues.

Implement the "New conversation" button: starts a new session explicitly. Previous session is finalized (idle-timeout logic still applies for automatic close).

**Deliverables:**
- Chat view with all message states
- Citation tooltip/footnote rendering
- Tier 2 disambiguation popup
- Tier 3 clarification as inline assistant message
- "New conversation" action

**Acceptance Criteria:**
- Message history renders correctly for sessions of 1, 20, and 100+ messages
- Tier 2 popup appears for a backend response with confidence 0.72 (mocked), with correct options
- Citation tooltip renders session ID and timestamp from a mocked `SessionRetrievedChunk`
- "New conversation" closes the current session and starts a new one (verified by checking the backend receives a new `session_id`)

---

### Step 3.3 — Feature Views (Reminders, Todos, Meetings, Schedule)

**Tasks:**

**Reminders view.** List of active reminders grouped by overdue / today / upcoming. Inline NLP create field (goes through full agentic pipeline). Edit, delete, and completion checkbox actions. Overdue reminders displayed with a distinct visual state — never hidden.

**Todos view.** List of active todos with priority and category indicators. Inline NLP create. Complete checkbox, edit, soft-delete. Completed todos accessible via a "Completed" tab (retained, not deleted).

**Meetings view.** List of meeting notes, each expandable to show attendees/topics/decisions/action items/follow-ups. `needs_review` items displayed with a "Review needed" badge. Inline capture: text area for pasting or typing a transcript, "Capture" button that triggers extraction. No conversational response during capture.

**Schedule view.** Day view (default) and week view toggle. Time slots displayed as a timeline. NLP update field for natural-language schedule edits. Conflict alerts displayed inline (not modal) — the user must explicitly choose to overwrite or keep.

**Deliverables:**
- Reminders view (list, create, edit, complete, delete)
- Todos view (list, create, complete, edit, delete, completed tab)
- Meetings view (list, capture, expandable detail, needs-review badge)
- Schedule view (day/week, NLP edit, conflict alert)

**Acceptance Criteria:**
- Each view renders correctly with a pre-seeded synthetic dataset (30 reminders, 50 todos, 10 meeting notes, a full week of schedule items)
- Overdue reminders displayed with correct visual distinction
- Meeting note capture submits to the backend and renders the extracted structured output on completion
- Schedule conflict alert appears (not silently overwrites) when a new item overlaps an existing one

---

### Step 3.4 — Settings and Diagnostics

**Tasks:**

**Settings → General:** session idle timeout configuration, daily summary time configuration.

**Settings → Models:** active model display, model catalog with download/switch actions, model status indicators (not_installed | downloading | available | active).

**Settings → Data & Privacy:** last-exported date display (with proactive export badge if > 30 days), "Export all data" action (JSON, file picker), "Delete all my data" action (confirm dialog, full wipe), permanent uninstall warning text.

**Settings → Backup & Recovery:** list of available snapshots (date/time), "Restore from this backup" action (confirm dialog, restore, restart backend).

**Settings → Diagnostics:** structured log viewer (last N log entries, filterable by level), local metrics dashboard (latency, confidence distribution, error rate, compute time), "Report a problem" action (packages redacted log file, opens a compose email window with log attached — no auto-send, user reviews before sending).

**Deliverables:**
- All Settings panels
- Export, wipe, restore, diagnostics, and "Report a problem" flows

**Acceptance Criteria:**
- Export produces a valid JSON file at the user-chosen location containing all 8 substantive tables
- Wipe produces a state where all feature views show "nothing here yet"
- Restore from snapshot produces a functional app state (integrity check passes)
- "Report a problem" opens a compose email window with the redacted log attached (no raw message content in the log)
- Export badge appears in the Settings nav when `last_exported_at` is > 30 days ago

---

**Phase 3 Milestone:** Frontend fully implemented. All views functional. IPC communication verified. First-launch flow operational. Settings and diagnostics complete.

---

## Phase 4 — System Integration and End-to-End Testing

**Objective:** Verify the entire system works as a whole. Every component has been unit-tested and integration-tested in isolation; this phase validates them together.

---

### Step 4.1 — End-to-End Integration Tests

Write end-to-end integration tests using Playwright (or Tauri's test utilities) that exercise complete user flows. All LLM calls are mocked to return pre-defined structured JSON responses — no real model inference in E2E tests.

**Required flows:**
- **Reminder flow:** type a reminder in natural language → disambiguation (if Tier 2) → confirm → reminder appears in Reminders view → mock clock to trigger time → overdue reminder appears with "overdue" state → complete → reminder removed from active list
- **Meeting note flow:** open Meetings view → paste a synthetic transcript → Capture → meeting note appears with extracted decisions/action items
- **Memory retrieval flow:** ingest a synthetic session → ask a question that should retrieve from that session → verify the response cites the correct session with session ID and timestamp
- **Model download flow:** first-launch state (no active model) → select model → mock download (complete instantly) → verify model is active → chat becomes available
- **Missed-fire reconciliation flow:** create a reminder with a past `scheduled_time`, set `fired_at IS NULL` in the test DB → launch the app → verify the reminder appears as "overdue" in the Reminders view

**Acceptance Criteria:**
- All five flows pass on a `windows-latest` CI runner
- No test uses real LLM inference
- Tests complete in under 5 minutes total

---

### Step 4.2 — IPC Contract Verification

Write tests that exercise every defined IPC message type with both valid and invalid payloads. Valid payloads must produce correct responses. Invalid payloads (wrong types, missing fields, wrong version) must produce the version-mismatch or validation-error response — never a crash.

Verify that a frontend running with message version N and a backend running version N+1 produces the "please restart" UI rather than silent failure.

**Acceptance Criteria:**
- All IPC message types validated
- Invalid payloads produce error responses, not crashes
- Version mismatch produces the "please restart" UI state

---

### Step 4.3 — Golden Eval Pass

Run the full RAGAS evaluation harness against the session-based golden eval set. All gate thresholds are defined in `eval/gates.json` (committed in Phase 1 Step 1.4 — not set here for the first time).

**CI Quality Gates:**

| Metric | Gate | Notes |
|---|---|---|
| Faithfulness | ≥ 0.6 | Floor for 7B local model output quality on CPU inference. Reflects the realistic 0.55–0.70 range for this model class — below 0.6 passes hallucinated answers; above 0.65 would likely block reasonable output. Re-evaluate at v1.1. |
| Refusal rate | baseline − 5pp ≤ rate ≤ baseline + 5pp | Two-sided band. Floor catches hallucination regression (the safety-critical direction). Ceiling catches over-refusal regression. Baseline established during Phase 1 Step 1.4. |
| Temporal accuracy | ≥ baseline − 5pp | Floor only — accuracy degrading is the failure mode; no meaningful upper bound. Baseline established during Phase 1 Step 1.4. |
| Agentic Tier-1 routing accuracy | ≥ 90% | On the held-out 200-utterance routing test set: correctly routed at Tier 1 or resolved without a third clarification round. |
| FTS5 paraphrase recall | Reported, not a blocking gate | Documented known limitation. Results committed as a CI artifact each run. Improvement path is a v1.1 candidate. |

If any blocking gate fails: identify failing cases, determine root cause (prompt template, retrieval quality, confidence threshold calibration, or eval set error), fix, and re-run. The eval must pass before Phase 5 begins.

---

### Step 4.4 — Reliability Testing

Execute the subprocess-kill fuzzing test suite: 100 iterations of random kill-and-recover cycles.

Execute disk-full simulation during model download: verify specific error message and clean state (no partial download artifacts).

Execute network-loss simulation during model download: verify clean error state with resume attempted before clean restart.

**Acceptance Criteria:**
- 100/100 kill-and-recover cycles produce zero data loss and zero silent hangs
- Disk-full during download produces the correct specific error message
- Network-loss during download produces a clean, resume-or-restart state with no partial artifacts

---

**Phase 4 Milestone:** System integration verified. End-to-end flows passing. RAGAS eval gates passed. Reliability tests passed. The system is ready for packaging and certification.

---

## Phase 5 — Packaging, Signing, and Store Submission

### Step 5.1 — Build Pipeline and MSIX Packaging

**Tasks:**

Configure Tauri's bundler for `.msix` output. Bundle: Tauri Rust shell, compiled React frontend (static assets), PyInstaller-frozen Python backend sidecar, pinned Ollama binary sidecar, bundled `all-MiniLM-L6-v2` model weights (~90MB).

Configure the MSIX app manifest: package family name, display name, publisher, semantic version, capability declarations (internet client, `ToastNotifications`), supported OS versions (Windows 10 22H2+, Windows 11).

Verify PyInstaller correctly handles: `sentence-transformers` model cache paths (the bundled model must be found relative to the frozen executable path, not a user-specific cache directory), SQLCipher native DLL, `keyring`/`win32cred` Windows Credential Manager bindings.

Configure the protected-branch signing workflow in GitHub Actions: on push to `release/*` branch only, sign the `.msix` with the code-signing certificate. Certificate stored as a GitHub Actions secret accessible only to the release branch workflow.

**Acceptance Criteria:**
- `.msix` installs cleanly on a Windows 10 22H2 reference machine and a Windows 11 machine
- All bundled components load correctly from frozen paths (no `ModuleNotFoundError`, no missing DLLs, no model-not-found errors)
- Installed size is within expected range (~170MB base install without generative model)
- Signed build produced on release branch; unsigned build confirmed not produced on PR branch

---

### Step 5.2 — WACK Pre-Certification

Run the Windows App Certification Kit (WACK) locally on the signed `.msix` on both Windows 10 22H2 and Windows 11. Fix all failures before Store submission.

**Common failure categories to pre-empt:**
- Unsupported Win32 APIs called by PyInstaller frozen executable
- Undeclared capability usage in the manifest
- Unsigned binaries within the MSIX package (both sidecars must be handled correctly within the MSIX trust chain)
- Startup performance failure (cold start must be within the <6s ceiling)
- Launch/crash test failure (app must reach interactive state within the WACK test window)

**Acceptance Criteria:**
- Zero WACK failures on Windows 10 22H2
- Zero WACK failures on Windows 11
- WACK reports committed as CI artifacts

---

### Step 5.3 — Store Listing and Submission

Prepare the Store listing: app name, description (does not overstate v1 capabilities — no voice support or mobile sync implied), screenshots (at least 4: chat, reminders, model download, settings), privacy policy URL (accurately describes local-only architecture and opt-in telemetry model), age rating questionnaire.

Submit the signed `.msix`. Configure staged rollout: 10–20% initial rollout, not 100% on day one. Monitor crash rate before expanding.

**Acceptance Criteria:**
- Store submission accepted (no immediate manifest rejection)
- Privacy policy URL live and accurate
- Staged rollout configured at ≤ 20% before launch

---

**Phase 5 Milestone:** Application packaged, signed, WACK-certified, and submitted. Staged rollout live.

---

## Phase 6 — Monitoring, Observability, and Production Readiness

### Step 6.1 — Observability Verification

Verify that structured logging is operational: rotating, size-capped log files for both the Python backend and the Tauri frontend. Log levels: `INFO`, `WARN`, `ERROR` in production builds (DEBUG disabled). No message content in logs — only event names, structured metadata, and stack traces.

Verify that the local metrics dashboard (`MetricsStore` pattern, accessible from Settings → Diagnostics) displays: end-to-end latency per query, agentic routing confidence distribution, error rate by error type, compute time per inference call.

Verify that "Report a problem" correctly packages the redacted log and opens a compose email window with it attached. No raw message content in any log payload.

---

### Step 6.2 — Production Readiness Review

Before expanding Store rollout from 10–20% to 100%, verify from real-usage telemetry (opt-in cohort):

| Metric | Threshold | Action if Below |
|---|---|---|
| Crash-free session rate | ≥ 99.5% | Halt rollout expansion; diagnose; prepare patch |
| Data-loss incidents | Zero | Halt rollout expansion immediately |
| Agentic Tier-1 auto-execute rate (real usage) | ≥ 70% | Investigate; may require prompt tuning before expansion |
| Store rating trajectory | Monitor | Inform v1.1 priorities |

Patch releases follow the same full gate list (characterization tests, performance benchmarks, WACK pre-check) before re-submission.

---

**Phase 6 Milestone:** Application in production. Observability verified. Rollout expansion decision made based on real data.

---

## Deferred Scope (Reference)

| Item | Target |
|---|---|
| Cloud LLM fallback (opt-in, explicit user consent) | v1.1 |
| Voice I/O | v1.1 |
| Keyboard shortcuts + export polish | v1.1 |
| FTS5 paraphrase recall improvement (semantic search over structured tables) | v1.1 candidate — pending Phase 4 eval results |
| E2E encrypted cloud sync | v2 |
| Mobile companion | v2 |
| Re-import from data export | v2 |
| Calendar / email integrations | v3+ |
| Team / multi-user features | Out of scope for this product line |
| sqlite-vec / hnswlib vector search | Revisit only if Phase 2 Step 2.5 benchmarking fails the <200ms target |
