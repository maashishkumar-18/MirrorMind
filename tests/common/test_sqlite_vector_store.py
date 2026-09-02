"""
Integration tests for SQLiteVectorStore (src/common/sqlite_vector_store.py)
— Phase 1 Step 1.1.

Uses a tmp_path file DB with the real 0001 migration applied via
MigrationRunner (same setup pattern as tests/common/db/test_schema_ddl.py).
The "in-memory" part of this store is the numpy embedding matrix, not the
DB — the DB is a normal on-disk SQLite file.
"""

import sqlite3
from datetime import UTC, datetime

import numpy as np
import pytest

from db.connection import open_session_db
from db.migration_runner import MigrationRunner
from src.common.sqlite_vector_store import SQLiteVectorStore
from src.common.types import SessionChunkRecord, SessionRetrievedChunk

pytestmark = pytest.mark.integration

DIM = 384


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _unit(*weights: float) -> np.ndarray:
    """A deterministic 384-d vector: first len(weights) dims set, rest zero."""
    v = np.zeros(DIM, dtype=np.float32)
    for i, w in enumerate(weights):
        v[i] = w
    return v


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "session.db")
    MigrationRunner(db_path=path, snapshot_dir=tmp_path / "snap").run()
    conn = open_session_db(path)
    for sid in ("s1", "s2"):
        conn.execute(
            "INSERT INTO sessions (id, started_at, created_at, updated_at) VALUES (?,?,?,?)",
            (sid, _now(), _now(), _now()),
        )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def store(db_path):
    return SQLiteVectorStore(db_path=db_path)


def _rec(chunk_id, session_id, vec, chunk_type="primary", **kw):
    return SessionChunkRecord(
        chunk_id=chunk_id,
        session_id=session_id,
        content=kw.get("content", f"content of {chunk_id}"),
        embedding=vec,
        token_count=kw.get("token_count", 10),
        chunk_type=chunk_type,
        window_start_message_idx=kw.get("ws"),
        window_end_message_idx=kw.get("we"),
        topics=kw.get("topics", []),
        action_types=kw.get("action_types", []),
        entities=kw.get("entities", []),
        message_roles=kw.get("message_roles", []),
        sentiment=kw.get("sentiment", ""),
    )


def test_missing_table_raises(tmp_path):
    empty = str(tmp_path / "empty.db")
    sqlite3.connect(empty).close()
    with pytest.raises(RuntimeError, match="session_chunks"):
        SQLiteVectorStore(db_path=empty)


def test_requires_exactly_one_of_db_path_or_connection(db_path):
    with pytest.raises(ValueError):
        SQLiteVectorStore()
    with pytest.raises(ValueError):
        SQLiteVectorStore(db_path=db_path, connection=sqlite3.connect(":memory:"))


def test_upsert_then_query_returns_session_retrieved_chunks(store):
    store.upsert(_rec("c1", "s1", _unit(1, 0, 0), topics=["budget"]))
    store.upsert(_rec("c2", "s1", _unit(0, 1, 0), chunk_type="sub_chunk", ws=0, we=9))

    results = store.query(_unit(0.9, 0.1, 0), top_k=2)
    assert [r.chunk_id for r in results] == ["c1", "c2"]
    assert all(isinstance(r, SessionRetrievedChunk) for r in results)

    top = results[0]
    assert top.session_id == "s1"
    assert top.chunk_type == "primary"
    assert top.score == pytest.approx(top.semantic_score)
    assert top.keyword_score is None and top.metadata_score is None
    assert 0.0 < top.score <= 1.0
    assert top.source_prefix.startswith("[Session: s1 |")
    assert "budget" in top.source_prefix
    assert top.parent_chunk_id is None  # primary


def test_subchunk_parent_is_derived_from_primary(store):
    store.upsert(_rec("p", "s1", _unit(1, 0, 0)))
    store.upsert(_rec("sub", "s1", _unit(1, 0, 0), chunk_type="sub_chunk", ws=0, we=9))
    (sub,) = (r for r in store.query(_unit(1, 0, 0), top_k=5) if r.chunk_id == "sub")
    assert sub.parent_chunk_id == "p"


