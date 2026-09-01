"""
Schema tests for db/migrations/0001_initial_schema.sql (Production Roadmap
Phase 0 Step 0.4).

Applies the migration to a fresh tmp_path database via MigrationRunner
(not raw executescript() -- exercising the same statement-by-statement
path production code uses) and asserts on the resulting schema.
"""

import sqlite3

import pytest

from db.migration_runner import MigrationRunner

pytestmark = pytest.mark.characterization

SUBSTANTIVE_TABLES = {
    "sessions",
    "messages",
    "session_chunks",
    "reminders",
    "todos",
    "meeting_notes",
    "schedules",
    "schedule_items",
    "summaries",
    "sync_metadata",
}

FTS_TABLES = {"reminders_fts", "todos_fts", "meeting_notes_fts", "schedule_items_fts"}


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "session.db")
    runner = MigrationRunner(
        db_path=db_path,
        snapshot_dir=tmp_path / "snapshots",
    )
    runner.run()
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    yield connection
    connection.close()


def _table_names(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


class TestAllTablesExist:
    def test_all_ten_substantive_tables_exist(self, conn):
        assert SUBSTANTIVE_TABLES.issubset(_table_names(conn))

    def test_schema_migrations_table_exists(self, conn):
        assert "schema_migrations" in _table_names(conn)

    def test_all_four_fts5_tables_exist(self, conn):
        assert FTS_TABLES.issubset(_table_names(conn))


class TestUniversalColumns:
    @pytest.mark.parametrize("table", sorted(SUBSTANTIVE_TABLES - {"sync_metadata"}))
    def test_universal_columns_present(self, conn, table):
        columns = _columns(conn, table)
        assert {"id", "created_at", "updated_at", "deleted_at", "sync_metadata"}.issubset(columns)

    def test_sync_metadata_table_has_no_sync_metadata_column_of_its_own(self, conn):
        """Documented exception: a self-referential JSON column on the
        sync-bookkeeping table itself is meaningless."""
        columns = _columns(conn, "sync_metadata")
        assert "sync_metadata" not in columns
        assert {"id", "created_at", "updated_at", "deleted_at"}.issubset(columns)


class TestSessionChunksColumns:
    def test_expected_columns_present(self, conn):
        columns = _columns(conn, "session_chunks")
        expected = {
            "id",
            "session_id",
            "chunk_type",
            "window_start_message_idx",
            "window_end_message_idx",
            "content",
            "embedding",
            "token_count",
            "topics",
            "action_types",
            "entities",
            "sentiment",
            "message_roles",
            "created_at",
            "updated_at",
            "deleted_at",
            "sync_metadata",
        }
        assert columns == expected

    def test_only_one_primary_chunk_per_session_enforced(self, conn):
        conn.execute(
            "INSERT INTO sessions (id, started_at, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("s1", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        insert_sql = (
            "INSERT INTO session_chunks "
            "(id, session_id, chunk_type, content, embedding, token_count, created_at, updated_at) "
            "VALUES (?, 's1', 'primary', 'text', X'00', 1, '2026-01-01', '2026-01-01')"
        )
        conn.execute(insert_sql, ("c1",))
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert_sql, ("c2",))
            conn.commit()

    def test_multiple_sub_chunks_per_session_allowed(self, conn):
        conn.execute(
            "INSERT INTO sessions (id, started_at, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("s1", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        for chunk_id in ("sub1", "sub2"):
            conn.execute(
                "INSERT INTO session_chunks "
                "(id, session_id, chunk_type, content, embedding, token_count, created_at, updated_at) "
                "VALUES (?, 's1', 'sub_chunk', 'text', X'00', 1, '2026-01-01', '2026-01-01')",
                (chunk_id,),
            )
        conn.commit()  # must not raise


class TestFts5Queryable:
    def test_reminders_fts_insert_and_match(self, conn):
        conn.execute(
            "INSERT INTO reminders (id, title, notes, scheduled_time, created_at, updated_at) "
            "VALUES ('r1', 'send invoice to Priya', '', '2026-01-01', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        hits = conn.execute(
            "SELECT rowid FROM reminders_fts WHERE reminders_fts MATCH 'invoice'"
        ).fetchall()
        assert len(hits) == 1

    def test_reminders_fts_delete_trigger_removes_index_entry(self, conn):
        conn.execute(
            "INSERT INTO reminders (id, title, notes, scheduled_time, created_at, updated_at) "
            "VALUES ('r1', 'send invoice', '', '2026-01-01', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        conn.execute("DELETE FROM reminders WHERE id = 'r1'")
        conn.commit()
        hits = conn.execute(
            "SELECT rowid FROM reminders_fts WHERE reminders_fts MATCH 'invoice'"
        ).fetchall()
        assert hits == []

    def test_todos_fts_insert_and_match(self, conn):
        conn.execute(
            "INSERT INTO todos (id, title, created_at, updated_at) "
            "VALUES ('t1', 'buy milk', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        hits = conn.execute("SELECT rowid FROM todos_fts WHERE todos_fts MATCH 'milk'").fetchall()
        assert len(hits) == 1

    def test_meeting_notes_fts_matches_searchable_text(self, conn):
        conn.execute(
            "INSERT INTO meeting_notes (id, raw_transcript, searchable_text, created_at, updated_at) "
            "VALUES ('m1', 'raw', 'Sarah decided on Q4 budget', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        hits = conn.execute(
            "SELECT rowid FROM meeting_notes_fts WHERE meeting_notes_fts MATCH 'budget'"
        ).fetchall()
        assert len(hits) == 1

    def test_schedule_items_fts_matches_title(self, conn):
        conn.execute(
            "INSERT INTO schedules (id, date, created_at, updated_at) "
            "VALUES ('sc1', '2026-01-01', '2026-01-01', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO schedule_items (id, schedule_id, title, start_time, end_time, created_at, updated_at) "
            "VALUES ('si1', 'sc1', 'dentist appointment', '2026-01-01T09:00', '2026-01-01T10:00', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        hits = conn.execute(
            "SELECT rowid FROM schedule_items_fts WHERE schedule_items_fts MATCH 'dentist'"
        ).fetchall()
        assert len(hits) == 1

    def test_soft_deleted_row_still_matches_fts_but_app_query_pattern_excludes_it(self, conn):
        """FTS5 itself has no concept of deleted_at -- StructuredTableSearch
        (Phase 1) must combine an FTS match with a base-table
        deleted_at IS NULL filter. This test demonstrates that combined
        query pattern explicitly, since FTS5 alone won't do it."""
        conn.execute(
            "INSERT INTO todos (id, title, created_at, updated_at) "
            "VALUES ('t1', 'buy milk', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        conn.execute("UPDATE todos SET deleted_at = '2026-01-02' WHERE id = 't1'")
        conn.commit()

        fts_hits = conn.execute(
            "SELECT rowid FROM todos_fts WHERE todos_fts MATCH 'milk'"
        ).fetchall()
        assert len(fts_hits) == 1  # FTS5 doesn't know about deleted_at

        combined = conn.execute(
            "SELECT t.id FROM todos t "
            "JOIN todos_fts ON todos_fts.rowid = t.rowid "
            "WHERE todos_fts MATCH 'milk' AND t.deleted_at IS NULL"
        ).fetchall()
        assert combined == []  # the app-level pattern correctly excludes it
