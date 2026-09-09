"""
SummaryHandler (Phase 1 Step 1.5a).

``generate_daily_summary(date)`` gathers everything from that day —
``todos`` / ``reminders`` / ``meeting_notes`` / ``schedule_items`` created or
scheduled on the date, plus the primary ``session_chunks`` for sessions that
started on it — and asks the local model for a short natural-language rollup.
``generate_weekly_summary(week_start)`` condenses the seven daily summaries.

Both are **idempotent**: regenerating overwrites the active row for the period
(``summaries`` has a partial unique index on ``(summary_type, period_start)``
where ``deleted_at IS NULL``) and keeps the original ``scheduled_at`` — the
*intended* generation time — rather than stamping the late re-run time
(roadmap Step 1.5).
"""

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import timedelta
from pathlib import Path

import yaml

from src.common.llm_client import simple_generate
from src.common.types import Summary
from src.features.base import TableHandler, new_id, now_iso

logger = logging.getLogger(__name__)

_DAILY_PROMPT = """You are a personal AI companion writing the user's daily rollup for {date}.

Use ONLY the material below. Write 3-6 short sentences (or tight bullets) covering what
happened, what's outstanding, and anything time-sensitive. Warm and direct. If a section
is empty, skip it. Do not invent anything.

{material}
"""

_WEEKLY_PROMPT = """You are a personal AI companion writing the user's weekly rollup for the
week beginning {week_start}.

Below are the daily summaries for the week. Condense them into one short weekly summary —
the throughline, what got done, what's carrying over. Warm and direct.

{material}
"""

_EMPTY_DAY = "(nothing recorded for this day)"
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


@dataclass
class SummaryConfig:
    """Loaded from config/features/summary.yaml (see RetrievalRouterConfig for
    the from_yaml/env-override pattern this mirrors)."""

    daily_time: str = "21:00"
    weekly_day: str = "sunday"
    weekly_time: str = "18:00"

    @classmethod
    def from_yaml(
        cls, path: str | None = None, *, app_config_path: str | None = None
    ) -> "SummaryConfig":
        config_path = (
            path
            or os.getenv("RAGPIPE_SUMMARY_CONFIG")
            or str(Path(__file__).parent.parent.parent / "config" / "features" / "summary.yaml")
        )
        data: dict = {}
        if Path(config_path).exists():
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

        schedule = data.get("schedule", {})
        defaults = cls()
        # Settings → General (Phase 3 Step 3.4): a user-set daily summary time in
        # data/app_config.json wins over the env var / YAML / hard default.
        from src.models.app_config import AppConfig

        daily_override = AppConfig.load(app_config_path).summary_time
        return cls(
            daily_time=daily_override
            or str(
                os.getenv(
                    "RAGPIPE_SUMMARY_DAILY_TIME", schedule.get("daily_time", defaults.daily_time)
                )
            ),
            weekly_day=str(
                os.getenv(
                    "RAGPIPE_SUMMARY_WEEKLY_DAY", schedule.get("weekly_day", defaults.weekly_day)
                )
            ).lower(),
            weekly_time=str(
                os.getenv(
                    "RAGPIPE_SUMMARY_WEEKLY_TIME", schedule.get("weekly_time", defaults.weekly_time)
                )
            ),
        )


