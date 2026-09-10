"""Deterministic model-management stubs for end-to-end tests (Phase 4 Step 4.1).

``RAGPIPE_FAKE_MODELS`` is a test seam — same spirit as ``RAGPIPE_FAKE_LLM``.
When set, :func:`build` returns an ``(OllamaManager, ModelManager)`` pair whose
downloads complete instantly against an in-memory "installed" set, so the
first-launch model-selection flow can be exercised with no real Ollama daemon
and no network. The packaged app never sets it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from src.models.model_manager import ModelManager
from src.models.ollama_manager import OllamaManager, normalize_model_name
from src.models.types import InstalledModel

_ENV_VAR = "RAGPIPE_FAKE_MODELS"


class _FakeOllamaManager(OllamaManager):
    """Reports models from a shared in-memory set instead of ``/api/tags``."""

    def __init__(self, *, active_downloads: set[str], installed: set[str]) -> None:
        super().__init__(active_downloads=active_downloads)
        self._installed = installed  # normalized ("name:tag") entries

    def is_running(self) -> bool:
        return True

    def get_installed_models(self) -> list[InstalledModel]:
        return [InstalledModel(name=n, size_bytes=1) for n in sorted(self._installed)]

    def show_model(self, model_name: str) -> dict | None:
        return {} if normalize_model_name(model_name) in self._installed else None

    def delete_model(self, model_name: str) -> bool:
        self._installed.discard(normalize_model_name(model_name))
        return True


class _InstantPullStreamer:
    """A ``PullStreamer`` that marks the model installed and reports success."""

    def __init__(self, installed: set[str]) -> None:
        self._installed = installed

    def pull(self, model_name: str) -> Iterator[dict]:
        self._installed.add(normalize_model_name(model_name))
        yield {"status": "pulling manifest"}
        yield {"status": "success"}


def build(active_downloads: set[str]) -> tuple[OllamaManager, ModelManager] | None:
    """``None`` unless ``RAGPIPE_FAKE_MODELS`` is set."""
    if not os.getenv(_ENV_VAR):
        return None
    installed: set[str] = set()
    ollama = _FakeOllamaManager(active_downloads=active_downloads, installed=installed)
    models = ModelManager(
        ollama,
        active_downloads=active_downloads,
        pull_streamer=_InstantPullStreamer(installed),
    )
    return ollama, models
