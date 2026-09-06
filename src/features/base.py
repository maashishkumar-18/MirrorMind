"""
Shared plumbing for the feature handlers (Phase 1 Step 1.5).

``TableHandler`` mirrors the store-constructor convention already used by
``src/common/sqlite_vector_store.py`` and ``src/retrieval/structured_search.py``:
pass exactly one of ``db_path`` / ``connection``, never run migrations, and
raise immediately if an expected table is missing (a composition-root wiring
error). ``key`` is the inert SQLCipher seam filled in by Phase 2 Step 2.1.

Writes follow the existing idiom: a single ``conn.execute(...)`` + ``conn.commit()``
for one statement, or ``with self._conn:`` for a DML-only multi-statement unit
(the DDL-atomicity caveat documented in ``db/migration_runner.py`` does not
apply to handler DML). Every table name passed to the helpers below is an
internal literal, never user input.
"""

import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from db.connection import open_session_db, set_session_row_factory


def now_iso() -> str:
    """Current UTC time as an ISO 8601 string — the timestamp format every
    ``created_at`` / ``updated_at`` column in the session DB uses."""
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    """A readable, collision-safe primary key, e.g. ``todo_9f2c1a4b7e30``."""
    return f"{prefix}_{uuid4().hex[:12]}"


class TableHandler:
    """Base for the structured-table feature handlers."""

    #: subclasses list the base tables they need present at construction
    _REQUIRED_TABLES: tuple[str, ...] = ()

    def __init__(
        self,
        db_path: str | None = None,
        *,
        connection: sqlite3.Connection | None = None,
        key: str | None = None,
    ):
        if (db_path is None) == (connection is None):
            raise ValueError("Pass exactly one of db_path or connection")

        self._conn = connection or open_session_db(db_path, key)  # type: ignore[arg-type]
        set_session_row_factory(self._conn)

        for table in self._REQUIRED_TABLES:
            self._require_table(table)

    def _require_table(self, name: str) -> None:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        if row is None:
            raise RuntimeError(
                f"{name} table is missing — apply db/migrations/0001_initial_schema.sql "
                "(via db.migration_runner.MigrationRunner) before constructing this handler."
            )

    # ------------------------------------------------------------------

    def _get_row(self, table: str, row_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            f"SELECT * FROM {table} WHERE id = ? AND deleted_at IS NULL", (row_id,)
        ).fetchone()

    def _require_row(self, table: str, row_id: str) -> sqlite3.Row:
        """Like ``_get_row`` but for a row that must exist — e.g. re-reading a
        row this handler just wrote in the same call."""
        row = self._get_row(table, row_id)
        if row is None:
            raise RuntimeError(f"{table} row {row_id!r} vanished mid-operation")
        return row

    def _soft_delete(self, table: str, row_id: str) -> bool:
        now = now_iso()
        cur = self._conn.execute(
            f"UPDATE {table} SET deleted_at = ?, updated_at = ? "
            f"WHERE id = ? AND deleted_at IS NULL",
            (now, now, row_id),
        )
        self._conn.commit()
        return cur.rowcount > 0
