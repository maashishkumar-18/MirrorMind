"""
Unit tests for the Toast bridge doubles (src/features/toast_bridge.py) —
Phase 1 Step 1.5b.
"""

import json

import pytest

from src.features.toast_bridge import (
    FileRecordingToastBridge,
    InMemoryToastBridge,
    NoOpToastBridge,
    resolve_bridge_from_env,
)

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


# --------------------------------------------------------------------------
# Phase 4 Step 4.1 — the e2e recording bridge + env resolver
# --------------------------------------------------------------------------


def test_file_recording_bridge_appends_one_json_line_per_call(tmp_path):
    path = tmp_path / "nested" / "toast-calls.jsonl"  # parent is created
    bridge = FileRecordingToastBridge(path)
    tid = bridge.register_toast("rem_1", "2026-02-12T09:00:00Z", "Call the dentist")
    bridge.cancel_toast(tid)
    bridge.fire_toast("rem_1", "Call the dentist")
    bridge.cancel_all()

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [entry["op"] for entry in lines] == ["register", "cancel", "fire", "cancel_all"]
    assert lines[0]["args"] == {
        "reminder_id": "rem_1",
        "scheduled_time": "2026-02-12T09:00:00Z",
        "body": "Call the dentist",
    }
    # still behaves like InMemoryToastBridge for in-process assertions
    assert [op for op, _ in bridge.calls] == ["register", "cancel", "fire", "cancel_all"]


def test_resolve_bridge_from_env_defaults_to_noop(monkeypatch):
    monkeypatch.delenv("RAGPIPE_FAKE_TOAST", raising=False)
    assert isinstance(resolve_bridge_from_env(), NoOpToastBridge)


def test_resolve_bridge_from_env_returns_file_recorder_when_set(monkeypatch, tmp_path):
    target = tmp_path / "toast.jsonl"
    monkeypatch.setenv("RAGPIPE_FAKE_TOAST", str(target))
    bridge = resolve_bridge_from_env()
    assert isinstance(bridge, FileRecordingToastBridge)
    bridge.register_toast("r", "2026-02-12T09:00:00Z", "x")
    assert target.exists()
