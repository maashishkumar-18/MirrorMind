"""
Unit tests for VectorStoreInterface (src/common/vector_store.py) — Phase 1
Step 1.1. The ABC itself and the three result dataclasses; the concrete
SQLiteVectorStore has its own module.
"""

import numpy as np
import pytest

from src.common.types import SessionChunkRecord, SessionRetrievedChunk, StoreStats, UpsertResult
from src.common.vector_store import VectorStoreInterface

pytestmark = pytest.mark.unit


def test_interface_cannot_be_instantiated():
    with pytest.raises(TypeError):
        VectorStoreInterface()  # type: ignore[abstract]


def test_partial_implementation_is_still_abstract():
    class Half(VectorStoreInterface):
        def upsert(self, chunk):  # noqa: D401
            return None

    with pytest.raises(TypeError):
        Half()  # type: ignore[abstract]


def test_minimal_concrete_subclass_satisfies_the_contract():
    class Fake(VectorStoreInterface):
        def __init__(self):
            self._store: dict[str, SessionChunkRecord] = {}

        def upsert(self, chunk):
            op = "updated" if chunk.chunk_id in self._store else "inserted"
            self._store[chunk.chunk_id] = chunk
            return UpsertResult(chunk.chunk_id, op, len(self._store))

        def query(self, embedding, top_k, filters=None):
            return [
                SessionRetrievedChunk(
                    chunk_id=c.chunk_id,
                    session_id=c.session_id,
                    content=c.content,
                    raw_content=c.content,
                )
                for c in list(self._store.values())[:top_k]
            ]

        def delete(self, chunk_id):
            return self._store.pop(chunk_id, None) is not None

        def get_stats(self):
            return StoreStats(len(self._store), 0, 384, 0, 0)

    store = Fake()
    rec = SessionChunkRecord(
        chunk_id="c1",
        session_id="s1",
        content="hello",
        embedding=np.ones(384, dtype=np.float32),
        token_count=1,
    )
    assert store.upsert(rec) == UpsertResult("c1", "inserted", 1)
    assert store.upsert(rec).operation == "updated"
    assert store.query(np.ones(384, dtype=np.float32), top_k=5)[0].chunk_id == "c1"
    assert store.delete("c1") is True
    assert store.delete("c1") is False
    assert store.get_stats().embedding_dim == 384


def test_result_dataclasses_construct_and_are_comparable():
    assert UpsertResult("c", "inserted", 3) == UpsertResult("c", "inserted", 3)
    stats = StoreStats(
        total_chunks=5, total_sessions=2, embedding_dim=384, primary_chunks=2, sub_chunks=3
    )
    assert stats.total_chunks == stats.primary_chunks + stats.sub_chunks
