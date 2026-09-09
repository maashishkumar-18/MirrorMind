"""
ScheduleHandler (Phase 1 Step 1.5a).

CRUD over ``schedule_items``, with a get-or-create of the parent ``schedules``
row (one per date). Every create/update is conflict-validated *before* commit:
if the new time slot overlaps an existing non-deleted item, the handler
returns a ``ScheduleConflict`` and writes nothing — it never silently
overwrites (project_logic.md §5). Overlap is global, not per-schedule: a
person cannot be in two places at once.

``create_schedule_item`` accepts ``overwrite_ids`` (Phase 3 Step 3.3, Q2): the
caller resolves a conflict by naming the items to overwrite. Each id must be in
the *current* conflict set; the handler soft-deletes those (soft-delete only,
same transaction as the insert) and then creates. An empty / omitted
``overwrite_ids`` keeps the historical behaviour — the ``ScheduleConflict`` is
returned and nothing is written. ``update_schedule_item`` has no overwrite path.
"""

import sqlite3

from src.common.types import ScheduleConflict, ScheduleItem
from src.features.base import TableHandler, new_id, now_iso

_EDITABLE_FIELDS = ("title", "start_time", "end_time", "location", "notes")


class ScheduleHandler(TableHandler):
    _REQUIRED_TABLES = ("schedules", "schedule_items")

    def create_schedule_item(
        self,
        title: str,
        start_time: str,
        end_time: str,
        *,
        location: str = "",
        notes: str = "",
        date: str | None = None,
        overwrite_ids: list[str] | None = None,
    ) -> ScheduleItem | ScheduleConflict:
        item_id = new_id("sci")
        attempted = ScheduleItem(
            id=item_id,
            schedule_id="",
            title=title,
            start_time=start_time,
            end_time=end_time,
            location=location,
            notes=notes,
        )
        # Check-then-act: the overlap query runs before the write block below.
        # Safe because the backend is single-connection and handlers are
        # single-threaded (the scheduler thread has its own connection and never
        # writes schedule_items) — there is no second writer to race. Revisit if
        # that changes (audit 1.5-C1).
        conflicts = self._overlapping(start_time, end_time)
        overwrite = set(overwrite_ids or [])
        if conflicts:
            stray = overwrite - {c.id for c in conflicts}
            if stray:
                raise ValueError(f"overwrite_ids not in the current conflict set: {sorted(stray)}")
            unresolved = [c for c in conflicts if c.id not in overwrite]
            if unresolved:
                return ScheduleConflict(attempted=attempted, conflicts_with=unresolved)

        now = now_iso()
        with self._conn:
            for cid in overwrite:
                # soft-delete only (project_logic.md §5) — same txn as the insert
                self._conn.execute(
                    "UPDATE schedule_items SET deleted_at = ?, updated_at = ? "
                    "WHERE id = ? AND deleted_at IS NULL",
                    (now, now, cid),
                )
            schedule_id = self._get_or_create_schedule(date or start_time[:10], now)
            self._conn.execute(
                "INSERT INTO schedule_items (id, schedule_id, title, start_time, end_time, "
                "location, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (item_id, schedule_id, title, start_time, end_time, location, notes, now, now),
            )
        return self._row_to_item(self._require_row("schedule_items", item_id))

    def update_schedule_item(
        self, item_id: str, **fields: object
    ) -> ScheduleItem | ScheduleConflict:
        unknown = set(fields) - set(_EDITABLE_FIELDS)
        if unknown:
            raise ValueError(f"cannot update fields: {sorted(unknown)}")

        row = self._get_row("schedule_items", item_id)
        if row is None:
            raise KeyError(f"schedule item {item_id} not found")
        if not fields:
            return self._row_to_item(row)

        new_start = str(fields.get("start_time", row["start_time"]))
        new_end = str(fields.get("end_time", row["end_time"]))
        conflicts = self._overlapping(new_start, new_end, exclude_id=item_id)
        if conflicts:
            attempted = self._row_to_item(row)
            attempted.start_time, attempted.end_time = new_start, new_end
            return ScheduleConflict(attempted=attempted, conflicts_with=conflicts)

        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._conn:
            self._conn.execute(
                f"UPDATE schedule_items SET {assignments}, updated_at = ? WHERE id = ?",
                (*fields.values(), now_iso(), item_id),
            )
        return self._row_to_item(self._require_row("schedule_items", item_id))

    def get_schedule_item(self, item_id: str) -> ScheduleItem | None:
        row = self._get_row("schedule_items", item_id)
        return self._row_to_item(row) if row else None

    def get_day_schedule(self, date: str) -> list[ScheduleItem]:
        rows = self._conn.execute(
            "SELECT si.* FROM schedule_items si "
            "JOIN schedules s ON s.id = si.schedule_id "
            "WHERE s.date = ? AND si.deleted_at IS NULL AND s.deleted_at IS NULL "
            "ORDER BY si.start_time",
            (date,),
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def get_range_schedule(self, start_date: str, end_date: str) -> list[ScheduleItem]:
        """Every non-deleted item on a date in ``[start_date, end_date]``
        (inclusive), ordered by date then start time — one query backing the
        Schedule view's week toggle (avoids 7× ``get_day_schedule``)."""
        rows = self._conn.execute(
            "SELECT si.* FROM schedule_items si "
            "JOIN schedules s ON s.id = si.schedule_id "
            "WHERE s.date BETWEEN ? AND ? "
            "AND si.deleted_at IS NULL AND s.deleted_at IS NULL "
            "ORDER BY s.date, si.start_time",
            (start_date, end_date),
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def delete_schedule_item(self, item_id: str) -> bool:
        return self._soft_delete("schedule_items", item_id)

    # ------------------------------------------------------------------

    def _get_or_create_schedule(self, date: str, now: str) -> str:
        row = self._conn.execute(
            "SELECT id FROM schedules WHERE date = ? AND deleted_at IS NULL", (date,)
        ).fetchone()
        if row is not None:
            return row["id"]
        schedule_id = new_id("sch")
        self._conn.execute(
            "INSERT INTO schedules (id, date, title, created_at, updated_at) "
            "VALUES (?, ?, NULL, ?, ?)",
            (schedule_id, date, now, now),
        )
        return schedule_id

    def _overlapping(
        self, start_time: str, end_time: str, *, exclude_id: str | None = None
    ) -> list[ScheduleItem]:
        sql = (
            "SELECT * FROM schedule_items "
            "WHERE deleted_at IS NULL AND start_time < ? AND end_time > ?"
        )
        params: list[object] = [end_time, start_time]
        if exclude_id is not None:
            sql += " AND id != ?"
            params.append(exclude_id)
        sql += " ORDER BY start_time"
        return [self._row_to_item(r) for r in self._conn.execute(sql, params).fetchall()]

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> ScheduleItem:
        return ScheduleItem(
            id=row["id"],
            schedule_id=row["schedule_id"],
            title=row["title"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            location=row["location"] or "",
            notes=row["notes"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
