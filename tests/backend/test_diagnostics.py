"""Diagnostics — log parsing/redaction + metrics aggregation (Phase 3 Step 3.4)."""

from __future__ import annotations

from src.backend.diagnostics import aggregate_metrics, read_log_entries

_LOG = (
    "2026-09-10 14:00:00,001 INFO backend backend ready\n"
    "2026-09-10 14:00:01,002 WARNING scheduler fire_due skipped\n"
    "2026-09-10 14:00:02,003 ERROR backend boom for alice@example.com\n"
    "Traceback (most recent call last):\n"
    '  File "x.py", line 1\n'
    "2026-09-10 14:00:03,004 DEBUG worker tick\n"
)


def _write_log(tmp_path):
    log = tmp_path / "backend.log"
    log.write_text(_LOG, encoding="utf-8")
    return log


def test_read_log_entries_parses_and_folds_tracebacks(tmp_path):
    entries, truncated = read_log_entries(None, 100, source=_write_log(tmp_path))
    assert [e.level for e in entries] == ["INFO", "WARNING", "ERROR", "DEBUG"]
    assert truncated is False
    err = next(e for e in entries if e.level == "ERROR")
    assert "Traceback (most recent call last):" in err.message  # continuation folded in


def test_read_log_entries_redacts_messages(tmp_path):
    entries, _ = read_log_entries(None, 100, source=_write_log(tmp_path))
    err = next(e for e in entries if e.level == "ERROR")
    assert "alice@example.com" not in err.message
    assert "<email>" in err.message


def test_read_log_entries_filters_at_or_above_level(tmp_path):
    entries, _ = read_log_entries("WARNING", 100, source=_write_log(tmp_path))
    assert {e.level for e in entries} == {"WARNING", "ERROR"}


def test_read_log_entries_keeps_the_newest_limit_and_flags_truncation(tmp_path):
    entries, truncated = read_log_entries(None, 2, source=_write_log(tmp_path))
    assert [e.level for e in entries] == ["ERROR", "DEBUG"]
    assert truncated is True


def test_read_log_entries_concatenates_rotations_oldest_first(tmp_path):
    (tmp_path / "backend.log.1").write_text(
        "2026-09-10 13:00:00,000 INFO backend older line\n", encoding="utf-8"
    )
    _write_log(tmp_path)
    entries, _ = read_log_entries(None, 100, source=tmp_path / "backend.log")
    assert entries[0].message == "older line"


def test_aggregate_metrics_on_an_empty_sample():
    s = aggregate_metrics([])
    assert s.sample_size == 0
    assert s.retrieval_latency_ms is None
    assert s.grounded_rate is None
    assert s.confidence_distribution == {"high": 0, "medium": 0, "low": 0, "none": 0}


def test_aggregate_metrics_computes_percentiles_and_rates():
    rows = [
        {
            "retrieval_time_ms": 10.0,
            "confidence_level": "high",
            "is_grounded": 1,
            "retrieval_hit": 1,
        },
        {
            "retrieval_time_ms": 20.0,
            "confidence_level": "high",
            "is_grounded": 0,
            "retrieval_hit": 1,
        },
        {
            "retrieval_time_ms": 30.0,
            "confidence_level": "low",
            "is_grounded": 1,
            "retrieval_hit": 0,
        },
        {"retrieval_time_ms": 40.0, "confidence_level": None, "is_grounded": 1, "retrieval_hit": 0},
    ]
    s = aggregate_metrics(rows)
    assert s.sample_size == 4
    assert s.retrieval_latency_ms["p50"] == 25.0
    assert s.confidence_distribution == {"high": 2, "medium": 0, "low": 1, "none": 1}
    assert s.grounded_rate == 0.75
    assert s.retrieval_hit_rate == 0.5
