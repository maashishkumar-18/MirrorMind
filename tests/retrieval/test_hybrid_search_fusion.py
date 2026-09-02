"""
Characterization tests for HybridSearch's score-fusion instance methods:
_merge_candidates, _weighted_merge, _reciprocal_rank_fusion, _deduplicate,
_normalize_scores (src/retrieval/hybrid_search.py:860-996).

These are pure functions with respect to their inputs (only `self.config`
is read, no I/O/network/model) but each -- except _deduplicate -- mutates
the SearchCandidate objects in the input list IN PLACE and returns the
SAME list object. _deduplicate returns a NEW list. This is pinned
explicitly below since it's exactly the kind of thing an unintended
rewrite could silently break.

HybridSearch is never constructed via its real __init__ -- that
unconditionally builds an EmbeddingGenerator() (default model
"gemini-embedding-001"), which raises ValueError without GEMINI_API_KEY
(src/ingestion/embedder.py:137-139). Only `self.config` is needed by the
methods under test, so HybridSearch.__new__() + a hand-built config is
used instead.

Expected values in tests/fixtures/hybrid_search/*.json were captured by
running the current, unmodified methods against hand-crafted candidate
sets and saving their actual output.
"""

import json
from pathlib import Path

import pytest

from src.retrieval.config import HybridWeights
from src.retrieval.hybrid_search import HybridSearch, HybridSearchConfig, SearchCandidate

pytestmark = pytest.mark.characterization

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "hybrid_search"
WEIGHTS = HybridWeights(semantic=0.6, keyword=0.2, metadata=0.2)


def _load(name):
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def _make_hs(**config_kwargs):
    hs = HybridSearch.__new__(HybridSearch)
    hs.config = HybridSearchConfig(weights=WEIGHTS, **config_kwargs)
    return hs


def _candidates_from(dicts):
    return [SearchCandidate(**d) for d in dicts]


def _assert_matches_expected(actual, expected_dicts):
    assert len(actual) == len(expected_dicts)
    for got, exp in zip(actual, expected_dicts, strict=False):
        assert got.chunk_id == exp["chunk_id"]
        assert got.score == pytest.approx(exp["score"])
        assert got.source == exp["source"]


class TestWeightedMerge:
    @pytest.mark.parametrize("case", _load("merge_weighted_fixtures.json"))
    def test_matches_captured_fixture(self, case):
        hs = _make_hs(merge_strategy="weighted")
        candidates = _candidates_from(case["input"])
        result = hs._weighted_merge(candidates, WEIGHTS)
        _assert_matches_expected(result, case["expected"])

    def test_unknown_source_gets_zero_weight(self):
        hs = _make_hs(merge_strategy="weighted")
        c = SearchCandidate(
            chunk_id="a",
            content="c",
            raw_content="r",
            score=1.0,
            source="nonsense",
            original_rank=0,
        )
        result = hs._weighted_merge([c], WEIGHTS)
        assert result[0].score == 0.0

    def test_keyword_fallback_gets_half_weight_penalty(self):
        hs = _make_hs(merge_strategy="weighted")
        c = SearchCandidate(
            chunk_id="a",
            content="c",
            raw_content="r",
            score=1.0,
            source="keyword_fallback",
            original_rank=0,
        )
        result = hs._weighted_merge([c], WEIGHTS)
        assert result[0].score == pytest.approx(WEIGHTS.keyword * 0.5)

    def test_mutates_input_list_in_place_and_returns_same_object(self):
        hs = _make_hs(merge_strategy="weighted")
        candidates = [
            SearchCandidate(
                chunk_id="a",
                content="c",
                raw_content="r",
                score=1.0,
                source="semantic",
                original_rank=0,
            )
        ]
        result = hs._weighted_merge(candidates, WEIGHTS)
        assert result is candidates
        assert candidates[0].score == pytest.approx(WEIGHTS.semantic)


