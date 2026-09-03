"""
Hybrid Search Engine (Phase 1 Step 1.3b — local, session-shaped).

Combines dense (vector) retrieval over the local ``SQLiteVectorStore`` with
sparse BM25 keyword retrieval and a light metadata-boost pass into a single
candidate pool for the reranker.

- Semantic: cosine similarity over the in-memory numpy embedding array,
  via ``VectorStoreInterface.query()`` — no Pinecone, no namespaces.
- Keyword: BM25 (``rank_bm25``) over the *dense candidate pool* returned by
  the semantic pass. A global BM25 corpus over every chunk is a v1.1
  follow-up; at personal scale the dense pool (top ``semantic_k``) is a
  sufficient keyword-rescoring surface.
- Metadata: re-query with each configured session field appended to the
  query text, down-weighted by ``metadata_match_threshold``.

The score-fusion / dedup / normalization helpers (``_merge_candidates``,
``_weighted_merge``, ``_reciprocal_rank_fusion``, ``_deduplicate``,
``_normalize_scores``) are characterization-pinned
(tests/retrieval/test_hybrid_search_fusion.py) and kept byte-identical.

All weights/thresholds load from config/retrieval/hybrid_search.yaml with
environment variable overrides.
"""

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from dotenv import load_dotenv

from src.common.types import SessionRetrievedChunk
from src.common.vector_store import VectorStoreInterface
from src.retrieval.config import HybridWeights

load_dotenv()


# ============================================================================
# Configuration
# ============================================================================


@dataclass
class HybridSearchConfig:
    """Configuration for hybrid search — loaded from YAML."""

    weights: HybridWeights
    semantic_k: int = 40
    keyword_k: int = 20
    max_total_candidates: int = 60
    dedup_threshold: float = 0.85
    dedup_strategy: str = "score"
    merge_strategy: str = "weighted"
    rrf_k: int = 60
    normalize_method: str = "minmax"
    metadata_enabled: bool = True
    metadata_fields: list[str] = field(default_factory=list)
    metadata_match_threshold: float = 0.3

    @classmethod
    def from_yaml(cls, path: str | None = None) -> "HybridSearchConfig":
        """
        Load configuration from YAML file.

        Priority:
        1. Explicit path argument
        2. RAGPIPE_HYBRID_SEARCH_CONFIG env var
        3. config/retrieval/hybrid_search.yaml (default)
        """
        config_path = (
            path
            or os.getenv("RAGPIPE_HYBRID_SEARCH_CONFIG")
            or str(
                Path(__file__).parent.parent.parent / "config" / "retrieval" / "hybrid_search.yaml"
            )
        )

        if Path(config_path).exists():
            with open(config_path) as f:
                data = yaml.safe_load(f)
        else:
            data = cls._default_config()

        # Apply environment variable overrides
        data = cls._apply_env_overrides(data)

        return cls(
            weights=HybridWeights(**data.get("weights", {})),
            semantic_k=int(data.get("candidates", {}).get("semantic_k", 40)),
            keyword_k=int(data.get("candidates", {}).get("keyword_k", 20)),
            max_total_candidates=int(data.get("candidates", {}).get("max_total", 60)),
            dedup_threshold=float(data.get("dedup", {}).get("threshold", 0.85)),
            dedup_strategy=str(data.get("dedup", {}).get("strategy", "score")),
            merge_strategy=str(data.get("merge", {}).get("strategy", "weighted")),
            rrf_k=int(data.get("merge", {}).get("rrf_k", 60)),
            normalize_method=str(data.get("normalize", {}).get("method", "minmax")),
            metadata_enabled=bool(data.get("metadata", {}).get("enabled", True)),
            metadata_fields=list(data.get("metadata", {}).get("fields", [])),
            metadata_match_threshold=float(data.get("metadata", {}).get("match_threshold", 0.3)),
        )

    @classmethod
    def _apply_env_overrides(cls, data: dict) -> dict:
        """Apply environment variable overrides to config data."""
        env_mapping = {
            "RAGPIPE_SEMANTIC_K": ("candidates", "semantic_k"),
            "RAGPIPE_KEYWORD_K": ("candidates", "keyword_k"),
            "RAGPIPE_MAX_CANDIDATES": ("candidates", "max_total"),
            "RAGPIPE_DEDUP_THRESHOLD": ("dedup", "threshold"),
            "RAGPIPE_MERGE_STRATEGY": ("merge", "strategy"),
            "RAGPIPE_SEMANTIC_WEIGHT": ("weights", "semantic"),
            "RAGPIPE_KEYWORD_WEIGHT": ("weights", "keyword"),
            "RAGPIPE_METADATA_WEIGHT": ("weights", "metadata"),
        }

        for env_var, (section, key) in env_mapping.items():
            value = os.getenv(env_var)
            if value is not None:
                if section not in data:
                    data[section] = {}
                try:
                    # Handle boolean values
                    if value.lower() in ["true", "false"]:
                        data[section][key] = value.lower() == "true"
                    elif "." in value:
                        data[section][key] = float(value)
                    else:
                        data[section][key] = int(value)
                except ValueError:
                    data[section][key] = value

        return data

    @staticmethod
    def _default_config() -> dict:
        """Minimal fallback configuration."""
        return {
            "weights": {"semantic": 0.6, "keyword": 0.25, "metadata": 0.15},
            "candidates": {"semantic_k": 40, "keyword_k": 20, "max_total": 60},
            "dedup": {"threshold": 0.85, "strategy": "score"},
            "merge": {"strategy": "weighted", "rrf_k": 60},
            "normalize": {"method": "minmax"},
            "metadata": {
                "enabled": True,
                "fields": ["topics", "action_types", "entities", "sentiment", "message_roles"],
                "match_threshold": 0.3,
            },
        }


