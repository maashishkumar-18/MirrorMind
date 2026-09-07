"""Automatic local backup, retention, and restore (Phase 2 Step 2.2).

The session database is SQLCipher-encrypted and unrecoverable if its key is
lost (project_logic.md §12). ``BackupManager`` is the mitigation: a rolling
set of daily snapshots under ``<RAGPIPE_DATA_DIR>/backups/``, each one itself
an encrypted single-file copy made with SQLite's online backup API (safe to
run while the database is in use — spike-verified keyed->keyed in Step 2.1).

This is the *daily* path. ``db.migration_runner.MigrationRunner.snapshot()``
is the separate *pre-migration* path; both use the same mechanism.

Backend only. The Settings -> Backup & Recovery panel, the restore confirm
dialog, and the actual backend restart after a restore are Phase 3 —
``RestoreResult.needs_restart`` is the seam.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from db.connection import connect_for_migrations, open_session_db
from db.health import check_integrity
from src.common.types import BackupSnapshot, RestoreResult

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).parent.parent.parent
_FILENAME_STAMP = "%Y%m%dT%H%M%S%fZ"  # matches MigrationRunner.snapshot()
_FILENAME_PREFIX = "session-"
_FILENAME_GLOB = f"{_FILENAME_PREFIX}*.db"


@dataclass
class BackupConfig:
    """Loaded from config/features/backup.yaml (mirrors SummaryConfig /
    SchedulerConfig's from_yaml + env-override pattern)."""

    daily_time: str = "02:00"
    retention: int = 7

    @classmethod
    def from_yaml(cls, path: str | None = None) -> BackupConfig:
        config_path = (
            path
            or os.getenv("RAGPIPE_BACKUP_CONFIG")
            or str(_REPO_ROOT / "config" / "features" / "backup.yaml")
        )
        data: dict = {}
        if Path(config_path).exists():
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

        section = data.get("backup", {})
        defaults = cls()
        return cls(
            daily_time=str(
                os.getenv(
                    "RAGPIPE_BACKUP_DAILY_TIME", section.get("daily_time", defaults.daily_time)
                )
            ),
            retention=int(
                os.getenv("RAGPIPE_BACKUP_RETENTION", section.get("retention", defaults.retention))
            ),
        )


def default_backup_dir(data_dir: str | None = None) -> Path:
    """``<RAGPIPE_DATA_DIR or ./data>/backups/`` — mirrors
    ``src/models/app_config.py::app_config_path``. Covered by the root-anchored
    ``/data/`` .gitignore rule."""
    base = data_dir or os.getenv("RAGPIPE_DATA_DIR") or str(_REPO_ROOT / "data")
    return Path(base) / "backups"


def _stamp_to_iso(stamp: str) -> str:
    return datetime.strptime(stamp, _FILENAME_STAMP).replace(tzinfo=UTC).isoformat()


class BackupManager:
    def __init__(
        self,
        db_path: str,
        *,
        key: str | None = None,
        backup_dir: str | Path | None = None,
        config: BackupConfig | None = None,
    ):
        self._db_path = db_path
        self._key = key
        self._dir = Path(backup_dir) if backup_dir is not None else default_backup_dir()
        self._config = config or BackupConfig.from_yaml()

    # ------------------------------------------------------------------

    def create_backup(self, *, when: datetime | None = None) -> BackupSnapshot:
        """Take one snapshot, prune, and return its metadata. ``when`` sets the
        filename timestamp (defaults to now) so ``run_due_backup`` can keep the
        snapshot's recorded date consistent with the ``now`` it was told."""
        self._dir.mkdir(parents=True, exist_ok=True)
        stamp = (when or datetime.now(UTC)).strftime(_FILENAME_STAMP)
        dest_path = self._dir / f"{_FILENAME_PREFIX}{stamp}.db"
        # Write to *.db.partial first, then os.replace to the final name — a
        # crash mid-copy leaves an orphaned .partial that list_backups()
        # (glob "session-*.db") ignores, never a truncated file a user could
        # pick from the restore list (Phase 2 Step 2.3 crash-safety audit).
        partial_path = dest_path.with_name(dest_path.name + ".partial")

        # Source is the live WAL database -> open_session_db (WAL pragma,
        # busy_timeout, driver-aware row factory), never a bare connect.
        source = open_session_db(self._db_path, self._key)
        dest = connect_for_migrations(str(partial_path), self._key)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
        os.replace(partial_path, dest_path)

        self.prune()
        return BackupSnapshot(
            path=dest_path,
            created_at=_stamp_to_iso(stamp),
            size_bytes=dest_path.stat().st_size,
        )

    def list_backups(self) -> list[BackupSnapshot]:
        """Existing snapshots, newest first. Sorted by filename — the ISO
        microsecond stamp sorts chronologically and is immune to filesystem
        mtime drift / cross-machine copies."""
        if not self._dir.exists():
            return []
        snapshots: list[BackupSnapshot] = []
        for path in sorted(self._dir.glob(_FILENAME_GLOB), reverse=True):
            stamp = path.stem[len(_FILENAME_PREFIX) :]
            try:
                created_at = _stamp_to_iso(stamp)
            except ValueError:
                continue  # not one of ours
            snapshots.append(
                BackupSnapshot(path=path, created_at=created_at, size_bytes=path.stat().st_size)
            )
        return snapshots

    def prune(self) -> list[Path]:
        """Delete snapshots past ``config.retention``. Returns what was removed."""
        removed: list[Path] = []
        for snap in self.list_backups()[self._config.retention :]:
            snap.path.unlink(missing_ok=True)
            removed.append(snap.path)
        return removed

    def restore(self, snapshot_path: str | Path) -> RestoreResult:
        """Validate ``snapshot_path`` and swap it in for the live database.

        The caller MUST ensure nothing else holds a connection to the live
        database (the Phase 3 process supervisor stops the backend first);
        this only replaces the file and signals ``needs_restart``.
        """
        snapshot_path = Path(snapshot_path)
        if not snapshot_path.exists():
            return RestoreResult(False, False, f"snapshot not found: {snapshot_path}")

        try:
            conn = open_session_db(str(snapshot_path), self._key)
        except Exception as exc:  # noqa: BLE001 — DatabaseKeyError / driver errors alike
            return RestoreResult(False, False, f"snapshot is unreadable: {exc}")
        try:
            integrity = check_integrity(conn)
        finally:
            conn.close()
        if not integrity.ok:
            return RestoreResult(
                False, False, f"snapshot failed integrity_check: {integrity.details}"
            )

        # The live DB and its sidecars must go together: unlink the stale
        # -wal/-shm *before* the swap so a crash in the tiny window after
        # os.replace can't pair the new file with the old WAL.
        incoming = Path(f"{self._db_path}.incoming")
        try:
            shutil.copy2(snapshot_path, incoming)
            Path(f"{self._db_path}-wal").unlink(missing_ok=True)
            Path(f"{self._db_path}-shm").unlink(missing_ok=True)
            os.replace(incoming, self._db_path)
        except OSError as exc:
            incoming.unlink(missing_ok=True)
            # A live connection to the DB (backend not fully stopped) trips
            # this on Windows -- surface it, don't raise from the recovery path.
            return RestoreResult(False, False, f"could not swap in the snapshot: {exc}")
        return RestoreResult(True, True, f"restored from {snapshot_path.name}")

    def run_due_backup(self, now: str) -> BackupSnapshot | None:
        """Scheduler entry point. Idempotent like
        ``SummaryHandler.generate_due_summaries``: at most one backup per
        **UTC** calendar day, and only once ``now`` is past ``daily_time``
        (``daily_time`` is UTC -- see config/features/backup.yaml). ``now`` may
        carry any offset or none; it is normalised to UTC here so the
        once-per-day guard and the filename stamp always agree."""
        when = datetime.fromisoformat(now.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        when = when.astimezone(UTC)
        today = when.date().isoformat()

        if when.strftime("%Y-%m-%dT%H:%M:%S") < f"{today}T{self._config.daily_time}:00":
            return None
        if any(snap.created_at[:10] == today for snap in self.list_backups()):
            return None
        return self.create_backup(when=when)
