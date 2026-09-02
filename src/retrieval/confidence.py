"""
Semantic coverage scoring.

Evaluates how well a piece of retrieved context covers a query — keyword
overlap by default, optionally blended with embedding cosine similarity.

Trimmed to just this in Phase 1 Step 1.3a: the document-era ``ConfidenceScorer``
/ ``WeightOptimizer`` / retry-parameter machinery went with the study-assistant
orchestrator. ``SemanticCoverageScorer`` stays because it is behaviourally
pinned by ``tests/retrieval/test_confidence_semantic_coverage.py`` and the
Router's grounding check (Step 1.3b) reuses it.
"""

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml


@dataclass
class SemanticCoverageConfig:
    """Configuration for semantic coverage scoring."""

    use_keywords: bool = True
    min_query_term_overlap: float = 0.3
    use_embeddings: bool = False
    embedding_model: str | None = None
    embedding_similarity_threshold: float = 0.7
    embedding_weight: float = 0.5  # Blend with keyword coverage
    cache_embeddings: bool = True

    @classmethod
    def from_yaml(cls, path: str | None = None) -> "SemanticCoverageConfig":
        """
        Load the ``semantic_coverage:`` section of the config YAML.

        Priority: explicit ``path`` > ``RAGPIPE_CONFIDENCE_CONFIG`` env var >
        ``config/retrieval/confidence.yaml`` > built-in defaults.
        """
        config_path = (
            path
            or os.getenv("RAGPIPE_CONFIDENCE_CONFIG")
            or str(Path(__file__).parents[2] / "config" / "retrieval" / "confidence.yaml")
        )
        section: dict = {}
        if Path(config_path).exists():
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            section = data.get("semantic_coverage", {}) or {}

        defaults = cls()
        return cls(
            use_keywords=bool(section.get("use_keywords", defaults.use_keywords)),
            min_query_term_overlap=float(
                section.get("min_query_term_overlap", defaults.min_query_term_overlap)
            ),
            use_embeddings=bool(section.get("use_embeddings", defaults.use_embeddings)),
            embedding_model=section.get("embedding_model", defaults.embedding_model),
            embedding_similarity_threshold=float(
                section.get(
                    "embedding_similarity_threshold", defaults.embedding_similarity_threshold
                )
            ),
            embedding_weight=float(section.get("embedding_weight", defaults.embedding_weight)),
            cache_embeddings=bool(section.get("cache_embeddings", defaults.cache_embeddings)),
        )


class SemanticCoverageScorer:
    """
    Evaluates semantic coverage between query and context.

    Supports both keyword-based and embedding-based coverage.
    """

    def __init__(self, config: SemanticCoverageConfig):
        self.config = config
        self._embedding_model = None
        self._embedding_cache: dict[str, np.ndarray] = {}

    def _get_embedding_model(self):
        """Lazy load embedding model."""
        if self._embedding_model is None and self.config.use_embeddings:
            try:
                from sentence_transformers import SentenceTransformer

                model_name = self.config.embedding_model or "all-MiniLM-L6-v2"
                self._embedding_model = SentenceTransformer(model_name)
            except ImportError:
                # Fall back to a simple embedding approximation
                self._embedding_model = "fallback"
                print("Warning: sentence-transformers not installed, using fallback embeddings")
        return self._embedding_model

    def _get_embedding(self, text: str) -> np.ndarray | None:
        """Get embedding for text with caching."""
        if not self.config.use_embeddings:
            return None

        # Check cache
        if self.config.cache_embeddings and text in self._embedding_cache:
            return self._embedding_cache[text]

        # Get embedding
        model = self._get_embedding_model()
        if model is None:
            return None

        if model == "fallback":
            # Simple fallback: use TF-IDF style
            embedding = self._fallback_embedding(text)
        else:
            try:
                embedding = model.encode(text, convert_to_numpy=True)
            except Exception:
                embedding = self._fallback_embedding(text)

        # Cache
        if self.config.cache_embeddings:
            self._embedding_cache[text] = embedding

        return embedding

    def _fallback_embedding(self, text: str) -> np.ndarray:
        """A deterministic pseudo-embedding for when no real model is available."""
        np.random.seed(sum(ord(c) for c in text[:100]))
        return np.random.normal(0, 1, 384)

    def _keyword_coverage(self, query: str, context: str) -> float:
        """Calculate keyword-based coverage."""
        if not query or not context:
            return 0.0

        context_lower = context.lower()
        query_terms = set()

        for word in query.lower().split():
            clean = word.strip('.,?!()[]{}":;')
            if len(clean) > 2:
                query_terms.add(clean)

        if not query_terms:
            return 0.5

        matched = sum(1 for term in query_terms if term in context_lower)
        coverage = matched / len(query_terms)

        return coverage

    def _embedding_coverage(self, query: str, context: str) -> float | None:
        """Calculate embedding-based coverage."""
        if not self.config.use_embeddings or not query or not context:
            return None

        query_embedding = self._get_embedding(query)
        context_embedding = self._get_embedding(context)

        if query_embedding is None or context_embedding is None:
            return None

        # Cosine similarity
        similarity = np.dot(query_embedding, context_embedding) / (
            np.linalg.norm(query_embedding) * np.linalg.norm(context_embedding) + 1e-6
        )

        # Normalize similarity to [0, 1]
        # Similarity of 0.5 -> 0.0, 1.0 -> 1.0
        normalized = max(0.0, (similarity - 0.5) / 0.5)
        return min(1.0, normalized)

    def score(self, query: str, context: str) -> float:
        """
        Calculate semantic coverage score.

        Returns score in [0, 1] where 1 = perfect coverage.
        """
        # Keyword coverage
        keyword_score = 0.0
        if self.config.use_keywords:
            keyword_score = self._keyword_coverage(query, context)

        # Embedding coverage
        embedding_score = None
        if self.config.use_embeddings:
            embedding_score = self._embedding_coverage(query, context)

        # Blend scores
        if embedding_score is not None:
            # Blend keyword and embedding scores
            embedding_weight = self.config.embedding_weight
            return keyword_score * (1 - embedding_weight) + embedding_score * embedding_weight
        else:
            # Use only keyword score with threshold scaling
            min_overlap = self.config.min_query_term_overlap
            if keyword_score >= min_overlap:
                return 0.5 + (keyword_score - min_overlap) * (0.5 / (1.0 - min_overlap))
            else:
                return keyword_score * (0.5 / min_overlap)

    def score_against_terms(self, query: str, keywords: str, context: str) -> float:
        """
        Score coverage against query and keywords.

        This combines query coverage with keyword-based coverage.
        """
        # Score against query
        query_score = self.score(query, context)

        # Score against keywords if provided
        if keywords:
            keyword_score = self.score(keywords, context)
            # Weighted average: query is more important
            return 0.7 * query_score + 0.3 * keyword_score

        return query_score
