"""On-launch database integrity checks (Production Roadmap Phase 2 Step 2.1).

Primitives only. Phase 3's backend entry point composes them: resolve the
key, run migrations, open the database, call :func:`check_integrity`, and on
a non-``ok`` result route to the restore-from-backup flow (the restore
mechanism itself is Phase 2 Step 2.2's ``BackupManager``) *before* touching
any other data.

``PRAGMA integrity_check`` (thorough, walks every page) vs. ``PRAGMA
quick_check`` (skips the page-by-page b-tree verification) — both live here so
callers have one canonical home. ``SQLiteVectorStore`` runs its own
construction-time ``quick_check`` as a store-local guard; that predates this
module and is left as-is.

Note on encrypted databases: a wrong key or a damaged *header* page never
reaches these functions — ``db.connection.open_session_db`` fails first with
:class:`src.security.errors.DatabaseKeyError`. A damaged *interior* page of a
correctly-keyed file opens fine but trips SQLCipher's per-page HMAC when
``integrity_check`` walks it; the driver raises rather than returning a
result row, so these helpers catch that and report ``ok=False`` with the
error text in ``details`` — a raised check is still a failed check.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from db.connection import database_errors


@dataclass(frozen=True)
class IntegrityResult:
    """Outcome of a ``PRAGMA *_check``.

    ``ok`` is ``True`` only when SQLite returned the single row ``"ok"``.
    ``details`` is the raw list of returned strings — one entry ``"ok"`` on
    success, or one-or-more human-readable problem descriptions on failure.
    """

    ok: bool
    details: list[str]


def _run_check(conn: sqlite3.Connection, pragma: str) -> IntegrityResult:
    try:
        rows = conn.execute(pragma).fetchall()
    except database_errors() as exc:
        # SQLCipher HMAC failure on a damaged interior page, or a plaintext
        # file too broken to run the pragma at all.
        return IntegrityResult(ok=False, details=[f"{type(exc).__name__}: {exc}"])
    details = [str(row[0]) for row in rows]
    return IntegrityResult(ok=details == ["ok"], details=details)


def check_integrity(conn: sqlite3.Connection) -> IntegrityResult:
    """Full ``PRAGMA integrity_check`` — the on-launch check."""
    return _run_check(conn, "PRAGMA integrity_check")


def check_quick(conn: sqlite3.Connection) -> IntegrityResult:
    """Faster ``PRAGMA quick_check`` — skips per-page b-tree verification."""
    return _run_check(conn, "PRAGMA quick_check")
