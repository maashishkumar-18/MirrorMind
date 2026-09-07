"""``AgenticActionType`` → Step 1.5 feature-handler dispatch (Phase 3 Step 3.1d).

Called by ``SessionWorker`` only for a Tier-1 (confidence >= 0.85) actionable
message. Builds the right handler on the worker's connection, calls its create
method with the extracted + coerced slots, and returns a ``DispatchOutcome``
carrying a **deterministic** confirmation string (no third LLM call) plus the
created entity's id for the frontend.

Never raises: a missing slot, a bad value, or a schedule conflict all come
back as a structured outcome.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from dateutil import parser as _dateparser

from src.common.types import (
    AgenticActionType,
    MeetingNote,
    Reminder,
    ScheduleConflict,
    ScheduleItem,
    Summary,
    Todo,
)
from src.features.base import now_iso
from src.features.meeting_note_handler import MeetingNoteHandler
from src.features.reminder_handler import ReminderHandler
from src.features.schedule_handler import ScheduleHandler
from src.features.summary_handler import SummaryHandler
from src.features.toast_bridge import NoOpToastBridge, ToastBridge
from src.features.todo_handler import TodoHandler

logger = logging.getLogger(__name__)

_MISSING_DETAIL = "I couldn't quite catch the details — try rephrasing with the specifics."
_SOFT_FAIL = "Something went wrong creating that — could you try again?"


def _coerce_iso(value: object, now: str) -> str | None:
    """Whatever the model emitted for a datetime slot → a normalized ISO
    string, or ``None`` if it is genuinely unparseable."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        pass
    try:
        base = datetime.fromisoformat(now)
    except ValueError:
        base = datetime.now(UTC)
    base = base.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        return _dateparser.parse(text, default=base).isoformat()
    except (ValueError, OverflowError):
        return None


@dataclass
class DispatchOutcome:
    ok: bool
    answer: str
    feature: dict | None = None  # {"kind", "id", "summary"}
    conflict: dict | None = None  # {"attempted": {...}, "conflicts_with": [{...}]}


def _feature(kind: str, entity_id: str, summary: str) -> dict:
    return {"kind": kind, "id": entity_id, "summary": summary}


