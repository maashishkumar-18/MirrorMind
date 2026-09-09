"""Diagnostics data for Settings → Diagnostics (Phase 3 Step 3.4).

Two read-only views over local files:

- **logs** — parse the rotating ``backend.log`` (+ its rotations) into structured
  entries, filter by level, redact every message (defence in depth — see
  ``log_redaction``), return the newest ``limit``.
- **metrics** — aggregate the newest rows of the local ``metrics.db``
  (``observability.metrics_store``) into the numbers the dashboard shows. Only
  what the chat spine records today is available — error rate and compute time
  are not instrumented yet (v1.1), the result carries ``None`` for them.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from src.backend.log_redaction import _log_files_oldest_first, redact
from src.backend.paths import data_dir, log_file

# "%(asctime)s %(levelname)s %(name)s %(message)s" — asctime is "2026-09-10 14:23:01,123"
_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+) "
    r"(?P<level>[A-Z]+) "
    r"(?P<logger>\S+) "
    r"(?P<msg>.*)$"
)
_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


@dataclass
class LogEntry:
    timestamp: str
    level: str
    logger: str
    message: str


def _parse_lines(text: str) -> list[LogEntry]:
    entries: list[LogEntry] = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            entries.append(LogEntry(m["ts"], m["level"], m["logger"], m["msg"]))
        elif entries:
            # a continuation line (traceback frame) — fold into the last entry
            entries[-1].message += "\n" + line
    return entries


def read_log_entries(
    level: str | None, limit: int, *, source: Path | None = None
) -> tuple[list[LogEntry], bool]:
    """The newest ``limit`` entries at or above ``level`` (None = all). Second
    element is ``True`` when older matching entries were dropped."""
    active = source or log_file()
    text = ""
    for part in _log_files_oldest_first(active):
        try:
            text += part.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

    entries = _parse_lines(text)
    if level:
        floor = _LEVEL_ORDER.get(level.upper(), 0)
        entries = [e for e in entries if _LEVEL_ORDER.get(e.level, 0) >= floor]

    for e in entries:
        e.message = redact(e.message)

    truncated = len(entries) > limit
    return entries[-limit:], truncated


# ------------------------------------------------------------------
# metrics
# ------------------------------------------------------------------


def _percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile — matches ``MetricsStore.percentile``."""
    ordered = sorted(values)
    k = (len(ordered) - 1) * (p / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    if lo == hi:
        return float(ordered[lo])
    return float(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo))


@dataclass
class MetricsSummary:
    sample_size: int
    retrieval_latency_ms: dict[str, float] | None
    confidence_distribution: dict[str, int]
    grounded_rate: float | None
    retrieval_hit_rate: float | None


def _mean_flag(rows: list[dict], column: str) -> float | None:
    vals = [row[column] for row in rows if row.get(column) is not None]
    return round(sum(1 for v in vals if v) / len(vals), 3) if vals else None


def aggregate_metrics(rows: list[dict]) -> MetricsSummary:
    latencies = [
        float(row["retrieval_time_ms"]) for row in rows if row.get("retrieval_time_ms") is not None
    ]
    latency = (
        {
            "p50": round(_percentile(latencies, 50), 1),
            "p95": round(_percentile(latencies, 95), 1),
            "p99": round(_percentile(latencies, 99), 1),
        }
        if latencies
        else None
    )

    levels = Counter((row.get("confidence_level") or "none").lower() for row in rows)
    distribution = {k: levels.get(k, 0) for k in ("high", "medium", "low", "none")}

    return MetricsSummary(
        sample_size=len(rows),
        retrieval_latency_ms=latency,
        confidence_distribution=distribution,
        grounded_rate=_mean_flag(rows, "is_grounded"),
        retrieval_hit_rate=_mean_flag(rows, "retrieval_hit"),
    )


def metrics_db_path() -> str:
    """Where ``SessionWorker`` writes the local metrics file (see
    ``session_worker._build``)."""
    return str(data_dir() / "metrics.db")
