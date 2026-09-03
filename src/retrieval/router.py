"""
Retrieval Router (Phase 1 Step 1.3b).

The first-class, tested component that turns an ``AgenticOutput`` into
retrieved context. Reads ``retrieval_route`` and dispatches:

- ``SEMANTIC``   -> ``HybridSearch`` (dense + BM25) then ``Reranker`` over the
  local ``SQLiteVectorStore``.
- ``STRUCTURED`` -> ``StructuredTableSearch`` (FTS5 over the structured tables).
- ``HYBRID``     -> both, merged.

``retrieve_needed = false`` short-circuits to an empty list — the caller
serves ``AgenticOutput.response`` directly (project_logic.md §4).

Depends on ``VectorStoreInterface``; the concrete store, the structured
searcher, the reranker and the embedder are all injected.
"""

from dataclasses import replace

from src.common.types import AgenticOutput, RetrievalRoute, SessionRetrievedChunk
from src.common.vector_store import VectorStoreInterface
from src.retrieval.hybrid_search import HybridSearch, SearchCandidate
from src.retrieval.reranker import Reranker
from src.retrieval.structured_search import StructuredTableSearch


class RetrievalRouter:
    def __init__(
        self,
        vector_store: VectorStoreInterface,
        structured_search: StructuredTableSearch,
        *,
        hybrid_search: HybridSearch | None = None,
        reranker: Reranker | None = None,
        embedder: object | None = None,
        top_k: int = 5,
    ):
        self.vector_store = vector_store
        self.structured_search = structured_search
        self.hybrid_search = hybrid_search or HybridSearch(vector_store, embedder=embedder)
        self.reranker = reranker or Reranker()
        self.top_k = top_k

    def route(self, agentic_output: AgenticOutput, query: str) -> list[SessionRetrievedChunk]:
        """Dispatch ``agentic_output`` to a retrieval path. ``[]`` when no
        retrieval is needed."""
        if not agentic_output.retrieve_needed:
            return []

        q = agentic_output.search_query or query
        route = agentic_output.retrieval_route

        if route == RetrievalRoute.SEMANTIC:
            return self._semantic(q)
        if route == RetrievalRoute.STRUCTURED:
            return self.structured_search.search(q, top_k=self.top_k)
        if route == RetrievalRoute.HYBRID:
            return self._merge(
                self._semantic(q), self.structured_search.search(q, top_k=self.top_k)
            )
        return []

    # ------------------------------------------------------------------

    def _semantic(self, query: str) -> list[SessionRetrievedChunk]:
        result = self.hybrid_search.search(query, keywords=query, filters=None)
        pool: dict[str, SessionRetrievedChunk] = {
            c._chunk.chunk_id: c._chunk for c in result.candidates if c._chunk is not None
        }
        if not pool:
            return []

        candidate_dicts = [
            self._candidate_dict(c) for c in result.candidates if c._chunk is not None
        ]
        reranked = self.reranker.rerank(query, candidate_dicts, top_k=self.top_k)

        out: list[SessionRetrievedChunk] = []
        for rc in reranked.chunks:
            base = pool.get(rc.chunk_id)
            if base is None:
                continue
            out.append(
                replace(
                    base,
                    score=float(rc.rerank_score),
                    semantic_score=base.semantic_score,
                    keyword_score=base.keyword_score,
                    metadata_score=None,
                )
            )
        return out

    @staticmethod
    def _candidate_dict(c: SearchCandidate) -> dict:
        chunk = c._chunk
        assert chunk is not None
        return {
            "chunk_id": chunk.chunk_id,
            "content": chunk.content,
            "raw_content": chunk.raw_content,
            "score": c.score,
            "metadata": {
                **c.metadata,
                "parent_chunk_id": chunk.parent_chunk_id,
                "session_id": chunk.session_id,
                "chunk_type": chunk.chunk_type,
                "timestamp": chunk.timestamp,
                "topics": list(chunk.topics),
                "action_types": list(chunk.action_types),
                "entities": list(chunk.entities),
                "message_roles": list(chunk.message_roles),
                "sentiment": chunk.sentiment,
            },
        }

    @staticmethod
    def _merge(
        semantic: list[SessionRetrievedChunk], structured: list[SessionRetrievedChunk]
    ) -> list[SessionRetrievedChunk]:
        by_id: dict[str, SessionRetrievedChunk] = {}
        for chunk in [*semantic, *structured]:
            existing = by_id.get(chunk.chunk_id)
            if existing is None or chunk.score > existing.score:
                by_id[chunk.chunk_id] = chunk
        return sorted(by_id.values(), key=lambda c: c.score, reverse=True)
