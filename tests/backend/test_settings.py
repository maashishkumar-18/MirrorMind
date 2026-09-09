"""Effective-value resolution for Settings → General (Phase 3 Step 3.4)."""

from __future__ import annotations

import pytest

from src.backend import settings
from src.models.app_config import AppConfig


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    monkeypatch.delenv("RAGPIPE_SESSION_IDLE_MINUTES", raising=False)
    monkeypatch.delenv("RAGPIPE_SUMMARY_DAILY_TIME", raising=False)
    return str(tmp_path / "app_config.json")


def test_idle_minutes_falls_back_to_the_hard_default(cfg_path):
    assert settings.effective_idle_minutes(cfg_path) == 45.0
    assert settings.idle_minutes_is_default(cfg_path) is True


def test_env_overrides_the_hard_default(cfg_path, monkeypatch):
    monkeypatch.setenv("RAGPIPE_SESSION_IDLE_MINUTES", "20")
    assert settings.effective_idle_minutes(cfg_path) == 20.0
    # env is not a user override — the "reset to default" affordance still shows
    assert settings.idle_minutes_is_default(cfg_path) is True


def test_app_config_wins_over_env(cfg_path, monkeypatch):
    monkeypatch.setenv("RAGPIPE_SESSION_IDLE_MINUTES", "20")
    AppConfig.load(cfg_path).set_idle_timeout_minutes(90)
    assert settings.effective_idle_minutes(cfg_path) == 90.0
    assert settings.idle_minutes_is_default(cfg_path) is False


def test_clearing_the_override_returns_to_the_default(cfg_path):
    cfg = AppConfig.load(cfg_path)
    cfg.set_idle_timeout_minutes(90)
    cfg.set_idle_timeout_minutes(None)
    assert settings.effective_idle_minutes(cfg_path) == 45.0


def test_summary_time_default_and_override(cfg_path):
    assert settings.effective_summary_time(cfg_path) == "21:00"
    assert settings.summary_time_is_default(cfg_path) is True

    AppConfig.load(cfg_path).set_summary_time("07:30")
    assert settings.effective_summary_time(cfg_path) == "07:30"
    assert settings.summary_time_is_default(cfg_path) is False


def test_bad_env_idle_is_ignored(cfg_path, monkeypatch):
    monkeypatch.setenv("RAGPIPE_SESSION_IDLE_MINUTES", "not-a-number")
    assert settings.effective_idle_minutes(cfg_path) == 45.0
