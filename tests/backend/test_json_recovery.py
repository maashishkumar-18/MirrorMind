"""Shared JSON recovery (Phase 3 Step 3.1d)."""

from __future__ import annotations

import pytest

from src.common.json_recovery import recover_json


def test_plain_json():
    assert recover_json('{"a": 1}') == {"a": 1}


def test_markdown_fence():
    assert recover_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_json_embedded_in_prose():
    assert recover_json('Sure! Here it is: {"a": 1, "b": "x"} — hope that helps') == {
        "a": 1,
        "b": "x",
    }


def test_missing_closing_brace_is_repaired():
    # last value complete, just the closing brace dropped (a common 7B truncation)
    assert recover_json('{"a": 1, "b": "done",') == {"a": 1, "b": "done"}


def test_total_garbage_raises():
    with pytest.raises(ValueError):
        recover_json("I have no idea what you want")
