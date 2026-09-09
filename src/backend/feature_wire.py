"""Feature-entity → wire-dict mappers for the Step 3.3 feature-view IPC methods.

Sibling of ``reminders_wire.py`` (reused verbatim for reminders). Each mapper
produces the exact field set of the matching ``*Wire`` model in
``src.common.ipc.methods`` — the handlers ``model_validate`` the dict so any
drift fails loudly in ``tests/backend/test_handlers.py``.
"""

from __future__ import annotations

from src.common.types import (
    ActionItem,
    MeetingNote,
    ScheduleConflict,
    ScheduleItem,
    Todo,
)


def todo_wire(t: Todo) -> dict[str, object]:
    return {
        "id": t.id,
        "session_id": t.session_id,
        "title": t.title,
        "notes": t.notes,
        "priority": t.priority,
        "category": t.category,
        "completed_at": t.completed_at,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


def action_item_wire(ai: ActionItem) -> dict[str, object]:
    return {"task": ai.task, "owner": ai.owner, "deadline": ai.deadline}


def meeting_note_wire(n: MeetingNote) -> dict[str, object]:
    return {
        "id": n.id,
        "session_id": n.session_id,
        "raw_transcript": n.raw_transcript,
        "attendees": list(n.attendees),
        "topics": list(n.topics),
        "decisions": list(n.decisions),
        "action_items": [action_item_wire(ai) for ai in n.action_items],
        "follow_ups": list(n.follow_ups),
        "needs_review": n.needs_review,
        "searchable_text": n.searchable_text,
        "created_at": n.created_at,
        "updated_at": n.updated_at,
    }


def schedule_item_wire(i: ScheduleItem) -> dict[str, object]:
    return {
        "id": i.id,
        "schedule_id": i.schedule_id,
        "title": i.title,
        "start_time": i.start_time,
        "end_time": i.end_time,
        "location": i.location,
        "notes": i.notes,
        "created_at": i.created_at,
        "updated_at": i.updated_at,
    }


def schedule_conflict_wire(c: ScheduleConflict) -> dict[str, object]:
    return {
        "attempted": schedule_item_wire(c.attempted),
        "conflicts_with": [schedule_item_wire(i) for i in c.conflicts_with],
    }
