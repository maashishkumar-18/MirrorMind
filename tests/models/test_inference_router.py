"""Unit tests for src/models/inference_router.py (Phase 1 Step 1.6)."""

import types

import pytest

from src.common.llm_client import ModelNotLoadedError, OllamaNotRunningError
from src.models.app_config import AppConfig
from src.models.inference_router import ModelInferenceRouter

pytestmark = pytest.mark.unit


class StubAdapter:
    def __init__(self, *, content="an answer", raises=None):
        self.content = content
        self.raises = raises
        self.seen_models: list[str] = []

    def generate(self, prompt, config, timeout_seconds):
        self.seen_models.append(config.model_name)
        if self.raises is not None:
            raise self.raises
        return types.SimpleNamespace(content=self.content)


class StubRegistry:
    def __init__(self, adapter):
        self._adapter = adapter

    def get(self, name):
        return self._adapter


@pytest.fixture
def app_cfg(tmp_path, monkeypatch):
    path = tmp_path / "app_config.json"
    monkeypatch.setenv("RAGPIPE_APP_CONFIG_PATH", str(path))
    monkeypatch.delenv("OLLAMA_DEFAULT_MODEL", raising=False)
    return path


def _router(app_cfg, adapter):
    return ModelInferenceRouter(registry=StubRegistry(adapter), app_config_path=str(app_cfg))


def test_no_active_model_blocks_inference(app_cfg):
    adapter = StubAdapter()
    result = _router(app_cfg, adapter).generate("hello")
    assert result.ok is False
    assert result.error_code == "no_model_active"
    assert adapter.seen_models == []


def test_env_default_does_not_satisfy_first_launch(app_cfg, monkeypatch):
    monkeypatch.setenv("OLLAMA_DEFAULT_MODEL", "llama3.1:8b")
    result = _router(app_cfg, StubAdapter()).generate("hello")
    assert result.ok is False
    assert result.error_code == "no_model_active"


def test_routes_to_active_model(app_cfg):
    AppConfig.load(str(app_cfg)).set_active_model("mistral:latest")
    adapter = StubAdapter(content="hi from mistral")
    result = _router(app_cfg, adapter).generate("hello")
    assert result.ok is True
    assert result.text == "hi from mistral"
    assert result.model_name == "mistral:latest"
    assert adapter.seen_models == ["mistral:latest"]


def test_switch_takes_effect_without_new_router(app_cfg):
    AppConfig.load(str(app_cfg)).set_active_model("llama3.2:latest")
    adapter = StubAdapter()
    router = _router(app_cfg, adapter)

    router.generate("q1")
    AppConfig.load(str(app_cfg)).set_active_model("phi4-mini:latest")
    router.generate("q2")

    assert adapter.seen_models == ["llama3.2:latest", "phi4-mini:latest"]


def test_model_not_loaded_maps_to_specific_message(app_cfg):
    AppConfig.load(str(app_cfg)).set_active_model("gemma2:9b")
    adapter = StubAdapter(raises=ModelNotLoadedError("still loading"))
    result = _router(app_cfg, adapter).generate("hello")
    assert result.ok is False
    assert result.error_code == "model_not_loaded"
    assert "gemma2:9b" in result.message
    assert "loading" in result.message.lower()


def test_ollama_not_running_maps_to_specific_message(app_cfg):
    AppConfig.load(str(app_cfg)).set_active_model("llama3.1:8b")
    adapter = StubAdapter(raises=OllamaNotRunningError("connection refused"))
    result = _router(app_cfg, adapter).generate("hello")
    assert result.ok is False
    assert result.error_code == "ollama_not_running"
    assert "isn't running" in result.message
