"""
Characterization tests for CrossEncoderReranker (src/retrieval/reranker.py:329-478).

The cross-encoder model is mocked (self._model set directly to a fake with
a .predict() method) -- sentence-transformers.CrossEncoder would otherwise
download real weights from HuggingFace Hub on first use. Since the model
is mocked, there's nothing "real" to capture scores from; the fake scores
in tests/fixtures/reranker/build_results_fixtures.json are author-chosen
fixed numbers, and the fixture's expected output was captured by running
the real rerank()/_build_results()/_normalize() against those chosen
numbers -- this pins the real ranking/normalization/threshold logic, not
any particular cross-encoder's output.

Two non-obvious behaviors this suite pins explicitly:
- min_score_threshold is checked AFTER normalization, against the
  normalized score -- with the default softmax normalization and a
  threshold of 0.1, a moderately-sized raw-score spread can filter out
  all but the single top-scored candidate (softmax concentrates
  probability mass on the max).
- Normalization runs over ALL sorted candidates before top_k truncation,
  not after -- top_k truncates the already-normalized list. A rank is
  still consumed (via enumerate) for a candidate filtered out by the
  threshold, so ranks in the final result can have gaps.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.retrieval.reranker import CrossEncoderReranker, RerankedChunk, RerankerConfig

pytestmark = pytest.mark.characterization

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "reranker"


def _load_cases():
    with open(FIXTURES_DIR / "build_results_fixtures.json", encoding="utf-8") as f:
        return json.load(f)


def _make_reranker(fake_scores, **config_overrides):
    config = RerankerConfig(**config_overrides)
    reranker = CrossEncoderReranker(config=config)
    fake_model = MagicMock()
    fake_model.predict.return_value = fake_scores
    reranker._model = fake_model
    return reranker, fake_model


class TestRerank:
    @pytest.mark.parametrize("case", _load_cases())
    def test_matches_captured_fixture(self, case):
        reranker, _ = _make_reranker(case["fake_scores"], **case["config_overrides"])
        results, _processing_time_ms, cache_hit = reranker.rerank(
            "q", case["candidates"], top_k=case["top_k"]
        )

        assert cache_hit == case["expected_cache_hit"]
        assert len(results) == len(case["expected_chunks"])
        for got, exp in zip(results, case["expected_chunks"], strict=False):
            assert isinstance(got, RerankedChunk)
            assert got.chunk_id == exp["chunk_id"]
            assert got.rank == exp["rank"]
            assert got.rerank_score == pytest.approx(exp["rerank_score"])
            assert got.original_score == pytest.approx(exp["original_score"])

    def test_empty_candidates_returns_immediately_without_touching_model(self):
        reranker, fake_model = _make_reranker([])
        results, processing_time_ms, cache_hit = reranker.rerank("q", [], top_k=5)

        assert results == []
        assert processing_time_ms == 0.0
        assert cache_hit is False
        fake_model.predict.assert_not_called()

    def test_second_call_with_same_query_and_candidates_is_a_cache_hit(self):
        candidates = [
            {
                "chunk_id": "a",
                "content": "alpha",
                "raw_content": "alpha",
                "score": 0.5,
                "metadata": {},
            },
        ]
        reranker, fake_model = _make_reranker([2.0])

        _results1, _t1, hit1 = reranker.rerank("q", candidates, top_k=1)
        assert hit1 is False
        fake_model.predict.assert_called_once()

        _results2, _t2, hit2 = reranker.rerank("q", candidates, top_k=1)
        assert hit2 is True
        # The model must not be called again on a cache hit.
        fake_model.predict.assert_called_once()

    def test_cache_disabled_calls_model_every_time(self):
        candidates = [
            {
                "chunk_id": "a",
                "content": "alpha",
                "raw_content": "alpha",
                "score": 0.5,
                "metadata": {},
            },
        ]
        reranker, fake_model = _make_reranker([2.0], cache_enabled=False)

        reranker.rerank("q", candidates, top_k=1)
        reranker.rerank("q", candidates, top_k=1)
        assert fake_model.predict.call_count == 2

    def test_model_predict_called_with_query_content_pairs(self):
        candidates = [
            {
                "chunk_id": "a",
                "content": "alpha content",
                "raw_content": "x",
                "score": 0.5,
                "metadata": {},
            },
            {
                "chunk_id": "b",
                "content": "beta content",
                "raw_content": "y",
                "score": 0.3,
                "metadata": {},
            },
        ]
        reranker, fake_model = _make_reranker([1.0, 2.0])

        reranker.rerank("my query", candidates, top_k=2)

        (pairs,), kwargs = fake_model.predict.call_args
        assert pairs == [("my query", "alpha content"), ("my query", "beta content")]
        assert kwargs["batch_size"] == reranker.config.batch_size
        assert kwargs["show_progress_bar"] == reranker.config.cross_encoder_show_progress


class TestGetCacheKey:
    def test_cache_key_is_order_independent_in_candidate_list(self):
        """The cache key sorts candidate_ids before hashing, so the same
        candidate set in a different order produces the same key."""
        reranker, _ = _make_reranker([1.0, 2.0])
        candidates_a = [{"chunk_id": "a"}, {"chunk_id": "b"}]
        candidates_b = [{"chunk_id": "b"}, {"chunk_id": "a"}]

        key_a = reranker._get_cache_key("q", candidates_a)
        key_b = reranker._get_cache_key("q", candidates_b)
        assert key_a == key_b

    def test_cache_key_differs_for_different_queries(self):
        reranker, _ = _make_reranker([1.0])
        candidates = [{"chunk_id": "a"}]
        assert reranker._get_cache_key("q1", candidates) != reranker._get_cache_key(
            "q2", candidates
        )


class TestNormalize:
    def test_softmax_sums_to_one(self):
        reranker, _ = _make_reranker([], normalize_method="softmax")
        result = reranker._normalize([1.0, 2.0, 3.0])
        assert sum(result) == pytest.approx(1.0)

    def test_minmax_all_equal_scores_become_one(self):
        reranker, _ = _make_reranker([], normalize_method="minmax")
        result = reranker._normalize([4.0, 4.0, 4.0])
        assert result == [1.0, 1.0, 1.0]

    def test_empty_scores_returns_empty(self):
        reranker, _ = _make_reranker([], normalize_method="minmax")
        assert reranker._normalize([]) == []


class TestLazyModelLoading:
    def test_model_property_is_not_touched_until_first_access(self):
        config = RerankerConfig()
        reranker = CrossEncoderReranker(config=config)
        assert reranker._model is None