class TestReciprocalRankFusion:
    @pytest.mark.parametrize("case", _load("merge_rrf_fixtures.json"))
    def test_matches_captured_fixture(self, case):
        hs = _make_hs(merge_strategy="rrf", rrf_k=60)
        candidates = _candidates_from(case["input"])
        result = hs._reciprocal_rank_fusion(candidates, WEIGHTS)
        _assert_matches_expected(result, case["expected"])

    def test_rrf_formula_is_weight_over_k_plus_rank_plus_one(self):
        hs = _make_hs(merge_strategy="rrf", rrf_k=60)
        c = SearchCandidate(
            chunk_id="a",
            content="c",
            raw_content="r",
            score=99.0,
            source="semantic",
            original_rank=0,
        )
        result = hs._reciprocal_rank_fusion([c], WEIGHTS)
        assert result[0].score == pytest.approx(WEIGHTS.semantic / (60 + 0 + 1))

    def test_mutates_input_list_in_place_and_returns_same_object(self):
        hs = _make_hs(merge_strategy="rrf", rrf_k=60)
        candidates = [
            SearchCandidate(
                chunk_id="a",
                content="c",
                raw_content="r",
                score=1.0,
                source="semantic",
                original_rank=0,
            )
        ]
        result = hs._reciprocal_rank_fusion(candidates, WEIGHTS)
        assert result is candidates


class TestMergeCandidatesDispatch:
    @pytest.mark.parametrize("case", _load("merge_dispatch_fixtures.json"))
    def test_matches_captured_fixture(self, case):
        hs = _make_hs(merge_strategy=case["merge_strategy"], rrf_k=60)
        candidates = _candidates_from(case["input"])
        result = hs._merge_candidates(candidates, WEIGHTS)
        _assert_matches_expected(result, case["expected"])

    def test_any_non_rrf_strategy_falls_back_to_weighted_merge(self):
        """_merge_candidates dispatches on `== "rrf"` vs. an unconditional
        else -- any other string value (not just "weighted") takes the
        weighted-merge path."""
        candidates_a = [
            SearchCandidate(
                chunk_id="a",
                content="c",
                raw_content="r",
                score=1.0,
                source="semantic",
                original_rank=0,
            )
        ]
        candidates_b = [
            SearchCandidate(
                chunk_id="a",
                content="c",
                raw_content="r",
                score=1.0,
                source="semantic",
                original_rank=0,
            )
        ]
        hs_weighted = _make_hs(merge_strategy="weighted")
        hs_typo = _make_hs(merge_strategy="weightedd")  # not "rrf" -- still falls back

        result_weighted = hs_weighted._merge_candidates(candidates_a, WEIGHTS)
        result_typo = hs_typo._merge_candidates(candidates_b, WEIGHTS)

        assert result_weighted[0].score == pytest.approx(result_typo[0].score)


class TestDeduplicate:
    @pytest.mark.parametrize("case", _load("dedup_fixtures.json"))
    def test_matches_captured_fixture(self, case):
        hs = _make_hs(dedup_strategy=case["dedup_strategy"])
        candidates = _candidates_from(case["input"])
        result = hs._deduplicate(candidates)
        _assert_matches_expected(result, case["expected"])

    def test_returns_a_new_list_not_the_same_object(self):
        """Unlike _weighted_merge/_reciprocal_rank_fusion/_normalize_scores,
        _deduplicate builds a fresh `unique` list rather than mutating and
        returning its input."""
        hs = _make_hs(dedup_strategy="score")
        candidates = [
            SearchCandidate(
                chunk_id="a",
                content="c",
                raw_content="r",
                score=1.0,
                source="semantic",
                original_rank=0,
            )
        ]
        result = hs._deduplicate(candidates)
        assert result is not candidates

    def test_empty_input_returns_empty_list(self):
        hs = _make_hs(dedup_strategy="score")
        assert hs._deduplicate([]) == []

    def test_second_occurrence_of_same_chunk_id_is_dropped_regardless_of_score(self):
        hs = _make_hs(dedup_strategy="score")
        first = SearchCandidate(
            chunk_id="a",
            content="c",
            raw_content="r",
            score=0.1,
            source="semantic",
            original_rank=0,
        )
        second = SearchCandidate(
            chunk_id="a",
            content="c",
            raw_content="r",
            score=99.0,
            source="keyword",
            original_rank=1,
        )
        result = hs._deduplicate([first, second])
        assert len(result) == 1
        assert result[0].score == pytest.approx(0.1)

    def test_non_score_dedup_strategy_keeps_first_seen_regardless_of_later_score(self):
        """When dedup_strategy != "score", the `if self.config.dedup_strategy
        == "score":` swap branch never fires -- a later, higher-scored
        candidate sharing the same parent_chunk_id is silently dropped in
        favor of whichever one was seen first. Found untested during a
        Phase 0 audit (only dedup_strategy="score" was exercised); this
        pins the real, distinct behavior of every other value."""
        hs = _make_hs(dedup_strategy="first_seen")
        first = SearchCandidate(
            chunk_id="p1_a",
            content="c",
            raw_content="r",
            score=0.1,
            source="semantic",
            original_rank=0,
            metadata={"parent_chunk_id": "parent-1"},
        )
        second = SearchCandidate(
            chunk_id="p1_b",
            content="c",
            raw_content="r",
            score=99.0,  # much higher score, but must NOT win under a non-"score" strategy
            source="semantic",
            original_rank=1,
            metadata={"parent_chunk_id": "parent-1"},
        )
        result = hs._deduplicate([first, second])
        assert len(result) == 1
        assert result[0].chunk_id == "p1_a"
        assert result[0].score == pytest.approx(0.1)


