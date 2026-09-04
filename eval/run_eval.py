"""
Session golden-eval harness (Phase 1 Step 1.4b).

Runs ``eval/golden_qa_set.json`` through the real session pipeline —

    RetrievalAgent.reason() → RetrievalRouter.route() → ContextBuilder
        → GenerationOrchestrator.generate()

— against a fresh, reproducible SQLite database seeded from
``eval/seed_corpus.json``. Scoring is entirely local (no RAGAS, no judge-API
key): faithfulness reuses ``GroundingValidator(method="llm_check")`` (one local
Ollama call), answer-correctness blends ground-truth token recall with
all-MiniLM cosine similarity, and refusal / agentic-routing / temporal accuracy
are heuristics over the pipeline's own structured output.

``main()`` reads the four calibrated thresholds from ``eval/gates.json`` and
exits non-zero on any breach. It also re-hashes ``golden_qa_set.json`` at
startup and fails loudly if it no longer matches the hash the gates were
calibrated against (``_meta.golden_set_sha256``) — the set cannot silently
drift away from its gates.

Usage:
    OLLAMA_DEFAULT_MODEL=llama3.1:8b python -m eval.run_eval  # must match gates.json _meta.model
    python -m eval.run_eval --sample-size 15 --seed 7
    python -m eval.run_eval --calibrate      # skip the hash + gate checks
See eval/README.md for full docs.
"""

import argparse
import hashlib
import json
import logging
import os
import random
import re
import shutil
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv()

import numpy as np

from db.migration_runner import MigrationRunner
from observability.metrics_store import MetricsStore, PipelineCallMetrics
from observability.tracing import (
    current_trace_id,
    current_trace_url,
    flush,
    score_trace,
    traced_pipeline_call,
)
from src.common.sqlite_vector_store import SQLiteVectorStore
from src.common.types import AgenticOutput, SessionMessage
from src.generation.config import (
    ConfidenceLevel,
    GenerationMode,
    GenerationRequest,
    GenerationResponse,
    RetrievalMetadata,
)
from src.generation.orchestrator import GenerationOrchestrator
from src.generation.post_processor import GroundingValidator
from src.ingestion.local_embedder import LocalEmbeddingProvider
from src.ingestion.pipeline import SessionIngestionPipeline
from src.retrieval.reranker import Reranker
from src.retrieval.retrieval_agent import RetrievalAgent
from src.retrieval.router import RetrievalRouter
from src.retrieval.structured_search import StructuredTableSearch

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("session_eval")

GOLDEN_SET_PATH = REPO_ROOT / "eval" / "golden_qa_set.json"
SEED_CORPUS_PATH = REPO_ROOT / "eval" / "seed_corpus.json"
GATES_PATH = REPO_ROOT / "eval" / "gates.json"
RESULTS_DIR = REPO_ROOT / "eval" / "results"

# ============================================================================
# Refusal detection
#
# config/generation/prompts/{context_aware,simple_explanation}.yaml instruct
# the model to emit this exact phrase when the retrieved memory is
# insufficient. The regex anchors on the stable opening and tolerates the
# model lightly varying the tail ("...in our past chats or your notes").
# MUST stay in lock-step with the templates' phrase (roadmap Step 1.4).
# ============================================================================

REFUSAL_PATTERNS = (
    # The templates' phrase ("I don't have anything about that in our past
    # conversations or your notes."), tolerating the model substituting the
    # specific topic for "that" and lightly varying the tail.
    re.compile(
        r"do(?:n'?t| not) have .{0,90}?(?:past (?:conversation|chat)s?|your notes)",
        re.IGNORECASE,
    ),
    # A bare "I don't have any information / record / mention of ..." — the
    # same refusal intent without the template's exact tail (llama-class
    # models drop it routinely).
    re.compile(
        r"do(?:n'?t| not) have any (?:information|record|details|mention|notes|data)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:no|not any) (?:mention|record|information|reference) of\b", re.IGNORECASE),
)


def _contains_refusal_phrase(answer: str) -> bool:
    text = answer or ""
    return any(pattern.search(text) for pattern in REFUSAL_PATTERNS)


