"""
Integration tests for SummaryHandler (src/features/summary_handler.py) — Phase 1 Step 1.5a.

Covers the roadmap acceptance criteria: a daily summary is generated from a
pre-seeded synthetic dataset with the correct scheduled timestamp, and
regeneration is idempotent (overwrites, keeps the original scheduled_at).
"""

import pytest

from src.features.schedule_handler import ScheduleHandler
from src.features.summary_handler import SummaryHandler

pytestmark = pytest.mark.integration

DATE = "2026-02-20"


@pytest.fixture
def seeded(session_conn):
    """A synthetic day: 1 reminder, 1 todo, 1 meeting note, 1 schedule item,
    plus a primary session chunk for a session started that day. Rows are
    inserted with created_at on DATE — the handlers stamp real wall-clock
    time, which the daily-summary date filter would then miss."""
    now = f"{DATE}T08:00:00Z"
    session_conn.execute(
        "INSERT INTO sessions (id, started_at, created_at, updated_at) VALUES (?,?,?,?)",
        ("day-sess", now, now, now),
    )
    session_conn.execute(
        "INSERT INTO session_chunks (id, session_id, chunk_type, content, embedding, "
        "token_count, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            "day-sess::primary",
            "day-sess",
            "primary",
            "We talked about the move logistics.",
            b"\x00" * 4,
            8,
            now,
            now,
        ),
    )
    session_conn.execute(
        "INSERT INTO reminders (id, title, scheduled_time, created_at, updated_at) VALUES (?,?,?,?,?)",
        ("rem-x", "Water the plants", f"{DATE}T10:00:00Z", now, now),
    )
    session_conn.execute(
        "INSERT INTO todos (id, title, priority, created_at, updated_at) VALUES (?,?,?,?,?)",
        ("todo-x", "Pay the movers deposit", "high", now, now),
    )
    session_conn.execute(
        "INSERT INTO meeting_notes (id, raw_transcript, decisions, searchable_text, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (
            "mn-x",
            "Move logistics with Dana",
            '["Dana packs the kitchen"]',
            "Dana · move · Dana packs the kitchen",
            now,
            now,
        ),
    )
    session_conn.commit()

    ScheduleHandler(connection=session_conn).create_schedule_item(
        "Movers arrive", f"{DATE}T08:00:00Z", f"{DATE}T12:00:00Z", location="the flat"
    )
    return session_conn


def test_daily_summary_from_seeded_day(seeded, monkeypatch):
    captured = {}

    def _fake_generate(prompt, *a, **k):
        captured["prompt"] = prompt
        return "Movers came at 8am, plants need watering, deposit is the priority."

    monkeypatch.setattr("src.features.summary_handler.simple_generate", _fake_generate)
    handler = SummaryHandler(connection=seeded)

    summary = handler.generate_daily_summary(DATE, scheduled_at=f"{DATE}T21:00:00")

    assert summary.summary_type == "daily"
    assert summary.period_start == DATE and summary.period_end == DATE
    assert summary.scheduled_at == f"{DATE}T21:00:00"
    assert summary.scheduled_at != summary.generated_at
    assert "deposit" in summary.content
    # every seeded source reached the prompt
    assert "Water the plants" in captured["prompt"]
    assert "Pay the movers deposit" in captured["prompt"]
    assert "Movers arrive" in captured["prompt"]
    assert "Dana packs the kitchen" in captured["prompt"]
    assert "move logistics" in captured["prompt"].lower()


def test_regeneration_is_idempotent(seeded, monkeypatch):
    monkeypatch.setattr("src.features.summary_handler.simple_generate", lambda *a, **k: "v1")
    handler = SummaryHandler(connection=seeded)
    first = handler.generate_daily_summary(DATE, scheduled_at=f"{DATE}T21:00:00")

    monkeypatch.setattr(
        "src.features.summary_handler.simple_generate", lambda *a, **k: "v2 rewritten"
    )
    second = handler.generate_daily_summary(DATE, scheduled_at=f"{DATE}T23:30:00")

    assert second.id == first.id
    assert second.content == "v2 rewritten"
    assert second.scheduled_at == f"{DATE}T21:00:00"  # original kept
    assert (
        seeded.execute(
            "SELECT COUNT(*) c FROM summaries WHERE summary_type='daily' AND period_start=? "
            "AND deleted_at IS NULL",
            (DATE,),
        ).fetchone()["c"]
        == 1
    )
    assert handler.get_summary("daily", DATE).content == "v2 rewritten"


def test_summary_generation_falls_back_to_material_on_llm_failure(seeded, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("ollama down")

    monkeypatch.setattr("src.features.summary_handler.simple_generate", _boom)
    handler = SummaryHandler(connection=seeded)
    summary = handler.generate_daily_summary(DATE, scheduled_at=f"{DATE}T21:00:00")
    assert "Water the plants" in summary.content  # raw gathered material


def test_weekly_summary_aggregates_dailies(seeded, monkeypatch):
    monkeypatch.setattr(
        "src.features.summary_handler.simple_generate", lambda *a, **k: "daily text"
    )
    handler = SummaryHandler(connection=seeded)
    handler.generate_daily_summary("2026-02-16", scheduled_at="2026-02-16T21:00:00")
    handler.generate_daily_summary("2026-02-18", scheduled_at="2026-02-18T21:00:00")

    captured = {}

    def _weekly(prompt, *a, **k):
        captured["prompt"] = prompt
        return "A busy week of moving prep."

    monkeypatch.setattr("src.features.summary_handler.simple_generate", _weekly)
    weekly = handler.generate_weekly_summary("2026-02-16")

    assert weekly.summary_type == "weekly"
    assert weekly.period_start == "2026-02-16" and weekly.period_end == "2026-02-22"
    assert "2026-02-16" in captured["prompt"] and "2026-02-18" in captured["prompt"]
