"""
Retrieval Router (Phase 1 Step 1.3b; config + HYBRID score normalization 1.3c).

The first-class, tested component that turns an ``AgenticOutput`` into
retrieved context. Reads ``retrieval_route`` and dispatches:

- ``SEMANTIC``   -> ``HybridSearch`` (dense + BM25) then ``Reranker`` over the
  local ``SQLiteVectorStore``.
- ``STRUCTURED`` -> ``StructuredTableSearch`` (FTS5 over the structured tables).
- ``HYBRID``     -> both, each sub-list min-max normalized to [0, 1] so a strong
  structured hit isn't buried under semantic scores, then merged (score ties go
  to the structured record — an exact match on stored user data).

``retrieve_needed = false`` short-circuits to an empty list — the caller
serves ``AgenticOutput.response`` directly (project_logic.md §4).

Depends on ``VectorStoreInterface``; the concrete store, the structured
searcher, the reranker and the embedder are all injected. ``top_k`` and the
timing budgets load from config/retrieval/router.yaml.
"""

import os
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from src.common.types import AgenticOutput, RetrievalRoute, SessionRetrievedChunk
from src.common.vector_store import VectorStoreInterface
from src.retrieval.hybrid_search import HybridSearch, SearchCandidate
from src.retrieval.reranker import Reranker
from src.retrieval.structured_search import StructuredTableSearch


@dataclass
class RetrievalRouterConfig:
    """Router configuration — loaded from config/retrieval/router.yaml."""

    top_k: int = 5
    # Timing budgets, measured against the LOCAL stack (all-MiniLM embed +
    # brute-force numpy cosine + local cross-encoder), replacing the deleted
    # orchestrator.yaml 15000ms cloud-API timeout. The authoritative
    # reference-hardware benchmark at 10k chunks is Phase 2 Step 2.5.
    vector_search_target_ms: int = 200
    end_to_end_target_ms: int = 3500

    @classmethod
    def from_yaml(cls, path: str | None = None) -> "RetrievalRouterConfig":
        config_path = (
            path
            or os.getenv("RAGPIPE_ROUTER_CONFIG")
            or str(Path(__file__).parent.parent.parent / "config" / "retrieval" / "router.yaml")
        )
        data: dict = {}
        if Path(config_path).exists():
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

        routing = data.get("routing", {})
        timing = data.get("timing", {})
        defaults = cls()
        top_k = int(os.getenv("RAGPIPE_ROUTER_TOP_K", routing.get("top_k", defaults.top_k)))
        return cls(
            top_k=top_k,
            vector_search_target_ms=int(
                timing.get("vector_search_target_ms", defaults.vector_search_target_ms)
            ),
            end_to_end_target_ms=int(
                timing.get("end_to_end_target_ms", defaults.end_to_end_target_ms)
            ),
        )


class RetrievalRouter:
    def __init__(
        self,
        vector_store: VectorStoreInterface,
        structured_search: StructuredTableSearch,
        *,
        hybrid_search: HybridSearch | None = None,
        reranker: Reranker | None = None,
        embedder: object | None = None,
        config: RetrievalRouterConfig | None = None,
        top_k: int | None = None,
    ):
        self.vector_store = vector_store
        self.structured_search = structured_search
        self.hybrid_search = hybrid_search or HybridSearch(vector_store, embedder=embedder)
        self.reranker = reranker or Reranker()
        self.config = config or RetrievalRouterConfig.from_yaml()
        self.top_k = top_k if top_k is not None else self.config.top_k

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
    def _normalize(chunks: list[SessionRetrievedChunk]) -> list[SessionRetrievedChunk]:
        """Min-max scale ``.score`` to [0, 1] within this list. A single
        result (or an all-equal list) becomes 1.0."""
        if not chunks:
            return []
        scores = [c.score for c in chunks]
        lo, hi = min(scores), max(scores)
        if hi == lo:
            return [replace(c, score=1.0) for c in chunks]
        return [replace(c, score=(c.score - lo) / (hi - lo)) for c in chunks]

    @classmethod
    def _merge(
        cls,
        semantic: list[SessionRetrievedChunk],
        structured: list[SessionRetrievedChunk],
    ) -> list[SessionRetrievedChunk]:
        by_id: dict[str, SessionRetrievedChunk] = {}
        for chunk in [*cls._normalize(semantic), *cls._normalize(structured)]:
            existing = by_id.get(chunk.chunk_id)
            if existing is None or chunk.score > existing.score:
                by_id[chunk.chunk_id] = chunk
        # Score desc; a tie goes to the structured record (an exact keyword
        # match on a stored reminder/todo/note is high-precision intent).
        return sorted(
            by_id.values(),
            key=lambda c: (-c.score, 0 if c.chunk_type == "structured_record" else 1),
        )
