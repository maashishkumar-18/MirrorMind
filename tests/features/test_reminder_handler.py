"""
Integration tests for ReminderHandler (src/features/reminder_handler.py) —
Phase 1 Step 1.5b.

Covers the roadmap acceptance criteria: reminder created -> stored -> Toast
registered (verified via InMemoryToastBridge); on-launch reconciliation
surfaces `scheduled_time < NOW AND fired_at IS NULL` as "overdue" and
`fired_at IS NOT NULL AND completed_at IS NULL` as "pending acknowledgment" —
the two conditions verified in separate tests.
"""

import pytest

from src.features.reminder_handler import ReminderHandler
from src.features.toast_bridge import InMemoryToastBridge

pytestmark = pytest.mark.integration

PAST = "2020-01-01T09:00:00Z"
FUTURE = "2999-01-01T09:00:00Z"


@pytest.fixture
def bridge():
    return InMemoryToastBridge()


@pytest.fixture
def handler(session_conn, bridge):
    return ReminderHandler(connection=session_conn, bridge=bridge)


def _ops(bridge):
    return [op for op, _ in bridge.calls]


def test_create_registers_a_toast_and_stores_the_id(handler, bridge):
    r = handler.create_reminder("Call the dentist", FUTURE, notes="annual", session_id="s1")
    assert r.id.startswith("rem_")
    assert r.toast_id == "toast_1"
    assert _ops(bridge) == ["register"]
    assert bridge.calls[0][1]["reminder_id"] == r.id
    assert handler.get_reminder(r.id).toast_id == "toast_1"


def test_update_time_cancels_then_reregisters(handler, bridge):
    r = handler.create_reminder("Pay the bill", FUTURE)
    updated = handler.update_reminder(r.id, scheduled_time="2999-06-01T10:00:00Z")
    assert _ops(bridge) == ["register", "cancel", "register"]
    assert bridge.calls[1][1] == {"toast_id": "toast_1"}
    assert updated.toast_id == "toast_3"
    assert updated.scheduled_time == "2999-06-01T10:00:00Z"


def test_update_notes_only_does_not_touch_the_toast(handler, bridge):
    r = handler.create_reminder("Pay the bill", FUTURE)
    handler.update_reminder(r.id, notes="electricity")
    assert _ops(bridge) == ["register"]


def test_delete_soft_deletes_and_cancels_the_toast(handler, bridge):
    r = handler.create_reminder("x", FUTURE)
    assert handler.delete_reminder(r.id) is True
    assert handler.get_reminder(r.id) is None
    assert _ops(bridge) == ["register", "cancel"]
    assert handler.delete_reminder(r.id) is False  # already gone, no extra cancel
    assert _ops(bridge) == ["register", "cancel"]


def test_complete_and_dismiss_stamp_their_columns(handler):
    a = handler.create_reminder("a", FUTURE)
    b = handler.create_reminder("b", FUTURE)
    assert handler.complete_reminder(a.id).completed_at is not None
    assert handler.dismiss_reminder(b.id).dismissed_at is not None
    assert {r.id for r in handler.get_reminders(active_only=True)} == set()


def test_reschedule_clears_fired_and_reregisters(handler, bridge, session_conn):
    r = handler.create_reminder("x", PAST)
    handler.fire_due("2021-01-01T00:00:00Z")
    assert handler.get_reminder(r.id).fired_at is not None

    rescheduled = handler.reschedule_reminder(r.id, FUTURE)
    assert rescheduled.fired_at is None
    assert rescheduled.scheduled_time == FUTURE
    assert _ops(bridge) == ["register", "fire", "cancel", "register"]


# --- fire_due -------------------------------------------------------------


def test_fire_due_marks_and_triggers_once(handler, bridge):
    due = handler.create_reminder("due now", PAST)
    handler.create_reminder("later", FUTURE)

    fired = handler.fire_due("2021-01-01T00:00:00Z")
    assert fired == [due.id]
    assert handler.get_reminder(due.id).fired_at == "2021-01-01T00:00:00Z"
    assert _ops(bridge) == ["register", "register", "fire"]

    # second tick: nothing new fires, no new bridge call
    assert handler.fire_due("2021-01-02T00:00:00Z") == []
    assert _ops(bridge) == ["register", "register", "fire"]


# --- reconcile_on_launch: two independent failure modes -----------------


def test_reconcile_surfaces_missed_fire_as_overdue(handler, session_conn):
    r = handler.create_reminder("missed", PAST)  # fired_at stays NULL

    result = handler.reconcile_on_launch(now="2021-01-01T00:00:00Z")
    assert [x.id for x in result.overdue] == [r.id]
    assert result.pending_acknowledgment == []


def test_reconcile_surfaces_fired_unacknowledged_as_pending(handler):
    r = handler.create_reminder("fired but not acked", PAST)
    handler.fire_due("2021-01-01T00:00:00Z")

    result = handler.reconcile_on_launch(now="2021-06-01T00:00:00Z")
    assert result.overdue == []
    assert [x.id for x in result.pending_acknowledgment] == [r.id]


def test_reconcile_ignores_completed_dismissed_and_future(handler):
    done = handler.create_reminder("done", PAST)
    handler.fire_due("2021-01-01T00:00:00Z")
    handler.complete_reminder(done.id)

    dismissed = handler.create_reminder("dismissed", PAST)
    handler.dismiss_reminder(dismissed.id)  # dismissed before firing

    handler.create_reminder("future", FUTURE)

    result = handler.reconcile_on_launch(now="2021-06-01T00:00:00Z")
    assert result.overdue == []
    assert result.pending_acknowledgment == []


def test_reconcile_keys_on_time_not_toast_id(session_conn):
    """A row whose toast_id write never landed (crash in create_reminder's
    non-transactional window) is still surfaced correctly."""
    session_conn.execute(
        "INSERT INTO reminders (id, title, scheduled_time, toast_id, created_at, updated_at) "
        "VALUES (?, ?, ?, NULL, ?, ?)",
        ("rem_orphan", "no toast", PAST, "2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
    )
    session_conn.commit()
    handler = ReminderHandler(connection=session_conn)

    result = handler.reconcile_on_launch(now="2021-01-01T00:00:00Z")
    assert [x.id for x in result.overdue] == ["rem_orphan"]