def test_cosine_ranking_of_three_known_vectors(store):
    # sub_chunks so several can share one session (only one primary is allowed)
    store.upsert(_rec("near", "s1", _unit(1, 0, 0), chunk_type="sub_chunk", ws=0, we=9))
    store.upsert(_rec("mid", "s1", _unit(1, 1, 0), chunk_type="sub_chunk", ws=5, we=14))
    store.upsert(_rec("far", "s1", _unit(0, 0, 1), chunk_type="sub_chunk", ws=10, we=19))
    ranked = [r.chunk_id for r in store.query(_unit(1, 0, 0), top_k=3)]
    assert ranked == ["near", "mid", "far"]


def test_session_id_and_chunk_type_filters(store):
    store.upsert(_rec("a", "s1", _unit(1, 0, 0), chunk_type="primary"))
    store.upsert(_rec("b", "s2", _unit(1, 0, 0), chunk_type="primary"))
    store.upsert(_rec("c", "s2", _unit(1, 0, 0), chunk_type="sub_chunk", ws=0, we=9))

    only_s2 = store.query(_unit(1, 0, 0), top_k=10, filters={"session_id": "s2"})
    assert {r.chunk_id for r in only_s2} == {"b", "c"}

    subs = store.query(_unit(1, 0, 0), top_k=10, filters={"chunk_type": "sub_chunk"})
    assert {r.chunk_id for r in subs} == {"c"}

    excl = store.query(_unit(1, 0, 0), top_k=10, filters={"exclude_chunk_ids": ["a", "b"]})
    assert {r.chunk_id for r in excl} == {"c"}


def test_upsert_updates_existing_row_and_reports_operation(store):
    r1 = store.upsert(_rec("c1", "s1", _unit(1, 0, 0), content="v1"))
    assert r1.operation == "inserted"
    r2 = store.upsert(_rec("c1", "s1", _unit(0, 1, 0), content="v2"))
    assert r2.operation == "updated"
    assert store.get_stats().total_chunks == 1
    (hit,) = store.query(_unit(0, 1, 0), top_k=1)
    assert hit.content == "v2"


def test_delete_soft_deletes_and_is_idempotent(store, db_path):
    store.upsert(_rec("c1", "s1", _unit(1, 0, 0)))
    assert store.delete("c1") is True
    assert store.delete("c1") is False
    assert store.query(_unit(1, 0, 0), top_k=5) == []

    conn = open_session_db(db_path)
    row = conn.execute("SELECT deleted_at FROM session_chunks WHERE id='c1'").fetchone()
    conn.close()
    assert row["deleted_at"] is not None  # row still there, just soft-deleted


def test_second_primary_per_session_raises_integrity_error(store):
    store.upsert(_rec("p1", "s1", _unit(1, 0, 0)))
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert(_rec("p2", "s1", _unit(0, 1, 0)))


def test_reopen_reloads_from_disk(db_path):
    s1 = SQLiteVectorStore(db_path=db_path)
    s1.upsert(_rec("c1", "s1", _unit(1, 0, 0)))
    s1.upsert(_rec("c2", "s2", _unit(0, 1, 0)))
    s1.delete("c2")

    s2 = SQLiteVectorStore(db_path=db_path)
    assert s2.get_stats().total_chunks == 1
    assert [r.chunk_id for r in s2.query(_unit(1, 0, 0), top_k=5)] == ["c1"]


def test_wal_mode_active(db_path):
    store = SQLiteVectorStore(db_path=db_path)
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_get_stats_counts(store):
    store.upsert(_rec("p1", "s1", _unit(1, 0, 0)))
    store.upsert(_rec("sub1", "s1", _unit(1, 1, 0), chunk_type="sub_chunk", ws=0, we=9))
    store.upsert(_rec("p2", "s2", _unit(0, 1, 0)))
    stats = store.get_stats()
    assert (stats.total_chunks, stats.total_sessions) == (3, 2)
    assert (stats.primary_chunks, stats.sub_chunks) == (2, 1)
    assert stats.embedding_dim == DIM


def test_empty_store_query_returns_empty(store):
    assert store.query(_unit(1, 0, 0), top_k=5) == []


def test_wrong_dimension_embedding_rejected(store):
    bad = SessionChunkRecord(
        chunk_id="c1",
        session_id="s1",
        content="x",
        embedding=np.ones(10, dtype=np.float32),
        token_count=1,
    )
    with pytest.raises(ValueError, match="dims"):
        store.upsert(bad)
