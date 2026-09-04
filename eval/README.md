# Session Golden Evaluation Set

The Personal AI Companion's golden eval (Phase 1 Step 1.4b). Three committed
files drive it:

| File | What it is |
|---|---|
| `seed_corpus.json` | A hand-authored synthetic memory — 10 multi-turn sessions + 20 structured records (reminders / todos / meeting notes / schedule items) about a small recurring cast. The harness seeds this into a fresh SQLite DB on every run. |
| `golden_qa_set.json` | 86 evaluation items. Every answerable item's `ground_truth` is a fact that exists **only** in `seed_corpus.json` — nothing is answerable from world knowledge. |
| `gates.json` | The four CI gate thresholds. Two are roadmap-fixed; two (`refusal_rate.baseline`, `temporal_accuracy.min`) are calibrated from real local runs — see "Calibration" below. |

There is **no RAGAS and no judge-API key.** Scoring is entirely local
(`run_eval.py`).

## How a run works

`python -m eval.run_eval` (`OLLAMA_DEFAULT_MODEL` must be set):

1. **Bootstrap** (`bootstrap_pipeline()`) — `MigrationRunner` on a tmp DB,
   insert every `seed_corpus.json` row, then ingest each session through the
   real `SessionIngestionPipeline`. `src.ingestion.metadata_extractor.simple_generate`
   is monkeypatched to the session's canned metadata block, so ingestion is
   deterministic and needs no Ollama. Embeddings are the real bundled
   `all-MiniLM-L6-v2`.
2. **Per item** — `RetrievalAgent.reason(question)` → `AgenticOutput`; if
   `retrieve_needed` then `RetrievalRouter.route()` → chunks, else no chunks;
   build a `GenerationRequest` and call `GenerationOrchestrator.generate()`.
