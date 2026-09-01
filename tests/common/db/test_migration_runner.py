"""
Tests for db/migration_runner.py (Production Roadmap Phase 0 Step 0.4).
"""

import sqlite3

import pytest

from db.migration_runner import MigrationIntegrityError, MigrationRunner, _split_sql_statements

pytestmark = pytest.mark.characterization


@pytest.fixture
def runner(tmp_path):
    db_path = str(tmp_path / "session.db")
    snapshot_dir = tmp_path / "snapshots"
    return MigrationRunner(db_path=db_path, snapshot_dir=snapshot_dir)


class TestRun:
    def test_first_run_applies_0001_and_returns_it(self, runner):
        applied = runner.run()
        assert applied == ["0001"]

    def test_first_run_takes_exactly_one_snapshot(self, runner):
        runner.run()
        snapshots = list(runner.snapshot_dir.glob("*.bak"))
        assert len(snapshots) == 1

    def test_second_run_is_a_true_no_op(self, runner):
        runner.run()
        applied_again = runner.run()
        assert applied_again == []

    def test_second_run_does_not_take_another_snapshot(self, runner):
        runner.run()
        snapshot_count_after_first = len(list(runner.snapshot_dir.glob("*.bak")))
        runner.run()
        snapshot_count_after_second = len(list(runner.snapshot_dir.glob("*.bak")))
        assert snapshot_count_after_second == snapshot_count_after_first == 1

    def test_bookkeeping_row_is_recorded(self, runner):
        runner.run()
        conn = sqlite3.connect(runner.db_path)
        rows = conn.execute("SELECT version, filename, checksum FROM schema_migrations").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "0001"
        assert rows[0][1] == "0001_initial_schema.sql"
        assert len(rows[0][2]) == 64  # sha256 hex digest length


class TestSnapshot:
    def test_snapshot_output_passes_integrity_check(self, runner):
        runner.run()
        snapshot_path = next(runner.snapshot_dir.glob("*.bak"))
        conn = sqlite3.connect(str(snapshot_path))
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        assert result == "ok"

    def test_first_ever_snapshot_is_of_the_pre_migration_empty_database(self, runner):
        """ "Pre-migration" means what it says: for the very first migration
        on a brand-new database, there is nothing to snapshot yet -- the
        schema doesn't exist until AFTER this snapshot is taken. The
        snapshot mechanism's real value shows up on a later migration
        against an existing, populated database (see
        test_snapshot_before_a_later_migration_captures_prior_state)."""
        runner.run()
        snapshot_path = next(runner.snapshot_dir.glob("*.bak"))
        conn = sqlite3.connect(str(snapshot_path))
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        conn.close()
        assert tables == set()

    def test_snapshot_before_a_later_migration_captures_prior_state(self, isolated_runner):
        """Apply 0001, insert data, then introduce a second migration file
        and run again -- the new pre-migration snapshot must capture the
        database as it stood right before 0002 runs, including 0001's
        schema and the inserted data."""
        isolated_runner.run()

        conn = sqlite3.connect(isolated_runner.db_path)
        conn.execute(
            "INSERT INTO sessions (id, started_at, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("s1", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        conn.commit()
        conn.close()

        (isolated_runner.migrations_dir / "0002_noop.sql").write_text(
            "CREATE TABLE placeholder_0002 (id INTEGER PRIMARY KEY);\n", encoding="utf-8"
        )
        isolated_runner.run()

        snapshots = sorted(isolated_runner.snapshot_dir.glob("*.bak"))
        assert len(snapshots) == 2  # one for 0001, one for 0002
        second_snapshot = snapshots[-1]

        conn = sqlite3.connect(str(second_snapshot))
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        rows = conn.execute("SELECT id FROM sessions").fetchall()
        conn.close()

        assert "sessions" in tables
        assert "placeholder_0002" not in tables  # 0002 hadn't run yet when this snapshot was taken
        assert rows == [("s1",)]
        assert "schema_migrations" in tables


@pytest.fixture
def isolated_runner(tmp_path):
    """A MigrationRunner pointed at a private copy of the real migrations
    directory, so integrity-check tests can safely mutate a migration file
    on disk without touching the actual repo file."""
    import shutil

    real_migrations_dir = MigrationRunner(db_path="unused").migrations_dir
    private_migrations_dir = tmp_path / "migrations"
    shutil.copytree(real_migrations_dir, private_migrations_dir)

    db_path = str(tmp_path / "session.db")
    return MigrationRunner(
        db_path=db_path,
        migrations_dir=private_migrations_dir,
        snapshot_dir=tmp_path / "snapshots",
    )


class TestIntegrityCheckIsolated:
    def test_mutated_migration_file_after_apply_raises_on_next_discover(self, isolated_runner):
        isolated_runner.run()

        migration_file = isolated_runner.migrations_dir / "0001_initial_schema.sql"
        original = migration_file.read_text(encoding="utf-8")
        migration_file.write_text(original + "\n-- tampered\n", encoding="utf-8")

        with pytest.raises(MigrationIntegrityError, match="has been modified"):
            isolated_runner.discover_migrations()

    def test_mutated_migration_file_after_apply_raises_on_next_run(self, isolated_runner):
        isolated_runner.run()

        migration_file = isolated_runner.migrations_dir / "0001_initial_schema.sql"
        original = migration_file.read_text(encoding="utf-8")
        migration_file.write_text(original + "\n-- tampered\n", encoding="utf-8")

        with pytest.raises(MigrationIntegrityError):
            isolated_runner.run()

    def test_unmutated_file_never_raises(self, isolated_runner):
        isolated_runner.run()
        isolated_runner.discover_migrations()  # must not raise


class TestStatus:
    def test_status_before_run_shows_unapplied(self, runner):
        status = runner.status()
        assert len(status) == 1
        assert status[0]["version"] == "0001"
        assert status[0]["applied"] is False

    def test_status_after_run_shows_applied(self, runner):
        runner.run()
        status = runner.status()
        assert status[0]["applied"] is True


class TestSplitSqlStatements:
    def test_plain_statements_split_on_semicolon(self):
        sql = "CREATE TABLE a (id INTEGER);\nCREATE TABLE b (id INTEGER);"
        statements = _split_sql_statements(sql)
        assert len(statements) == 2

    def test_trigger_body_kept_as_a_single_statement(self):
        sql = (
            "CREATE TABLE t (id INTEGER);\n"
            "CREATE TRIGGER trg AFTER INSERT ON t BEGIN\n"
            "    INSERT INTO t (id) VALUES (1);\n"
            "    INSERT INTO t (id) VALUES (2);\n"
            "END;\n"
            "CREATE TABLE u (id INTEGER);"
        )
        statements = _split_sql_statements(sql)
        assert len(statements) == 3
        assert "CREATE TRIGGER" in statements[1]
        assert statements[1].count("INSERT INTO t") == 2

    def test_comment_only_lines_are_dropped(self):
        sql = "-- a comment\nCREATE TABLE a (id INTEGER);\n-- another\n"
        statements = _split_sql_statements(sql)
        assert len(statements) == 1

    def test_real_migration_file_splits_into_36_statements(self, isolated_runner):
        migration_file = isolated_runner.migrations_dir / "0001_initial_schema.sql"
        sql = migration_file.read_text(encoding="utf-8")
        statements = _split_sql_statements(sql)
        assert len(statements) == 36
