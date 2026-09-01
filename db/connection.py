"""
Session database connection helper (Production Roadmap Phase 0 Step 0.4).

SQLCipher deferral, stated explicitly so Phase 0/1 don't accidentally
create a Phase 2 dependency: SQLCipher integration is Phase 2 Step 2.1 —
it isn't installed or available yet. `open_session_db`'s `key` parameter
exists now purely so Phase 2 Step 2.1 can wire in `pysqlcipher3`/
`sqlcipher3` behind this same call signature without changing any caller.
Every Phase 0/1 caller passes no `key` and gets a plain, unencrypted
stdlib `sqlite3` connection — unaffected by the later swap. Do not import
`pysqlcipher3`/`sqlcipher3` here or anywhere else in Phase 0/1; this
module and its tests must run against plain `sqlite3` only.
"""

import sqlite3


def open_session_db(path: str, key: str | None = None) -> sqlite3.Connection:
    """
    Open a connection to the session database at `path`.

    WAL mode and a generous busy_timeout are set on every connection,
    mirroring observability/metrics_store.py's MetricsStore pattern —
    the closest working local-SQLite analog already in this repo.
    Foreign key enforcement is turned on (SQLite has it off by default
    per-connection).

    `key` is reserved for Phase 2 Step 2.1's SQLCipher integration and is
    currently unused — passing one is not an error, but it has no effect
    yet. See this module's docstring.
    """
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
