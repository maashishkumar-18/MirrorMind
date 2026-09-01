"""
Characterization tests for AnswerCleaner (src/generation/post_processor.py:318-405).

Pure regex/string function, no I/O. Pins the exact stripping semantics
(both an opening and closing tag are required for [HIDE]/<THINK> removal
-- an unclosed tag is left untouched, since the regex requires both), the
trailing "---" strip, blank-line collapsing to a max of 2 consecutive
blanks, and truncation breaking on the last space before max_length with
a "..." suffix appended (so truncated output length can exceed
max_length by up to 3 chars).
"""

import pytest

from src.generation.post_processor import AnswerCleaner

pytestmark = pytest.mark.characterization


class TestClean:
    def test_empty_or_none_ish_input_returns_empty_string(self):
        cleaner = AnswerCleaner()
        assert cleaner.clean("") == ""

    def test_plain_text_is_returned_stripped(self):
        cleaner = AnswerCleaner()
        assert cleaner.clean("  hello world  ") == "hello world"


class TestRemoveArtifacts:
    def test_hide_block_is_stripped(self):
        cleaner = AnswerCleaner()
        result = cleaner.clean("before [HIDE]secret internal notes[/HIDE] after")
        assert result == "before  after"

    def test_hide_block_case_insensitive(self):
        cleaner = AnswerCleaner()
        result = cleaner.clean("before [hide]secret[/hide] after")
        assert result == "before  after"

    def test_hide_block_spans_newlines(self):
        cleaner = AnswerCleaner()
        result = cleaner.clean("before [HIDE]line1\nline2[/HIDE] after")
        assert result == "before  after"

    def test_think_block_is_stripped(self):
        cleaner = AnswerCleaner()
        result = cleaner.clean("before <THINK>reasoning here</THINK> after")
        assert result == "before  after"

    def test_multiple_hide_blocks_all_stripped(self):
        cleaner = AnswerCleaner()
        result = cleaner.clean("[HIDE]one[/HIDE]mid[HIDE]two[/HIDE]")
        assert result == "mid"

    def test_unclosed_hide_tag_is_left_untouched(self):
        """The regex requires both an opening [HIDE] and a closing
        [/HIDE] -- an unclosed tag has no match and is left as-is."""
        cleaner = AnswerCleaner()
        text = "before [HIDE]never closed"
        result = cleaner.clean(text)
        assert "[HIDE]" in result
        assert result == text.strip()

    def test_trailing_separator_is_stripped(self):
        cleaner = AnswerCleaner()
        result = cleaner.clean("answer text\n---")
        assert result == "answer text"

    def test_trailing_separator_with_multiple_newlines_is_stripped(self):
        cleaner = AnswerCleaner()
        result = cleaner.clean("answer text\n\n\n---")
        assert result == "answer text"

    def test_non_trailing_separator_is_not_stripped(self):
        """The trailing-"---" regex is anchored to the end of the string
        ($) -- a "---" in the middle of the text is left alone."""
        cleaner = AnswerCleaner()
        result = cleaner.clean("before\n---\nafter")
        assert "---" in result

    def test_remove_artifacts_false_leaves_hide_blocks_intact(self):
        cleaner = AnswerCleaner(remove_artifacts=False)
        result = cleaner.clean("[HIDE]visible now[/HIDE]")
        assert "[HIDE]" in result


class TestNormalizeWhitespace:
    def test_trailing_whitespace_per_line_is_removed(self):
        cleaner = AnswerCleaner()
        result = cleaner.clean("line one   \nline two\t\t")
        assert result == "line one\nline two"

    def test_leading_and_trailing_blank_lines_are_removed(self):
        cleaner = AnswerCleaner()
        result = cleaner.clean("\n\n\ncontent here\n\n\n")
        assert result == "content here"

    def test_more_than_two_consecutive_blank_lines_collapse_to_two(self):
        cleaner = AnswerCleaner()
        result = cleaner.clean("first\n\n\n\n\nsecond")
        assert result == "first\n\n\nsecond"

    def test_exactly_two_blank_lines_preserved(self):
        cleaner = AnswerCleaner()
        result = cleaner.clean("first\n\n\nsecond")
        assert result == "first\n\n\nsecond"

    def test_normalize_whitespace_false_leaves_trailing_spaces(self):
        cleaner = AnswerCleaner(normalize_whitespace=False)
        result = cleaner.clean("line with trailing space   ")
        assert result == "line with trailing space"  # only the final .strip() removes it


class TestTruncation:
    def test_no_truncation_when_max_length_none(self):
        cleaner = AnswerCleaner(max_length=None)
        long_text = "word " * 100
        result = cleaner.clean(long_text)
        assert result == long_text.strip()

    def test_truncates_at_last_space_before_max_length_and_appends_ellipsis(self):
        cleaner = AnswerCleaner(max_length=20, normalize_whitespace=False)
        text = "this is a long sentence that exceeds the limit"
        result = cleaner.clean(text)
        # text[:20] = "this is a long sente" -- 21 chars, rsplit(" ", 1)[0] drops the
        # trailing partial word.
        assert result.endswith("...")
        assert len(result) <= 20 + 3

    def test_no_truncation_when_text_is_within_max_length(self):
        cleaner = AnswerCleaner(max_length=100)
        result = cleaner.clean("short text")
        assert result == "short text"
        assert not result.endswith("...")


class TestConstructorDefaults:
    def test_defaults_are_remove_artifacts_and_normalize_whitespace_true_no_max_length(self):
        cleaner = AnswerCleaner()
        assert cleaner.remove_artifacts is True
        assert cleaner.normalize_whitespace is True
        assert cleaner.max_length is None
