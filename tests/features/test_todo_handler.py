"""
Integration tests for TodoHandler (src/features/todo_handler.py) — Phase 1 Step 1.5a.

Real migrated tmp DB (via the shared `session_conn` fixture); the FTS mirror is
exercised to confirm the base-table writes propagate through the triggers.
"""

import pytest

from src.features.todo_handler import TodoHandler

pytestmark = pytest.mark.integration


@pytest.fixture
def handler(session_conn):
    return TodoHandler(connection=session_conn)


def test_create_get_update_complete_delete_lifecycle(handler, session_conn):
    todo = handler.create_todo(
        "Finish the launch checklist",
        notes="Priya is chasing the last items",
        priority="high",
        category="work",
        session_id="s1",
    )
    assert todo.id.startswith("todo_")
    assert handler.get_todo(todo.id).title == "Finish the launch checklist"

    updated = handler.update_todo(todo.id, priority="medium", notes="almost done")
    assert updated.priority == "medium" and updated.notes == "almost done"
    assert updated.updated_at >= todo.updated_at

    done = handler.complete_todo(todo.id)
    assert done.completed_at is not None

    assert handler.delete_todo(todo.id) is True
    assert handler.get_todo(todo.id) is None
    assert handler.delete_todo(todo.id) is False  # already gone


def test_get_todos_filters(handler):
    a = handler.create_todo("active high", priority="high")
    b = handler.create_todo("active low", priority="low")
    c = handler.create_todo("done one", priority="high")
    handler.complete_todo(c.id)

    all_ids = {t.id for t in handler.get_todos()}
    assert all_ids == {a.id, b.id, c.id}

    active_ids = {t.id for t in handler.get_todos(active_only=True)}
    assert active_ids == {a.id, b.id}

    high_active = handler.get_todos(active_only=True, priority="high")
    assert [t.id for t in high_active] == [a.id]


def test_bad_priority_rejected(handler):
    with pytest.raises(ValueError):
        handler.create_todo("nope", priority="urgent")
    good = handler.create_todo("ok")
    with pytest.raises(ValueError):
        handler.update_todo(good.id, priority="sometime")


def test_fts_mirror_stays_in_sync(handler, session_conn):
    todo = handler.create_todo("Get fresh basil and pine nuts", notes="for pesto")
    rows = session_conn.execute(
        "SELECT t.id FROM todos t JOIN todos_fts f ON f.rowid = t.rowid "
        "WHERE todos_fts MATCH ? AND t.deleted_at IS NULL",
        ('"basil"',),
    ).fetchall()
    assert [r["id"] for r in rows] == [todo.id]

    handler.delete_todo(todo.id)
    # soft-deleted rows are filtered by the JOIN predicate, not removed from FTS
    still = session_conn.execute(
        "SELECT t.id FROM todos t JOIN todos_fts f ON f.rowid = t.rowid "
        "WHERE todos_fts MATCH ? AND t.deleted_at IS NULL",
        ('"basil"',),
    ).fetchall()
    assert still == []
