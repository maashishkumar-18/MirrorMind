"""Phase 2 Step 2.1 — db/health.py integrity primitives."""

import pytest

from db.connection import open_session_db
from db.health import check_integrity, check_quick
from db.migration_runner import MigrationRunner
from src.security.errors import DatabaseKeyError

pytestmark = pytest.mark.integration

KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0"


@pytest.fixture
def plaintext_db(tmp_path):
    path = str(tmp_path / "session.db")
    MigrationRunner(db_path=path, snapshot_dir=tmp_path / "snap").run()
    return path


@pytest.fixture
def encrypted_db(tmp_path):
    path = str(tmp_path / "session.db")
    MigrationRunner(db_path=path, snapshot_dir=tmp_path / "snap", key=KEY).run()
    return path


def _fill_reminders(conn, n):
    conn.executemany(
        "INSERT INTO reminders (id, title, scheduled_time, created_at, updated_at) "
        "VALUES (?, ?, '2026-01-01T00:00:00Z', 'x', 'x')",
        [(f"r{i}", f"reminder number {i} with some body text") for i in range(n)],
    )
    conn.commit()


def test_check_integrity_ok_on_fresh_plaintext_db(plaintext_db):
    conn = open_session_db(plaintext_db)
    result = check_integrity(conn)
    conn.close()
    assert result.ok and result.details == ["ok"]


def test_check_quick_ok_on_fresh_plaintext_db(plaintext_db):
    conn = open_session_db(plaintext_db)
    result = check_quick(conn)
    conn.close()
    assert result.ok


def test_check_integrity_ok_on_fresh_encrypted_db(encrypted_db):
    conn = open_session_db(encrypted_db, KEY)
    result = check_integrity(conn)
    conn.close()
    assert result.ok and result.details == ["ok"]


def test_check_integrity_detects_a_corrupted_plaintext_page(plaintext_db):
    conn = open_session_db(plaintext_db)
    _fill_reminders(conn, 400)
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    conn.close()

    # Zero an interior page, leaving page 1 (header + schema root) intact so
    # the file still opens.
    with open(plaintext_db, "r+b") as fh:
        fh.seek(page_size * 3)
        fh.write(b"\x00" * page_size)

    conn = open_session_db(plaintext_db)
    result = check_integrity(conn)
    conn.close()
    assert not result.ok
    assert result.details != ["ok"]
    assert result.details  # carries at least one problem description


def test_corrupted_encrypted_interior_page_is_detected(encrypted_db):
    conn = open_session_db(encrypted_db, KEY)
    _fill_reminders(conn, 400)
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    conn.close()

    raw = bytearray(open(encrypted_db, "rb").read())
    raw[page_size * 3 + 100] ^= 0xFF
    with open(encrypted_db, "wb") as fh:
        fh.write(raw)

    # The damage surfaces somewhere in the open -> integrity-check path: either
    # open_session_db's probe rejects it (DatabaseKeyError), or the connection
    # opens and check_integrity reports ok=False when it walks the bad page.
    try:
        conn = open_session_db(encrypted_db, KEY)
    except DatabaseKeyError:
        return
    result = check_integrity(conn)
    conn.close()
    assert not result.ok
