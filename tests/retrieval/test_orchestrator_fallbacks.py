"""
Characterization tests for Phase 0 Step 0.2 Bug 1:
RetrievalOrchestrator._fallback_search / _fallback_reranker /
_fallback_confidence_scorer (src/retrieval/orchestrator.py).

Before this fix, all three had broken bare imports (`from hybrid_search
import ...` instead of `from src.retrieval.hybrid_search import ...`), and
_fallback_search/_fallback_reranker additionally constructed the real
dataclasses with wrong/missing fields, and _fallback_search called
VectorStore.query() with a raw query string instead of an embedded vector
and read a `.matches` attribute off a plain list. These tests pin the
fixed behavior.

RetrievalOrchestrator() is deliberately never constructed via its real
__init__ — that auto-constructs 8 components, and HybridSearch.__init__ ->
EmbeddingGenerator() -> GoogleProvider.__init__ raises ValueError without
GEMINI_API_KEY (src/ingestion/embedder.py:137-139). Only the three
_fallback_* methods under test are exercised, via
RetrievalOrchestrator.__new__().
"""

from unittest.mock import MagicMock, patch

import pytest

from src.retrieval.confidence import ConfidenceLevel, ConfidenceResult
from src.retrieval.hybrid_search import HybridSearchResult, SearchCandidate
from src.retrieval.orchestrator import RetrievalOrchestrator
from src.retrieval.reranker import RerankedChunk, RerankerResult

pytestmark = pytest.mark.characterization


def _make_orchestrator(vector_store=None) -> RetrievalOrchestrator:
    orch = RetrievalOrchestrator.__new__(RetrievalOrchestrator)
    orch.vector_store = vector_store or MagicMock()
    return orch


class TestFallbackSearch:
    def test_success_path_returns_populated_hybrid_search_result(self):
        orch = _make_orchestrator()
        orch.vector_store.query.return_value = [
            {"id": "chunk-1", "score": 0.9, "metadata": {"text": "hello", "raw_text": "hello"}},
            {"id": "chunk-2", "score": 0.5, "metadata": {"text": "world", "raw_text": "world"}},
        ]

        fake_embedder = MagicMock()
        fake_embedder.embed_query.return_value = [0.1, 0.2, 0.3]

        with patch("src.ingestion.embedder.EmbeddingGenerator", return_value=fake_embedder):
            result = orch._fallback_search(
                query="what did we discuss",
                keywords="discuss",
                namespace="default",
                metadata_filters=None,
            )

        assert isinstance(result, HybridSearchResult)
        assert result.total_retrieved == 2
        assert result.sources_used == ["vector_fallback"]
        assert result.query == "what did we discuss"

        first = result.candidates[0]
        assert isinstance(first, SearchCandidate)
        assert first.chunk_id == "chunk-1"
        assert first.content == "hello"
        assert first.score == 0.9
        assert first.source == "semantic_fallback"
        assert first.original_rank == 0

        # The real VectorStore.query() call must be embedded-vector-based,
        # not the raw query string.
        orch.vector_store.query.assert_called_once()
        _, kwargs = orch.vector_store.query.call_args
        assert kwargs["vector"] == [0.1, 0.2, 0.3]
        assert "query" not in kwargs

    def test_exception_path_returns_empty_result_not_raising(self):
        orch = _make_orchestrator()
        orch.vector_store.query.side_effect = RuntimeError("boom")

        fake_embedder = MagicMock()
        fake_embedder.embed_query.return_value = [0.1, 0.2, 0.3]

        with patch("src.ingestion.embedder.EmbeddingGenerator", return_value=fake_embedder):
            result = orch._fallback_search(
                query="q", keywords="k", namespace="default", metadata_filters=None
            )

        assert isinstance(result, HybridSearchResult)
        assert result.candidates == []
        assert result.total_retrieved == 0
        assert result.sources_used == []

    def test_no_import_error_or_type_error(self):
        """The regression this bug fix targets: the old bare `from
        hybrid_search import ...` import and the mismatched dataclass
        fields would raise ImportError/TypeError the moment this path was
        actually exercised."""
        orch = _make_orchestrator()
        orch.vector_store.query.side_effect = RuntimeError("component failure")

        fake_embedder = MagicMock()
        fake_embedder.embed_query.return_value = [0.1]

        with patch("src.ingestion.embedder.EmbeddingGenerator", return_value=fake_embedder):
            # Must not raise ImportError or TypeError.
            orch._fallback_search(
                query="q", keywords="k", namespace="default", metadata_filters=None
            )


class TestFallbackReranker:
    def test_returns_populated_reranker_result(self):
        orch = _make_orchestrator()
        candidates = [
            {"chunk_id": "c1", "content": "a", "raw_content": "a", "score": 0.9, "metadata": {}},
            {"chunk_id": "c2", "content": "b", "raw_content": "b", "score": 0.4, "metadata": {}},
        ]

        result = orch._fallback_reranker(candidates)

        assert isinstance(result, RerankerResult)
        assert result.total_input == 2
        assert result.total_output == 2
        assert result.backend_used == "fallback"
        assert result.model_used == "fallback"

        first = result.chunks[0]
        assert isinstance(first, RerankedChunk)
        assert first.chunk_id == "c1"
        assert first.rank == 1
        assert first.original_score == 0.9
        assert first.rerank_score == 0.9
        # combined_score must be computable (0.3*original + 0.7*rerank).
        assert first.combined_score == pytest.approx(0.9)

    def test_truncates_to_ten_candidates(self):
        orch = _make_orchestrator()
        candidates = [
            {"chunk_id": f"c{i}", "content": "x", "raw_content": "x", "score": 0.1, "metadata": {}}
            for i in range(25)
        ]

        result = orch._fallback_reranker(candidates)

        assert result.total_input == 25
        assert len(result.chunks) == 10
        assert result.total_output == 10

    def test_no_import_error_or_type_error(self):
        orch = _make_orchestrator()
        orch._fallback_reranker(
            [{"chunk_id": "c1", "content": "a", "raw_content": "a", "score": 0.5, "metadata": {}}]
        )


class TestFallbackConfidenceScorer:
    def test_returns_low_confidence_result(self):
        orch = _make_orchestrator()

        result = orch._fallback_confidence_scorer()

        assert isinstance(result, ConfidenceResult)
        assert result.score == 0.3
        assert result.level == ConfidenceLevel.LOW
        assert result.action == "fallback"
        assert result.recommendations

    def test_no_import_error_or_type_error(self):
        orch = _make_orchestrator()
        orch._fallback_confidence_scorer()
