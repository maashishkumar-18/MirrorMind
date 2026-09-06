"""
Tests for the feature-handler entity dataclasses in src/common/types.py
(Production Roadmap Phase 1 Step 1.5): Reminder, Todo, MeetingNote,
ScheduleItem, Summary, plus the ActionItem / ScheduleConflict helpers.

Plain dataclasses that hydrate a structured-table row — new code, so `unit`
(not `characterization`, which test_types_session.py reserves for the two
frozen Step 0.4 types).
"""

import dataclasses

import pytest

from src.common.types import (
    ActionItem,
    MeetingNote,
    Reminder,
    ScheduleConflict,
    ScheduleItem,
    Summary,
    Todo,
)

pytestmark = pytest.mark.unit


def test_reminder_minimal_and_defaults():
    r = Reminder(id="rem_1", title="Call the dentist", scheduled_time="2026-02-12T09:00:00Z")
    assert r.session_id is None
    assert r.notes == ""
    assert r.fired_at is None and r.completed_at is None and r.toast_id is None


def test_todo_minimal_and_defaults():
    t = Todo(id="todo_1", title="Buy milk")
    assert t.priority is None and t.category is None and t.completed_at is None
    assert dataclasses.asdict(t)["title"] == "Buy milk"


def test_meeting_note_nests_action_items():
    note = MeetingNote(
        id="mn_1",
        raw_transcript="...",
        action_items=[ActionItem(task="ship it", owner="Sam", deadline="Friday")],
    )
    assert note.attendees == [] and note.needs_review is False
    d = dataclasses.asdict(note)
    assert d["action_items"] == [{"task": "ship it", "owner": "Sam", "deadline": "Friday"}]


def test_action_item_defaults():
    ai = ActionItem(task="write the guide")
    assert ai.owner is None and ai.deadline is None


def test_schedule_item_and_conflict():
    item = ScheduleItem(
        id="sci_1",
        schedule_id="sch_1",
        title="Dentist",
        start_time="2026-02-20T14:00:00Z",
        end_time="2026-02-20T14:45:00Z",
    )
    conflict = ScheduleConflict(attempted=item, conflicts_with=[item])
    assert conflict.attempted is item
    assert conflict.conflicts_with[0].title == "Dentist"


def test_schedule_conflict_default_list_is_independent():
    a = ScheduleConflict(attempted=ScheduleItem("a", "s", "t", "x", "y"))
    b = ScheduleConflict(attempted=ScheduleItem("b", "s", "t", "x", "y"))
    a.conflicts_with.append(ScheduleItem("c", "s", "t", "x", "y"))
    assert b.conflicts_with == []


def test_summary_round_trips():
    s = Summary(
        id="sum_1",
        summary_type="daily",
        period_start="2026-02-20",
        period_end="2026-02-20",
        content="A quiet day.",
        scheduled_at="2026-02-20T21:00:00",
        generated_at="2026-02-20T21:00:03Z",
    )
    assert dataclasses.asdict(s)["summary_type"] == "daily"
