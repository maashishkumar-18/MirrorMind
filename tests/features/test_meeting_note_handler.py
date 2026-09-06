"""
Integration tests for MeetingNoteHandler (src/features/meeting_note_handler.py)
— Phase 1 Step 1.5a.

`simple_generate` is monkeypatched at the consuming module. Covers the roadmap
acceptance criterion: a note with neither decisions nor action_items is stored
with needs_review = 1.
"""

import json

import pytest

from src.features.meeting_note_handler import MeetingNoteHandler

pytestmark = pytest.mark.integration

_GOOD_PAYLOAD = {
    "attendees": ["Priya", "Sam"],
    "topics": ["launch", "budget"],
    "decisions": ["Launch moves to Friday", "Budget capped at $5000"],
    "action_items": [
        {"task": "Send the updated checklist", "owner": "Priya", "deadline": "Wednesday"},
        {"task": "Prep the announcement blog post", "owner": "Sam", "deadline": None},
    ],
    "follow_ups": ["Confirm marketing is ready by Thursday"],
}


@pytest.fixture
def handler(session_conn):
    return MeetingNoteHandler(connection=session_conn)


def _patch(monkeypatch, value):
    monkeypatch.setattr(
        "src.features.meeting_note_handler.simple_generate",
        lambda *a, **k: value if isinstance(value, str) else json.dumps(value),
    )


def test_extraction_parses_fields_and_flattens_searchable_text(handler, monkeypatch, session_conn):
    _patch(monkeypatch, _GOOD_PAYLOAD)
    note = handler.capture_meeting_note("Long transcript...", session_id="s1")

    assert note.attendees == ["Priya", "Sam"]
    assert note.decisions == ["Launch moves to Friday", "Budget capped at $5000"]
    assert note.action_items[0].owner == "Priya"
    assert note.action_items[1].deadline is None
    assert note.needs_review is False
    assert "Send the updated checklist" in note.searchable_text
    assert "Priya" in note.searchable_text

    hit = session_conn.execute(
        "SELECT m.id FROM meeting_notes m JOIN meeting_notes_fts f ON f.rowid = m.rowid "
        "WHERE meeting_notes_fts MATCH ? AND m.deleted_at IS NULL",
        ('"marketing"',),
    ).fetchall()
    assert [r["id"] for r in hit] == [note.id]


def test_markdown_fenced_json_is_recovered(handler, monkeypatch):
    _patch(monkeypatch, "```json\n" + json.dumps(_GOOD_PAYLOAD) + "\n```")
    note = handler.capture_meeting_note("t")
    assert note.attendees == ["Priya", "Sam"]


def test_no_decisions_or_action_items_sets_needs_review(handler, monkeypatch):
    _patch(
        monkeypatch,
        {
            "attendees": ["x"],
            "topics": ["chat"],
            "decisions": [],
            "action_items": [],
            "follow_ups": [],
        },
    )
    note = handler.capture_meeting_note("we just chatted")
    assert note.needs_review is True


def test_extraction_failure_stores_raw_and_flags_review(handler, monkeypatch):
    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("model down")

    monkeypatch.setattr("src.features.meeting_note_handler.simple_generate", _boom)
    note = handler.capture_meeting_note("raw transcript kept verbatim")

    assert calls["n"] == 3  # 1 + 2 retries
    assert note.needs_review is True
    assert note.raw_transcript == "raw transcript kept verbatim"
    assert note.decisions == [] and note.action_items == []


def test_garbage_json_then_flag(handler, monkeypatch):
    _patch(monkeypatch, "not json at all, sorry")
    note = handler.capture_meeting_note("t")
    assert note.needs_review is True


def test_get_and_filter_by_needs_review(handler, monkeypatch):
    _patch(monkeypatch, _GOOD_PAYLOAD)
    ok = handler.capture_meeting_note("good one")
    _patch(monkeypatch, "garbage")
    bad = handler.capture_meeting_note("bad one")

    assert {n.id for n in handler.get_meeting_notes()} == {ok.id, bad.id}
    assert [n.id for n in handler.get_meeting_notes(needs_review=True)] == [bad.id]
    assert [n.id for n in handler.get_meeting_notes(needs_review=False)] == [ok.id]

    assert handler.delete_meeting_note(bad.id) is True
    assert handler.get_meeting_note(bad.id) is None
