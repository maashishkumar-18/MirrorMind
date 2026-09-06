"""
TodoHandler (Phase 1 Step 1.5a).

CRUD over the ``todos`` table. NLP-extracted ``priority`` / ``category`` are
always user-editable post-creation (roadmap Step 1.5). Completed todos are
retained with ``completed_at`` set — there is no hard delete; ``delete_todo``
soft-deletes. The ``todos_ai`` / ``todos_au`` / ``todos_ad`` FTS5 triggers keep
``todos_fts`` in sync automatically, so this handler only ever writes the base
table.
"""

import sqlite3

from src.common.types import Todo
from src.features.base import TableHandler, new_id, now_iso

_VALID_PRIORITIES = (None, "low", "medium", "high")
_EDITABLE_FIELDS = ("title", "notes", "priority", "category")


class TodoHandler(TableHandler):
    _REQUIRED_TABLES = ("todos",)

    def create_todo(
        self,
        title: str,
        *,
        notes: str = "",
        priority: str | None = None,
        category: str | None = None,
        session_id: str | None = None,
    ) -> Todo:
        if priority not in _VALID_PRIORITIES:
            raise ValueError(f"priority must be one of {_VALID_PRIORITIES}, got {priority!r}")

        todo_id = new_id("todo")
        now = now_iso()
        self._conn.execute(
            "INSERT INTO todos (id, session_id, title, notes, priority, category, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (todo_id, session_id, title, notes, priority, category, now, now),
        )
        self._conn.commit()
        return self._row_to_todo(self._require_row("todos", todo_id))

    def get_todo(self, todo_id: str) -> Todo | None:
        row = self._get_row("todos", todo_id)
        return self._row_to_todo(row) if row else None

    def get_todos(self, *, active_only: bool = False, priority: str | None = None) -> list[Todo]:
        sql = "SELECT * FROM todos WHERE deleted_at IS NULL"
        params: list[object] = []
        if active_only:
            sql += " AND completed_at IS NULL"
        if priority is not None:
            sql += " AND priority = ?"
            params.append(priority)
        sql += " ORDER BY created_at"
        return [self._row_to_todo(r) for r in self._conn.execute(sql, params).fetchall()]

    def update_todo(self, todo_id: str, **fields: object) -> Todo:
        unknown = set(fields) - set(_EDITABLE_FIELDS)
        if unknown:
            raise ValueError(f"cannot update fields: {sorted(unknown)}")
        if "priority" in fields and fields["priority"] not in _VALID_PRIORITIES:
            raise ValueError(f"priority must be one of {_VALID_PRIORITIES}")

        row = self._get_row("todos", todo_id)
        if row is None:
            raise KeyError(f"todo {todo_id} not found")

        if fields:
            assignments = ", ".join(f"{name} = ?" for name in fields)
            with self._conn:
                self._conn.execute(
                    f"UPDATE todos SET {assignments}, updated_at = ? WHERE id = ?",
                    (*fields.values(), now_iso(), todo_id),
                )
        return self._row_to_todo(self._require_row("todos", todo_id))

    def complete_todo(self, todo_id: str) -> Todo:
        row = self._get_row("todos", todo_id)
        if row is None:
            raise KeyError(f"todo {todo_id} not found")
        now = now_iso()
        self._conn.execute(
            "UPDATE todos SET completed_at = ?, updated_at = ? WHERE id = ?",
            (now, now, todo_id),
        )
        self._conn.commit()
        return self._row_to_todo(self._require_row("todos", todo_id))

    def delete_todo(self, todo_id: str) -> bool:
        return self._soft_delete("todos", todo_id)

    @staticmethod
    def _row_to_todo(row: sqlite3.Row) -> Todo:
        return Todo(
            id=row["id"],
            session_id=row["session_id"],
            title=row["title"],
            notes=row["notes"] or "",
            priority=row["priority"],
            category=row["category"],
            completed_at=row["completed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