3. **Score** (all local):

   | Score | How | Applies to |
   |---|---|---|
   | `refusal` | the answer matches `REFUSAL_PATTERNS` (the templates' exact refusal phrase) **or** `response.error_type` is set. Nothing else. | `category == "unanswerable"` |
   | `faithfulness` | `GroundingValidator(method="llm_check")` confidence — one local Ollama call grading the answer against the retrieved context, keyword-overlap fallback on failure. | answerable items |
   | `correctness` | `0.5 · ground-truth-token-recall + 0.5 · all-MiniLM cosine(answer, ground_truth)`, clamped to `[0, 1]`. | answerable items (report only, not gated) |
   | `agentic_routing` | fraction of the applicable expected fields (`retrieve_needed`, `action_type`, and `retrieval_route` only when retrieval is expected) that match the `AgenticOutput`. | every item |
   | `temporal` | does the answer contain every weekday/month/clock-time/ordinal token from the ground truth? | `is_temporal` answerable items whose ground truth has such a token |

   `disambiguation_tier` is a report-breakdown dimension only — **never scored.**
4. **Report** — timestamped `eval/results/eval_<ts>.{json,md}` (gitignored,
   uploaded as a CI artifact). Aggregates + per-category / per-tier breakdowns
   + full per-item detail.

## `golden_qa_set.json` schema

```jsonc
{
  "id": "q001",
  "question": "when did we say we'd move the launch to?",
  "ground_truth": "Friday" | null,              // null for unanswerable
  "answerable": true | false,
  "category": "conversation_recall | reminder | todo | meeting | schedule |
               summary_request | paraphrase_recall | action_request |
               conversation | unanswerable",
  "expected_retrieve_needed": true | false,
  "expected_route": "semantic | structured | hybrid" | null,   // null when retrieve_needed=false
  "expected_action_type": "retrieval_query | reminder | todo | meeting_note |
                           schedule | summary_request | conversation | none",
  "disambiguation_tier": 1 | 2 | 3 | 4 | null,   // roadmap §3 confidence tier; report-only
  "is_temporal": true | false,
  "source_ref": "session s3" | "reminder r7" | null,
  "notes": "..." | null
}
```

Coverage (roadmap Step 1.4 deliverables, asserted by
`tests/eval/test_harness_smoke.py`): every `category` and `action_type`; all
four disambiguation tiers; **11 "structured-data paraphrase recall"** items
(the query shares no keywords with the stored value); **20 unanswerable /
out-of-domain** items including hard negatives (a plausible follow-up whose
specific answer was deliberately never stated); **17 temporal** items.

## Calibration

Two of the four gates are **roadmap-fixed** and committed straight from
`audits/production_roadmap.md` (Step 1.4 + the Phase 4 gate table), not
calibrated here: `faithfulness.min` = 0.6 and `agentic_routing.min` = 0.9.
The roadmap is explicit that these are enforced from Phase 4 onward ("The
eval must pass before Phase 5 begins"), not at 1.4b — 1.4b's job is to
*commit* them alongside a working harness and a reviewed set.

The other two — `refusal_rate.baseline` and `temporal_accuracy.min` — are
measured from real local `--calibrate` runs against **`llama3.1:8b`**, the
app's default model (`.env.example`; `config/generation/models.yaml` falls
back to it). `temporal_accuracy.min` is the measured accuracy minus a 5pp
margin for run-to-run stochasticity (a second confirmation run checks the
band holds); the independent reviewer (`docs/eval_review.md`) signs off on
the floor. `refusal_rate` is a two-sided band: `baseline ± band_pp`.

`gates.json._meta.golden_set_sha256` pins the exact `golden_qa_set.json` the
gates were calibrated against; `run_eval.py` re-hashes the file at startup
and refuses to run if it has drifted (recalibrate, don't silently move the
set out from under its gates). `--calibrate` skips both the hash check and
the gate pass/fail and, with no `--sample-size`, runs the 35-item subset the
two calibrated gates are derived from (every unanswerable item + every
temporal answerable item) — a full 8B pass on CPU is ~1.5h and is left to
the manual `eval` CI job.

## Running it

```bash
# Full 86-item gated run (what the manual CI `eval` job runs)
OLLAMA_DEFAULT_MODEL=llama3.1:8b LANGFUSE_PUBLIC_KEY="" LANGFUSE_SECRET_KEY="" \
  ./.venv/Scripts/python.exe -m eval.run_eval

# Fast iteration — a random subset
python -m eval.run_eval --sample-size 15 --seed 7

# Re-calibrate the gates (35-item subset, no hash/gate check)
python -m eval.run_eval --calibrate
```

| Flag | Meaning |
|---|---|
| `--sample-size N` / `--seed N` | evaluate a reproducible random subset |
| `--calibrate` | skip the hash + gate checks; with no `--sample-size`, run the 35-item calibration subset (unanswerable + temporal answerable) |
| `--skip-rerank` | stub the cross-encoder (faster, lower retrieval quality) |
| `--golden-set` / `--corpus` / `--gates` / `--output-dir` | path overrides |

## CI

| Job | Trigger | What runs |
|---|---|---|
| `test` → `tests/eval/test_harness_smoke.py` | every push / PR | Schema + wiring only. `bootstrap_pipeline()` + a 2-item pass with **every LLM call stubbed** (agent, generation model, cross-encoder, `llm_check`). No Ollama, no network. |
| `eval` | `workflow_dispatch` only | The real gated run on `ubuntu-latest`: install Ollama, `ollama pull llama3.1:8b` (must match `gates.json` `_meta.model`), `python -m eval.run_eval`. Exits non-zero on any gate breach. Report uploaded as the `eval-results` artifact. |

## Second-reviewer sign-off

`docs/eval_review.md` — an independent fresh-context review of a ≥20-item
sample: coverage completeness, ground-truth accuracy checked directly
against `seed_corpus.json`, question realism, and the recommended
`temporal_accuracy` floor. Verdict: **sign-off with follow-ups.** Implements
the mitigation for product-spec Risk #6 ("old eval set gives false confidence
in release readiness").

Non-blocking follow-ups it raised (tracked for a post-1.4b eval-hardening
pass): tighten/document the `paraphrase_recall` "no keyword overlap" claim
(~half the 11 items share a topical noun with their source record); add
headroom above the 20-item unanswerable floor; widen or reword the q061
(`conversation` vs `action_request`) and q044/q045 (`structured` vs `hybrid`)
routing expectations; give q024 ("reminders coming up this week") a reference
"today" or drop its relative phrasing.

**q082** ("what's the capital of Australia?") is a deliberate out-of-domain
probe: `expected_action_type: conversation`, `expected_retrieve_needed:
false`. The companion is not a general-knowledge bot, so the *intent* is that
it declines to answer from memory — but because the base model may simply
know "Canberra", a q082 non-refusal in a report is expected behaviour, not a
regression.
