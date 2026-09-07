"""SessionRepository — the first writer to sessions/messages (Phase 3 Step 3.1b)."""

from __future__ import annotations

import sqlite3

import pytest

from db.connection import open_session_db
from src.backend.session_repository import SessionRepository


@pytest.fixture
def repo(keyed_db):
    conn = open_session_db(keyed_db)
    try:
        yield SessionRepository(connection=conn)
    finally:
        conn.close()


def test_create_and_finalize_session(repo):
    sid = repo.create_session()
    assert sid.startswith("session_")
    row = repo._get_row("sessions", sid)
    assert row["ended_at"] is None and row["close_reason"] is None

    repo.finalize_session(sid, "explicit")
    row = repo._get_row("sessions", sid)
    assert row["ended_at"] is not None and row["close_reason"] == "explicit"


def test_finalize_rejects_bad_reason(repo):
    sid = repo.create_session()
    with pytest.raises(ValueError):
        repo.finalize_session(sid, "nope")


def test_append_message_assigns_sequential_turn_index(repo):
    sid = repo.create_session()
    assert repo.append_message(sid, "user", "hi") == 0
    assert repo.append_message(sid, "assistant", "hello") == 1
    assert repo.append_message(sid, "user", "bye") == 2


def test_append_message_rejects_bad_role(repo):
    sid = repo.create_session()
    with pytest.raises(ValueError):
        repo.append_message(sid, "system", "x")


def test_unique_turn_index_is_enforced_by_schema(repo):
    sid = repo.create_session()
    repo.append_message(sid, "user", "a")
    # forge a duplicate turn_index directly — the UNIQUE(session_id, turn_index) fires
    with pytest.raises(sqlite3.IntegrityError):
        repo._conn.execute(
            "INSERT INTO messages (id, session_id, turn_index, role, content, created_at, updated_at) "
            "VALUES ('dup', ?, 0, 'user', 'x', 't', 't')",
            (sid,),
        )


def test_recent_turns_is_oldest_first_and_limited(repo):
    sid = repo.create_session()
    for i in range(10):
        repo.append_message(sid, "user" if i % 2 == 0 else "assistant", f"m{i}")
    turns = repo.recent_turns(sid, 4)
    assert [t.content for t in turns] == ["m6", "m7", "m8", "m9"]


def test_all_messages_and_history(repo):
    sid = repo.create_session()
    repo.append_message(sid, "user", "one")
    repo.append_message(sid, "assistant", "two")
    assert [m.content for m in repo.all_messages(sid)] == ["one", "two"]
    hist = repo.history(sid)
    assert hist[0]["turn_index"] == 0 and hist[1]["role"] == "assistant"
    assert "created_at" in hist[0]


def test_required_tables_guard(tmp_path):
    bare = tmp_path / "bare.db"
    conn = sqlite3.connect(bare)
    conn.execute("CREATE TABLE sessions (id TEXT)")  # messages missing
    conn.commit()
    with pytest.raises(RuntimeError, match="messages table is missing"):
        SessionRepository(connection=conn)
    conn.close()