class TestNormalizeScores:
    @pytest.mark.parametrize("case", _load("normalize_fixtures.json"))
    def test_matches_captured_fixture(self, case):
        hs = _make_hs(normalize_method=case["normalize_method"])
        candidates = _candidates_from(case["input"])
        result = hs._normalize_scores(candidates)
        _assert_matches_expected(result, case["expected"])

    def test_all_equal_scores_become_one(self):
        hs = _make_hs(normalize_method="minmax")
        candidates = [
            SearchCandidate(
                chunk_id=cid,
                content="c",
                raw_content="r",
                score=5.0,
                source="semantic",
                original_rank=i,
            )
            for i, cid in enumerate(["a", "b", "c"])
        ]
        result = hs._normalize_scores(candidates)
        assert all(c.score == 1.0 for c in result)

    def test_minmax_bounds_are_zero_and_one(self):
        hs = _make_hs(normalize_method="minmax")
        candidates = [
            SearchCandidate(
                chunk_id=cid,
                content="c",
                raw_content="r",
                score=score,
                source="semantic",
                original_rank=i,
            )
            for i, (cid, score) in enumerate([("a", 10.0), ("b", 5.0), ("c", 0.0)])
        ]
        result = hs._normalize_scores(candidates)
        scores = {c.chunk_id: c.score for c in result}
        assert scores["a"] == pytest.approx(1.0)
        assert scores["c"] == pytest.approx(0.0)
        assert scores["b"] == pytest.approx(0.5)

    def test_empty_input_returns_same_empty_list(self):
        hs = _make_hs(normalize_method="minmax")
        candidates = []
        result = hs._normalize_scores(candidates)
        assert result is candidates

    def test_mutates_input_list_in_place_and_returns_same_object(self):
        hs = _make_hs(normalize_method="minmax")
        candidates = [
            SearchCandidate(
                chunk_id=cid,
                content="c",
                raw_content="r",
                score=score,
                source="semantic",
                original_rank=i,
            )
            for i, (cid, score) in enumerate([("a", 10.0), ("b", 0.0)])
        ]
        result = hs._normalize_scores(candidates)
        assert result is candidates

    def test_unrecognized_normalize_method_leaves_scores_completely_unnormalized(self):
        """Neither the "minmax" nor the "zscore" branch fires for any other
        string value -- the function silently returns candidates with
        their ORIGINAL, un-normalized scores (not scaled into [0, 1] at
        all). Found untested during a Phase 0 audit (only the two
        recognized methods were exercised); a config typo or a new,
        not-yet-implemented method name would silently produce raw,
        un-normalized scores downstream with no error."""
        hs = _make_hs(normalize_method="not_a_real_method")
        candidates = [
            SearchCandidate(
                chunk_id=cid,
                content="c",
                raw_content="r",
                score=score,
                source="semantic",
                original_rank=i,
            )
            for i, (cid, score) in enumerate([("a", 10.0), ("b", 5.0), ("c", 0.0)])
        ]
        result = hs._normalize_scores(candidates)
        scores = {c.chunk_id: c.score for c in result}
        assert scores == {"a": 10.0, "b": 5.0, "c": 0.0}  # unchanged from the raw input
