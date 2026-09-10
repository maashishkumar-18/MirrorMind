"""Persistence for the ``sessions`` and ``messages`` tables (Phase 3 Step 3.1b).

The first writer to these two tables — until now only the 0001 schema and
``eval/run_eval.py``'s test seeder touched them. Subclasses ``TableHandler``
(same construction contract, the same keyed ``open_session_db`` connection, the
same missing-table guard) so it sits consistently alongside the Step 1.5
feature handlers. Owned and only ever called by the single-threaded
``SessionWorker``.
"""

from __future__ import annotations

import os
import time

from src.common.types import SessionMessage
from src.features.base import TableHandler, new_id, now_iso

_VALID_CLOSE_REASONS = ("idle_timeout", "explicit", "app_shutdown")


def _fuzz_stall() -> None:
    """Test seam (Phase 4 Step 4.4b): widen the window a subprocess-kill can land
    in *while a message INSERT is uncommitted*, so the fuzz harness can prove a
    mid-write SIGKILL leaves a consistent DB (WAL rollback). No-op in the app."""
    ms = os.getenv("RAGPIPE_FUZZ_STALL_BEFORE_COMMIT_MS")
    if ms:
        try:
            time.sleep(int(ms) / 1000.0)
        except ValueError:
            pass


#: chat.history returns at most this many of a session's newest turns (oldest-first
#: after the cap). Sessions are already bounded by idle-close + chat.new, so this is
#: a defensive ceiling on the transcript the frontend renders un-virtualized, not a
#: routine limit. ``all_messages`` (the re-ingest input) is deliberately uncapped.
_HISTORY_MAX = 200


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

    def finalize_dangling_sessions(self, reason: str) -> int:
        """Close every session left open (``ended_at IS NULL``) — used in the
        `SessionWorker` prologue to tidy up after a crash / kill with no
        `app.shutdown`. Returns how many rows were closed."""
        if reason not in _VALID_CLOSE_REASONS:
            raise ValueError(f"close_reason must be one of {_VALID_CLOSE_REASONS}, got {reason!r}")
        now = now_iso()
        with self._conn:
            cur = self._conn.execute(
                "UPDATE sessions SET ended_at = ?, close_reason = ?, updated_at = ? "
                "WHERE ended_at IS NULL AND deleted_at IS NULL",
                (now, reason, now),
            )
        return cur.rowcount

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
            _fuzz_stall()  # inside the `with` block — the INSERT is not yet committed
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
        """Turn records for the ``chat.history`` IPC method — the newest
        ``_HISTORY_MAX`` turns of the session, returned oldest-first."""
        rows = self._conn.execute(
            "SELECT turn_index, role, content, created_at FROM messages "
            "WHERE session_id = ? AND deleted_at IS NULL "
            "ORDER BY turn_index DESC LIMIT ?",
            (session_id, _HISTORY_MAX),
        ).fetchall()
        return [
            {
                "turn_index": r["turn_index"],
                "role": r["role"],
                "content": r["content"],
                "created_at": r["created_at"],
            }
            for r in reversed(rows)
        ]