# ============================================================================
# Pipeline bootstrap — seed a fresh session DB, build the real components
# ============================================================================


class _FakeCrossEncoder:
    """Deterministic stand-in for sentence-transformers CrossEncoder — used by
    the smoke test and ``--skip-rerank`` so no HuggingFace model is loaded."""

    def predict(self, pairs, **_):
        return np.arange(len(pairs), 0, -1, dtype=float)


@dataclass
class Pipeline:
    agent: RetrievalAgent
    router: RetrievalRouter
    orchestrator: GenerationOrchestrator
    grounding_validator: GroundingValidator
    embedder: LocalEmbeddingProvider
    db_path: str
    tmp_dir: str | None = None

    def close(self) -> None:
        if self.tmp_dir and Path(self.tmp_dir).exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)


def load_seed_corpus(path: Path = SEED_CORPUS_PATH) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _seed_database(conn, corpus: dict[str, Any]) -> None:
    """Insert the seed corpus rows. Order respects the FK graph
    (schedules → schedule_items, sessions → everything else). Structured-record
    ``created_at`` / ``updated_at`` are backdated to the linked session's start
    (or the corpus base date) so the citations the model sees carry the
    corpus's own dates, not the ingest wall-clock."""
    base_ts = f"{corpus.get('_meta', {}).get('base_date', '2026-02-01')}T00:00:00Z"
    session_ts = {s["id"]: s["started_at"] for s in corpus["sessions"]}

    def _record_ts(session_id: str | None) -> str:
        return session_ts.get(session_id or "", base_ts)

    for s in corpus["sessions"]:
        ts = s["started_at"]
        conn.execute(
            "INSERT INTO sessions (id, started_at, created_at, updated_at) VALUES (?,?,?,?)",
            (s["id"], ts, ts, ts),
        )
        for turn_index, m in enumerate(s["messages"]):
            conn.execute(
                "INSERT INTO messages (id, session_id, turn_index, role, content, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (f"{s['id']}-m{turn_index}", s["id"], turn_index, m["role"], m["content"], ts, ts),
            )

    for r in corpus.get("reminders", []):
        conn.execute(
            "INSERT INTO reminders (id, session_id, title, notes, scheduled_time, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (
                r["id"],
                r.get("session_id"),
                r["title"],
                r.get("notes", ""),
                r["scheduled_time"],
                _record_ts(r.get("session_id")),
                _record_ts(r.get("session_id")),
            ),
        )

    for t in corpus.get("todos", []):
        conn.execute(
            "INSERT INTO todos (id, session_id, title, notes, priority, category, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                t["id"],
                t.get("session_id"),
                t["title"],
                t.get("notes", ""),
                t.get("priority"),
                t.get("category"),
                _record_ts(t.get("session_id")),
                _record_ts(t.get("session_id")),
            ),
        )

    for mn in corpus.get("meeting_notes", []):
        conn.execute(
            "INSERT INTO meeting_notes (id, session_id, raw_transcript, attendees, topics, "
            "decisions, action_items, follow_ups, needs_review, searchable_text, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                mn["id"],
                mn.get("session_id"),
                mn["raw_transcript"],
                json.dumps(mn.get("attendees", [])),
                json.dumps(mn.get("topics", [])),
                json.dumps(mn.get("decisions", [])),
                json.dumps(mn.get("action_items", [])),
                json.dumps(mn.get("follow_ups", [])),
                int(mn.get("needs_review", 0)),
                mn.get("searchable_text", ""),
                _record_ts(mn.get("session_id")),
                _record_ts(mn.get("session_id")),
            ),
        )

    for sc in corpus.get("schedules", []):
        conn.execute(
            "INSERT INTO schedules (id, date, title, created_at, updated_at) VALUES (?,?,?,?,?)",
            (
                sc["id"],
                sc["date"],
                sc.get("title"),
                f"{sc['date']}T00:00:00Z",
                f"{sc['date']}T00:00:00Z",
            ),
        )
    for si in corpus.get("schedule_items", []):
        conn.execute(
            "INSERT INTO schedule_items (id, schedule_id, title, start_time, end_time, "
            "location, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                si["id"],
                si["schedule_id"],
                si["title"],
                si["start_time"],
                si["end_time"],
                si.get("location", ""),
                si.get("notes", ""),
                si["start_time"],
                si["start_time"],
            ),
        )
    conn.commit()


