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


# -- Step 3.3 / Q2: overwrite_ids conflict resolution ------------------------


def test_overwrite_ids_soft_deletes_the_conflict_then_creates(handler, session_conn):
    standup = handler.create_schedule_item(
        "Standup", "2026-02-20T09:00:00Z", "2026-02-20T09:30:00Z"
    )
    assert isinstance(standup, ScheduleItem)

    created = handler.create_schedule_item(
        "Dentist",
        "2026-02-20T09:15:00Z",
        "2026-02-20T10:00:00Z",
        overwrite_ids=[standup.id],
    )
    assert isinstance(created, ScheduleItem) and created.title == "Dentist"
    assert [i.title for i in handler.get_day_schedule("2026-02-20")] == ["Dentist"]

    # soft-delete only — the row is still there, just flagged
    row = session_conn.execute(
        "SELECT deleted_at FROM schedule_items WHERE id = ?", (standup.id,)
    ).fetchone()
    assert row["deleted_at"] is not None


def test_overwrite_ids_covering_only_some_conflicts_returns_the_rest(handler):
    a = handler.create_schedule_item("A", "2026-02-20T09:00:00Z", "2026-02-20T09:20:00Z")
    b = handler.create_schedule_item("B", "2026-02-20T09:20:00Z", "2026-02-20T09:40:00Z")
    assert isinstance(a, ScheduleItem) and isinstance(b, ScheduleItem)

    result = handler.create_schedule_item(
        "Long block",
        "2026-02-20T09:10:00Z",
        "2026-02-20T09:30:00Z",
        overwrite_ids=[a.id],
    )
    assert isinstance(result, ScheduleConflict)
    assert [c.id for c in result.conflicts_with] == [b.id]  # only the unresolved one


def test_overwrite_ids_not_in_the_conflict_set_is_rejected(handler):
    standup = handler.create_schedule_item(
        "Standup", "2026-02-20T09:00:00Z", "2026-02-20T09:30:00Z"
    )
    assert isinstance(standup, ScheduleItem)
    with pytest.raises(ValueError, match="conflict set"):
        handler.create_schedule_item(
            "Dentist",
            "2026-02-20T09:15:00Z",
            "2026-02-20T10:00:00Z",
            overwrite_ids=["sci_does_not_exist"],
        )


def test_get_range_schedule_spans_days(handler):
    handler.create_schedule_item("Mon", "2026-02-16T09:00:00Z", "2026-02-16T10:00:00Z")
    handler.create_schedule_item("Wed", "2026-02-18T09:00:00Z", "2026-02-18T10:00:00Z")
    handler.create_schedule_item("Sun next", "2026-02-22T09:00:00Z", "2026-02-22T10:00:00Z")

    week = handler.get_range_schedule("2026-02-16", "2026-02-20")
    assert [i.title for i in week] == ["Mon", "Wed"]  # Sunday is outside the range
