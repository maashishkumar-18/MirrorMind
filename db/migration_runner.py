"""
Session database migration runner (Production Roadmap Phase 0 Step 0.4).

Forward-only, idempotent, snapshot-before-first-pending-migration. As of
this file, db/migrations/ contains only 0001_initial_schema.sql — per the
already-landed production_roadmap.md v1.2 correction, the MetricsStore
cost_usd -> compute_ms change (Phase 1 Step 1.4) is a separate idempotent
in-process ALTER TABLE against observability/metrics.db, not a numbered
migration here.
"""

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DEFAULT_SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

_FILENAME_VERSION_RE = re.compile(r"^(\d+)_")
_BEGIN_RE = re.compile(r"\bBEGIN\b", re.IGNORECASE)
_TRIGGER_END_RE = re.compile(r"^\s*END\s*;\s*$", re.IGNORECASE)


def _split_sql_statements(sql: str) -> list[str]:
    """
    Split a migration file into individual top-level statements.

    Deliberately NOT a general SQL parser -- sqlite3.Connection.executescript()
    was tried first and confirmed (empirically, not just by reading docs) to
    NOT provide atomicity: a later statement's failure does not roll back
    earlier statements in the same script, even with explicit BEGIN/COMMIT
    written into the script text. Migrations are applied statement-by-
    statement instead, under an explicit BEGIN/COMMIT/ROLLBACK managed by
    MigrationRunner.run() (see that method's docstring for why individual
    conn.execute() calls inside plain `with conn:` are ALSO not sufficient
    on their own for DDL-only scripts like this one).

    This splitter only needs to handle what this repo's own migration files
    actually contain: statements terminated by `;`, plus CREATE TRIGGER ...
    BEGIN ... END; blocks that must stay whole (their body contains its own
    internal `;`-terminated sub-statements). It is not a general-purpose SQL
    tokenizer -- a `;` inside a string literal value, for example, would
    split incorrectly. Every migration file in db/migrations/ must be
    written with this in mind (as 0001_initial_schema.sql is).
    """
    statements = []
    current: list[str] = []
    in_trigger_body = False

    for line in sql.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue

        current.append(line)

        if in_trigger_body:
            if _TRIGGER_END_RE.match(line):
                in_trigger_body = False
                statements.append("\n".join(current))
                current = []
            continue

        if _BEGIN_RE.search(line):
            # Entered a CREATE TRIGGER ... BEGIN body -- don't split until
            # its matching END; is seen.
            in_trigger_body = True
            continue

        if line.rstrip().endswith(";"):
            statements.append("\n".join(current))
            current = []

    remainder = "\n".join(current).strip()
    if remainder:
        statements.append(remainder)

    return statements


@dataclass
class Migration:
    version: str  # "0001"
    filename: str  # "0001_initial_schema.sql"
    sql: str
    checksum: str  # sha256(sql.encode()).hexdigest() -- over the whole file, unsplit
    statements: list[str]  # pre-split top-level statements, see _split_sql_statements


class MigrationIntegrityError(Exception):
    """
    Raised when an already-applied migration's on-disk content no longer
    matches the checksum recorded in schema_migrations at the time it was
    applied.

    Protects the forward-only guarantee: never edit an applied migration
    file after the fact — add a new one instead.
    """


