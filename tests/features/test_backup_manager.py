"""Phase 2 Step 2.2 — BackupManager: create / list / prune / restore / run_due_backup."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from db.connection import open_session_db
from db.health import check_integrity
from db.migration_runner import MigrationRunner
from src.features.backup_manager import BackupConfig, BackupManager

pytestmark = pytest.mark.integration

PLAIN_HEADER = b"SQLite format 3\x00"
KEY = "2222333344445555666677778888999900001111aaaabbbbccccddddeeeeffff0"


@pytest.fixture(params=["plaintext", "encrypted"])
def db(request, tmp_path):
    """A migrated session DB, once plaintext and once SQLCipher-encrypted."""
    key = KEY if request.param == "encrypted" else None
    path = str(tmp_path / "session.db")
    MigrationRunner(db_path=path, snapshot_dir=tmp_path / "premigration", key=key).run()
    conn = open_session_db(path, key)
    conn.execute(
        "INSERT INTO reminders (id, title, scheduled_time, created_at, updated_at) "
        "VALUES ('r1', 'buy milk', '2026-01-01T00:00:00Z', 'x', 'x')"
    )
    conn.commit()
    conn.close()
    return {"path": path, "key": key}


@pytest.fixture
def manager(db, tmp_path):
    return BackupManager(
        db["path"],
        key=db["key"],
        backup_dir=tmp_path / "backups",
        config=BackupConfig(daily_time="02:00", retention=3),
    )


def _header(path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(16)


def test_create_backup_writes_a_file_with_metadata(manager, db):
    snap = manager.create_backup()
    assert snap.path.exists()
    assert snap.size_bytes > 0
    assert snap.created_at.startswith("20")
    # header matches the source: encrypted DB -> encrypted backup
    expect_encrypted = db["key"] is not None
    assert (_header(snap.path) != PLAIN_HEADER) == expect_encrypted


def test_backup_opens_with_the_key_and_carries_the_data(manager, db):
    snap = manager.create_backup()
    conn = open_session_db(str(snap.path), db["key"])
    assert conn.execute("SELECT title FROM reminders WHERE id='r1'").fetchone()[0] == "buy milk"
    assert check_integrity(conn).ok
    conn.close()


def test_encrypted_backup_is_not_openable_as_plaintext(tmp_path):
    path = str(tmp_path / "session.db")
    MigrationRunner(db_path=path, snapshot_dir=tmp_path / "pm", key=KEY).run()
    mgr = BackupManager(path, key=KEY, backup_dir=tmp_path / "b", config=BackupConfig())
    snap = mgr.create_backup()
    with pytest.raises(sqlite3.DatabaseError):
        plain = sqlite3.connect(str(snap.path))
        plain.execute("SELECT count(*) FROM sqlite_master").fetchone()


def test_list_backups_is_newest_first(manager):
    base = datetime(2026, 3, 1, 2, 0, tzinfo=UTC)
    snaps = [manager.create_backup(when=base + timedelta(days=i)) for i in range(3)]
    listed = manager.list_backups()
    assert [s.path.name for s in listed] == [s.path.name for s in reversed(snaps)]


def test_prune_keeps_exactly_retention(manager):
    base = datetime(2026, 3, 1, 2, 0, tzinfo=UTC)
    for i in range(6):
        manager.create_backup(when=base + timedelta(days=i))
    assert len(manager.list_backups()) == 3  # retention=3, pruned on every create
    # the survivors are the 3 newest
    assert [s.created_at[:10] for s in manager.list_backups()] == [
        "2026-03-06",
        "2026-03-05",
        "2026-03-04",
    ]
    assert manager.prune() == []  # already at the limit


def test_restore_round_trips_and_clears_wal_shm(manager, db):
    snap = manager.create_backup()

    live = open_session_db(db["path"], db["key"])
    live.execute("UPDATE reminders SET title='CHANGED' WHERE id='r1'")
    live.commit()
    live.close()  # a clean close removes -wal/-shm

    result = manager.restore(snap.path)
    assert result.ok and result.needs_restart

    reopened = open_session_db(db["path"], db["key"])
    assert reopened.execute("SELECT title FROM reminders WHERE id='r1'").fetchone()[0] == "buy milk"
    reopened.close()


def test_restore_of_a_corrupt_snapshot_leaves_the_live_db_untouched(manager, db, tmp_path):
    snap = manager.create_backup()
    raw = bytearray(snap.path.read_bytes())
    raw[100:200] = b"\x00" * 100  # trash the header
    snap.path.write_bytes(raw)

    result = manager.restore(snap.path)
    assert not result.ok and not result.needs_restart

    conn = open_session_db(db["path"], db["key"])
    assert conn.execute("SELECT title FROM reminders WHERE id='r1'").fetchone()[0] == "buy milk"
    conn.close()


def test_restore_missing_snapshot(manager, tmp_path):
    result = manager.restore(tmp_path / "nope.db")
    assert not result.ok
    assert "not found" in result.detail


class TestRunDueBackup:
    def test_skips_before_daily_time(self, manager):
        assert manager.run_due_backup("2026-03-01T01:59:00+00:00") is None
        assert manager.list_backups() == []

    def test_takes_one_backup_after_daily_time(self, manager):
        snap = manager.run_due_backup("2026-03-01T02:05:00+00:00")
        assert snap is not None
        assert len(manager.list_backups()) == 1

    def test_idempotent_within_the_same_day(self, manager):
        manager.run_due_backup("2026-03-01T02:05:00+00:00")
        assert manager.run_due_backup("2026-03-01T18:00:00+00:00") is None
        assert len(manager.list_backups()) == 1

    def test_takes_a_new_backup_the_next_day(self, manager):
        manager.run_due_backup("2026-03-01T02:05:00+00:00")
        snap = manager.run_due_backup("2026-03-02T02:05:00+00:00")
        assert snap is not None
        assert len(manager.list_backups()) == 2
