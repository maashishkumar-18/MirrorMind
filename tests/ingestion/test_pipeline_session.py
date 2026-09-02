"""
End-to-end integration test for SessionIngestionPipeline
(src/ingestion/pipeline.py) — Phase 1 Step 1.2. The one LLM call
(metadata extraction) is mocked; everything else is real (local embeddings,
real SQLite via SQLiteVectorStore).
"""

import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from db.connection import open_session_db
from db.migration_runner import MigrationRunner
from src.common.sqlite_vector_store import SQLiteVectorStore
from src.common.types import SessionMessage
from src.ingestion.pipeline import SessionIngestionPipeline

pytestmark = pytest.mark.integration

TS = "2026-09-02T10:00:00Z"
META_JSON = json.dumps(
    {
        "topics": ["weekend plans", "groceries"],
        "action_types": ["todo"],
        "entities": ["Priya"],
        "sentiment": "positive",
        "confidence": 0.9,
    }
)


def _now():
    return datetime.now(UTC).isoformat()


def _messages(n):
    turns = [
        "Let's plan the weekend",
        "Sure — what did you have in mind?",
        "Groceries on Saturday morning, then the market",
        "Sounds good. Add milk and eggs to the list?",
        "Yes, and coffee. Priya is coming over Sunday",
    ]
    return [
        SessionMessage("user" if i % 2 == 0 else "assistant", turns[i % len(turns)] + f" ({i})")
        for i in range(n)
    ]


@pytest.fixture
def store(tmp_path):
    path = str(tmp_path / "session.db")
    MigrationRunner(db_path=path, snapshot_dir=tmp_path / "snap").run()
    conn = open_session_db(path)
    conn.execute(
        "INSERT INTO sessions (id, started_at, created_at, updated_at) VALUES (?,?,?,?)",
        ("s1", _now(), _now(), _now()),
    )
    conn.commit()
    conn.close()
    return SQLiteVectorStore(db_path=path), path


def test_25_message_session_end_to_end(store):
    vec_store, db_path = store
    with patch("src.ingestion.metadata_extractor.simple_generate", return_value=META_JSON):
        result = SessionIngestionPipeline(vec_store).ingest_session(
            "s1", _messages(25), timestamp=TS
        )

    assert result.primary_count == 1
    assert result.sub_chunk_count == 4
    assert result.failed_chunk_ids == []
    assert result.metadata.topics == ["weekend plans", "groceries"]
    assert "s1::primary" in result.chunk_ids

    stats = vec_store.get_stats()
    assert (stats.primary_chunks, stats.sub_chunks, stats.total_sessions) == (1, 4, 1)
    assert stats.embedding_dim == 384

    # Persisted content is RAW session text — no enrichment prefix.
    conn = open_session_db(db_path)
    rows = conn.execute(
        "SELECT id, content, chunk_type, window_start_message_idx, window_end_message_idx "
        "FROM session_chunks WHERE session_id='s1' ORDER BY id"
    ).fetchall()
    conn.close()
    by_id = {r["id"]: r for r in rows}
    assert not by_id["s1::primary"]["content"].startswith("[Session:")
    assert by_id["s1::primary"]["content"].startswith("[User]:")
    assert by_id["s1::primary"]["window_start_message_idx"] is None
    assert by_id["s1::sub::5-14"]["window_start_message_idx"] == 5
    assert by_id["s1::sub::5-14"]["window_end_message_idx"] == 14

    # Semantic query works against the freshly-ingested chunks: a stored
    # embedding retrieves its own chunk first.
    probe_vec = vec_store._matrix[vec_store._id_pos["s1::sub::15-24"]]
    hits = vec_store.query(probe_vec, top_k=3)
    assert hits[0].chunk_id == "s1::sub::15-24"
    assert hits[0].session_id == "s1"


def test_reingest_is_idempotent(store):
    vec_store, _ = store
    with patch("src.ingestion.metadata_extractor.simple_generate", return_value=META_JSON):
        pipe = SessionIngestionPipeline(vec_store)
        pipe.ingest_session("s1", _messages(25), timestamp=TS)
        second = pipe.ingest_session("s1", _messages(25), timestamp=TS)

    assert (second.primary_count, second.sub_chunk_count) == (1, 4)
    assert vec_store.get_stats().total_chunks == 5  # not 10


def test_short_session_primary_only(store):
    vec_store, _ = store
    with patch("src.ingestion.metadata_extractor.simple_generate", return_value=META_JSON):
        result = SessionIngestionPipeline(vec_store).ingest_session(
            "s1", _messages(6), timestamp=TS
        )
    assert (result.primary_count, result.sub_chunk_count) == (1, 0)
    assert vec_store.get_stats().total_chunks == 1