class SummaryHandler(TableHandler):
    _REQUIRED_TABLES = ("summaries",)

    def __init__(
        self,
        db_path: str | None = None,
        *,
        connection: sqlite3.Connection | None = None,
        key: str | None = None,
        config: SummaryConfig | None = None,
    ):
        super().__init__(db_path, connection=connection, key=key)
        self.config = config or SummaryConfig.from_yaml()

    def generate_daily_summary(self, date: str, *, scheduled_at: str | None = None) -> Summary:
        material = self._gather_day(date)
        content = self._generate(
            _DAILY_PROMPT.format(date=date, material=material),
            fallback=material,
        )
        return self._upsert(
            summary_type="daily",
            period_start=date,
            period_end=date,
            content=content,
            scheduled_at=scheduled_at or f"{date}T{self.config.daily_time}:00",
        )

    def generate_weekly_summary(
        self, week_start: str, *, scheduled_at: str | None = None
    ) -> Summary:
        start = date_cls.fromisoformat(week_start)
        end = start + timedelta(days=6)
        rows = self._conn.execute(
            "SELECT period_start, content FROM summaries "
            "WHERE summary_type = 'daily' AND deleted_at IS NULL "
            "AND period_start >= ? AND period_start <= ? ORDER BY period_start",
            (week_start, end.isoformat()),
        ).fetchall()
        material = (
            "\n\n".join(f"### {r['period_start']}\n{r['content']}" for r in rows)
            or "(no daily summaries recorded for this week)"
        )
        content = self._generate(
            _WEEKLY_PROMPT.format(week_start=week_start, material=material),
            fallback=material,
        )
        return self._upsert(
            summary_type="weekly",
            period_start=week_start,
            period_end=end.isoformat(),
            content=content,
            scheduled_at=scheduled_at or f"{end.isoformat()}T{self.config.weekly_time}:00",
        )

    def get_summary(self, summary_type: str, period_start: str) -> Summary | None:
        row = self._conn.execute(
            "SELECT * FROM summaries WHERE summary_type = ? AND period_start = ? "
            "AND deleted_at IS NULL",
            (summary_type, period_start),
        ).fetchone()
        return self._row_to_summary(row) if row else None

    def generate_due_summaries(self, now: str, *, catch_up_days: int = 7) -> list[Summary]:
        """Scheduler entry point. Generate every daily summary whose scheduled
        time has passed within the catch-up window and that isn't recorded yet
        (skipping days with nothing to summarize — that bounds the backfill),
        plus the most recent weekly summary if it is due. ``now`` is an ISO
        timestamp; returns everything generated (``[]`` on a normal tick)."""
        today = date_cls.fromisoformat(now[:10])
        generated: list[Summary] = []

        for offset in range(catch_up_days, -1, -1):
            day = (today - timedelta(days=offset)).isoformat()
            due_at = f"{day}T{self.config.daily_time}:00"
            if now < due_at or self.get_summary("daily", day) is not None:
                continue
            if self._gather_day(day) == _EMPTY_DAY:
                continue
            generated.append(self.generate_daily_summary(day, scheduled_at=due_at))

        weekly = self._maybe_weekly(now, today)
        if weekly is not None:
            generated.append(weekly)
        return generated

    def _maybe_weekly(self, now: str, today: date_cls) -> Summary | None:
        target = _WEEKDAYS.get(self.config.weekly_day, 6)
        anchor = today - timedelta(days=(today.weekday() - target) % 7)
        week_start = (anchor - timedelta(days=6)).isoformat()
        due_at = f"{anchor.isoformat()}T{self.config.weekly_time}:00"
        if now < due_at or self.get_summary("weekly", week_start) is not None:
            return None
        has_daily = self._conn.execute(
            "SELECT 1 FROM summaries WHERE summary_type = 'daily' AND deleted_at IS NULL "
            "AND period_start >= ? AND period_start <= ? LIMIT 1",
            (week_start, anchor.isoformat()),
        ).fetchone()
        if has_daily is None:
            return None
        return self.generate_weekly_summary(week_start, scheduled_at=due_at)

    # ------------------------------------------------------------------

    def _gather_day(self, date: str) -> str:
        like = f"{date}%"
        sections: list[str] = []

        reminders = self._conn.execute(
            "SELECT title, scheduled_time FROM reminders "
            "WHERE deleted_at IS NULL AND scheduled_time LIKE ? ORDER BY scheduled_time",
            (like,),
        ).fetchall()
        if reminders:
            sections.append(
                "Reminders:\n"
                + "\n".join(f"- {r['title']} (at {r['scheduled_time']})" for r in reminders)
            )

        todos = self._conn.execute(
            "SELECT title, priority, completed_at FROM todos "
            "WHERE deleted_at IS NULL AND created_at LIKE ? ORDER BY created_at",
            (like,),
        ).fetchall()
        if todos:
            sections.append(
                "Todos:\n"
                + "\n".join(
                    f"- {t['title']}"
                    + (f" [{t['priority']}]" if t["priority"] else "")
                    + (" (done)" if t["completed_at"] else "")
                    for t in todos
                )
            )

        items = self._conn.execute(
            "SELECT si.title, si.start_time, si.location FROM schedule_items si "
            "JOIN schedules s ON s.id = si.schedule_id "
            "WHERE si.deleted_at IS NULL AND s.deleted_at IS NULL AND s.date = ? "
            "ORDER BY si.start_time",
            (date,),
        ).fetchall()
        if items:
            sections.append(
                "Schedule:\n"
                + "\n".join(
                    f"- {i['title']} at {i['start_time']}"
                    + (f", {i['location']}" if i["location"] else "")
                    for i in items
                )
            )

        notes = self._conn.execute(
            "SELECT searchable_text FROM meeting_notes "
            "WHERE deleted_at IS NULL AND created_at LIKE ? ORDER BY created_at",
            (like,),
        ).fetchall()
        if notes:
            sections.append(
                "Meeting notes:\n" + "\n".join(f"- {n['searchable_text']}" for n in notes)
            )

        chunks = self._conn.execute(
            "SELECT sc.content FROM session_chunks sc "
            "JOIN sessions se ON se.id = sc.session_id "
            "WHERE sc.deleted_at IS NULL AND se.deleted_at IS NULL "
            "AND sc.chunk_type = 'primary' AND se.started_at LIKE ? ORDER BY se.started_at",
            (like,),
        ).fetchall()
        if chunks:
            joined = "\n".join(c["content"] for c in chunks)[:4000]
            sections.append(f"Conversations:\n{joined}")

        return "\n\n".join(sections) or _EMPTY_DAY

    def _generate(self, prompt: str, *, fallback: str) -> str:
        try:
            text = simple_generate(prompt).strip()
            return text or fallback
        except (
            Exception
        ) as e:  # noqa: BLE001 — a summary is lower-stakes; fall back to the raw material
            logger.warning("summary generation failed (%s); using the gathered material", e)
            return fallback

    def _upsert(
        self,
        *,
        summary_type: str,
        period_start: str,
        period_end: str,
        content: str,
        scheduled_at: str,
    ) -> Summary:
        now = now_iso()
        existing = self._conn.execute(
            "SELECT id, scheduled_at FROM summaries "
            "WHERE summary_type = ? AND period_start = ? AND deleted_at IS NULL",
            (summary_type, period_start),
        ).fetchone()
        if existing is not None:
            self._conn.execute(
                "UPDATE summaries SET content = ?, generated_at = ?, updated_at = ? WHERE id = ?",
                (content, now, now, existing["id"]),
            )
            self._conn.commit()
            return self._row_to_summary(self._require_row("summaries", existing["id"]))

        summary_id = new_id("sum")
        self._conn.execute(
            "INSERT INTO summaries (id, summary_type, period_start, period_end, content, "
            "scheduled_at, generated_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                summary_id,
                summary_type,
                period_start,
                period_end,
                content,
                scheduled_at,
                now,
                now,
                now,
            ),
        )
        self._conn.commit()
        return self._row_to_summary(self._require_row("summaries", summary_id))

    @staticmethod
    def _row_to_summary(row: sqlite3.Row) -> Summary:
        return Summary(
            id=row["id"],
            summary_type=row["summary_type"],
            period_start=row["period_start"],
            period_end=row["period_end"],
            content=row["content"],
            scheduled_at=row["scheduled_at"],
            generated_at=row["generated_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
