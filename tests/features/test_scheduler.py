"""
Tests for the local scheduler thread (src/features/scheduler.py) and the
summary catch-up path it drives — Phase 1 Step 1.5b.
"""

import time

import pytest

from src.features.reminder_handler import ReminderHandler
from src.features.scheduler import SchedulerConfig, SchedulerThread
from src.features.summary_handler import SummaryConfig, SummaryHandler
from src.features.toast_bridge import InMemoryToastBridge

pytestmark = pytest.mark.integration

PAST = "2020-01-01T09:00:00Z"


@pytest.fixture
def summary_config():
    return SummaryConfig(daily_time="21:00", weekly_day="sunday", weekly_time="18:00")


def _seed_day(conn, date: str):
    now = f"{date}T08:00:00Z"
    conn.execute(
        "INSERT INTO reminders (id, title, scheduled_time, created_at, updated_at) VALUES (?,?,?,?,?)",
        (f"rem-{date}", "Water the plants", f"{date}T10:00:00Z", now, now),
    )
    conn.execute(
        "INSERT INTO todos (id, title, created_at, updated_at) VALUES (?,?,?,?)",
        (f"todo-{date}", "Pay the deposit", now, now),
    )
    conn.commit()


# --- generate_due_summaries -------------------------------------------------


def test_generate_due_summaries_backfills_missed_day(session_conn, summary_config, monkeypatch):
    _seed_day(session_conn, "2026-02-25")
    monkeypatch.setattr("src.features.summary_handler.simple_generate", lambda *a, **k: "rollup")
    handler = SummaryHandler(connection=session_conn, config=summary_config)

    generated = handler.generate_due_summaries("2026-03-01T23:00:00+00:00", catch_up_days=10)

    dailies = [s for s in generated if s.summary_type == "daily"]
    assert [s.period_start for s in dailies] == ["2026-02-25"]
    assert dailies[0].scheduled_at == "2026-02-25T21:00:00"
    assert dailies[0].scheduled_at != dailies[0].generated_at

    # idempotent — a second run generates nothing
    assert handler.generate_due_summaries("2026-03-02T23:00:00+00:00", catch_up_days=10) == []


def test_generate_due_summaries_skips_days_with_no_data(session_conn, summary_config, monkeypatch):
    _seed_day(session_conn, "2026-02-25")
    monkeypatch.setattr("src.features.summary_handler.simple_generate", lambda *a, **k: "rollup")
    handler = SummaryHandler(connection=session_conn, config=summary_config)

    generated = handler.generate_due_summaries("2026-03-01T23:00:00+00:00", catch_up_days=10)
    assert {s.period_start for s in generated if s.summary_type == "daily"} == {"2026-02-25"}
    # 2026-02-24 was inside the window but had no data -> no row
    assert handler.get_summary("daily", "2026-02-24") is None


def test_generate_due_summaries_does_not_run_before_the_scheduled_time(
    session_conn, summary_config, monkeypatch
):
    today = "2026-02-25"
    _seed_day(session_conn, today)
    monkeypatch.setattr("src.features.summary_handler.simple_generate", lambda *a, **k: "rollup")
    handler = SummaryHandler(connection=session_conn, config=summary_config)

    # 20:00 — before the 21:00 daily_time
    generated = handler.generate_due_summaries(f"{today}T20:00:00+00:00", catch_up_days=3)
    assert [s for s in generated if s.period_start == today] == []


# --- SchedulerThread ------------------------------------------------------


def test_tick_fires_a_due_reminder_and_backfills_a_summary(
    session_conn, summary_config, monkeypatch
):
    _seed_day(session_conn, "2026-02-25")
    session_conn.execute(
        "INSERT INTO reminders (id, title, scheduled_time, created_at, updated_at) VALUES (?,?,?,?,?)",
        ("rem-due", "ping", PAST, PAST, PAST),
    )
    session_conn.commit()
    monkeypatch.setattr("src.features.summary_handler.simple_generate", lambda *a, **k: "rollup")

    bridge = InMemoryToastBridge()
    sched = SchedulerThread("unused", bridge, config=SchedulerConfig(summary_catch_up_days=10))
    sched._reminders = ReminderHandler(connection=session_conn, bridge=bridge)
    sched._summaries = SummaryHandler(connection=session_conn, config=summary_config)

    sched._tick("2026-03-01T23:00:00+00:00")

    assert ("fire", {"reminder_id": "rem-due", "body": "ping"}) in bridge.calls
    assert (
        session_conn.execute("SELECT fired_at FROM reminders WHERE id='rem-due'").fetchone()[0]
        is not None
    )
    assert sched._summaries.get_summary("daily", "2026-02-25") is not None

    # a second tick fires nothing new
    calls_before = len(bridge.calls)
    sched._tick("2026-03-01T23:05:00+00:00")
    assert not any(op == "fire" for op, _ in bridge.calls[calls_before:])


def test_tick_takes_a_due_backup_once_per_day(session_conn, migrated_db_path, tmp_path):
    # Phase 2 Step 2.2 — the third _tick responsibility.
    from src.features.backup_manager import BackupConfig, BackupManager

    bridge = InMemoryToastBridge()
    sched = SchedulerThread("unused", bridge, config=SchedulerConfig())
    sched._reminders = ReminderHandler(connection=session_conn, bridge=bridge)
    sched._summaries = SummaryHandler(connection=session_conn, config=SummaryConfig())
    sched._backups = BackupManager(
        migrated_db_path,
        backup_dir=tmp_path / "backups",
        config=BackupConfig(daily_time="02:00", retention=7),
    )

    sched._tick("2026-03-01T03:00:00+00:00")
    sched._tick("2026-03-01T20:00:00+00:00")
    assert len(sched._backups.list_backups()) == 1

    sched._tick("2026-03-02T03:00:00+00:00")
    assert len(sched._backups.list_backups()) == 2


def test_tick_without_backups_component_is_a_noop(session_conn):
    # the existing hand-injected-handlers pattern must keep working
    bridge = InMemoryToastBridge()
    sched = SchedulerThread("unused", bridge, config=SchedulerConfig())
    sched._reminders = ReminderHandler(connection=session_conn, bridge=bridge)
    sched._summaries = SummaryHandler(connection=session_conn, config=SummaryConfig())
    assert sched._backups is None
    sched._tick("2026-03-01T03:00:00+00:00")  # must not raise


def test_scheduler_thread_runs_and_stops_cleanly(migrated_db_path):
    conn_bridge = InMemoryToastBridge()
    # a due reminder for the running thread to pick up
    from db.connection import open_session_db

    conn = open_session_db(migrated_db_path)
    conn.execute(
        "INSERT INTO reminders (id, title, scheduled_time, created_at, updated_at) VALUES (?,?,?,?,?)",
        ("rem-live", "live ping", PAST, PAST, PAST),
    )
    conn.commit()
    conn.close()

    sched = SchedulerThread(
        migrated_db_path, conn_bridge, config=SchedulerConfig(poll_seconds=0.05)
    )
    sched.start()
    deadline = time.time() + 3.0
    while time.time() < deadline and not any(op == "fire" for op, _ in conn_bridge.calls):
        time.sleep(0.05)
    sched.stop(timeout=2.0)

    assert any(op == "fire" for op, _ in conn_bridge.calls)
    assert not sched.is_alive()