def _friendly(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%a %-d %b, %-I:%M %p")
    except (ValueError, TypeError):
        try:  # Windows strftime has no %-
            return datetime.fromisoformat(iso).strftime("%a %d %b, %I:%M %p")
        except (ValueError, TypeError):
            return iso


# --------------------------------------------------------------------------


def dispatch(
    action_type: AgenticActionType,
    slots: dict[str, object],
    utterance: str,
    *,
    conn: object,
    session_id: str | None,
    bridge: ToastBridge | None = None,
    model: str | None = None,
) -> DispatchOutcome:
    bridge = bridge or NoOpToastBridge()
    try:
        if action_type == AgenticActionType.REMINDER:
            return _reminder(slots, conn, session_id, bridge)
        if action_type == AgenticActionType.TODO:
            return _todo(slots, conn, session_id)
        if action_type == AgenticActionType.SCHEDULE:
            return _schedule(slots, conn)
        if action_type == AgenticActionType.MEETING_NOTE:
            return _meeting_note(utterance, conn, session_id, model)
        if action_type == AgenticActionType.SUMMARY_REQUEST:
            return _summary(slots, conn)
    except Exception:  # noqa: BLE001 — dispatch never raises into send()
        logger.exception("action dispatch for %s failed", action_type.value)
        return DispatchOutcome(ok=False, answer=_SOFT_FAIL)
    return DispatchOutcome(ok=False, answer=_MISSING_DETAIL)


def _reminder(slots, conn, session_id, bridge) -> DispatchOutcome:
    title = slots.get("title")
    when = _coerce_iso(slots.get("scheduled_time"), now_iso())
    if not isinstance(title, str) or not when:
        return DispatchOutcome(ok=False, answer=_MISSING_DETAIL)
    r: Reminder = ReminderHandler(connection=conn, bridge=bridge).create_reminder(
        title, when, notes=str(slots.get("notes") or ""), session_id=session_id
    )
    return DispatchOutcome(
        ok=True,
        answer=f"Reminder set: {r.title} — {_friendly(r.scheduled_time)}.",
        feature=_feature("reminder", r.id, r.title),
    )


def _todo(slots, conn, session_id) -> DispatchOutcome:
    title = slots.get("title")
    if not isinstance(title, str):
        return DispatchOutcome(ok=False, answer=_MISSING_DETAIL)
    handler = TodoHandler(connection=conn)
    priority = slots.get("priority")
    priority = priority if priority in ("low", "medium", "high") else None
    kwargs = dict(
        notes=str(slots.get("notes") or ""),
        category=str(slots.get("category")) if slots.get("category") else None,
        session_id=session_id,
    )
    try:
        t: Todo = handler.create_todo(title, priority=priority, **kwargs)
    except ValueError:  # a stray priority slipped the filter — drop it and retry
        t = handler.create_todo(title, priority=None, **kwargs)
    tail = f" ({t.priority} priority)" if t.priority else ""
    return DispatchOutcome(
        ok=True,
        answer=f"Added to your todos: {t.title}{tail}.",
        feature=_feature("todo", t.id, t.title),
    )


def _schedule(slots, conn) -> DispatchOutcome:
    title = slots.get("title")
    start = _coerce_iso(slots.get("start_time"), now_iso())
    end = _coerce_iso(slots.get("end_time"), now_iso())
    if not isinstance(title, str) or not start:
        return DispatchOutcome(ok=False, answer=_MISSING_DETAIL)
    if not end:  # default to a one-hour block
        end = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
    result = ScheduleHandler(connection=conn).create_schedule_item(
        title,
        start,
        end,
        location=str(slots.get("location") or ""),
        notes=str(slots.get("notes") or ""),
    )
    if isinstance(result, ScheduleConflict):
        clashes = ", ".join(
            f"'{c.title}' {_friendly(c.start_time)}–{_friendly(c.end_time)}"
            for c in result.conflicts_with
        )
        return DispatchOutcome(
            ok=False,
            answer=f"That overlaps {clashes}. Keep which?",
            conflict={
                "attempted": _item_dict(result.attempted),
                "conflicts_with": [_item_dict(c) for c in result.conflicts_with],
            },
        )
    item: ScheduleItem = result
    return DispatchOutcome(
        ok=True,
        answer=f"Scheduled: {item.title}, {_friendly(item.start_time)}.",
        feature=_feature("schedule_item", item.id, item.title),
    )


def _meeting_note(utterance, conn, session_id, model) -> DispatchOutcome:
    note: MeetingNote = MeetingNoteHandler(connection=conn, model=model).capture_meeting_note(
        utterance, session_id=session_id
    )
    bits = []
    if note.decisions:
        bits.append(f"{len(note.decisions)} decision(s)")
    if note.action_items:
        bits.append(f"{len(note.action_items)} action item(s)")
    body = ", ".join(bits) if bits else "no decisions or action items found"
    review = " — flagged for review." if note.needs_review else "."
    return DispatchOutcome(
        ok=True,
        answer=f"Meeting note captured: {body}{review}",
        feature=_feature("meeting_note", note.id, body),
    )


def _summary(slots, conn) -> DispatchOutcome:
    period = slots.get("period")
    period = period if period in ("daily", "weekly") else "daily"
    date = slots.get("date")
    date = str(date)[:10] if isinstance(date, str) and date else now_iso()[:10]
    handler = SummaryHandler(connection=conn)
    s: Summary = (
        handler.generate_weekly_summary(date)
        if period == "weekly"
        else handler.generate_daily_summary(date)
    )
    return DispatchOutcome(
        ok=True, answer=s.content, feature=_feature("summary", s.id, f"{period} summary")
    )


def _item_dict(i: ScheduleItem) -> dict:
    return {
        "id": i.id,
        "title": i.title,
        "start_time": i.start_time,
        "end_time": i.end_time,
        "location": i.location,
    }
