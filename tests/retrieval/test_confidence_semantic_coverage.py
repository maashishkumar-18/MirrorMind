"""
Characterization tests for SemanticCoverageScorer
(src/retrieval/confidence.py:424-580).

With the default use_embeddings=False, this is a pure keyword-overlap
scorer with zero external dependencies -- no sentence-transformers import
is ever attempted (confirmed: _get_embedding_model()'s import is gated
behind `self.config.use_embeddings`, checked before the lazy-load runs).
The embeddings=True blended path is characterized separately by directly
setting `scorer._embedding_model` to a fake stub, bypassing the lazy-load
entirely -- sentence-transformers IS installed in this repo's venv, so an
ImportError can't be triggered naturally to reach the "fallback" sentinel
path; _fallback_embedding is tested directly as its own pure function
instead.

Expected values in tests/fixtures/confidence/*.json were captured by
running the current, unmodified score()/_keyword_coverage()/
score_against_terms() against hand-picked query/context pairs.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from src.retrieval.confidence import SemanticCoverageConfig, SemanticCoverageScorer

pytestmark = pytest.mark.characterization

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "confidence"


def _load(name):
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def scorer():
    return SemanticCoverageScorer(SemanticCoverageConfig())  # use_embeddings=False default


class TestKeywordCoverageAndScore:
    @pytest.mark.parametrize("case", _load("coverage_fixtures.json"))
    def test_matches_captured_fixture(self, scorer, case):
        keyword_coverage = scorer._keyword_coverage(case["query"], case["context"])
        score = scorer.score(case["query"], case["context"])

        assert keyword_coverage == pytest.approx(case["expected_keyword_coverage"])
        assert score == pytest.approx(case["expected_score"])

    def test_empty_query_or_context_gives_zero_keyword_coverage(self, scorer):
        assert scorer._keyword_coverage("", "some context") == 0.0
        assert scorer._keyword_coverage("some query", "") == 0.0

    def test_query_with_only_short_terms_gives_half_coverage(self, scorer):
        """Every word <= 2 chars after stripping punctuation is dropped;
        an empty query_terms set is treated as 0.5 coverage, not 0.0 or an
        error."""
        assert scorer._keyword_coverage("a an to", "irrelevant context") == 0.5

    def test_score_defaults_to_use_embeddings_false(self):
        config = SemanticCoverageConfig()
        assert config.use_embeddings is False

    def test_full_keyword_match_saturates_score_at_one(self, scorer):
        score = scorer.score("reminder deadline", "the reminder deadline is soon")
        assert score == pytest.approx(1.0)


class TestScoreAgainstTerms:
    @pytest.mark.parametrize("case", _load("score_against_terms_fixtures.json"))
    def test_matches_captured_fixture(self, scorer, case):
        result = scorer.score_against_terms(case["query"], case["keywords"], case["context"])
        assert result == pytest.approx(case["expected"])

    def test_without_keywords_falls_back_to_plain_query_score(self, scorer):
        query, context = "reminder deadline", "the reminder deadline is soon"
        assert scorer.score_against_terms(query, "", context) == pytest.approx(
            scorer.score(query, context)
        )

    def test_with_keywords_blends_seventy_thirty(self, scorer):
        query, keywords, context = "alpha", "beta", "alpha beta"
        query_score = scorer.score(query, context)
        keyword_score = scorer.score(keywords, context)
        expected = 0.7 * query_score + 0.3 * keyword_score
        assert scorer.score_against_terms(query, keywords, context) == pytest.approx(expected)


class TestFallbackEmbedding:
    def test_is_deterministic_for_the_same_text(self, scorer):
        e1 = scorer._fallback_embedding("some text")
        e2 = scorer._fallback_embedding("some text")
        assert np.array_equal(e1, e2)

    def test_returns_384_dimensional_vector(self, scorer):
        embedding = scorer._fallback_embedding("some text")
        assert embedding.shape == (384,)

    def test_different_text_gives_different_embedding(self, scorer):
        e1 = scorer._fallback_embedding("alpha")
        e2 = scorer._fallback_embedding("completely different text")
        assert not np.array_equal(e1, e2)


class TestEmbeddingBlendedPath:
    def test_use_embeddings_true_blends_keyword_and_embedding_scores(self):
        config = SemanticCoverageConfig(use_embeddings=True, embedding_weight=0.5)
        scorer = SemanticCoverageScorer(config)

        # Bypass the real lazy-load entirely -- sentence-transformers IS
        # installed here, so only setting _embedding_model directly (not
        # patching the import) reliably exercises this path without a
        # real model download.
        fake_model = type(
            "FakeModel",
            (),
            {
                "encode": lambda self, text, convert_to_numpy=True: (
                    np.ones(4) if text else np.zeros(4)
                )
            },
        )()
        scorer._embedding_model = fake_model

        result = scorer.score("query text", "context text")

        keyword_score = scorer._keyword_coverage("query text", "context text")
        # Both embeddings are np.ones(4) -> cosine similarity 1.0 -> normalized to 1.0.
        embedding_score = 1.0
        expected = keyword_score * 0.5 + embedding_score * 0.5
        assert result == pytest.approx(expected)

    def test_use_embeddings_true_but_empty_query_returns_none_embedding_score(self):
        config = SemanticCoverageConfig(use_embeddings=True)
        scorer = SemanticCoverageScorer(config)
        assert scorer._embedding_coverage("", "context") is None