class MigrationRunner:
    def __init__(
        self,
        db_path: str,
        migrations_dir: Path | None = None,
        snapshot_dir: Path | None = None,
    ):
        self.db_path = db_path
        self.migrations_dir = migrations_dir or DEFAULT_MIGRATIONS_DIR
        self.snapshot_dir = snapshot_dir or DEFAULT_SNAPSHOT_DIR

    def discover_migrations(self) -> list[Migration]:
        """
        All migrations found on disk, sorted by version ascending.

        Raises MigrationIntegrityError if an already-applied migration's
        on-disk checksum no longer matches what's recorded in
        schema_migrations.
        """
        migrations = []
        for path in sorted(self.migrations_dir.glob("*.sql")):
            match = _FILENAME_VERSION_RE.match(path.name)
            if not match:
                continue  # not a versioned migration file -- ignore
            version = match.group(1)
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            migrations.append(
                Migration(
                    version=version,
                    filename=path.name,
                    sql=sql,
                    checksum=checksum,
                    statements=_split_sql_statements(sql),
                )
            )
        migrations.sort(key=lambda m: m.version)

        applied = self._applied_versions_no_lock()
        for migration in migrations:
            recorded_checksum = applied.get(migration.version)
            if recorded_checksum is not None and recorded_checksum != migration.checksum:
                raise MigrationIntegrityError(
                    f"Migration {migration.version} ({migration.filename}) has been modified "
                    f"since it was applied: recorded checksum {recorded_checksum}, "
                    f"on-disk checksum {migration.checksum}. Never edit an applied migration "
                    f"file -- add a new one instead."
                )

        return migrations

    def applied_versions(self, conn: sqlite3.Connection) -> dict[str, str]:
        """version -> checksum, for every migration already recorded as applied."""
        try:
            rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
        except sqlite3.OperationalError:
            # schema_migrations doesn't exist yet -- no migrations have ever
            # been applied to this database (it's created by 0001 itself).
            return {}
        return {row[0]: row[1] for row in rows}

    def _applied_versions_no_lock(self) -> dict[str, str]:
        conn = sqlite3.connect(self.db_path)
        try:
            return self.applied_versions(conn)
        finally:
            conn.close()

    def snapshot(self) -> Path:
        """
        Copy the current database to a timestamped .bak file via SQLite's
        online backup API. Safe to call against a database that doesn't
        exist yet on disk (produces an empty snapshot) -- callers only
        invoke this once at least one migration is about to run.

        Microsecond precision in the filename, not just seconds: two
        `run()` calls in quick succession (confirmed by this module's own
        test suite -- two migrations applied back-to-back in a test) can
        land in the same second, and a second snapshot with the exact same
        second-granularity filename would silently overwrite the first
        one's on-disk .bak file.
        """
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        db_stem = Path(self.db_path).stem
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        dest_path = self.snapshot_dir / f"{db_stem}-pre-migration-{timestamp}.bak"

        source = sqlite3.connect(self.db_path)
        dest = sqlite3.connect(str(dest_path))
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()

        return dest_path

    def run(self) -> list[str]:
        """
        Apply all pending migrations in order. Returns the list of newly-
        applied versions -- [] if nothing was pending (a true no-op: no
        snapshot taken, no connection writes).

        Each migration applies statement-by-statement (see
        _split_sql_statements) inside an EXPLICIT `BEGIN`/`COMMIT`
        transaction (connection opened with `isolation_level=None`, i.e.
        Python's sqlite3 module manages no implicit transaction of its
        own), followed by its schema_migrations bookkeeping insert in the
        SAME transaction, with an explicit `ROLLBACK` on any failure --
        success and bookkeeping commit atomically together, so a failing
        statement mid-migration leaves schema_migrations correctly
        reflecting "not applied" and no partial DDL committed, and a
        re-run retries cleanly.

        This explicit BEGIN/COMMIT/ROLLBACK is required, not optional
        styling. Two more convenient-looking alternatives were tried and
        both empirically confirmed broken for this use case, not just
        assumed broken from documentation:

        1. `sqlite3.Connection.executescript()` does not provide
           atomicity at all -- an earlier statement in the same script
           commits even when a later statement in the SAME call fails,
           even with explicit BEGIN/COMMIT written into the script text.

        2. Individual `conn.execute()` calls inside a `with conn:` block
           (the connection's DEFAULT, "legacy" isolation-level behavior)
           looked correct in initial testing, but that testing only
           exercised DML (INSERT/UPDATE/DELETE) after the DDL. Python's
           sqlite3 module in its default legacy mode only opens an
           implicit transaction before a DML statement -- DDL statements
           (CREATE TABLE/INDEX/TRIGGER, which is everything a schema
           migration actually contains) are NOT covered by that implicit
           transaction and autocommit individually regardless of
           `with conn:`. Confirmed by reproduction: two CREATE TABLE
           statements inside `with conn:`, the second one failing (e.g.
           a duplicate name or a syntax error) -- the FIRST table
           survives on disk after the exception. `with conn:` alone is
           therefore not sufficient for a pure-DDL migration file; only
           an explicit BEGIN issued before the first statement (with
           isolation_level=None so the driver doesn't also try to manage
           transactions on top of that) makes DDL genuinely transactional
           at the SQLite engine level, which does support transactional
           DDL when explicitly told to. See
           tests/common/db/test_migration_runner.py::TestAtomicity for
           the regression test proving this specific failure mode is
           fixed.
        """
        migrations = self.discover_migrations()
        conn = sqlite3.connect(self.db_path)
        try:
            applied = self.applied_versions(conn)
            pending = [m for m in migrations if m.version not in applied]
            if not pending:
                return []

            conn.close()  # release before snapshot() opens its own connections
            self.snapshot()
            conn = sqlite3.connect(self.db_path, isolation_level=None)

            newly_applied = []
            for migration in pending:
                conn.execute("BEGIN")
                try:
                    for statement in migration.statements:
                        conn.execute(statement)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, filename, checksum, applied_at) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            migration.version,
                            migration.filename,
                            migration.checksum,
                            datetime.now(UTC).isoformat(),
                        ),
                    )
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
                newly_applied.append(migration.version)

            return newly_applied
        finally:
            conn.close()

    def status(self) -> list[dict[str, Any]]:
        """Diagnostics: every discovered migration, with its applied state."""
        migrations = self.discover_migrations()
        conn = sqlite3.connect(self.db_path)
        try:
            applied = self.applied_versions(conn)
        finally:
            conn.close()

        return [
            {
                "version": m.version,
                "filename": m.filename,
                "checksum": m.checksum,
                "applied": m.version in applied,
            }
            for m in migrations
        ]
