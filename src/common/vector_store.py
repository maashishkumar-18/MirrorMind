"""
VectorStoreInterface (Production Roadmap Phase 1 Step 1.1).

The storage-agnostic contract for the session vector store. Both the
ingestion pipeline (writes, via ``upsert``) and the retrieval pipeline
(reads, via ``query``) depend on this ABC — neither imports the concrete
implementation (``src/common/sqlite_vector_store.py``). The concrete
``SQLiteVectorStore`` is constructed once at the application composition
root and injected into whichever components need it.

Introducing this seam here (rather than letting retrieval import
``src.ingestion.vector_store`` as it does today) is what lets Phase 1
migrate the three pipelines incrementally instead of as one coordinated
commit — see RAG_MIGRATION_AUDIT.md's top risk finding.
"""

from abc import ABC, abstractmethod

import numpy as np

from src.common.types import (
    SessionChunkRecord,
    SessionRetrievedChunk,
    StoreStats,
    UpsertResult,
)

__all__ = [
    "VectorStoreInterface",
    "SessionChunkRecord",
    "SessionRetrievedChunk",
    "StoreStats",
    "UpsertResult",
]


class VectorStoreInterface(ABC):
    """Read/write contract for the session-chunk vector store."""

    @abstractmethod
    def upsert(self, chunk: SessionChunkRecord) -> UpsertResult:
        """
        Insert a new chunk or replace an existing one (matched by
        ``chunk.chunk_id``). Implementations persist the row and keep any
        in-memory index in sync.
        """
        ...

    @abstractmethod
    def query(
        self,
        embedding: np.ndarray,
        top_k: int,
        filters: dict | None = None,
    ) -> list[SessionRetrievedChunk]:
        """
        Return up to ``top_k`` chunks most similar to ``embedding`` (cosine),
        best first. ``filters`` is an optional dict of equality/membership
        constraints an implementation may support (e.g. ``session_id``,
        ``chunk_type``); unknown keys are ignored.
        """
        ...

    @abstractmethod
    def delete(self, chunk_id: str) -> bool:
        """
        Soft-delete the chunk with ``chunk_id`` (``deleted_at = now``) and
        drop it from any in-memory index. Returns ``False`` if no active
        chunk with that id existed.
        """
        ...

    @abstractmethod
    def get_stats(self) -> StoreStats:
        """Diagnostic snapshot of the store's current contents."""
        ...
