"""``OllamaManager`` — the Python backend's read-only window onto the bundled
Ollama sidecar (Phase 1 Step 1.6).

The Tauri shell starts and supervises the Ollama process; this class only ever
talks to its HTTP API (default ``http://localhost:11434``). Host resolution
matches ``src/common/llm_client.py::OllamaAdapter``: explicit ``host`` arg, then
``$OLLAMA_HOST``, then the localhost default.

All methods degrade gracefully — a daemon that is down is reported as
``is_running() == False`` / ``NOT_INSTALLED`` / ``None``, never an exception.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Set as AbstractSet

import requests

from src.models.app_config import AppConfig
from src.models.types import InstalledModel, ModelStatus

logger = logging.getLogger(__name__)


def normalize_model_name(name: str) -> str:
    """Ollama reports fully-tagged names (``mistral:latest``); users and the
    catalog sometimes drop the tag (``mistral``). Compare on the tagged form."""
    return name if ":" in name else f"{name}:latest"


class OllamaManager:
    def __init__(
        self,
        host: str | None = None,
        *,
        timeout_seconds: float = 10.0,
        active_downloads: AbstractSet[str] = frozenset(),
    ):
        resolved = host or os.getenv("OLLAMA_HOST") or "http://localhost:11434"
        self.host = resolved.rstrip("/")
        self.timeout_seconds = timeout_seconds
        # Read-only view of ModelManager's in-flight-download tracker. This
        # class only READS it (to report DOWNLOADING); ModelManager is the
        # sole writer. Typed AbstractSet to signal that intent even though the
        # shared object is a mutable set passed by reference.
        self._active_downloads = active_downloads

    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        """True if the Ollama HTTP API answers ``GET /api/tags`` with 200."""
        try:
            resp = requests.get(f"{self.host}/api/tags", timeout=self.timeout_seconds)
            return resp.status_code == 200
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            return False

    def get_installed_models(self) -> list[InstalledModel]:
        """Parse ``GET /api/tags``. Empty list if the daemon is unreachable or
        returns an error."""
        try:
            resp = requests.get(f"{self.host}/api/tags", timeout=self.timeout_seconds)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            return []
        if resp.status_code != 200:
            return []

        out: list[InstalledModel] = []
        for item in resp.json().get("models", []):
            details = item.get("details") or {}
            out.append(
                InstalledModel(
                    name=str(item.get("name", "")),
                    size_bytes=int(item.get("size", 0) or 0),
                    digest=str(item.get("digest", "")),
                    modified_at=str(item.get("modified_at", "")),
                    family=str(details.get("family", "")),
                    parameter_size=str(details.get("parameter_size", "")),
                    quantization_level=str(details.get("quantization_level", "")),
                )
            )
        return out

    def get_model_status(self, model_name: str, *, active_model: str | None = None) -> ModelStatus:
        """Resolve one model's lifecycle state. ``active_model`` defaults to the
        app-config value."""
        target = normalize_model_name(model_name)

        if model_name in self._active_downloads or target in self._active_downloads:
            return ModelStatus.DOWNLOADING

        installed = {normalize_model_name(m.name) for m in self.get_installed_models()}
        if target not in installed:
            return ModelStatus.NOT_INSTALLED

        if active_model is None:
            active_model = AppConfig.load().active_model
        if active_model and normalize_model_name(active_model) == target:
            return ModelStatus.ACTIVE
        return ModelStatus.AVAILABLE

    def delete_model(self, model_name: str) -> bool:
        """``DELETE /api/delete``. True if something was removed, False on 404
        (nothing there) or any transport error."""
        try:
            resp = requests.delete(
                f"{self.host}/api/delete",
                json={"name": model_name},
                timeout=self.timeout_seconds,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            return False
        return resp.status_code == 200

    def show_model(self, model_name: str) -> dict | None:
        """``POST /api/show`` — model details, or ``None`` if not present / on
        any error. Used by ``ModelManager.verify_model_integrity``."""
        try:
            resp = requests.post(
                f"{self.host}/api/show",
                json={"name": model_name},
                timeout=self.timeout_seconds,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            return None
        if resp.status_code != 200:
            return None
        body = resp.json()
        return body if isinstance(body, dict) else None
