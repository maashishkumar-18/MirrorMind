"""Phase 2 Step 2.2 — DataManager: export, export badge, full wipe."""

import json

import pytest

from src.features.data_admin import EXPORT_TABLES, WIPE_TABLES, DataManager
from src.features.toast_bridge import InMemoryToastBridge
from src.models.app_config import AppConfig

pytestmark = pytest.mark.integration


@pytest.fixture
def app_config(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGPIPE_APP_CONFIG_PATH", str(tmp_path / "app_config.json"))
    monkeypatch.delenv("RAGPIPE_DATA_DIR", raising=False)
    cfg = AppConfig.load()
    cfg.set_active_model("llama3.1:8b")
    return cfg


@pytest.fixture
def seeded_conn(session_conn):
    """`session_conn` already holds session `s1`. Add one row to every table."""
    c = session_conn
    c.executescript(
        """
        INSERT INTO messages (id, session_id, turn_index, role, content, created_at, updated_at)
            VALUES ('m1', 's1', 0, 'user', 'hello', 'x', 'x');
        INSERT INTO reminders (id, title, scheduled_time, created_at, updated_at)
            VALUES ('r1', 'buy milk', '2026-01-01T00:00:00Z', 'x', 'x');
        INSERT INTO todos (id, title, created_at, updated_at)
            VALUES ('t1', 'file taxes', 'x', 'x');
        INSERT INTO meeting_notes (id, raw_transcript, created_at, updated_at)
            VALUES ('mn1', 'we discussed budget', 'x', 'x');
        INSERT INTO schedules (id, date, created_at, updated_at)
            VALUES ('sch1', '2026-03-01', 'x', 'x');
        INSERT INTO schedule_items (id, schedule_id, title, start_time, end_time, created_at, updated_at)
            VALUES ('si1', 'sch1', 'standup', '09:00', '09:15', 'x', 'x');
        INSERT INTO summaries (id, summary_type, period_start, period_end, content, scheduled_at, generated_at, created_at, updated_at)
            VALUES ('sum1', 'daily', '2026-03-01', '2026-03-01', 'a good day', 'x', 'x', 'x', 'x');
        INSERT INTO sync_metadata (id, table_name, row_id, created_at, updated_at)
            VALUES ('sy1', 'reminders', 'r1', 'x', 'x');
        """
    )
    c.execute(
        "INSERT INTO session_chunks (id, session_id, chunk_type, content, embedding, token_count, "
        "created_at, updated_at) VALUES ('c1', 's1', 'primary', 'hello', ?, 1, 'x', 'x')",
        (b"\x00" * 1536,),
    )
    c.commit()
    return c


@pytest.fixture
def manager(seeded_conn, app_config):
    return DataManager(connection=seeded_conn, bridge=InMemoryToastBridge(), app_config=app_config)


# --- export ---------------------------------------------------------------


def test_export_contains_the_8_tables_and_not_session_chunks(manager):
    data = manager.export_data(now="2026-03-02T12:00:00+00:00")
    assert set(data["tables"]) == set(EXPORT_TABLES)
    assert "session_chunks" not in data["tables"]
    assert data["schema_version"] == "0001"
    assert data["exported_at"] == "2026-03-02T12:00:00+00:00"
    # one seeded row per table
    assert data["tables"]["reminders"][0]["title"] == "buy milk"
    assert len(data["tables"]["messages"]) == 1


def test_export_rows_drop_private_columns(manager):
    row = manager.export_data()["tables"]["reminders"][0]
    assert "deleted_at" not in row
    assert "sync_metadata" not in row
    assert "toast_id" in row  # a real column is kept


def test_export_excludes_soft_deleted_rows(manager, seeded_conn):
    seeded_conn.execute("UPDATE todos SET deleted_at = 'x' WHERE id = 't1'")
    seeded_conn.commit()
    assert manager.export_data()["tables"]["todos"] == []


def test_write_export_is_atomic_and_parses_equal_to_export_data(manager, tmp_path):
    out = tmp_path / "export.json"
    manager.write_export(out, now="2026-03-02T12:00:00+00:00")
    assert out.exists()
    assert list(tmp_path.glob("*.tmp")) == []
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk == manager.export_data(now="2026-03-02T12:00:00+00:00")


def test_write_export_stamps_last_exported_at(manager, app_config, tmp_path):
    manager.write_export(tmp_path / "e.json", now="2026-03-02T12:00:00+00:00")
    assert AppConfig.load().last_exported_at == "2026-03-02T12:00:00+00:00"


# --- badge ---------------------------------------------------------------


def test_badge_never_exported(manager):
    state = manager.export_badge_state(now="2026-03-02T00:00:00+00:00")
    assert state.needs_export is True
    assert state.last_exported_at is None
    assert state.days_since is None
    assert state.settings_line == (
        "Never — your data cannot be recovered if Windows is reinstalled without an export."
    )


def test_badge_recent_export_no_nudge(manager, app_config):
    app_config.set_last_exported_at("2026-03-01T00:00:00+00:00")
    state = manager.export_badge_state(now="2026-03-11T00:00:00+00:00")  # 10 days
    assert state.needs_export is False
    assert state.days_since == 10
    assert state.settings_line == "Last exported: 2026-03-01"


def test_badge_stale_export_nudges(manager, app_config):
    app_config.set_last_exported_at("2026-01-01T00:00:00+00:00")
    state = manager.export_badge_state(now="2026-02-10T00:00:00+00:00")  # 40 days
    assert state.needs_export is True
    assert state.days_since == 40


# --- full wipe ---------------------------------------------------------------


def test_full_wipe_soft_deletes_every_table(manager, seeded_conn):
    manager.full_wipe()
    for table in WIPE_TABLES:
        live = seeded_conn.execute(
            f"SELECT count(*) FROM {table} WHERE deleted_at IS NULL"
        ).fetchone()[0]
        assert live == 0, table
        total = seeded_conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        assert total >= 1, f"{table} rows were hard-deleted"


def test_full_wipe_clears_toasts_and_resets_last_exported_only(manager, app_config):
    app_config.set_last_exported_at("2026-01-01T00:00:00+00:00")
    manager.full_wipe()
    assert manager._bridge.calls.count(("cancel_all", {})) == 1
    reloaded = AppConfig.load()
    assert reloaded.last_exported_at is None
    assert reloaded.active_model == "llama3.1:8b"  # kept


def test_full_wipe_completes_even_if_cancel_all_raises(seeded_conn, app_config):
    class BoomBridge(InMemoryToastBridge):
        def cancel_all(self) -> None:
            raise RuntimeError("winrt down")

    mgr = DataManager(connection=seeded_conn, bridge=BoomBridge(), app_config=app_config)
    mgr.full_wipe()  # must not raise
    live = seeded_conn.execute(
        "SELECT count(*) FROM reminders WHERE deleted_at IS NULL"
    ).fetchone()[0]
    assert live == 0


def test_data_manager_requires_exactly_one_of_conn_or_path(app_config):
    with pytest.raises(ValueError):
        DataManager(bridge=InMemoryToastBridge(), app_config=app_config)
