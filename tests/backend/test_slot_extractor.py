"""SlotExtractor (Phase 3 Step 3.1d) — simple_generate monkeypatched."""

from __future__ import annotations

import json

import pytest

import src.backend.slot_extractor as se
from src.backend.slot_extractor import SlotExtractor
from src.common.types import AgenticActionType


@pytest.fixture
def stub(monkeypatch):
    box: dict = {}

    def _set(raw: str):
        box["raw"] = raw
        monkeypatch.setattr(se, "simple_generate", lambda *a, **k: box["raw"])

    return _set


def test_reminder_well_formed(stub):
    stub(json.dumps({"title": "call dentist", "scheduled_time": "2026-09-10T15:00:00+00:00"}))
    out = SlotExtractor().extract(
        AgenticActionType.REMINDER, "remind me", "2026-09-08T00:00:00+00:00"
    )
    assert out == {"title": "call dentist", "scheduled_time": "2026-09-10T15:00:00+00:00"}


def test_nulls_and_empties_are_dropped(stub):
    stub(json.dumps({"title": "x", "priority": None, "category": "", "notes": "keep"}))
    out = SlotExtractor().extract(AgenticActionType.TODO, "todo", "2026-09-08T00:00:00+00:00")
    assert out == {"title": "x", "notes": "keep"}


def test_truncated_json_recovered(stub):
    stub('{"title": "draft the report", "priority": "high",')  # closing brace dropped
    out = SlotExtractor().extract(AgenticActionType.TODO, "t", "2026-09-08T00:00:00+00:00")
    assert out == {"title": "draft the report", "priority": "high"}


def test_garbage_returns_empty(stub):
    stub("no json here at all")
    assert (
        SlotExtractor().extract(AgenticActionType.SCHEDULE, "s", "2026-09-08T00:00:00+00:00") == {}
    )


def test_transport_failure_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(se, "simple_generate", boom)
    assert (
        SlotExtractor().extract(AgenticActionType.REMINDER, "x", "2026-09-08T00:00:00+00:00") == {}
    )


def test_non_actionable_type_returns_empty():
    assert (
        SlotExtractor().extract(AgenticActionType.CONVERSATION, "hi", "2026-09-08T00:00:00+00:00")
        == {}
    )
