"""Shared `Reminder` → wire-shape mapping (Phase 3 Step 3.1c).

Used by both the `reminders.reconciliation` handler and the `app.reminders_pending`
event builder in `main`, so the two never drift.
"""

from __future__ import annotations

from src.common.types import ReconciliationResult, Reminder


def reminder_wire(r: Reminder) -> dict[str, str | None]:
    return {
        "id": r.id,
        "title": r.title,
        "scheduled_time": r.scheduled_time,
        "notes": r.notes,
        "fired_at": r.fired_at,
        "completed_at": r.completed_at,
        "dismissed_at": r.dismissed_at,
        "created_at": r.created_at,
    }


def reconciliation_payload(recon: ReconciliationResult) -> dict[str, list[dict[str, str | None]]]:
    return {
        "overdue": [reminder_wire(r) for r in recon.overdue],
        "pending_acknowledgment": [reminder_wire(r) for r in recon.pending_acknowledgment],
    }