def bootstrap_pipeline(
    corpus_path: Path = SEED_CORPUS_PATH,
    *,
    db_path: str | None = None,
    model: str | None = None,
    agent: RetrievalAgent | None = None,
    orchestrator: GenerationOrchestrator | None = None,
    grounding_validator: GroundingValidator | None = None,
    embedder: LocalEmbeddingProvider | None = None,
    stub_rerank: bool = False,
) -> Pipeline:
    """Seed a fresh session DB from ``corpus_path`` and wire the real pipeline.

    ``src.ingestion.metadata_extractor.simple_generate`` is monkeypatched to the
    per-session canned metadata block so ingestion is deterministic and needs
    no Ollama. Everything else (agent reasoning, generation, faithfulness) uses
    the real local model unless the caller injects a stub.
    """
    from unittest.mock import patch

    tmp_dir: str | None = None
    if db_path is None:
        tmp_dir = tempfile.mkdtemp(prefix="eval_session_")
        db_path = str(Path(tmp_dir) / "session.db")

    snapshot_dir = Path(db_path).parent / "snapshots"
    MigrationRunner(db_path=db_path, snapshot_dir=snapshot_dir).run()

    corpus = load_seed_corpus(corpus_path)

    from db.connection import open_session_db
    from src.ingestion.metadata_extractor import MetadataExtractor

    # Review item 4: a malformed canned block silently degrades to an empty
    # SessionMetadata. Verify every block round-trips before ingesting.
    mx = MetadataExtractor()
    for s in corpus["sessions"]:
        parsed = mx._parse_response(s["metadata"], s["id"], s["started_at"])
        if (
            parsed.topics != s["metadata"]["topics"]
            or parsed.action_types != s["metadata"]["action_types"]
        ):
            raise ValueError(f"seed_corpus session {s['id']} metadata block does not round-trip")

    conn = open_session_db(db_path)
    _seed_database(conn, corpus)
    conn.close()

    store = SQLiteVectorStore(db_path=db_path)
    ingest = SessionIngestionPipeline(store)
    for s in corpus["sessions"]:
        messages = [SessionMessage(role=m["role"], content=m["content"]) for m in s["messages"]]
        with patch(
            "src.ingestion.metadata_extractor.simple_generate",
            return_value=json.dumps(s["metadata"]),
        ):
            ingest.ingest_session(s["id"], messages, timestamp=s["started_at"])

    reranker = Reranker()
    if stub_rerank:
        reranker.backend._model = _FakeCrossEncoder()

    router = RetrievalRouter(store, StructuredTableSearch(db_path=db_path), reranker=reranker)

    return Pipeline(
        agent=agent or RetrievalAgent(model=model),
        router=router,
        orchestrator=orchestrator or GenerationOrchestrator(),
        grounding_validator=grounding_validator
        or GroundingValidator(method="llm_check", model=model),
        embedder=embedder or LocalEmbeddingProvider(),
        db_path=db_path,
        tmp_dir=tmp_dir,
    )


# ============================================================================
# Golden set loading / sampling
# ============================================================================


