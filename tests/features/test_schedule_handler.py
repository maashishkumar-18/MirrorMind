"""
Integration tests for ScheduleHandler (src/features/schedule_handler.py) — Phase 1 Step 1.5a.

Covers the roadmap acceptance criterion: an overlapping schedule item is
detected and surfaced as a ScheduleConflict — never silently committed.
"""

import pytest

from src.common.types import ScheduleConflict, ScheduleItem
from src.features.schedule_handler import ScheduleHandler

pytestmark = pytest.mark.integration


@pytest.fixture
def handler(session_conn):
    return ScheduleHandler(connection=session_conn)


def _count_items(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM schedule_items WHERE deleted_at IS NULL"
    ).fetchone()["c"]


def test_create_and_get_day_schedule_creates_parent_schedule(handler, session_conn):
    item = handler.create_schedule_item(
        "Dental cleaning",
        "2026-02-20T14:00:00Z",
        "2026-02-20T14:45:00Z",
        location="Bright Smile Dental",
    )
    assert isinstance(item, ScheduleItem)
    assert item.schedule_id.startswith("sch_")

    schedules = session_conn.execute("SELECT date FROM schedules").fetchall()
    assert [s["date"] for s in schedules] == ["2026-02-20"]

    day = handler.get_day_schedule("2026-02-20")
    assert [i.id for i in day] == [item.id]


def test_second_item_same_day_reuses_schedule_row(handler, session_conn):
    handler.create_schedule_item("Standup", "2026-02-20T09:00:00Z", "2026-02-20T09:15:00Z")
    handler.create_schedule_item("Dentist", "2026-02-20T14:00:00Z", "2026-02-20T14:45:00Z")
    assert session_conn.execute("SELECT COUNT(*) c FROM schedules").fetchone()["c"] == 1
    assert len(handler.get_day_schedule("2026-02-20")) == 2


def test_overlapping_item_is_a_conflict_and_commits_nothing(handler, session_conn):
    handler.create_schedule_item("Standup", "2026-02-20T09:00:00Z", "2026-02-20T09:30:00Z")
    before = _count_items(session_conn)

    result = handler.create_schedule_item(
        "Overlaps standup", "2026-02-20T09:15:00Z", "2026-02-20T10:00:00Z"
    )
    assert isinstance(result, ScheduleConflict)
    assert [c.title for c in result.conflicts_with] == ["Standup"]
    assert result.attempted.title == "Overlaps standup"
    assert _count_items(session_conn) == before  # nothing written


def test_back_to_back_items_do_not_conflict(handler):
    a = handler.create_schedule_item("First", "2026-02-20T09:00:00Z", "2026-02-20T09:30:00Z")
    b = handler.create_schedule_item("Second", "2026-02-20T09:30:00Z", "2026-02-20T10:00:00Z")
    assert isinstance(a, ScheduleItem) and isinstance(b, ScheduleItem)


def test_update_into_an_overlap_is_rejected(handler):
    a = handler.create_schedule_item("A", "2026-02-20T09:00:00Z", "2026-02-20T10:00:00Z")
    b = handler.create_schedule_item("B", "2026-02-20T11:00:00Z", "2026-02-20T12:00:00Z")
    assert isinstance(a, ScheduleItem) and isinstance(b, ScheduleItem)

    result = handler.update_schedule_item(b.id, start_time="2026-02-20T09:30:00Z")
    assert isinstance(result, ScheduleConflict)
    # b's stored time is unchanged
    assert handler.get_schedule_item(b.id).start_time == "2026-02-20T11:00:00Z"

    ok = handler.update_schedule_item(b.id, title="B renamed")
    assert isinstance(ok, ScheduleItem) and ok.title == "B renamed"
