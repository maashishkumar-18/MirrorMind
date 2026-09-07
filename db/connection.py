"""Session database connection helpers.

The session database is a single SQLite file, SQLCipher-encrypted at rest
(Production Roadmap Phase 2 Step 2.1). ``open_session_db`` is the one entry
point every store / handler goes through:

- ``key=None``  -> a plain stdlib ``sqlite3`` connection. This is still the
  default for tests and for any caller that has not been wired to the key
  store yet; a plaintext file is opened exactly as before Phase 2.
- ``key=<64-hex>`` -> a ``sqlcipher3`` connection with ``PRAGMA key`` applied
  as the very first statement, then verified with a cheap read. A wrong key
  or an unreadable cipher file raises
  :class:`src.security.errors.DatabaseKeyError`, not a raw driver exception.

The key itself is owned by :mod:`src.security.keyring_store` (generated with
``os.urandom(32)``, stored in the Windows Credential Manager). This module
only consumes it.

``connect_for_migrations`` is the lower-level variant :mod:`db.migration_runner`
uses — same cipher handling, but no WAL / foreign-key pragmas and a caller-
chosen ``isolation_level`` (the runner drives its own explicit
BEGIN/COMMIT/ROLLBACK; see that module).
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from src.security.errors import DatabaseKeyError

# Raw-key form: the 64 hex chars ARE the AES key -- no PBKDF2, no salt.
# Valid because the key is 32 bytes of os.urandom, not a user passphrase.
_KEY_PRAGMA = "PRAGMA key = \"x'{hex_key}'\""
_HEX64 = re.compile(r"\A[0-9a-fA-F]{64}\Z")


def set_session_row_factory(conn: sqlite3.Connection) -> None:
    """Apply the name-and-index addressable row factory the stores/handlers
    expect. ``sqlite3.Row`` is bound to ``sqlite3.Cursor`` and raises a
    ``TypeError`` on a ``sqlcipher3`` cursor, so an encrypted connection
    needs ``sqlcipher3``'s own (API-identical) ``Row``. Call this instead of
    assigning ``conn.row_factory = sqlite3.Row`` directly.
    """
    if type(conn).__module__.startswith("sqlcipher3"):
        from sqlcipher3 import dbapi2 as sqlcipher

        conn.row_factory = sqlcipher.Row
    else:
        conn.row_factory = sqlite3.Row


def _sqlcipher_connect(path: str, key: str, *, isolation_level: Any = "") -> sqlite3.Connection:
    """Open ``path`` with SQLCipher, apply and verify ``key``.

    Import is local so a ``key=None`` caller never needs ``sqlcipher3``
    installed (keeps the plain-sqlite3 path dependency-free for now).
    """
    # The key is interpolated into the PRAGMA text (PRAGMA args can't be
    # bound), so it must be exactly 64 hex chars -- anything else is a
    # composition-root bug, and a stray quote would break the statement and
    # leak this connection. resolve_key() only ever yields this shape.
    if not isinstance(key, str) or not _HEX64.match(key):
        raise DatabaseKeyError("database key must be 64 hexadecimal characters (32 bytes)")

    from sqlcipher3 import dbapi2 as sqlcipher

    conn = sqlcipher.connect(path, timeout=30, isolation_level=isolation_level)
    try:
        # PRAGMA key MUST be the first statement on the connection. The
        # spike confirmed SQLCipher does not fail here on a wrong key --
        # the following read is what rejects it.
        conn.execute(_KEY_PRAGMA.format(hex_key=key))
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except BaseException as exc:
        conn.close()
        if isinstance(exc, sqlcipher.DatabaseError):
            raise DatabaseKeyError(f"could not open encrypted database at {path!r}: {exc}") from exc
        raise
    return conn


def open_session_db(path: str, key: str | None = None) -> sqlite3.Connection:
    """
    Open a connection to the session database at ``path``.

    WAL mode and a generous busy_timeout are set on every connection,
    mirroring observability/metrics_store.py's MetricsStore pattern.
    Foreign key enforcement is turned on (SQLite has it off by default
    per-connection).

    Pass ``key`` (a 64-character hex string from
    :func:`src.security.keyring_store.resolve_key`) to open a SQLCipher-
    encrypted file. Omit it for a plain, unencrypted ``sqlite3`` connection.
    """
    if key is None:
        conn: sqlite3.Connection = sqlite3.connect(path, timeout=30)
    else:
        conn = _sqlcipher_connect(path, key)

    set_session_row_factory(conn)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _driver_errors(attr: str) -> tuple[type[Exception], ...]:
    errs: list[type[Exception]] = [getattr(sqlite3, attr)]
    try:
        from sqlcipher3 import dbapi2 as sqlcipher

        errs.append(getattr(sqlcipher, attr))
    except ImportError:  # pragma: no cover - sqlcipher3 is a hard dep in practice
        pass
    return tuple(errs)


def operational_errors() -> tuple[type[Exception], ...]:
    """The ``OperationalError`` classes to catch regardless of whether a
    connection is plain ``sqlite3`` or ``sqlcipher3`` — the two driver
    modules define distinct, unrelated exception hierarchies, so
    ``except sqlite3.OperationalError`` alone misses the encrypted path.
    """
    return _driver_errors("OperationalError")


def database_errors() -> tuple[type[Exception], ...]:
    """Both drivers' ``DatabaseError`` (superclass of OperationalError /
    IntegrityError / DataError). Used to catch a corrupt-file read —
    "database disk image is malformed" / "file is not a database".
    """
    return _driver_errors("DatabaseError")


def connect_for_migrations(
    path: str, key: str | None = None, *, isolation_level: Any = None
) -> sqlite3.Connection:
    """A bare connection for :mod:`db.migration_runner`.

    Same cipher handling as :func:`open_session_db` but no WAL / foreign-key
    pragmas — the migration runner manages transaction semantics itself and
    ``isolation_level=None`` (the default here) is what makes DDL genuinely
    transactional under an explicit BEGIN/COMMIT (see that module's docstring;
    the spike confirmed the behaviour is identical under ``sqlcipher3``).
    """
    if key is None:
        return sqlite3.connect(path, isolation_level=isolation_level)
    return _sqlcipher_connect(path, key, isolation_level=isolation_level)
