"""
Characterization tests for TokenCounter (src/retrieval/context_builder.py:240-297).

This is a near-duplicate of src/generation/prompt_builder.py's TokenCounter
(tests/generation/test_prompt_builder_token_counter.py) but NOT identical
-- different public method names (count_tokens()/count_tokens_batch() here
vs. count()/count_batch() there), and this version gates the tiktoken
import behind a module-level TIKTOKEN_AVAILABLE flag (set once at import
time) plus an extra nested try/except around the cl100k_base fallback that
the sibling class doesn't have. The two are tested in separate files and
never assumed interchangeable -- a future rewrite should consider
unifying them, but that unification is out of scope for Phase 0.
"""

import pytest

import src.retrieval.context_builder as context_builder_module
from src.retrieval.context_builder import TokenCounter

pytestmark = pytest.mark.characterization


class TestConstruction:
    def test_real_tiktoken_encoder_is_used_when_available(self):
        counter = TokenCounter(model_name="gpt-4o")
        assert counter._encoder is not None

    def test_falls_back_to_cl100k_base_for_unknown_model_name(self):
        counter = TokenCounter(model_name="not-a-real-model-name")
        assert counter._encoder is not None

    def test_encoder_is_none_when_tiktoken_unavailable_flag_is_false(self, monkeypatch):
        """Unlike prompt_builder.TokenCounter (which does `try: import
        tiktoken` inside __init__), this class checks a module-level
        TIKTOKEN_AVAILABLE flag computed once at import time -- setting it
        False is the correct way to simulate unavailability here."""
        monkeypatch.setattr(context_builder_module, "TIKTOKEN_AVAILABLE", False)
        counter = TokenCounter(model_name="gpt-4o")
        assert counter._encoder is None


class TestCountTokens:
    def test_empty_string_is_zero_tokens(self):
        counter = TokenCounter()
        assert counter.count_tokens("") == 0

    def test_real_encoder_produces_a_positive_count_for_nonempty_text(self):
        counter = TokenCounter()
        assert counter.count_tokens("hello world") > 0

    def test_char_based_fallback_when_encoder_unavailable(self):
        counter = TokenCounter()
        counter._encoder = None
        text = "a" * 40
        assert counter.count_tokens(text) == len(text) // 4

    def test_fallback_math_is_len_over_four_integer_division(self):
        counter = TokenCounter()
        counter._encoder = None
        assert counter.count_tokens("abc") == 0
        assert counter.count_tokens("abcd") == 1
        assert counter.count_tokens("abcdefg") == 1

    def test_estimate_tokens_matches_count_tokens_fallback(self):
        counter = TokenCounter()
        text = "some sample text here"
        assert counter._estimate_tokens(text) == len(text) // 4


class TestCountTokensBatch:
    def test_dead_code_path_still_produces_correct_per_text_counts(self):
        """count_tokens_batch computes `combined`/`all_tokens` via a
        single batch encode() call, then DISCARDS that result and falls
        through to a per-text count_tokens() loop anyway (the combined
        encoding is dead code, not used for the returned counts). Pinned
        as-is: the returned counts must still equal individual
        count_tokens() calls, whatever the wasted intermediate work is."""
        counter = TokenCounter()
        texts = ["hello", "hello world", "a longer sentence with more words"]
        result = counter.count_tokens_batch(texts)
        assert result == [counter.count_tokens(t) for t in texts]

    def test_fallback_batch_when_encoder_unavailable(self):
        counter = TokenCounter()
        counter._encoder = None
        texts = ["abcd", "abcdefgh"]
        assert counter.count_tokens_batch(texts) == [1, 2]

    def test_empty_list_returns_empty_list(self):
        counter = TokenCounter()
        assert counter.count_tokens_batch([]) == []


class TestNotInterchangeableWithSiblingClass:
    def test_this_class_has_no_count_or_count_batch_methods(self):
        """Confirms the method-name difference from
        src/generation/prompt_builder.py's TokenCounter is real, not an
        assumption -- a rewrite that tries to call .count()/.count_batch()
        on this class would fail with AttributeError."""
        counter = TokenCounter()
        assert not hasattr(counter, "count")
        assert not hasattr(counter, "count_batch")
