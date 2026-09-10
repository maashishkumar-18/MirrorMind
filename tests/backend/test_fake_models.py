"""``src/backend/fake_models.py`` — the Phase 4 e2e model-management stub."""

import pytest

from src.backend import fake_models
from src.models.types import DownloadProgress, ModelStatus

pytestmark = pytest.mark.unit


def test_build_is_none_without_env(monkeypatch):
    monkeypatch.delenv("RAGPIPE_FAKE_MODELS", raising=False)
    assert fake_models.build(set()) is None


def test_download_completes_instantly_and_marks_installed(monkeypatch):
    monkeypatch.setenv("RAGPIPE_FAKE_MODELS", "1")
    built = fake_models.build(set())
    assert built is not None
    ollama, models = built

    assert ollama.is_running() is True
    assert ollama.get_model_status("llama3.1:8b") is ModelStatus.NOT_INSTALLED

    events: list[DownloadProgress] = []
    result = models.download_model("llama3.1:8b", events.append)
    assert result.verified is True or models.verify_model_integrity("llama3.1:8b")
    assert models.verify_model_integrity("llama3.1:8b") is True
    assert ollama.get_model_status("llama3.1:8b") is ModelStatus.AVAILABLE
