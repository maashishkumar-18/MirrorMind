"""
Characterization tests for BM25Index (src/retrieval/hybrid_search.py:195-304).

Pins current behavior before Phase 1's retrieval rewrite. Uses the real
rank_bm25 library (cheap, deterministic, no network) rather than mocking
it -- these are genuine BM25 scores, not stand-ins. Expected values in
tests/fixtures/bm25/queries_expected.json were captured by running the
current, unmodified BM25Index against tests/fixtures/bm25/corpus.json and
saving its actual output -- that is what "characterization" means here:
pin what it does now, not what it should do.
"""

import json
from pathlib import Path

import pytest

from src.retrieval.hybrid_search import BM25Index

pytestmark = pytest.mark.characterization

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "bm25"


def _load_corpus():
    with open(FIXTURES_DIR / "corpus.json", encoding="utf-8") as f:
        return json.load(f)


def _load_expected_cases():
    with open(FIXTURES_DIR / "queries_expected.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def built_index():
    idx = BM25Index()
    idx.build_index(_load_corpus())
    return idx


class TestSearch:
    @pytest.mark.parametrize("case", _load_expected_cases())
    def test_matches_captured_fixture_exactly(self, built_index, case):
        result = built_index.search(case["query"], top_k=case["top_k"])
        expected = [tuple(pair) for pair in case["expected"]]

        assert len(result) == len(expected)
        for (got_idx, got_score), (exp_idx, exp_score) in zip(result, expected, strict=False):
            assert got_idx == exp_idx
            assert got_score == pytest.approx(exp_score)

    def test_results_are_ranked_best_first(self, built_index):
        result = built_index.search("meeting notes action items")
        scores = [score for _, score in result]
        assert scores == sorted(scores, reverse=True)

    def test_zero_scored_documents_are_excluded(self, built_index):
        result = built_index.search("quantum computing")
        assert result == []

    def test_top_k_none_returns_every_nonzero_scored_document(self, built_index):
        # "meeting notes action items" matches 5 of the 10 corpus documents
        # with the current tokenizer/scoring -- top_k=None must return all
        # of them, not just a default-truncated subset.
        result = built_index.search("meeting notes action items", top_k=None)
        assert len(result) == 5

    def test_search_before_build_raises_runtime_error(self):
        idx = BM25Index()
        with pytest.raises(RuntimeError, match="not initialized"):
            idx.search("anything")


class TestPersistence:
    def test_save_and_load_round_trip(self, tmp_path):
        index_path = str(tmp_path / "bm25_index")
        idx = BM25Index(index_path=index_path)
        idx.build_index(_load_corpus())

        meta_path = tmp_path / "bm25_index.meta.json"
        assert meta_path.exists()

        loaded = BM25Index(index_path=index_path)
        assert loaded.load_index() is True
        assert loaded._initialized is True
        assert loaded.documents == _load_corpus()

        # The reloaded index must produce the same search results as the
        # original.
        original_result = idx.search("meeting notes action items")
        loaded_result = loaded.search("meeting notes action items")
        assert original_result == loaded_result

    def test_load_without_index_path_returns_false(self):
        idx = BM25Index(index_path=None)
        assert idx.load_index() is False

    def test_load_with_missing_file_returns_false(self, tmp_path):
        idx = BM25Index(index_path=str(tmp_path / "does_not_exist"))
        assert idx.load_index() is False

    def test_build_index_without_path_does_not_write_to_disk(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        idx = BM25Index(index_path=None)
        idx.build_index(_load_corpus())
        assert list(tmp_path.iterdir()) == []
