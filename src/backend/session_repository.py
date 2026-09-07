"""Persistence for the ``sessions`` and ``messages`` tables (Phase 3 Step 3.1b).

The first writer to these two tables — until now only the 0001 schema and
``eval/run_eval.py``'s test seeder touched them. Subclasses ``TableHandler``
(same construction contract, the same keyed ``open_session_db`` connection, the
same missing-table guard) so it sits consistently alongside the Step 1.5
feature handlers. Owned and only ever called by the single-threaded
``SessionWorker``.
"""

from __future__ import annotations

from src.common.types import SessionMessage
from src.features.base import TableHandler, new_id, now_iso

_VALID_CLOSE_REASONS = ("idle_timeout", "explicit", "app_shutdown")


class SessionRepository(TableHandler):
    _REQUIRED_TABLES = ("sessions", "messages")

    def create_session(self) -> str:
        """Open a new conversation window. Returns its id."""
        session_id = new_id("session")
        now = now_iso()
        with self._conn:
            self._conn.execute(
                "INSERT INTO sessions (id, started_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, now, now, now),
            )
        return session_id

    def finalize_session(self, session_id: str, reason: str) -> None:
        """Close a session: stamp ``ended_at`` + ``close_reason``. Idempotent —
        a second call just refreshes the timestamps."""
        if reason not in _VALID_CLOSE_REASONS:
            raise ValueError(f"close_reason must be one of {_VALID_CLOSE_REASONS}, got {reason!r}")
        now = now_iso()
        with self._conn:
            self._conn.execute(
                "UPDATE sessions SET ended_at = ?, close_reason = ?, updated_at = ? "
                "WHERE id = ? AND deleted_at IS NULL",
                (now, reason, now, session_id),
            )

    def session_started_at(self, session_id: str) -> str | None:
        row = self._get_row("sessions", session_id)
        return row["started_at"] if row is not None else None

    def append_message(self, session_id: str, role: str, content: str) -> int:
        """Append one turn. ``turn_index`` is assigned as ``MAX(turn_index)+1``
        for the session (0 for the first). Returns the assigned index."""
        if role not in ("user", "assistant"):
            raise ValueError(f"role must be 'user' or 'assistant', got {role!r}")
        now = now_iso()
        with self._conn:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(turn_index) + 1, 0) AS next FROM messages "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            turn_index = int(row["next"])
            self._conn.execute(
                "INSERT INTO messages (id, session_id, turn_index, role, content, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_id("msg"), session_id, turn_index, role, content, now, now),
            )
        return turn_index

    def recent_turns(self, session_id: str, limit: int) -> list[SessionMessage]:
        """The last ``limit`` non-deleted messages, oldest-first — the agent's
        ``conversation_history`` input."""
        rows = self._conn.execute(
            "SELECT role, content FROM messages "
            "WHERE session_id = ? AND deleted_at IS NULL "
            "ORDER BY turn_index DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [SessionMessage(role=r["role"], content=r["content"]) for r in reversed(rows)]

    def all_messages(self, session_id: str) -> list[SessionMessage]:
        """Every non-deleted message of the session, oldest-first — the
        re-ingestion input."""
        rows = self._conn.execute(
            "SELECT role, content FROM messages "
            "WHERE session_id = ? AND deleted_at IS NULL ORDER BY turn_index ASC",
            (session_id,),
        ).fetchall()
        return [SessionMessage(role=r["role"], content=r["content"]) for r in rows]

    def history(self, session_id: str) -> list[dict[str, object]]:
        """Full turn records for the ``chat.history`` IPC method."""
        rows = self._conn.execute(
            "SELECT turn_index, role, content, created_at FROM messages "
            "WHERE session_id = ? AND deleted_at IS NULL ORDER BY turn_index ASC",
            (session_id,),
        ).fetchall()
        return [
            {
                "turn_index": r["turn_index"],
                "role": r["role"],
                "content": r["content"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
