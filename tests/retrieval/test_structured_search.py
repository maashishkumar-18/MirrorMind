"""
Integration tests for StructuredTableSearch (src/retrieval/structured_search.py)
— Phase 1 Step 1.3b. Real tmp-file SQLite with the 0001 migration and real
FTS5 (same setup pattern as tests/common/test_sqlite_vector_store.py).
"""

from datetime import UTC, datetime

import pytest

from db.connection import open_session_db
from db.migration_runner import MigrationRunner
from src.common.types import SessionRetrievedChunk
from src.retrieval.structured_search import StructuredTableSearch

pytestmark = pytest.mark.integration


def _now() -> str:
    return datetime.now(UTC).isoformat()


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "session.db")
    MigrationRunner(db_path=path, snapshot_dir=tmp_path / "snap").run()
    conn = open_session_db(path)
    conn.execute(
        "INSERT INTO sessions (id, started_at, created_at, updated_at) VALUES (?,?,?,?)",
        ("s1", _now(), _now(), _now()),
    )
    conn.executemany(
        "INSERT INTO reminders (id, session_id, title, notes, scheduled_time, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            ("r1", "s1", "Call the dentist", "book a cleaning appointment", _now(), _now(), _now()),
            ("r2", "s1", "Pay the electricity bill", "", _now(), _now(), _now()),
        ],
    )
    conn.execute(
        "INSERT INTO todos (id, session_id, title, notes, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        ("t1", "s1", "Buy groceries", "milk eggs coffee", _now(), _now()),
    )
    conn.execute(
        "INSERT INTO meeting_notes (id, session_id, raw_transcript, searchable_text, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        ("m1", "s1", "raw", "Sarah agreed the Q4 budget with the team", _now(), _now()),
    )
    conn.execute(
        "INSERT INTO schedules (id, date, created_at, updated_at) VALUES (?,?,?,?)",
        ("sc1", "2026-09-10", _now(), _now()),
    )
    conn.execute(
        "INSERT INTO schedule_items (id, schedule_id, title, start_time, end_time, notes, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "si1",
            "sc1",
            "Team standup",
            "2026-09-10T09:00",
            "2026-09-10T09:30",
            "daily sync",
            _now(),
            _now(),
        ),
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def search(db_path):
    return StructuredTableSearch(db_path=db_path)


def test_requires_exactly_one_of_db_path_or_connection():
    with pytest.raises(ValueError):
        StructuredTableSearch()


def test_matches_a_reminder_and_returns_structured_record_chunks(search):
    results = search.search("dentist appointment")
    assert results
    assert all(isinstance(r, SessionRetrievedChunk) for r in results)
    assert all(r.chunk_type == "structured_record" for r in results)
    top = results[0]
    assert top.chunk_id == "reminders:r1"
    assert "dentist" in top.content.lower()
    assert top.session_id == "s1"
    assert 0.0 < top.score <= 1.0
    assert top.source_prefix.startswith("[reminders ")


def test_searches_across_all_four_tables(search):
    assert search.search("groceries")[0].chunk_id == "todos:t1"
    assert search.search("budget")[0].chunk_id == "meeting_notes:m1"
    assert search.search("standup")[0].chunk_id == "schedule_items:si1"


def test_table_filter_restricts_the_search(search):
    assert search.search("groceries", tables=["reminders"]) == []
    assert search.search("groceries", tables=["todos"])[0].chunk_id == "todos:t1"


def test_unknown_table_raises(search):
    with pytest.raises(ValueError, match="unknown structured table"):
        search.search("x", tables=["not_a_table"])


def test_soft_deleted_rows_are_excluded(db_path):
    conn = open_session_db(db_path)
    conn.execute("UPDATE reminders SET deleted_at = ? WHERE id = 'r1'", (_now(),))
    conn.commit()
    conn.close()
    assert StructuredTableSearch(db_path=db_path).search("dentist") == []


def test_empty_or_punctuation_query_returns_empty(search):
    assert search.search("") == []
    assert search.search("   ") == []
    assert search.search("!!!") == []


def test_top_k_caps_the_result_count(search):
    # both reminders contain a common-ish token via OR-tokenisation
    results = search.search("dentist bill", top_k=1)
    assert len(results) == 1