def load_golden_set(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["items"]


def sample_items(
    items: list[dict[str, Any]], sample_size: int | None, seed: int
) -> list[dict[str, Any]]:
    if sample_size is None or sample_size >= len(items):
        return items
    return random.Random(seed).sample(items, sample_size)


def calibration_subset(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The subset the two *calibrated* gate thresholds are derived from
    (``--calibrate`` with no ``--sample-size``): every unanswerable /
    out-of-domain item (``refusal_rate.baseline`` — roadmap Step 1.4 wants
    >=20) and every temporal answerable item (``temporal_accuracy.min``). The
    other two gates (``faithfulness``, ``agentic_routing``) are roadmap-fixed,
    not calibrated here. Running the full set is deferred to the first manual
    ``eval`` CI job — a full 8B pass on CPU is ~1.5h."""
    keep = []
    for it in items:
        if it["category"] == "unanswerable" or (it["is_temporal"] and it["answerable"]):
            keep.append(it)
    return keep


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ============================================================================
# Phase A — run each item through the real pipeline
# ============================================================================


def _confidence_level(confidence: float) -> ConfidenceLevel:
    if confidence >= 0.85:
        return ConfidenceLevel.HIGH
    if confidence >= 0.70:
        return ConfidenceLevel.MEDIUM
    if confidence >= 0.50:
        return ConfidenceLevel.LOW
    return ConfidenceLevel.NONE


@dataclass
class ItemRecord:
    item: dict[str, Any]
    agentic_output: AgenticOutput
    response: GenerationResponse
    chunks: list[Any]
    pipeline_time_ms: float
    retrieval_time_ms: float
    trace_id: str | None = None
    trace_url: str | None = None
    # scores, filled in Phase B
    scores: dict[str, float | None] = field(default_factory=dict)
    refused: bool = False


def run_pipeline_phase(
    pipeline: Pipeline, items: list[dict[str, Any]], env: str = "ci"
) -> list[ItemRecord]:
    records: list[ItemRecord] = []
    for idx, item in enumerate(items, start=1):
        item_start = time.time()
        request_id = str(uuid.uuid4())
        with traced_pipeline_call(
            request_id=request_id,
            query=item["question"],
            env=env,
            tags=[item["category"], "answerable" if item["answerable"] else "unanswerable"],
            metadata={"golden_set_id": item["id"]},
        ) as trace:
            ao = pipeline.agent.reason(item["question"])

            retrieval_start = time.time()
            chunks = pipeline.router.route(ao, item["question"]) if ao.retrieve_needed else []
            retrieval_time_ms = (time.time() - retrieval_start) * 1000

            retrieval_metadata = RetrievalMetadata(
                confidence_score=ao.confidence,
                confidence_level=_confidence_level(ao.confidence),
                retrieval_time_ms=retrieval_time_ms,
                total_chunks_retrieved=len(chunks),
                session_id="",
                retrieval_method=ao.retrieval_route.value,
            )
            request = GenerationRequest(
                request_id=request_id,
                query=item["question"],
                mode=GenerationMode.CONTEXT_AWARE,
                chunks=chunks,
                retrieval_metadata=retrieval_metadata,
            )
            response = pipeline.orchestrator.generate(request)
            trace.update(output={"answer": response.answer, "is_grounded": response.is_grounded})
            trace_id = current_trace_id()
            trace_url = current_trace_url()

        pipeline_time_ms = (time.time() - item_start) * 1000
        logger.info(
            "[%d/%d] %s (%s) route=%s->%s chunks=%d %.0fms",
            idx,
            len(items),
            item["id"],
            item["category"],
            ao.action_type.value,
            ao.retrieval_route.value if ao.retrieve_needed else "none",
            len(chunks),
            pipeline_time_ms,
        )
        records.append(
            ItemRecord(
                item=item,
                agentic_output=ao,
                response=response,
                chunks=chunks,
                pipeline_time_ms=pipeline_time_ms,
                retrieval_time_ms=retrieval_time_ms,
                trace_id=trace_id,
                trace_url=trace_url,
            )
        )
    return records


# ============================================================================
# Phase B — local scoring
# ============================================================================

_WORD_RE = re.compile(r"[a-z0-9]+")
# Weekday / month / quarter / clock-time / ordinal-day tokens.
_TEMPORAL_RE = re.compile(
    r"\b\d{1,2}:\d{2}\s*(?:am|pm)?\b"
    r"|\b\d{1,2}\s*(?:am|pm)\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)\b"
    r"|\bq[1-4]\b"
    r"|\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b"
    r"|\b(?:january|february|march|april|may|june|july|august|september|october|"
    r"november|december)\b",
    re.IGNORECASE,
)


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def score_refusal(response: GenerationResponse) -> bool:
    """A response counts as a refusal if it emits the refusal phrase or the
    generation itself errored. Nothing else (review item 2: low confidence /
    retrieve_needed=false is Tier-4 conversation, not a refusal)."""
    return _contains_refusal_phrase(response.answer) or response.error_type is not None


def score_faithfulness(
    validator: GroundingValidator, answer: str, chunks: list[Any], answerable: bool
) -> float | None:
    """GroundingValidator(llm_check) confidence — answerable items only."""
    if not answerable:
        return None
    _, confidence = validator.validate(answer=answer, chunks=chunks)
    return float(confidence)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def score_correctness(
    embedder: LocalEmbeddingProvider, answer: str, ground_truth: str | None, answerable: bool
) -> float | None:
    """0.5 * ground-truth-token-recall + 0.5 * all-MiniLM cosine, clamped to
    [0, 1]. Answerable items only (review item 3 — 'correctly refused' is
    captured by score_refusal, not conflated into the correctness mean)."""
    if not answerable or not ground_truth:
        return None
    gt_tokens = set(_tokens(ground_truth))
    ans_tokens = set(_tokens(answer))
    recall = (len(gt_tokens & ans_tokens) / len(gt_tokens)) if gt_tokens else 0.0
    cosine = _cosine(embedder.embed_query(ground_truth), embedder.embed_query(answer or ""))
    return max(0.0, min(1.0, 0.5 * recall + 0.5 * cosine))


def score_agentic_routing(ao: AgenticOutput, item: dict[str, Any]) -> float:
    """Fraction of the applicable expected fields that match. retrieve_needed
    and action_type are always applicable; retrieval_route is scored only when
    the item expects retrieval (review item 1)."""
    checks = [
        ao.retrieve_needed == item["expected_retrieve_needed"],
        ao.action_type.value == item["expected_action_type"],
    ]
    if item["expected_retrieve_needed"]:
        checks.append(ao.retrieval_route.value == item["expected_route"])
    return sum(checks) / len(checks)


def _temporal_tokens(text: str) -> set[str]:
    return {m.group(0).lower().replace(" ", "") for m in _TEMPORAL_RE.finditer(text or "")}


def score_temporal(answer: str, item: dict[str, Any]) -> float | None:
    """For is_temporal answerable items: does the answer contain every
    date/time token from the ground truth? ``None`` when the item is not
    temporal, is unanswerable, or its ground truth carries no temporal token."""
    if not item["is_temporal"] or not item["answerable"] or not item.get("ground_truth"):
        return None
    expected = _temporal_tokens(item["ground_truth"])
    if not expected:
        return None
    got = _temporal_tokens(answer)
    return 1.0 if expected <= got else 0.0


def score_records(pipeline: Pipeline, records: list[ItemRecord]) -> None:
    for r in records:
        item = r.item
        answerable = item["answerable"]
        r.refused = score_refusal(r.response)
        r.scores = {
            "refusal": (r.refused if item["category"] == "unanswerable" else None),
            "faithfulness": score_faithfulness(
                pipeline.grounding_validator, r.response.answer, r.chunks, answerable
            ),
            "correctness": score_correctness(
                pipeline.embedder, r.response.answer, item.get("ground_truth"), answerable
            ),
            "agentic_routing": score_agentic_routing(r.agentic_output, item),
            "temporal": score_temporal(r.response.answer, item),
        }


# ============================================================================
# Observability
# ============================================================================


def record_observability_metrics(records: list[ItemRecord], env: str = "ci") -> None:
    store = MetricsStore()
    for r in records:
        faithfulness = r.scores.get("faithfulness")
        store.record(
            PipelineCallMetrics(
                request_id=r.response.request_id,
                env=env,
                query=r.item["question"],
                total_time_ms=r.pipeline_time_ms,
                retrieval_time_ms=r.retrieval_time_ms,
                generation_time_ms=r.response.generation_time_ms,
                confidence_score=r.agentic_output.confidence,
                confidence_level=r.response.retrieval_metadata.confidence_level.value,
                retrieval_hit=len(r.chunks) > 0,
                candidates_retrieved=len(r.chunks),
                is_grounded=r.response.is_grounded,
                citations_count=len(r.response.citations),
                refused=r.refused,
                input_tokens=r.response.usage.input_tokens,
                output_tokens=r.response.usage.output_tokens,
                compute_ms=r.response.generation_time_ms + r.retrieval_time_ms,
                prompt_version=r.response.prompt_version,
                model_name=r.response.model_info.model_name,
                retrieval_pipeline_name=r.agentic_output.retrieval_route.value,
                faithfulness_score=faithfulness,
                langfuse_trace_id=r.trace_id,
                langfuse_trace_url=r.trace_url,
            )
        )
        if r.trace_id and faithfulness is not None:
            score_trace(r.trace_id, "faithfulness", faithfulness)


# ============================================================================
# Phase C — aggregate + report
# ============================================================================


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def aggregate(records: list[ItemRecord]) -> dict[str, Any]:
    faithfulness = [
        r.scores["faithfulness"] for r in records if r.scores["faithfulness"] is not None
    ]
    correctness = [r.scores["correctness"] for r in records if r.scores["correctness"] is not None]
    routing = [r.scores["agentic_routing"] for r in records]
    temporal = [r.scores["temporal"] for r in records if r.scores["temporal"] is not None]
    unanswerable = [r for r in records if r.item["category"] == "unanswerable"]
    refusals = [1.0 if r.refused else 0.0 for r in unanswerable]

    def _bucket(key_fn) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for r in records:
            k = key_fn(r)
            b = out.setdefault(k, {"n": 0, "routing": [], "faithfulness": [], "correctness": []})
            b["n"] += 1
            b["routing"].append(r.scores["agentic_routing"])
            if r.scores["faithfulness"] is not None:
                b["faithfulness"].append(r.scores["faithfulness"])
            if r.scores["correctness"] is not None:
                b["correctness"].append(r.scores["correctness"])
        return {
            k: {
                "n": b["n"],
                "agentic_routing": _mean(b["routing"]),
                "faithfulness": _mean(b["faithfulness"]),
                "correctness": _mean(b["correctness"]),
            }
            for k, b in sorted(out.items())
        }

    return {
        "faithfulness_mean": _mean(faithfulness),
        "correctness_mean": _mean(correctness),
        "agentic_routing": _mean(routing),
        "refusal_rate": _mean(refusals),
        "temporal_accuracy": _mean(temporal),
        "n_answerable_scored": len(faithfulness),
        "n_unanswerable": len(unanswerable),
        "n_temporal_scored": len(temporal),
        "by_category": _bucket(lambda r: r.item["category"]),
        "by_answerability": _bucket(
            lambda r: "answerable" if r.item["answerable"] else "unanswerable"
        ),
        "by_disambiguation_tier": _bucket(lambda r: f"tier_{r.item['disambiguation_tier']}"),
    }


def check_gates(agg: dict[str, Any], gates: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return ``(passed, lines)``. A gate whose metric is ``None`` (nothing to
    score in this run — e.g. a small ``--sample-size``) is skipped, not failed."""
    lines: list[str] = []
    passed = True

    def _gate(name: str, ok: bool | None, detail: str) -> None:
        nonlocal passed
        if ok is None:
            lines.append(f"  ~ {name}: SKIPPED ({detail})")
            return
        lines.append(f"  {'PASS' if ok else 'FAIL'} {name}: {detail}")
        passed = passed and ok

    fm = agg["faithfulness_mean"]
    _gate(
        "faithfulness",
        None if fm is None else fm >= gates["faithfulness"]["min"],
        f"{fm:.3f} >= {gates['faithfulness']['min']}" if fm is not None else "no answerable items",
    )
    ar = agg["agentic_routing"]
    _gate(
        "agentic_routing",
        None if ar is None else ar >= gates["agentic_routing"]["min"],
        f"{ar:.3f} >= {gates['agentic_routing']['min']}" if ar is not None else "no items",
    )
    rr = agg["refusal_rate"]
    band = gates["refusal_rate"]["band_pp"] / 100.0
    base = gates["refusal_rate"]["baseline"]
    _gate(
        "refusal_rate",
        None if rr is None else abs(rr - base) <= band,
        f"|{rr:.3f} - {base}| <= {band:.2f}" if rr is not None else "no unanswerable items",
    )
    ta = agg["temporal_accuracy"]
    _gate(
        "temporal_accuracy",
        None if ta is None else ta >= gates["temporal_accuracy"]["min"],
        (
            f"{ta:.3f} >= {gates['temporal_accuracy']['min']}"
            if ta is not None
            else "no temporal items"
        ),
    )
    return passed, lines


def build_report(
    records: list[ItemRecord],
    agg: dict[str, Any],
    args: argparse.Namespace,
    duration_s: float,
    gate_passed: bool | None,
    gate_lines: list[str],
) -> dict[str, Any]:
    items = []
    for r in records:
        items.append(
            {
                "id": r.item["id"],
                "question": r.item["question"],
                "category": r.item["category"],
                "answerable": r.item["answerable"],
                "ground_truth": r.item.get("ground_truth"),
                "generated_answer": r.response.answer,
                "expected": {
                    "retrieve_needed": r.item["expected_retrieve_needed"],
                    "route": r.item["expected_route"],
                    "action_type": r.item["expected_action_type"],
                },
                "agentic_output": {
                    "action_type": r.agentic_output.action_type.value,
                    "confidence": r.agentic_output.confidence,
                    "retrieve_needed": r.agentic_output.retrieve_needed,
                    "retrieval_route": r.agentic_output.retrieval_route.value,
                    "search_query": r.agentic_output.search_query,
                },
                "chunks": [{"chunk_id": c.chunk_id, "chunk_type": c.chunk_type} for c in r.chunks],
                "scores": r.scores,
                "refused": r.refused,
                "error_type": r.response.error_type,
                "is_temporal": r.item["is_temporal"],
                "disambiguation_tier": r.item["disambiguation_tier"],
                "pipeline_time_ms": round(r.pipeline_time_ms, 1),
                "langfuse_trace_url": r.trace_url,
            }
        )

    return {
        "run_metadata": {
            "timestamp": datetime.now(UTC).isoformat(),
            "duration_seconds": round(duration_s, 1),
            "sample_size": len(records),
            "total_items_in_golden_set": args.total_items,
            "seed": args.seed,
            "model": os.getenv("OLLAMA_DEFAULT_MODEL", "llama3.1:8b"),
            "golden_set_sha256": sha256_file(args.golden_set),
            "calibrate_mode": args.calibrate,
        },
        "aggregate": agg,
        "gates": {"passed": gate_passed, "detail": gate_lines},
        "items": items,
    }


def render_markdown(report: dict[str, Any]) -> str:
    meta = report["run_metadata"]
    agg = report["aggregate"]
    lines = [
        "# Session Golden-Eval Report",
        "",
        f"- Run at: {meta['timestamp']}",
        f"- Model: {meta['model']}",
        f"- Items: {meta['sample_size']} / {meta['total_items_in_golden_set']} (seed={meta['seed']})",
        f"- Duration: {meta['duration_seconds']}s",
        f"- golden_set_sha256: `{meta['golden_set_sha256']}`",
        "",
        "## Aggregate",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| faithfulness_mean | {_fmt(agg['faithfulness_mean'])} ({agg['n_answerable_scored']}) |",
        f"| correctness_mean | {_fmt(agg['correctness_mean'])} |",
        f"| agentic_routing | {_fmt(agg['agentic_routing'])} |",
        f"| refusal_rate | {_fmt(agg['refusal_rate'])} ({agg['n_unanswerable']}) |",
        f"| temporal_accuracy | {_fmt(agg['temporal_accuracy'])} ({agg['n_temporal_scored']}) |",
        "",
        "## Gates",
        "",
        f"**{'PASSED' if report['gates']['passed'] else 'FAILED / not evaluated'}**",
        "",
        "```",
        *report["gates"]["detail"],
        "```",
        "",
        "## By category",
        "",
        "| Category | N | routing | faithfulness | correctness |",
        "|---|---|---|---|---|",
    ]
    for cat, b in agg["by_category"].items():
        lines.append(
            f"| {cat} | {b['n']} | {_fmt(b['agentic_routing'])} | "
            f"{_fmt(b['faithfulness'])} | {_fmt(b['correctness'])} |"
        )
    lines += ["", "## Per-item", ""]
    for it in report["items"]:
        exp, got = it["expected"], it["agentic_output"]
        lines += [
            f"### {it['id']} — {it['category']} "
            f"({'answerable' if it['answerable'] else 'unanswerable'})",
            "",
            f"**Q:** {it['question']}",
            "",
            f"**Ground truth:** {it['ground_truth']}",
            "",
            f"**Answer:** {it['generated_answer']}",
            "",
            f"**Routing:** expected retrieve={exp['retrieve_needed']} route={exp['route']} "
            f"action={exp['action_type']}  |  got retrieve={got['retrieve_needed']} "
            f"route={got['retrieval_route']} action={got['action_type']} "
            f"conf={got['confidence']:.2f}",
            "",
            f"**Scores:** {it['scores']}",
            "",
        ]
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "n/a"


# ============================================================================
# CLI
# ============================================================================


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Session golden-eval harness.")
    p.add_argument("--sample-size", type=int, default=None)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--golden-set", type=Path, default=GOLDEN_SET_PATH)
    p.add_argument("--corpus", type=Path, default=SEED_CORPUS_PATH)
    p.add_argument("--gates", type=Path, default=GATES_PATH)
    p.add_argument("--output-dir", type=Path, default=RESULTS_DIR)
    p.add_argument(
        "--calibrate",
        action="store_true",
        help="Skip the golden-set hash check and do not exit non-zero on a gate breach "
        "(for the calibration run that populates gates.json).",
    )
    p.add_argument(
        "--skip-rerank",
        action="store_true",
        help="Stub the cross-encoder (no HuggingFace model) — faster, lower retrieval quality.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    with open(args.gates, encoding="utf-8") as f:
        gates = json.load(f)

    golden_hash = sha256_file(args.golden_set)
    expected_hash = gates.get("_meta", {}).get("golden_set_sha256", "")
    if not args.calibrate:
        if not expected_hash or expected_hash.upper() == "PENDING":
            logger.error(
                "eval/gates.json has no calibrated golden_set_sha256 — run --calibrate first."
            )
            return 2
        if golden_hash != expected_hash:
            logger.error(
                "golden_qa_set.json hash %s != gates.json _meta.golden_set_sha256 %s — "
                "the set has drifted from its calibrated gates; recalibrate.",
                golden_hash,
                expected_hash,
            )
            return 2

    all_items = load_golden_set(args.golden_set)
    args.total_items = len(all_items)
    if args.calibrate and args.sample_size is None:
        items = calibration_subset(all_items)
        logger.info(
            "Loaded %d golden items; --calibrate subset = %d items.", len(all_items), len(items)
        )
    else:
        items = sample_items(all_items, args.sample_size, args.seed)
        logger.info("Loaded %d golden items, evaluating %d.", len(all_items), len(items))

    run_start = time.time()
    pipeline = bootstrap_pipeline(args.corpus, stub_rerank=args.skip_rerank)
    try:
        records = run_pipeline_phase(pipeline, items)
        score_records(pipeline, records)
    finally:
        pipeline.close()

    record_observability_metrics(records)
    flush()

    agg = aggregate(records)
    if args.calibrate:
        gate_passed, gate_lines = None, ["(--calibrate: gates not evaluated)"]
    else:
        gate_passed, gate_lines = check_gates(agg, gates)

    duration_s = time.time() - run_start
    report = build_report(records, agg, args, duration_s, gate_passed, gate_lines)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = args.output_dir / f"eval_{stamp}.json"
    md_path = args.output_dir / f"eval_{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    logger.info("Wrote %s and %s", json_path, md_path)

    logger.info(
        "Aggregate: %s",
        {
            k: _fmt(agg[k])
            for k in (
                "faithfulness_mean",
                "correctness_mean",
                "agentic_routing",
                "refusal_rate",
                "temporal_accuracy",
            )
        },
    )
    for line in gate_lines:
        logger.info(line)

    if args.calibrate:
        return 0
    return 0 if gate_passed else 1


if __name__ == "__main__":
    sys.exit(main())
