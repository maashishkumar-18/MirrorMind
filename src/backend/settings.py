"""Effective-value resolution for Settings → General (Phase 3 Step 3.4).

project_logic.md §13 / roadmap Step 3.4: the session idle timeout and the daily
summary time are user-configurable. They persist as ``AppConfig`` fields
(``data/app_config.json``), and an unset field (``None``) falls back to the
existing env / YAML default.

This module is the **single resolver** — the ``SessionWorker`` (idle check), the
``SchedulerThread`` (summary time), and the ``settings.get`` IPC handler all call
these so they never disagree about what the effective value is.
"""

from __future__ import annotations

import logging
import os

from src.models.app_config import AppConfig

logger = logging.getLogger(__name__)

DEFAULT_IDLE_MINUTES = 45.0


def _env_idle_minutes() -> float | None:
    raw = os.getenv("RAGPIPE_SESSION_IDLE_MINUTES")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("RAGPIPE_SESSION_IDLE_MINUTES=%r is not a number; ignoring", raw)
        return None


def _load(app_config_path: str | None) -> AppConfig:
    return AppConfig.load(app_config_path)


def effective_idle_minutes(app_config_path: str | None = None) -> float:
    """``AppConfig.idle_timeout_minutes`` → ``$RAGPIPE_SESSION_IDLE_MINUTES`` → 45."""
    cfg = _load(app_config_path)
    if cfg.idle_timeout_minutes:
        return float(cfg.idle_timeout_minutes)
    env = _env_idle_minutes()
    return env if env is not None else DEFAULT_IDLE_MINUTES


def idle_minutes_is_default(app_config_path: str | None = None) -> bool:
    """True when the effective value comes from env/YAML/hard default, not a
    user override in ``AppConfig`` (drives the "Reset to default" affordance)."""
    return not _load(app_config_path).idle_timeout_minutes


def env_yaml_summary_time() -> str:
    """``$RAGPIPE_SUMMARY_DAILY_TIME`` → ``config/features/summary.yaml`` →
    ``21:00``. The fallback below a user-set ``AppConfig.summary_time``; also
    what ``SchedulerThread`` layers its own AppConfig read on top of."""
    from src.features.summary_handler import SummaryConfig

    return SummaryConfig.from_yaml().daily_time


def effective_summary_time(app_config_path: str | None = None) -> str:
    """``AppConfig.summary_time`` → env → YAML → ``21:00``."""
    return _load(app_config_path).summary_time or env_yaml_summary_time()


def summary_time_is_default(app_config_path: str | None = None) -> bool:
    return not _load(app_config_path).summary_time


def resolved(cfg: AppConfig) -> dict[str, object]:
    """The effective General-settings values + per-field default flags, computed
    **entirely from the passed ``AppConfig``** (plus env/YAML) — no second
    app-config read, so the value and its ``*_is_default`` flag can never skew,
    and ``settings.update`` can answer from the object it just saved."""
    idle = cfg.idle_timeout_minutes
    summary = cfg.summary_time
    return {
        "idle_timeout_minutes": int(idle or _env_idle_minutes() or DEFAULT_IDLE_MINUTES),
        "summary_time": summary or env_yaml_summary_time(),
        "idle_timeout_is_default": not idle,
        "summary_time_is_default": not summary,
    }
