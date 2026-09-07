"""
Local scheduler thread (Phase 1 Step 1.5b).

A single daemon thread in the Python backend. Every ``poll_seconds`` it:

- fires every due-and-unfired reminder (``ReminderHandler.fire_due``) — the
  fallback path for a Windows toast that never arrived,
- generates any missed daily/weekly summary
  (``SummaryHandler.generate_due_summaries``), stamped with its *intended*
  time, not the late run time.

Starts with the backend, stops cleanly with it. The SQLite connection is
opened **inside ``run()``** — ``sqlite3`` connections are thread-affine, so a
connection made on the composition-root thread cannot be used here.
"""

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import yaml

from db.connection import open_session_db
from src.features.backup_manager import BackupConfig, BackupManager
from src.features.base import now_iso
from src.features.reminder_handler import ReminderHandler
from src.features.summary_handler import SummaryConfig, SummaryHandler
from src.features.toast_bridge import ToastBridge

logger = logging.getLogger(__name__)


@dataclass
class SchedulerConfig:
    """Loaded from config/features/scheduler.yaml (mirrors SummaryConfig /
    RetrievalRouterConfig's from_yaml + env-override pattern)."""

    poll_seconds: float = 60.0
    summary_catch_up_days: int = 7

    @classmethod
    def from_yaml(cls, path: str | None = None) -> "SchedulerConfig":
        config_path = (
            path
            or os.getenv("RAGPIPE_SCHEDULER_CONFIG")
            or str(Path(__file__).parent.parent.parent / "config" / "features" / "scheduler.yaml")
        )
        data: dict = {}
        if Path(config_path).exists():
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

        section = data.get("scheduler", {})
        defaults = cls()
        return cls(
            poll_seconds=float(
                os.getenv(
                    "RAGPIPE_SCHEDULER_POLL_SECONDS",
                    section.get("poll_seconds", defaults.poll_seconds),
                )
            ),
            summary_catch_up_days=int(
                section.get("summary_catch_up_days", defaults.summary_catch_up_days)
            ),
        )


class SchedulerThread(threading.Thread):
    def __init__(
        self,
        db_path: str,
        bridge: ToastBridge,
        *,
        key: str | None = None,
        config: SchedulerConfig | None = None,
        summary_config: SummaryConfig | None = None,
        backup_config: BackupConfig | None = None,
    ):
        super().__init__(daemon=True, name="companion-scheduler")
        self._db_path = db_path
        self._key = key
        self._bridge = bridge
        self._config = config or SchedulerConfig.from_yaml()
        self._summary_config = summary_config
        self._backup_config = backup_config
        self._stop_event = threading.Event()
        self._reminders: ReminderHandler | None = None
        self._summaries: SummaryHandler | None = None
        self._backups: BackupManager | None = None

    def run(self) -> None:
        # The key is applied here, at the connection; the handlers below
        # receive that connection directly, so their own db_path/key path
        # is not involved.
        conn = open_session_db(self._db_path, self._key)
        self._reminders = ReminderHandler(connection=conn, bridge=self._bridge)
        self._summaries = SummaryHandler(connection=conn, config=self._summary_config)
        # BackupManager opens its own short-lived source connection per backup
        # (a WAL reader alongside this handler connection is safe).
        self._backups = BackupManager(self._db_path, key=self._key, config=self._backup_config)
        try:
            while not self._stop_event.is_set():
                self._tick(now_iso())
                if self._stop_event.wait(self._config.poll_seconds):
                    break
        finally:
            conn.close()

    def stop(self, timeout: float = 5.0) -> bool:
        """Signal the thread to stop and wait up to ``timeout`` for it to
        exit. Returns ``True`` if it exited, ``False`` if it is still running
        (a wedged tick) — the caller / ShutdownCoordinator can log that rather
        than assume a clean stop."""
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout)
        if self.is_alive():
            logger.warning("scheduler thread did not stop within %.1fs", timeout)
            return False
        return True

    def _tick(self, now: str) -> None:
        assert self._reminders is not None and self._summaries is not None
        try:
            self._reminders.fire_due(now)
        except Exception:  # noqa: BLE001 — a bad tick must not kill the thread
            logger.exception("scheduler: fire_due failed")
        try:
            self._summaries.generate_due_summaries(
                now, catch_up_days=self._config.summary_catch_up_days
            )
        except Exception:  # noqa: BLE001
            logger.exception("scheduler: generate_due_summaries failed")
        if self._backups is not None:
            try:
                self._backups.run_due_backup(now)
            except Exception:  # noqa: BLE001
                logger.exception("scheduler: run_due_backup failed")
