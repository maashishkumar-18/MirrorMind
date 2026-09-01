"""
Characterization tests for TokenCounter (src/generation/prompt_builder.py:475-518).

tiktoken IS installed in this repo's venv, so the ImportError fallback
path is exercised by temporarily making `import tiktoken` fail via
sys.modules injection, not by uninstalling anything. See also
tests/retrieval/test_context_builder_token_counter.py for the sibling
class in src/retrieval/context_builder.py -- the two are near-duplicates
but NOT identical (different public method names: count()/count_batch()
here vs. count_tokens()/count_tokens_batch() there), tested separately
and never assumed interchangeable.
"""

import sys

import pytest

from src.generation.prompt_builder import TokenCounter

pytestmark = pytest.mark.characterization


class TestConstruction:
    def test_real_tiktoken_encoder_is_used_when_available(self):
        counter = TokenCounter(model_name="gpt-4o")
        assert counter._encoder is not None

    def test_falls_back_to_cl100k_base_for_unknown_model_name(self):
        counter = TokenCounter(model_name="not-a-real-model-name")
        assert counter._encoder is not None  # encoding_for_model fails, falls back to get_encoding

    def test_encoder_is_none_when_tiktoken_import_fails(self, monkeypatch):
        monkeypatch.setitem(
            sys.modules, "tiktoken", None
        )  # forces `import tiktoken` to raise ImportError
        counter = TokenCounter(model_name="gpt-4o")
        assert counter._encoder is None


class TestCount:
    def test_empty_string_is_zero_tokens(self):
        counter = TokenCounter()
        assert counter.count("") == 0

    def test_real_encoder_produces_a_positive_count_for_nonempty_text(self):
        counter = TokenCounter()
        assert counter.count("hello world") > 0

    def test_char_based_fallback_when_encoder_unavailable(self):
        counter = TokenCounter()
        counter._encoder = None  # simulate no encoder without needing a real import failure
        text = "a" * 40
        assert counter.count(text) == len(text) // 4

    def test_fallback_math_is_len_over_four_integer_division(self):
        counter = TokenCounter()
        counter._encoder = None
        assert counter.count("abc") == 0  # 3 // 4 == 0
        assert counter.count("abcd") == 1  # 4 // 4 == 1
        assert counter.count("abcdefg") == 1  # 7 // 4 == 1


class TestCountBatch:
    def test_delegates_to_count_for_each_text(self):
        counter = TokenCounter()
        texts = ["hello", "hello world", ""]
        result = counter.count_batch(texts)
        assert result == [counter.count(t) for t in texts]

    def test_empty_list_returns_empty_list(self):
        counter = TokenCounter()
        assert counter.count_batch([]) == []
