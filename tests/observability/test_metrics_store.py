"""
Characterization tests for MetricsStore (observability/metrics_store.py).

Real SQLite against tmp_path -- no network/model dependency. No
persistent connection is held; every method opens/closes its own
connection under a threading.Lock, with WAL mode + busy_timeout=30000 set
on every open.
"""

import sqlite3

import pytest

from observability.metrics_store import MetricsStore, PipelineCallMetrics

pytestmark = pytest.mark.characterization


@pytest.fixture
def store(tmp_path):
    return MetricsStore(db_path=str(tmp_path / "metrics.db"))


class TestInitDb:
    def test_pipeline_calls_table_is_created(self, store):
        conn = sqlite3.connect(store.db_path)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        conn.close()
        assert "pipeline_calls" in tables

    def test_expected_columns_are_present(self, store):
        conn = sqlite3.connect(store.db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(pipeline_calls)").fetchall()}
        conn.close()
        expected = {
            "id",
            "request_id",
            "timestamp",
            "env",
            "query",
            "total_time_ms",
            "retrieval_time_ms",
            "generation_time_ms",
            "stage_timings_json",
            "confidence_score",
            "confidence_level",
            "retrieval_hit",
            "candidates_retrieved",
            "is_grounded",
            "citations_count",
            "refused",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "prompt_version",
            "model_name",
            "retrieval_pipeline_name",
            "faithfulness_score",
            "langfuse_trace_id",
            "langfuse_trace_url",
        }
        assert columns == expected

    def test_wal_mode_is_active(self, store):
        conn = sqlite3.connect(store.db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode.lower() == "wal"

    def test_busy_timeout_is_30000(self, store):
        conn = store._connect()
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.close()
        assert timeout == 30000

    def test_init_is_idempotent(self, tmp_path):
        db_path = str(tmp_path / "metrics.db")
        MetricsStore(db_path=db_path)
        MetricsStore(db_path=db_path)  # must not raise on a second open


class TestRecordAndQuery:
    def test_record_then_query_recent_round_trips(self, store):
        metrics = PipelineCallMetrics(
            request_id="req-1",
            env="ci",
            query="what did we discuss",
            total_time_ms=123.4,
            confidence_score=0.9,
            confidence_level="HIGH",
            retrieval_hit=True,
            candidates_retrieved=5,
            is_grounded=True,
            citations_count=2,
            refused=False,
            stage_timings={"retrieval": 50.0, "generation": 73.4},
        )
        store.record(metrics)

        rows = store.query_recent()
        assert len(rows) == 1
        row = rows[0]
        assert row["request_id"] == "req-1"
        assert row["query"] == "what did we discuss"
        assert row["total_time_ms"] == pytest.approx(123.4)

    def test_bool_fields_coerced_to_int_in_storage(self, store):
        store.record(
            PipelineCallMetrics(
                request_id="r", env="ci", retrieval_hit=True, is_grounded=False, refused=True
            )
        )
        row = store.query_recent()[0]
        assert row["retrieval_hit"] == 1
        assert row["is_grounded"] == 0
        assert row["refused"] == 1

    def test_stage_timings_json_round_trips_via_query(self, store):
        import json

        store.record(
            PipelineCallMetrics(request_id="r", env="ci", stage_timings={"a": 1.5, "b": 2.5})
        )
        row = store.query_recent()[0]
        assert json.loads(row["stage_timings_json"]) == {"a": 1.5, "b": 2.5}

    def test_timestamp_defaults_to_now_when_unset(self, store):
        store.record(PipelineCallMetrics(request_id="r", env="ci"))
        row = store.query_recent()[0]
        assert row["timestamp"] is not None
        assert row["timestamp"] != ""

    def test_explicit_timestamp_is_preserved(self, store):
        store.record(
            PipelineCallMetrics(request_id="r", env="ci", timestamp="2026-01-01T00:00:00+00:00")
        )
        row = store.query_recent()[0]
        assert row["timestamp"] == "2026-01-01T00:00:00+00:00"

    def test_query_recent_orders_newest_first(self, store):
        store.record(
            PipelineCallMetrics(request_id="r1", env="ci", timestamp="2026-01-01T00:00:00+00:00")
        )
        store.record(
            PipelineCallMetrics(request_id="r2", env="ci", timestamp="2026-01-02T00:00:00+00:00")
        )
        rows = store.query_recent()
        assert [r["request_id"] for r in rows] == ["r2", "r1"]

    def test_query_all_orders_oldest_first(self, store):
        store.record(
            PipelineCallMetrics(request_id="r1", env="ci", timestamp="2026-01-01T00:00:00+00:00")
        )
        store.record(
            PipelineCallMetrics(request_id="r2", env="ci", timestamp="2026-01-02T00:00:00+00:00")
        )
        rows = store.query_all()
        assert [r["request_id"] for r in rows] == ["r1", "r2"]

    def test_query_recent_respects_limit(self, store):
        for i in range(5):
            store.record(PipelineCallMetrics(request_id=f"r{i}", env="ci"))
        rows = store.query_recent(limit=2)
        assert len(rows) == 2

    def test_query_filters_by_env(self, store):
        store.record(PipelineCallMetrics(request_id="r1", env="ci"))
        store.record(PipelineCallMetrics(request_id="r2", env="simulated_production"))
        rows = store.query_recent(env="ci")
        assert len(rows) == 1
        assert rows[0]["request_id"] == "r1"

    def test_query_all_with_no_rows_returns_empty_list(self, store):
        assert store.query_all() == []


class TestPercentile:
    def test_empty_table_returns_none(self, store):
        assert store.percentile("total_time_ms", 50) is None

    def test_exact_index_case(self, store):
        for value in [1.0, 2.0, 3.0, 4.0, 5.0]:
            store.record(PipelineCallMetrics(request_id="r", env="ci", total_time_ms=value))
        assert store.percentile("total_time_ms", 50) == pytest.approx(3.0)
        assert store.percentile("total_time_ms", 0) == pytest.approx(1.0)
        assert store.percentile("total_time_ms", 100) == pytest.approx(5.0)

    def test_interpolated_case(self, store):
        for value in [10.0, 20.0, 30.0, 40.0]:
            store.record(PipelineCallMetrics(request_id="r", env="ci", total_time_ms=value))
        # n=4, k=(4-1)*0.5=1.5 -> interpolate between values[1]=20 and values[2]=30
        assert store.percentile("total_time_ms", 50) == pytest.approx(25.0)

    def test_unknown_column_raises_value_error(self, store):
        with pytest.raises(ValueError, match="Unknown/non-numeric column"):
            store.percentile("not_a_real_column", 50)

    def test_null_values_are_excluded(self, store):
        store.record(PipelineCallMetrics(request_id="r1", env="ci", total_time_ms=10.0))
        store.record(
            PipelineCallMetrics(request_id="r2", env="ci")
        )  # total_time_ms defaults to 0.0, not NULL
        # Explicitly test the NULL-exclusion path via faithfulness_score, which
        # defaults to None (stored as NULL) unless explicitly set.
        assert store.percentile("faithfulness_score", 50) is None

    def test_percentile_respects_env_filter(self, store):
        store.record(PipelineCallMetrics(request_id="r1", env="ci", total_time_ms=10.0))
        store.record(
            PipelineCallMetrics(request_id="r2", env="simulated_production", total_time_ms=100.0)
        )
        assert store.percentile("total_time_ms", 50, env="ci") == pytest.approx(10.0)
