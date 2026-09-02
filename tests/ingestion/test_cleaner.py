"""Unit tests for the session TextCleaner (src/ingestion/cleaner.py) — Phase 1 Step 1.2."""

import pytest

from src.common.types import SessionMessage
from src.ingestion.cleaner import TextCleaner

pytestmark = pytest.mark.unit


@pytest.fixture
def cleaner():
    return TextCleaner()


def test_fixes_mojibake(cleaner):
    assert cleaner.clean_text("cafÃ©") == "café"


def test_normalizes_unicode_nfkc(cleaner):
    # fullwidth digits -> ascii
    assert cleaner.clean_text("１２３") == "123"


def test_strips_control_chars_but_keeps_newlines(cleaner):
    assert cleaner.clean_text("a\x00b\x07c\nd") == "abc\nd"


def test_collapses_whitespace_and_caps_blank_lines(cleaner):
    assert cleaner.clean_text("a    b  \n\n\n\nc   ") == "a b\n\nc"


def test_clean_messages_preserves_role_and_order(cleaner):
    msgs = [SessionMessage("user", "  hi   there  "), SessionMessage("assistant", "cafÃ©")]
    out = cleaner.clean_messages(msgs)
    assert [m.role for m in out] == ["user", "assistant"]
    assert out[0].content == "hi there"
    assert out[1].content == "café"
    assert msgs[0].content == "  hi   there  "  # input not mutated


def test_cleaned_document_symbol_is_gone():
    import src.ingestion.cleaner as mod

    assert not hasattr(mod, "CleanedDocument")
    assert not hasattr(TextCleaner, "get_cleaning_report")
