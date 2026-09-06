"""
Unit tests for the Toast bridge doubles (src/features/toast_bridge.py) —
Phase 1 Step 1.5b.
"""

import pytest

from src.features.toast_bridge import InMemoryToastBridge, NoOpToastBridge

pytestmark = pytest.mark.unit


def test_in_memory_bridge_records_every_call():
    bridge = InMemoryToastBridge()
    t1 = bridge.register_toast("rem_1", "2026-02-12T09:00:00Z", "Call the dentist")
    t2 = bridge.register_toast("rem_2", "2026-02-13T09:00:00Z", "Water the plants")
    bridge.cancel_toast(t1)
    bridge.fire_toast("rem_2", "Water the plants")

    assert t1 != t2
    assert [op for op, _ in bridge.calls] == ["register", "register", "cancel", "fire"]
    assert bridge.calls[0][1] == {
        "reminder_id": "rem_1",
        "scheduled_time": "2026-02-12T09:00:00Z",
        "body": "Call the dentist",
    }
    assert bridge.calls[2][1] == {"toast_id": t1}
    assert bridge.calls[3][1] == {"reminder_id": "rem_2", "body": "Water the plants"}


def test_noop_bridge_returns_an_id_and_does_nothing_else():
    bridge = NoOpToastBridge()
    toast_id = bridge.register_toast("rem_1", "2026-02-12T09:00:00Z", "x")
    assert toast_id and isinstance(toast_id, str)
    # no exceptions, no state
    bridge.cancel_toast(toast_id)
    bridge.fire_toast("rem_1", "x")
    bridge.cancel_all()


def test_cancel_all_is_recorded_by_the_in_memory_bridge():
    # Phase 2 Step 2.2 — full wipe calls this once.
    bridge = InMemoryToastBridge()
    bridge.register_toast("rem_1", "2026-02-12T09:00:00Z", "x")
    bridge.cancel_all()
    assert ("cancel_all", {}) in bridge.calls
    assert [op for op, _ in bridge.calls] == ["register", "cancel_all"]
