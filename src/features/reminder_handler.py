"""
ReminderHandler (Phase 1 Step 1.5b).

CRUD over the ``reminders`` table, coupled to a ``ToastBridge``: creating a
reminder writes the row and then registers a scheduled Windows toast; updating
or deleting one cancels the old toast and (for updates) registers a new one.

Two non-CRUD entry points the scheduler / app-launch path use:

- ``fire_due(now)`` — the scheduler's poll body: mark every due-and-unfired
  reminder ``fired_at`` and trigger its toast. Idempotent.
- ``reconcile_on_launch(now)`` — two independent queries surfacing the two
  distinct failure modes (see ``ReconciliationResult``).

The bridge call in ``create_reminder`` / ``update_reminder`` is **not** part of
the DB transaction (it can't be — it's an external call). A crash between the
row write and the ``toast_id`` write leaves a non-deleted, non-fired row with
``toast_id = NULL``; that is benign — reconciliation keys on ``scheduled_time``
/ ``fired_at``, never ``toast_id``, and a later re-register pass can heal it.
"""

import sqlite3

from src.common.types import ReconciliationResult, Reminder
from src.features.base import TableHandler, new_id, now_iso
from src.features.toast_bridge import NoOpToastBridge, ToastBridge

_EDITABLE_FIELDS = ("title", "notes", "scheduled_time")


