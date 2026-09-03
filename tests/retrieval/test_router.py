"""
End-to-end integration test for RetrievalRouter (src/retrieval/router.py) —
Phase 1 Step 1.3b.

Real local stack: tmp-file SQLite + 0001 migration, a synthetic session
ingested through SessionIngestionPipeline (only the metadata LLM call is
mocked — embeddings are real all-MiniLM-L6-v2), plus seeded structured rows.
The cross-encoder model is stubbed exactly as
tests/retrieval/test_reranker_cross_encoder.py does (no HuggingFace download).
All three routes are exercised and asserted to return SessionRetrievedChunk
instances with the right chunk_type.
"""

import json
from datetime import UTC, datetime
from unittest.mock import patch

import numpy as np
import pytest

from db.connection import open_session_db
from db.migration_runner import MigrationRunner
from src.common.sqlite_vector_store import SQLiteVectorStore
from src.common.types import (
    AgenticActionType,
    AgenticOutput,
    RetrievalRoute,
    SessionMessage,
    SessionRetrievedChunk,
)
from src.ingestion.pipeline import SessionIngestionPipeline
from src.retrieval.context_builder import ContextBuilder
from src.retrieval.reranker import Reranker
from src.retrieval.router import RetrievalRouter
from src.retrieval.structured_search import StructuredTableSearch

pytestmark = pytest.mark.integration

TS = "2026-09-02T10:00:00Z"
META_JSON = json.dumps(
    {
        "topics": ["weekend trip", "packing"],
        "action_types": ["todo"],
        "entities": ["Priya"],
        "sentiment": "positive",
        "confidence": 0.9,
    }
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _messages(n: int) -> list[SessionMessage]:
    turns = [
        "Let's plan the weekend trip to the coast",
        "Great — when do you want to leave?",
        "Saturday morning. We need to pack the tent and sleeping bags",
        "I'll add the tent to the packing list. Anything else?",
        "Sunscreen and the cooler. Priya is bringing snacks",
    ]
    return [
        SessionMessage("user" if i % 2 == 0 else "assistant", f"{turns[i % len(turns)]} ({i})")
        for i in range(n)
    ]


class _FakeCrossEncoder:
    """Deterministic, pool-size-adaptive stand-in for sentence-transformers CrossEncoder."""

    def predict(self, pairs, **_):
        return np.arange(len(pairs), 0, -1, dtype=float)


@pytest.fixture
def router(tmp_path):
    path = str(tmp_path / "session.db")
    MigrationRunner(db_path=path, snapshot_dir=tmp_path / "snap").run()

    conn = open_session_db(path)
    conn.execute(
        "INSERT INTO sessions (id, started_at, created_at, updated_at) VALUES (?,?,?,?)",
        ("s1", _now(), _now(), _now()),
    )
    conn.executemany(
        "INSERT INTO reminders (id, session_id, title, notes, scheduled_time, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            ("r1", "s1", "Pack the tent for the trip", "", _now(), _now(), _now()),
            ("r2", "s1", "Call the dentist", "cleaning", _now(), _now(), _now()),
        ],
    )
    conn.execute(
        "INSERT INTO todos (id, session_id, title, notes, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        ("t1", "s1", "Buy sunscreen and a cooler", "for the weekend trip", _now(), _now()),
    )
    conn.commit()
    conn.close()

    store = SQLiteVectorStore(db_path=path)
    with patch("src.ingestion.metadata_extractor.simple_generate", return_value=META_JSON):
        SessionIngestionPipeline(store).ingest_session("s1", _messages(25), timestamp=TS)

    reranker = Reranker()
    reranker.backend._model = _FakeCrossEncoder()

    return RetrievalRouter(
        store,
        StructuredTableSearch(db_path=path),
        reranker=reranker,
        top_k=5,
    )


def _ao(route: RetrievalRoute, query: str, *, retrieve: bool = True) -> AgenticOutput:
    return AgenticOutput(
        action_type=AgenticActionType.RETRIEVAL_QUERY,
        confidence=0.9,
        retrieve_needed=retrieve,
        retrieval_route=route,
        search_query=query,
        response="",
    )


def test_semantic_route_returns_session_chunks(router):
    out = router.route(_ao(RetrievalRoute.SEMANTIC, "what should we pack for the trip"), "q")
    assert out
    assert all(isinstance(c, SessionRetrievedChunk) for c in out)
    assert all(c.chunk_type in {"primary", "sub_chunk"} for c in out)
    # topics denormalised at ingest survive the round trip through upsert/_load
    assert "weekend trip" in out[0].topics


def test_structured_route_returns_structured_records(router):
    out = router.route(_ao(RetrievalRoute.STRUCTURED, "dentist"), "q")
    assert out
    assert all(c.chunk_type == "structured_record" for c in out)
    assert any("dentist" in c.content.lower() for c in out)


def test_hybrid_route_merges_both_kinds(router):
    out = router.route(_ao(RetrievalRoute.HYBRID, "pack the tent for the trip"), "q")
    kinds = {c.chunk_type for c in out}
    assert "structured_record" in kinds
    assert kinds & {"primary", "sub_chunk"}
    assert len({c.chunk_id for c in out}) == len(out)  # no duplicate chunk_ids


def test_retrieve_needed_false_short_circuits(router):
    out = router.route(_ao(RetrievalRoute.SEMANTIC, "hi", retrieve=False), "hi")
    assert out == []


def test_hybrid_normalizes_so_a_lone_structured_hit_ranks_top(router):
    # "pack the tent for the trip" is the exact r1 reminder title -> a strong
    # structured hit. Raw structured score (1/(1+bm25)) is small vs. semantic
    # rerank scores; after _merge's per-list min-max + structured tie-break it
    # should reach 1.0 and lead the merged list (Step 1.3c fix).
    out = router.route(_ao(RetrievalRoute.HYBRID, "pack the tent for the trip"), "q")
    assert out
    assert out[0].chunk_type == "structured_record"
    assert out[0].score == pytest.approx(1.0)


@pytest.mark.parametrize(
    "route,query",
    [
        (RetrievalRoute.SEMANTIC, "what should we pack for the trip"),
        (RetrievalRoute.STRUCTURED, "dentist"),
        (RetrievalRoute.HYBRID, "pack the tent for the trip"),
    ],
)
def test_end_to_end_route_then_context_assembly(router, route, query):
    """The roadmap's end-to-end retrieval test: route -> ContextBuilder."""
    chunks = router.route(_ao(route, query), "q")
    assert chunks
    assembled = ContextBuilder().build(chunks)
    assert assembled.formatted_context
    assert assembled.chunks_used >= 1
    assert "· approx." in assembled.formatted_context  # a session/record citation
    lowered = assembled.formatted_context.lower()
    for word in ("course", "chapter", "slide", "page "):
        assert word not in lowered
