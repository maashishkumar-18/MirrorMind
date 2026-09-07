"""action_dispatch (Phase 3 Step 3.1d) — real migrated DB + real handlers."""

from __future__ import annotations

import pytest

from db.connection import open_session_db
from src.backend import action_dispatch
from src.backend.action_dispatch import _coerce_iso, dispatch
from src.common.types import AgenticActionType

NOW = "2026-09-08T09:00:00+00:00"


@pytest.fixture
def conn(keyed_db):
    c = open_session_db(keyed_db)
    try:
        yield c
    finally:
        c.close()


def _count(c, table):
    return c.execute(f"SELECT COUNT(*) FROM {table} WHERE deleted_at IS NULL").fetchone()[0]


def test_reminder(conn):
    out = dispatch(
        AgenticActionType.REMINDER,
        {"title": "call the dentist", "scheduled_time": "2026-09-10T15:00:00+00:00"},
        "remind me to call the dentist",
        conn=conn,
        session_id=None,
    )
    assert out.ok and out.feature["kind"] == "reminder"
    assert _count(conn, "reminders") == 1
    assert "call the dentist" in out.answer


def test_todo_with_bad_priority_retries_without(conn):
    out = dispatch(
        AgenticActionType.TODO,
        {"title": "draft the report", "priority": "urgent"},
        "add a todo",
        conn=conn,
        session_id=None,
    )
    assert out.ok and _count(conn, "todos") == 1
    row = conn.execute("SELECT priority FROM todos").fetchone()
    assert row["priority"] is None


def test_schedule_and_conflict(conn):
    ok = dispatch(
        AgenticActionType.SCHEDULE,
        {
            "title": "Q4 review",
            "start_time": "2026-09-10T14:00:00+00:00",
            "end_time": "2026-09-10T15:00:00+00:00",
        },
        "schedule the Q4 review",
        conn=conn,
        session_id=None,
    )
    assert ok.ok and _count(conn, "schedule_items") == 1

    clash = dispatch(
        AgenticActionType.SCHEDULE,
        {
            "title": "1:1 with Sam",
            "start_time": "2026-09-10T14:30:00+00:00",
            "end_time": "2026-09-10T15:30:00+00:00",
        },
        "schedule a 1:1",
        conn=conn,
        session_id=None,
    )
    assert not clash.ok and clash.conflict is not None
    assert [i["title"] for i in clash.conflict["conflicts_with"]] == ["Q4 review"]
    assert _count(conn, "schedule_items") == 1  # the clashing item was NOT created


def test_missing_required_slot_creates_nothing(conn):
    out = dispatch(
        AgenticActionType.REMINDER, {"title": "x"}, "remind me", conn=conn, session_id=None
    )
    assert not out.ok and out.feature is None
    assert _count(conn, "reminders") == 0


def test_meeting_note_self_extracts(conn, monkeypatch):
    import src.features.meeting_note_handler as mnh

    monkeypatch.setattr(
        mnh,
        "simple_generate",
        lambda *a, **k: '{"attendees": ["Sam"], "topics": [], "decisions": ["ship on Friday"], "action_items": [], "follow_ups": []}',
    )
    out = dispatch(
        AgenticActionType.MEETING_NOTE,
        {},
        "Sam and I decided to ship on Friday",
        conn=conn,
        session_id=None,
    )
    assert out.ok and _count(conn, "meeting_notes") == 1
    assert "1 decision" in out.answer


def test_summary_request_defaults_to_daily(conn, monkeypatch):
    import src.features.summary_handler as sh

    monkeypatch.setattr(
        sh.SummaryHandler, "_generate", lambda self, prompt, fallback: "your day: nothing much"
    )
    out = dispatch(
        AgenticActionType.SUMMARY_REQUEST, {}, "summarize my day", conn=conn, session_id=None
    )
    assert out.ok and out.answer == "your day: nothing much"
    assert conn.execute("SELECT summary_type FROM summaries").fetchone()["summary_type"] == "daily"


def test_coerce_iso():
    assert _coerce_iso("2026-09-10T15:00:00+00:00", NOW) == "2026-09-10T15:00:00+00:00"
    assert _coerce_iso("Sept 10 2026 3pm", NOW).startswith("2026-09-10T15:00")
    assert _coerce_iso("whenever", NOW) is None
    assert _coerce_iso(None, NOW) is None


def test_dispatch_never_raises(conn, monkeypatch):
    monkeypatch.setattr(
        action_dispatch, "ReminderHandler", lambda **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    out = dispatch(
        AgenticActionType.REMINDER,
        {"title": "x", "scheduled_time": "2026-09-10T15:00:00+00:00"},
        "x",
        conn=conn,
        session_id=None,
    )
    assert not out.ok
