"""Data export, the proactive-export badge, and full wipe (Phase 2 Step 2.2).

``DataManager`` owns the three user-facing data-management actions the
Settings → Data & Privacy screen will call (the screen itself, its confirm
dialogs, and the system file picker are Phase 3):

- **Export** — a plaintext JSON copy of the 8 substantive tables the user can
  keep off-machine. ``session_chunks`` is excluded: the embeddings are
  derivable from ``messages`` and raw float32 BLOBs are unreadable in JSON
  (project_logic.md §12). Re-import is a v2 feature.
- **Export badge state** — "Last exported: …" and whether the >30-day nudge
  badge should show.
- **Full wipe** — soft-delete every row in every substantive table, clear all
  scheduled toasts, and reset ``last_exported_at`` (but keep the active model:
  wiping data is not a reason to force the user back through model selection).

Collaborators (``ToastBridge``, ``AppConfig``) are injected at construction so
callers and tests wire real doubles without monkeypatching.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from db.connection import open_session_db, set_session_row_factory
from src.common.types import ExportBadgeState
from src.features.toast_bridge import ToastBridge
from src.models.app_config import AppConfig

logger = logging.getLogger(__name__)

#: The 8 substantive tables a user-facing export contains (roadmap Step 2.2).
EXPORT_TABLES: tuple[str, ...] = (
    "sessions",
    "messages",
    "reminders",
    "todos",
    "meeting_notes",
    "schedules",
    "schedule_items",
    "summaries",
)

#: Every substantive table full wipe soft-deletes — the export set plus the
#: two the user never sees directly.
WIPE_TABLES: tuple[str, ...] = (*EXPORT_TABLES, "session_chunks", "sync_metadata")

#: Columns dropped from every exported row: soft-delete bookkeeping (rows are
#: filtered on it already) and the inert v2-sync column.
_PRIVATE_COLUMNS = frozenset({"deleted_at", "sync_metadata"})

_EXPORT_BADGE_DAYS = 30

# The three Settings → Data & Privacy strings, verbatim from project_logic.md §12 /
# production_roadmap.md Step 2.2. Co-located here so the Phase 3 frontend imports
# them rather than re-transcribing spec text (and risking drift).
NEVER_EXPORTED_LINE = (
    "Never — your data cannot be recovered if Windows is reinstalled without an export."
)
UNINSTALL_WARNING = (
    "Uninstalling this app will permanently delete your encrypted database. "
    "Export your data first."
)
EXPORT_BLURB = (
    "Export your data — saves a backup copy of everything for safekeeping. "
    "Note: re-importing into the app is not yet supported; this export preserves "
    "your data for a future update."
)


def _row_public(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys() if k not in _PRIVATE_COLUMNS}


def export_badge_state(app_config: AppConfig, now: str | None = None) -> ExportBadgeState:
    """The "Last exported: …" line + the >30-day nudge flag, from ``AppConfig``
    alone (no DB connection needed). Shared by ``DataManager.export_badge_state``
    and the ``data.info`` IPC handler (Phase 3 Step 3.4)."""
    now = now or _now_iso()
    last = app_config.last_exported_at
    if not last:
        return ExportBadgeState(
            needs_export=True,
            last_exported_at=None,
            days_since=None,
            settings_line=NEVER_EXPORTED_LINE,
        )
    days_since = (_parse(now) - _parse(last)).days
    return ExportBadgeState(
        needs_export=days_since > _EXPORT_BADGE_DAYS,
        last_exported_at=last,
        days_since=days_since,
        settings_line=f"Last exported: {last[:10]}",
    )


class DataManager:
    def __init__(
        self,
        *,
        connection: sqlite3.Connection | None = None,
        db_path: str | None = None,
        key: str | None = None,
        bridge: ToastBridge,
        app_config: AppConfig,
    ):
        if (db_path is None) == (connection is None):
            raise ValueError("Pass exactly one of db_path or connection")
        self._conn = connection or open_session_db(db_path, key)  # type: ignore[arg-type]
        set_session_row_factory(self._conn)
        self._bridge = bridge
        self._app_config = app_config

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _schema_version(self) -> str:
        row = self._conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return (row[0] if row else None) or ""

    def _select_live(self, table: str) -> sqlite3.Cursor:
        # table is always a literal from EXPORT_TABLES / WIPE_TABLES.
        return self._conn.execute(f"SELECT * FROM {table} WHERE deleted_at IS NULL")

    def export_data(self, *, now: str | None = None) -> dict:
        """The full export as an in-memory dict. Fine at personal-use scale;
        ``write_export`` is the streaming path for very large ``messages``."""
        return {
            "schema_version": self._schema_version(),
            "exported_at": now or _now_iso(),
            "tables": {
                table: [_row_public(r) for r in self._select_live(table)] for table in EXPORT_TABLES
            },
        }

    def write_export(self, path: str | Path, *, now: str | None = None) -> Path:
        """Write the export JSON to ``path`` atomically (tmp + ``os.replace``),
        streaming row-by-row so a multi-year ``messages`` table never lands in
        memory as one list. Stamps ``last_exported_at`` on success. Parses back
        equal to ``export_data()`` (same content; formatting is not identical)."""
        now = now or _now_iso()
        path = Path(path)
        tmp = path.with_name(path.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write("{\n")
                fh.write(f'  "schema_version": {json.dumps(self._schema_version())},\n')
                fh.write(f'  "exported_at": {json.dumps(now)},\n')
                fh.write('  "tables": {\n')
                for t_idx, table in enumerate(EXPORT_TABLES):
                    fh.write(f"    {json.dumps(table)}: [")
                    wrote_row = False
                    for row in self._select_live(table):
                        fh.write(",\n" if wrote_row else "\n")
                        fh.write("      " + json.dumps(_row_public(row), default=str))
                        wrote_row = True
                    fh.write("\n    ]" if wrote_row else "]")
                    fh.write(",\n" if t_idx < len(EXPORT_TABLES) - 1 else "\n")
                fh.write("  }\n}\n")
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)  # no orphaned *.tmp on a mid-stream error
            raise
        self._app_config.set_last_exported_at(now)
        return path

    # ------------------------------------------------------------------
    # Export badge
    # ------------------------------------------------------------------

    def export_badge_state(self, now: str | None = None) -> ExportBadgeState:
        return export_badge_state(self._app_config, now)

    # ------------------------------------------------------------------
    # Full wipe
    # ------------------------------------------------------------------

    def full_wipe(self) -> None:
        """Soft-delete every row in every substantive table, clear all toasts,
        reset ``last_exported_at``. Keeps ``active_model``. Does not uninstall."""
        # 1. Toasts first — a stale toast is a UX nuisance, never a reason to
        #    abort the wipe or leave data half-deleted.
        try:
            self._bridge.cancel_all()
        except Exception:  # noqa: BLE001
            logger.warning("full_wipe: bridge.cancel_all failed; continuing", exc_info=True)

        # 2. One transaction for all tables.
        now = _now_iso()
        with self._conn:
            for table in WIPE_TABLES:
                # table is always an internal literal from WIPE_TABLES.
                self._conn.execute(
                    f"UPDATE {table} SET deleted_at = ?, updated_at = ? WHERE deleted_at IS NULL",
                    (now, now),
                )

        # 3. Only after the commit — so a failed wipe leaves the config honest.
        self._app_config.set_last_exported_at(None)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