class ReminderHandler(TableHandler):
    _REQUIRED_TABLES = ("reminders",)

    def __init__(
        self,
        db_path: str | None = None,
        *,
        connection: sqlite3.Connection | None = None,
        key: str | None = None,
        bridge: ToastBridge | None = None,
    ):
        super().__init__(db_path, connection=connection, key=key)
        self.bridge: ToastBridge = bridge or NoOpToastBridge()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_reminder(
        self,
        title: str,
        scheduled_time: str,
        *,
        notes: str = "",
        session_id: str | None = None,
    ) -> Reminder:
        reminder_id = new_id("rem")
        now = now_iso()
        self._conn.execute(
            "INSERT INTO reminders (id, session_id, title, notes, scheduled_time, "
            "toast_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
            (reminder_id, session_id, title, notes, scheduled_time, now, now),
        )
        self._conn.commit()

        toast_id = self.bridge.register_toast(reminder_id, scheduled_time, title)
        self._conn.execute(
            "UPDATE reminders SET toast_id = ?, updated_at = ? WHERE id = ?",
            (toast_id, now_iso(), reminder_id),
        )
        self._conn.commit()
        return self._row_to_reminder(self._require_row("reminders", reminder_id))

    def update_reminder(self, reminder_id: str, **fields: object) -> Reminder:
        unknown = set(fields) - set(_EDITABLE_FIELDS)
        if unknown:
            raise ValueError(f"cannot update fields: {sorted(unknown)}")

        row = self._get_row("reminders", reminder_id)
        if row is None:
            raise KeyError(f"reminder {reminder_id} not found")
        if not fields:
            return self._row_to_reminder(row)

        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._conn:
            self._conn.execute(
                f"UPDATE reminders SET {assignments}, updated_at = ? WHERE id = ?",
                (*fields.values(), now_iso(), reminder_id),
            )

        # A time or title change means the pending toast is stale — re-register.
        if "scheduled_time" in fields or "title" in fields:
            self._reregister_toast(reminder_id, row["toast_id"])
        return self._row_to_reminder(self._require_row("reminders", reminder_id))

    def delete_reminder(self, reminder_id: str) -> bool:
        row = self._get_row("reminders", reminder_id)
        deleted = self._soft_delete("reminders", reminder_id)
        if deleted and row is not None and row["toast_id"]:
            self.bridge.cancel_toast(row["toast_id"])
        return deleted

    def complete_reminder(self, reminder_id: str) -> Reminder:
        return self._stamp(reminder_id, "completed_at")

    def dismiss_reminder(self, reminder_id: str) -> Reminder:
        return self._stamp(reminder_id, "dismissed_at")

    def reschedule_reminder(self, reminder_id: str, new_time: str) -> Reminder:
        row = self._get_row("reminders", reminder_id)
        if row is None:
            raise KeyError(f"reminder {reminder_id} not found")
        now = now_iso()
        self._conn.execute(
            "UPDATE reminders SET scheduled_time = ?, fired_at = NULL, updated_at = ? WHERE id = ?",
            (new_time, now, reminder_id),
        )
        self._conn.commit()
        self._reregister_toast(reminder_id, row["toast_id"])
        return self._row_to_reminder(self._require_row("reminders", reminder_id))

    def get_reminder(self, reminder_id: str) -> Reminder | None:
        row = self._get_row("reminders", reminder_id)
        return self._row_to_reminder(row) if row else None

    def get_reminders(self, *, active_only: bool = False) -> list[Reminder]:
        sql = "SELECT * FROM reminders WHERE deleted_at IS NULL"
        if active_only:
            sql += " AND completed_at IS NULL AND dismissed_at IS NULL"
        sql += " ORDER BY scheduled_time"
        return [self._row_to_reminder(r) for r in self._conn.execute(sql).fetchall()]

    # ------------------------------------------------------------------
    # Scheduler / launch-time entry points
    # ------------------------------------------------------------------

    def fire_due(self, now: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT id, title FROM reminders "
            "WHERE scheduled_time <= ? AND fired_at IS NULL AND deleted_at IS NULL "
            "ORDER BY scheduled_time",
            (now,),
        ).fetchall()
        fired: list[str] = []
        for row in rows:
            self._conn.execute(
                "UPDATE reminders SET fired_at = ?, updated_at = ? WHERE id = ?",
                (now, now_iso(), row["id"]),
            )
            self.bridge.fire_toast(row["id"], row["title"])
            fired.append(row["id"])
        if fired:
            self._conn.commit()
        return fired

    def reconcile_on_launch(self, now: str | None = None) -> ReconciliationResult:
        cutoff = now or now_iso()
        overdue = self._conn.execute(
            "SELECT * FROM reminders "
            "WHERE scheduled_time < ? AND fired_at IS NULL AND deleted_at IS NULL "
            "AND completed_at IS NULL AND dismissed_at IS NULL "
            "ORDER BY scheduled_time",
            (cutoff,),
        ).fetchall()
        pending = self._conn.execute(
            "SELECT * FROM reminders "
            "WHERE fired_at IS NOT NULL AND completed_at IS NULL "
            "AND dismissed_at IS NULL AND deleted_at IS NULL "
            "ORDER BY scheduled_time"
        ).fetchall()
        return ReconciliationResult(
            overdue=[self._row_to_reminder(r) for r in overdue],
            pending_acknowledgment=[self._row_to_reminder(r) for r in pending],
        )

    # ------------------------------------------------------------------

    def _stamp(self, reminder_id: str, column: str) -> Reminder:
        row = self._get_row("reminders", reminder_id)
        if row is None:
            raise KeyError(f"reminder {reminder_id} not found")
        now = now_iso()
        self._conn.execute(
            f"UPDATE reminders SET {column} = ?, updated_at = ? WHERE id = ?",
            (now, now, reminder_id),
        )
        self._conn.commit()
        return self._row_to_reminder(self._require_row("reminders", reminder_id))

    def _reregister_toast(self, reminder_id: str, old_toast_id: str | None) -> None:
        if old_toast_id:
            self.bridge.cancel_toast(old_toast_id)
        row = self._require_row("reminders", reminder_id)
        toast_id = self.bridge.register_toast(reminder_id, row["scheduled_time"], row["title"])
        self._conn.execute(
            "UPDATE reminders SET toast_id = ?, updated_at = ? WHERE id = ?",
            (toast_id, now_iso(), reminder_id),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_reminder(row: sqlite3.Row) -> Reminder:
        return Reminder(
            id=row["id"],
            session_id=row["session_id"],
            title=row["title"],
            notes=row["notes"] or "",
            scheduled_time=row["scheduled_time"],
            fired_at=row["fired_at"],
            completed_at=row["completed_at"],
            dismissed_at=row["dismissed_at"],
            toast_id=row["toast_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