# ============================================================================
# Search Result Types
# ============================================================================


@dataclass
class SearchCandidate:
    """A single candidate from any search method."""

    chunk_id: str
    content: str
    raw_content: str
    score: float  # Normalized score [0, 1]
    source: str  # "semantic", "keyword_bm25", or "metadata"
    original_rank: int  # Rank in its source list
    metadata: dict[str, Any] = field(default_factory=dict)

    # Session-era traceability (Phase 1 Step 1.3b). Optional — the fusion
    # characterization fixtures build SearchCandidate from dicts with only
    # the seven fields above, so every field below must have a default.
    session_id: str = ""
    chunk_type: str = ""
    timestamp: str = ""
    topics: list[str] = field(default_factory=list)
    action_types: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    message_roles: list[str] = field(default_factory=list)
    sentiment: str = ""
    parent_chunk_id: str | None = None
    # The source SessionRetrievedChunk, carried as a NAMED field (not a
    # metadata dict key) so the pinned fusion helpers — which rewrite
    # `.metadata` and `.score` — can never drop it. `repr=False` keeps
    # candidate reprs readable in test failures.
    _chunk: SessionRetrievedChunk | None = field(default=None, repr=False, compare=False)


@dataclass
class HybridSearchResult:
    """Complete hybrid search result."""

    candidates: list[SearchCandidate]
    total_retrieved: int
    sources_used: list[str]
    processing_time_ms: float
    query: str


# ============================================================================
# True Keyword Search with BM25
# ============================================================================


class BM25Index:
    """
    Lightweight BM25 index for sparse keyword retrieval.

    PRODUCTION NOTE: For large-scale deployments, replace with:
    - Elasticsearch/OpenSearch
    - Pinecone sparse-dense index
    - LanceDB with BM25
    - Tantivy or similar dedicated search engine

    This implementation uses rank_bm25 for demonstration and testing.
    """

    def __init__(self, index_path: str | None = None):
        self.index_path = index_path
        self.index = None
        self.documents = []
        self.tokenizer = lambda x: self._tokenize(x)
        self._initialized = False

    def _tokenize(self, text: str) -> list[str]:
        """Simple tokenizer for BM25."""
        # Convert to lowercase and split on non-alphanumeric
        import re

        text = text.lower()
        tokens = re.findall(r"\w+", text)
        return tokens

    def build_index(self, documents: list[dict[str, Any]]) -> None:
        """
        Build BM25 index from a list of documents.

        Args:
            documents: List of dicts with 'id', 'content', and optional 'metadata'
        """
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            raise ImportError(
                "rank_bm25 is required for BM25 indexing. " "Install with: pip install rank_bm25"
            )

        self.documents = documents
        tokenized_docs = [self.tokenizer(doc["content"]) for doc in documents]
        self.index = BM25Okapi(tokenized_docs)
        self._initialized = True

        # Save index if path provided
        if self.index_path:
            self._save_index()

    def _save_index(self) -> None:
        """Save index to disk for persistence."""
        if not self.index_path:
            return

        # Save document metadata
        meta_path = Path(self.index_path).with_suffix(".meta.json")
        meta_path.parent.mkdir(parents=True, exist_ok=True)

        with open(meta_path, "w") as f:
            json.dump({"documents": self.documents, "num_docs": len(self.documents)}, f)

    def load_index(self) -> bool:
        """Load index from disk."""
        if not self.index_path:
            return False

        meta_path = Path(self.index_path).with_suffix(".meta.json")
        if not meta_path.exists():
            return False

        try:
            with open(meta_path) as f:
                data = json.load(f)
                self.documents = data["documents"]
                # Rebuild index from documents
                self.build_index(self.documents)
                return True
        except Exception as e:
            print(f"Failed to load BM25 index: {e}")
            return False

    def search(self, query: str, top_k: int | None = None) -> list[tuple[int, float]]:
        """
        Search the BM25 index.

        Args:
            query: Query text
            top_k: Number of results to return. If None, returns every
                non-zero-scored document ranked best-first — used by callers
                that need to post-filter (e.g. by namespace) before truncating.

        Returns:
            List of (document_index, score) tuples
        """
        if not self._initialized:
            raise RuntimeError(
                "BM25 index not initialized. Call build_index() or load_index() first."
            )

        tokenized_query = self.tokenizer(query)
        scores = self.index.get_scores(tokenized_query)

        ranked_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        if top_k is not None:
            ranked_indices = ranked_indices[:top_k]

        return [(i, scores[i]) for i in ranked_indices if scores[i] > 0]


