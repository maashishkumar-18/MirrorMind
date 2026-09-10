"""Seed / mutate a MirrorMind session DB for the Phase 4 e2e harness.

Not part of the app. The e2e bridge (``desktop/e2e/support/bridge-server.mjs``)
shells out to this for the handful of states IPC cannot express — chiefly a
reminder whose ``scheduled_time`` is already in the past with ``fired_at IS
NULL`` (the missed-fire reconciliation flow), and "mock the clock" mutations on
an existing row.

Usage::

    python -m tools.e2e_seed <db_path> <spec.json>

The DB must already exist (the backend runs migrations on first start; the
harness starts it once before seeding). ``RAGPIPE_DB_KEY`` (64 hex) selects the
SQLCipher key, exactly as the backend resolves it.

Spec shape::

    {
      "reminders": [
        {"id": "r-past", "title": "Water the plants",
         "scheduled_time": "2020-01-01T09:00:00+00:00",
         "notes": "", "fired_at": null, "session_id": null}
      ],
      "sql": ["UPDATE reminders SET scheduled_time='2020-01-01T00:00:00+00:00' WHERE id='r1'"]
    }
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from db.connection import open_session_db  # noqa: E402


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _insert_reminder(conn, spec: dict) -> None:
    now = _now()
    conn.execute(
        "INSERT INTO reminders (id, session_id, title, notes, scheduled_time, "
        "fired_at, completed_at, dismissed_at, toast_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            spec["id"],
            spec.get("session_id"),
            spec["title"],
            spec.get("notes", ""),
            spec["scheduled_time"],
            spec.get("fired_at"),
            spec.get("completed_at"),
            spec.get("dismissed_at"),
            spec.get("toast_id"),
            spec.get("created_at", now),
            spec.get("updated_at", now),
        ),
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    db_path, spec_path = argv
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    key = os.getenv("RAGPIPE_DB_KEY") or None

    conn = open_session_db(db_path, key)
    try:
        with conn:
            for r in spec.get("reminders", []):
                _insert_reminder(conn, r)
            for stmt in spec.get("sql", []):
                conn.execute(stmt)
    finally:
        conn.close()
    print(
        f"seeded {db_path}: {len(spec.get('reminders', []))} reminders, "
        f"{len(spec.get('sql', []))} statements"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
