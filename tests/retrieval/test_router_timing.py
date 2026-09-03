"""
Router / vector-search timing re-validation (Phase 1 Step 1.3c).

Replaces the deleted orchestrator.yaml's 15000ms cloud-API timeout with the
local-stack budgets from config/retrieval/router.yaml. This test verifies the
timing instrumentation and the code path at small scale (~120 chunks); the
authoritative production-scale benchmark (10,000 chunks on reference hardware —
production_roadmap §9.1) is Phase 2 Step 2.5, not here.

Real all-MiniLM embeddings; the cross-encoder is stubbed (no HF download), so
the measured route() latency excludes real cross-encoder inference — a
comment below records the real number seen locally during implementation.
"""

import time
from datetime import UTC, datetime

import numpy as np
import pytest

from db.connection import open_session_db
from db.migration_runner import MigrationRunner
from src.common.sqlite_vector_store import SQLiteVectorStore
from src.common.types import (
    AgenticActionType,
    AgenticOutput,
    RetrievalRoute,
    SessionChunkRecord,
)
from src.retrieval.router import RetrievalRouter, RetrievalRouterConfig
from src.retrieval.structured_search import StructuredTableSearch

pytestmark = pytest.mark.integration

N_CHUNKS = 120
DIM = 384
BUDGET = RetrievalRouterConfig()  # built-in defaults == config/retrieval/router.yaml


def _now() -> str:
    return datetime.now(UTC).isoformat()


class _FakeCrossEncoder:
    def predict(self, pairs, **_):
        return np.arange(len(pairs), 0, -1, dtype=float)


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("timing")
    path = str(tmp / "session.db")
    MigrationRunner(db_path=path, snapshot_dir=tmp / "snap").run()

    conn = open_session_db(path)
    conn.execute(
        "INSERT INTO sessions (id, started_at, created_at, updated_at) VALUES ('s1',?,?,?)",
        (_now(), _now(), _now()),
    )
    conn.commit()
    conn.close()

    store = SQLiteVectorStore(db_path=path)
    rng = np.random.default_rng(7)
    for i in range(N_CHUNKS):
        vec = rng.standard_normal(DIM).astype(np.float32)
        store.upsert(
            SessionChunkRecord(
                chunk_id=f"s1::sub::{i}",
                session_id="s1",
                content=f"synthetic session chunk number {i} about topic {i % 11}",
                embedding=vec,
                token_count=12,
                chunk_type="sub_chunk",
                window_start_message_idx=i,
                window_end_message_idx=i + 9,
                topics=[f"topic{i % 11}"],
            )
        )
    return store, path


def test_vector_search_latency_under_budget(seeded):
    store, _ = seeded
    q = np.random.default_rng(1).standard_normal(DIM).astype(np.float32)
    for _ in range(5):  # warm
        store.query(q, top_k=10)

    n = 50
    start = time.perf_counter()
    for _ in range(n):
        store.query(q, top_k=10)
    mean_ms = (time.perf_counter() - start) / n * 1000

    assert mean_ms < BUDGET.vector_search_target_ms, f"{mean_ms:.1f}ms mean over {N_CHUNKS} chunks"


def test_semantic_route_latency_under_end_to_end_budget(seeded):
    store, path = seeded
    reranker_router = RetrievalRouter(
        store,
        StructuredTableSearch(db_path=path),
        reranker=_reranker(),
        top_k=5,
    )
    ao = AgenticOutput(
        action_type=AgenticActionType.RETRIEVAL_QUERY,
        confidence=0.9,
        retrieve_needed=True,
        retrieval_route=RetrievalRoute.SEMANTIC,
        search_query="synthetic session chunk about topic 3",
        response="",
    )
    reranker_router.route(ao, "q")  # warm (loads embedder)

    n = 10
    start = time.perf_counter()
    for _ in range(n):
        reranker_router.route(ao, "q")
    mean_ms = (time.perf_counter() - start) / n * 1000

    # Measured locally during 1.3c implementation (120 chunks, warm):
    #   vector_store.query()          ~0.1 ms
    #   route() + REAL cross-encoder  ~15 ms
    #   route() + stubbed encoder     a few ms
    # All ~100x-1000x under budget at this scale; the 10k-chunk /
    # reference-hardware benchmark is Phase 2 Step 2.5.
    assert mean_ms < BUDGET.end_to_end_target_ms, f"{mean_ms:.1f}ms mean route() latency"


def _reranker():
    from src.retrieval.reranker import Reranker

    r = Reranker()
    r.backend._model = _FakeCrossEncoder()
    return r
