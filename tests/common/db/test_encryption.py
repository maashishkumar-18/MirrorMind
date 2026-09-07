"""Phase 2 Step 2.1 — SQLCipher encryption behind db.connection / db.migration_runner."""

import sqlite3

import pytest

from db.connection import connect_for_migrations, open_session_db, operational_errors
from db.health import check_integrity
from db.migration_runner import MigrationRunner
from src.security.errors import DatabaseKeyError

pytestmark = pytest.mark.integration

PLAIN_HEADER = b"SQLite format 3\x00"
KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
OTHER_KEY = "ffeeddccbbaa99887766554433221100ffeeddccbbaa998877665544332211ff"


def _read_header(path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(16)


def test_keyed_connection_round_trips_and_file_is_encrypted(tmp_path):
    db = tmp_path / "session.db"
    conn = open_session_db(str(db), KEY)
    conn.execute("CREATE TABLE note (body TEXT)")
    conn.execute("INSERT INTO note VALUES ('hello')")
    conn.commit()
    conn.close()

    assert _read_header(db) != PLAIN_HEADER

    reopened = open_session_db(str(db), KEY)
    assert reopened.execute("SELECT body FROM note").fetchone()[0] == "hello"
    reopened.close()


def test_plain_sqlite3_cannot_open_an_encrypted_file(tmp_path):
    db = tmp_path / "session.db"
    open_session_db(str(db), KEY).close()

    with pytest.raises(sqlite3.DatabaseError):
        plain = sqlite3.connect(str(db))
        plain.execute("SELECT count(*) FROM sqlite_master").fetchone()


def test_wrong_key_raises_database_key_error(tmp_path):
    db = tmp_path / "session.db"
    conn = open_session_db(str(db), KEY)
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()

    with pytest.raises(DatabaseKeyError):
        open_session_db(str(db), OTHER_KEY)


def test_no_key_still_opens_plaintext(tmp_path):
    db = tmp_path / "session.db"
    conn = open_session_db(str(db))
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    assert _read_header(db) == PLAIN_HEADER


@pytest.mark.parametrize(
    "bad_key",
    ["", "tooshort", "g" * 64, KEY + "0", 'ab"; DROP TABLE t; --' + "0" * 40],
)
def test_malformed_key_raises_database_key_error_not_a_leak(tmp_path, bad_key):
    # Phase 2 audit finding: an unvalidated key was string-formatted into the
    # PRAGMA — a stray quote broke the statement (raising an unwrapped Warning)
    # and leaked the connection. Now every non-64-hex key is a clean
    # DatabaseKeyError before any connection is opened.
    db = tmp_path / "session.db"
    with pytest.raises(DatabaseKeyError):
        open_session_db(str(db), bad_key)
    assert not db.exists()  # nothing was opened/created


class TestMigrationsUnderCipher:
    @pytest.fixture
    def runner(self, tmp_path):
        """Keyed runner pointed at a private copy of db/migrations/ so the
        rollback test can drop a broken migration file without touching the
        real repo directory."""
        import shutil

        real_dir = MigrationRunner(db_path="unused").migrations_dir
        private_dir = tmp_path / "migrations"
        shutil.copytree(real_dir, private_dir)
        return MigrationRunner(
            db_path=str(tmp_path / "session.db"),
            migrations_dir=private_dir,
            snapshot_dir=tmp_path / "snapshots",
            key=KEY,
        )

    def test_0001_applies_on_a_fresh_encrypted_db(self, runner):
        assert runner.run() == ["0001"]
        conn = open_session_db(runner.db_path, KEY)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"sessions", "messages", "reminders", "todos", "summaries"} <= tables
        conn.close()

    def test_second_run_is_a_no_op(self, runner):
        runner.run()
        assert runner.run() == []

    def test_fts5_virtual_tables_work_under_cipher(self, runner):
        runner.run()
        conn = open_session_db(runner.db_path, KEY)
        conn.execute(
            "INSERT INTO reminders (id, title, scheduled_time, created_at, updated_at) "
            "VALUES ('r1', 'buy oat milk', '2026-01-01T00:00:00Z', 'x', 'x')"
        )
        conn.commit()
        hit = conn.execute(
            "SELECT rowid FROM reminders_fts WHERE reminders_fts MATCH 'milk'"
        ).fetchall()
        conn.close()
        assert [tuple(r) for r in hit] == [(1,)]

    def test_integrity_check_passes_on_a_fresh_encrypted_db(self, runner):
        runner.run()
        conn = open_session_db(runner.db_path, KEY)
        result = check_integrity(conn)
        conn.close()
        assert result.ok
        assert result.details == ["ok"]

    def test_pre_migration_snapshot_is_itself_encrypted(self, runner):
        runner.run()
        snapshots = list(runner.snapshot_dir.glob("*.bak"))
        assert len(snapshots) == 1
        assert _read_header(snapshots[0]) != PLAIN_HEADER
        # ...and it opens with the key
        conn = connect_for_migrations(str(snapshots[0]), KEY)
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        conn.close()

    def test_interior_ddl_failure_rolls_back_cleanly_under_cipher(self, runner):
        runner.run()  # 0001 applied

        (runner.migrations_dir / "0002_broken.sql").write_text(
            "CREATE TABLE should_not_survive (id INTEGER PRIMARY KEY);\n"
            "CREATE TABLE BROKEN SYNTAX (((;\n",
            encoding="utf-8",
        )
        with pytest.raises(operational_errors()):
            runner.run()

        conn = open_session_db(runner.db_path, KEY)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        conn.close()
        assert "should_not_survive" not in tables
        assert "0002" not in applied