# ============================================================================
# Hybrid Search Engine
# ============================================================================


class HybridSearch:
    """
    Local hybrid search combining semantic, keyword, and metadata retrieval
    over the session ``VectorStoreInterface``.

    Depends only on the ``VectorStoreInterface`` ABC — the concrete
    ``SQLiteVectorStore`` is injected at the application composition root.
    """

    def __init__(
        self,
        vector_store: VectorStoreInterface,
        config: HybridSearchConfig | None = None,
        config_path: str | None = None,
        *,
        embedder: Any | None = None,
    ):
        """
        Args:
            vector_store: the session vector store (VectorStoreInterface).
            config: HybridSearchConfig object (overrides the config file).
            config_path: Path to a YAML config (overrides env var + default).
            embedder: an EmbeddingGenerator (defaults to a local one).
        """
        self.vector_store = vector_store
        self.config = config or HybridSearchConfig.from_yaml(config_path)

        if embedder is None:
            from src.ingestion.embedder import EmbeddingGenerator

            embedder = EmbeddingGenerator(enable_logging=False)
        self._embedder = embedder

    # ========================================================================
    # Public API
    # ========================================================================

    def search(
        self,
        query: str,
        keywords: str,
        *,
        filters: dict[str, Any] | None = None,
        pipeline_weights: HybridWeights | None = None,
    ) -> HybridSearchResult:
        """
        Execute hybrid search across all configured sources.

        Args:
            query: Full query for semantic search.
            keywords: Keyword-only variant for sparse (BM25) search.
            filters: Optional store filter dict (e.g. ``{"session_id": ...}``);
                passed straight through to ``VectorStoreInterface.query``.
            pipeline_weights: Optional per-call weight override.

        Returns:
            HybridSearchResult with deduplicated, scored candidates.
        """
        start_time = time.time()
        weights = pipeline_weights or self.config.weights

        candidates: list[SearchCandidate] = []
        sources_used: list[str] = []

        needs_semantic = weights.semantic > 0
        needs_keyword = weights.keyword > 0 and bool(keywords)
        needs_metadata = (
            weights.metadata > 0 and self.config.metadata_enabled and self.config.metadata_fields
        )
        metadata_field_queries = (
            [f"{query} {meta_field}" for meta_field in self.config.metadata_fields]
            if needs_metadata
            else []
        )

        # Batch-embed every query variant in one call.
        embed_texts: list[str] = []
        if needs_semantic:
            embed_texts.append(query)
        embed_texts.extend(metadata_field_queries)

        vectors = self._embedder.embed_queries(embed_texts) if embed_texts else []

        offset = 0
        semantic_vector = None
        if needs_semantic:
            semantic_vector = vectors[offset] if offset < len(vectors) else None
            offset += 1
        metadata_vectors = vectors[offset : offset + len(metadata_field_queries)]

        # 1. Semantic (dense) search
        semantic_candidates: list[SearchCandidate] = []
        if needs_semantic:
            semantic_candidates = self._semantic_search(
                query, self.config.semantic_k, filters, semantic_vector
            )
            candidates.extend(semantic_candidates)
            if semantic_candidates:
                sources_used.append("semantic")

        # 2. Keyword (BM25) search over the dense candidate pool
        if needs_keyword:
            keyword_results = self._keyword_search(
                keywords, self.config.keyword_k, semantic_candidates
            )
            candidates.extend(keyword_results)
            if keyword_results:
                sources_used.append("keyword_bm25")

        # 3. Metadata-boost search
        if needs_metadata:
            metadata_results = self._metadata_search(
                query, self.config.semantic_k // 2, filters, metadata_vectors
            )
            candidates.extend(metadata_results)
            if metadata_results:
                sources_used.append("metadata")

        # 4. Merge → 5. Deduplicate → 6. Limit
        merged = self._merge_candidates(candidates, weights)
        deduped = self._deduplicate(merged)
        final = deduped[: self.config.max_total_candidates]

        processing_time = (time.time() - start_time) * 1000

        return HybridSearchResult(
            candidates=final,
            total_retrieved=len(final),
            sources_used=sources_used,
            processing_time_ms=round(processing_time, 2),
            query=query,
        )

    # ========================================================================
    # Search Methods
    # ========================================================================

    def _semantic_search(
        self,
        query: str,
        top_k: int,
        filters: dict[str, Any] | None = None,
        precomputed_vector: Any | None = None,
    ) -> list[SearchCandidate]:
        """Dense retrieval via ``VectorStoreInterface.query``."""
        vec = precomputed_vector
        if vec is None:
            vec = self._embedder.embed_query(query)
        if vec is None:
            return []
        vec = np.asarray(vec, dtype=np.float32)
        # ndarray truthiness is ambiguous — check .size, never `if not vec`.
        if vec.size == 0:
            return []

        hits = self.vector_store.query(vec, top_k=top_k, filters=filters or None)
        return [
            self._candidate_from_chunk(h, source="semantic", rank=i) for i, h in enumerate(hits, 1)
        ]

    def _keyword_search(
        self,
        keywords: str,
        top_k: int,
        semantic_candidates: list[SearchCandidate],
    ) -> list[SearchCandidate]:
        """
        BM25 sparse retrieval over the dense candidate pool.

        Re-scores the chunks the semantic pass already surfaced — a chunk
        the embedding missed entirely is out of reach here; raising
        ``semantic_k`` widens the keyword-search surface. A global BM25
        corpus over every chunk is a v1.1 follow-up.
        """
        pool = [c for c in semantic_candidates if c._chunk is not None]
        if not keywords or not pool:
            return []

        documents = [
            {"id": c._chunk.chunk_id, "content": c._chunk.raw_content or c._chunk.content}
            for c in pool
        ]
        index = BM25Index()
        index.build_index(documents)
        hits = index.search(keywords, top_k=top_k)
        if not hits:
            return []

        by_id = {c._chunk.chunk_id: c._chunk for c in pool}
        results = [
            self._candidate_from_chunk(
                by_id[documents[doc_idx]["id"]],
                source="keyword_bm25",
                rank=rank,
                score=float(score),
            )
            for rank, (doc_idx, score) in enumerate(hits, 1)
        ]
        return self._normalize_scores(results)

    def _metadata_search(
        self,
        query: str,
        top_k: int,
        filters: dict[str, Any] | None = None,
        precomputed_vectors: list[Any] | None = None,
    ) -> list[SearchCandidate]:
        """
        Light metadata boost: re-query with each configured session field
        appended to the query, down-weighted by ``metadata_match_threshold``.
        """
        fields = self.config.metadata_fields
        if not fields:
            return []

        candidates: list[SearchCandidate] = []
        vectors = precomputed_vectors or [None] * len(fields)
        per_field_k = max(5, top_k // max(1, len(fields)))

        for meta_field, vec in zip(fields, vectors, strict=False):
            field_results = self._semantic_search(
                f"{query} {meta_field}", per_field_k, filters, vec
            )
            for c in field_results:
                c.source = "metadata"
                c.score *= self.config.metadata_match_threshold
            candidates.extend(field_results)

        return candidates

    @staticmethod
    def _candidate_from_chunk(
        chunk: SessionRetrievedChunk,
        *,
        source: str,
        rank: int,
        score: float | None = None,
    ) -> SearchCandidate:
        """Adapt a store ``SessionRetrievedChunk`` into a ``SearchCandidate``."""
        return SearchCandidate(
            chunk_id=chunk.chunk_id,
            content=chunk.content,
            raw_content=chunk.raw_content,
            score=float(chunk.score if score is None else score),
            source=source,
            original_rank=rank,
            metadata={**chunk.metadata, "parent_chunk_id": chunk.parent_chunk_id},
            session_id=chunk.session_id,
            chunk_type=chunk.chunk_type,
            timestamp=chunk.timestamp,
            topics=list(chunk.topics),
            action_types=list(chunk.action_types),
            entities=list(chunk.entities),
            message_roles=list(chunk.message_roles),
            sentiment=chunk.sentiment,
            parent_chunk_id=chunk.parent_chunk_id,
            _chunk=chunk,
        )

    # ========================================================================
    # Merging & Scoring  (characterization-pinned — keep byte-identical)
    # ========================================================================

    def _merge_candidates(
        self, candidates: list[SearchCandidate], weights: HybridWeights
    ) -> list[SearchCandidate]:
        """Merge candidates from different sources with weighted scoring."""

        if self.config.merge_strategy == "rrf":
            return self._reciprocal_rank_fusion(candidates, weights)
        else:
            return self._weighted_merge(candidates, weights)

    def _weighted_merge(
        self, candidates: list[SearchCandidate], weights: HybridWeights
    ) -> list[SearchCandidate]:
        """Merge by applying source-specific weights to scores."""
        source_weights = {
            "semantic": weights.semantic,
            "keyword": weights.keyword,
            "keyword_bm25": weights.keyword,
            "keyword_fallback": weights.keyword * 0.5,  # Penalize fallback
            "metadata": weights.metadata,
        }

        for candidate in candidates:
            w = source_weights.get(candidate.source, 0.0)
            candidate.score *= w

        # Sort by weighted score descending
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    def _reciprocal_rank_fusion(
        self, candidates: list[SearchCandidate], weights: HybridWeights
    ) -> list[SearchCandidate]:
        """Merge using Reciprocal Rank Fusion."""
        # Group by source and rank within each source
        by_source: dict[str, list[SearchCandidate]] = {}
        for c in candidates:
            by_source.setdefault(c.source, []).append(c)

        # Sort each source list by score
        for source in by_source:
            by_source[source].sort(key=lambda c: c.score, reverse=True)

        # Apply RRF formula: score = 1 / (k + rank)
        source_weights = {
            "semantic": weights.semantic,
            "keyword": weights.keyword,
            "keyword_bm25": weights.keyword,
            "keyword_fallback": weights.keyword * 0.5,
            "metadata": weights.metadata,
        }

        for source, source_candidates in by_source.items():
            w = source_weights.get(source, 0.0)
            for rank, candidate in enumerate(source_candidates):
                candidate.score = w / (self.config.rrf_k + rank + 1)

        # Sort all by new score
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    # ========================================================================
    # Deduplication  (characterization-pinned — keep byte-identical)
    # ========================================================================

    def _deduplicate(self, candidates: list[SearchCandidate]) -> list[SearchCandidate]:
        """Remove near-duplicate chunks.

        Near-duplicates are chunks that were split from the same source
        chunk (tracked via metadata["parent_chunk_id"], set by the chunker
        only when a chunk was split — empty otherwise). Chunk IDs themselves
        (e.g. "ch 9_chunk_0003") can't be used for this via string-splitting:
        every chunk in a file shares the same prefix once the trailing index
        is stripped, which would collapse the entire file into one chunk.
        """
        if not candidates:
            return []

        unique = []
        seen_ids = set()

        for candidate in candidates:
            if candidate.chunk_id in seen_ids:
                continue

            parent_id = candidate.metadata.get("parent_chunk_id") or None

            is_duplicate = False
            if parent_id:
                for existing in unique:
                    existing_parent_id = existing.metadata.get("parent_chunk_id") or None
                    if parent_id == existing_parent_id:
                        # Split from the same source chunk — keep the higher-scored one
                        if self.config.dedup_strategy == "score":
                            if candidate.score > existing.score:
                                unique.remove(existing)
                                unique.append(candidate)
                        is_duplicate = True
                        break

            if not is_duplicate:
                unique.append(candidate)
                seen_ids.add(candidate.chunk_id)

        # Re-sort after dedup
        unique.sort(key=lambda c: c.score, reverse=True)
        return unique

    # ========================================================================
    # Score Normalization  (characterization-pinned — keep byte-identical)
    # ========================================================================

    def _normalize_scores(self, candidates: list[SearchCandidate]) -> list[SearchCandidate]:
        """Normalize scores to [0, 1] range."""
        if not candidates:
            return candidates

        scores = [c.score for c in candidates]
        min_score = min(scores)
        max_score = max(scores)

        if max_score == min_score:
            for c in candidates:
                c.score = 1.0
            return candidates

        if self.config.normalize_method == "minmax":
            for c in candidates:
                c.score = (c.score - min_score) / (max_score - min_score)
        elif self.config.normalize_method == "zscore":
            mean = sum(scores) / len(scores)
            std = (sum((s - mean) ** 2 for s in scores) / len(scores)) ** 0.5
            if std > 0:
                for c in candidates:
                    c.score = max(0.0, min(1.0, (c.score - mean) / (2 * std) + 0.5))

        return candidates
