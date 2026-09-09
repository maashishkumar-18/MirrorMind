"""Unit tests for src/models/app_config.py (Phase 1 Step 1.6)."""

import pytest

from src.models.app_config import (
    AppConfig,
    app_config_path,
    model_setup_required,
    resolve_default_model,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    """Point the app-config at a fresh file under tmp_path and clear every
    env var that path/model resolution consults."""
    path = tmp_path / "app_config.json"
    monkeypatch.setenv("RAGPIPE_APP_CONFIG_PATH", str(path))
    monkeypatch.delenv("RAGPIPE_DATA_DIR", raising=False)
    monkeypatch.delenv("OLLAMA_DEFAULT_MODEL", raising=False)
    return path


def test_missing_file_is_first_launch(cfg_path):
    cfg = AppConfig.load()
    assert cfg.active_model is None
    assert model_setup_required() is True


def test_save_then_reload_roundtrip(cfg_path):
    cfg = AppConfig.load()
    cfg.set_active_model("llama3.1:8b")

    assert cfg_path.exists()
    reloaded = AppConfig.load()
    assert reloaded.active_model == "llama3.1:8b"
    assert reloaded.updated_at  # stamped on save
    assert model_setup_required() is False


def test_save_is_atomic_no_tmp_left(cfg_path):
    AppConfig.load().set_active_model("mistral:latest")
    assert not (cfg_path.parent / (cfg_path.name + ".tmp")).exists()
    assert list(cfg_path.parent.glob("*.tmp")) == []


def test_set_active_model_none_clears(cfg_path):
    cfg = AppConfig.load()
    cfg.set_active_model("phi4-mini:latest")
    cfg.set_active_model(None)
    assert AppConfig.load().active_model is None
    assert model_setup_required() is True


def test_corrupt_json_falls_back_to_defaults(cfg_path):
    cfg_path.write_text("{{{ not valid json", encoding="utf-8")
    cfg = AppConfig.load()  # must not raise
    assert cfg.active_model is None


def test_non_object_root_falls_back_to_defaults(cfg_path):
    cfg_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert AppConfig.load().active_model is None


def test_resolve_default_model_fallback_chain(tmp_path, monkeypatch):
    path = tmp_path / "app_config.json"
    monkeypatch.setenv("RAGPIPE_APP_CONFIG_PATH", str(path))

    # 1. hardcoded default when nothing is set
    monkeypatch.delenv("OLLAMA_DEFAULT_MODEL", raising=False)
    assert resolve_default_model() == "llama3.1:8b"

    # 2. env var when set and no active model
    monkeypatch.setenv("OLLAMA_DEFAULT_MODEL", "gemma2:9b")
    assert resolve_default_model() == "gemma2:9b"

    # 3. app-config active model wins over env
    AppConfig.load().set_active_model("qwen2.5:7b")
    assert resolve_default_model() == "qwen2.5:7b"


def test_last_exported_at_roundtrip(cfg_path):
    # Phase 2 Step 2.2
    cfg = AppConfig.load()
    assert cfg.last_exported_at is None
    cfg.set_last_exported_at("2026-03-02T12:00:00+00:00")
    reloaded = AppConfig.load()
    assert reloaded.last_exported_at == "2026-03-02T12:00:00+00:00"
    cfg.set_last_exported_at(None)
    assert AppConfig.load().last_exported_at is None


def test_v1_file_without_last_exported_at_loads(cfg_path):
    cfg_path.write_text(
        '{"version": 1, "active_model": "llama3.1:8b", "updated_at": "2026-01-01T00:00:00Z"}',
        encoding="utf-8",
    )
    cfg = AppConfig.load()
    assert cfg.active_model == "llama3.1:8b"
    assert cfg.last_exported_at is None


def test_v2_file_without_the_general_settings_loads(cfg_path):
    # Phase 3 Step 3.4 bumped the version 2 -> 3; a v2 file has no
    # idle_timeout_minutes / summary_time and must load with them None.
    cfg_path.write_text(
        '{"version": 2, "active_model": "llama3.1:8b", '
        '"last_exported_at": "2026-05-01T00:00:00Z", "updated_at": "2026-05-01T00:00:00Z"}',
        encoding="utf-8",
    )
    cfg = AppConfig.load()
    assert cfg.active_model == "llama3.1:8b"
    assert cfg.last_exported_at == "2026-05-01T00:00:00Z"
    assert cfg.idle_timeout_minutes is None
    assert cfg.summary_time is None


def test_general_settings_roundtrip_and_clear(cfg_path):
    # Phase 3 Step 3.4
    cfg = AppConfig.load()
    assert cfg.idle_timeout_minutes is None and cfg.summary_time is None

    cfg.set_idle_timeout_minutes(90)
    cfg.set_summary_time("07:30")
    reloaded = AppConfig.load()
    assert reloaded.idle_timeout_minutes == 90
    assert reloaded.summary_time == "07:30"
    assert reloaded.version == 3

    cfg.set_idle_timeout_minutes(None)
    cfg.set_summary_time(None)
    cleared = AppConfig.load()
    assert cleared.idle_timeout_minutes is None and cleared.summary_time is None


def test_path_resolution_prefers_explicit_then_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGPIPE_APP_CONFIG_PATH", str(tmp_path / "explicit.json"))
    monkeypatch.setenv("RAGPIPE_DATA_DIR", str(tmp_path / "data"))
    assert app_config_path() == tmp_path / "explicit.json"
    assert app_config_path("/tmp/override.json").name == "override.json"

    monkeypatch.delenv("RAGPIPE_APP_CONFIG_PATH", raising=False)
    assert app_config_path() == tmp_path / "data" / "app_config.json"
