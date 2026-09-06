"""Data contracts for the model-management subsystem (Phase 1 Step 1.6).

These are internal orchestration contracts (download progress, model status),
not the cross-layer entity contracts in ``src/common/types.py``. Kept in the
subsystem package for the same reason ``src/retrieval/router.py`` keeps
``RetrievalRouterConfig`` local.

No implementation logic here — pure data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ModelStatus(str, Enum):
    """Lifecycle state of a single model, as reported by ``OllamaManager``."""

    NOT_INSTALLED = "not_installed"
    DOWNLOADING = "downloading"
    AVAILABLE = "available"  # installed, but not the active model
    ACTIVE = "active"  # installed and selected as the active model


@dataclass
class InstalledModel:
    """One entry from Ollama's ``GET /api/tags`` ``models[]`` array."""

    name: str
    size_bytes: int
    digest: str = ""
    modified_at: str = ""
    family: str = ""
    parameter_size: str = ""
    quantization_level: str = ""


@dataclass
class ModelCatalogEntry:
    """One entry from the bundled ``config/models/catalog.json``. Curated,
    never fetched from the network."""

    name: str
    display_name: str
    size_bytes: int
    description: str
    min_ram_gb: float
    recommended: bool = False


@dataclass
class DownloadProgress:
    """Passed to ``ModelManager.download_model``'s ``progress_callback`` on
    every meaningful pull event.

    ``speed_mbps`` is **megabytes per second** (not megabits) — the field name
    matches the roadmap spec's ``{percent, speed_mbps, eta_seconds}``.
    ``phase`` / ``message`` carry the non-numeric signals (notably the
    "restarting from the beginning" notice) the roadmap's three-state
    guarantee requires the user to see.
    """

    phase: str  # "manifest" | "downloading" | "verifying" | "restarting" | "complete"
    percent: float = 0.0
    speed_mbps: float = 0.0
    eta_seconds: float | None = None
    message: str | None = None


@dataclass
class DownloadResult:
    """Terminal outcome of a successful ``download_model`` call. A failure
    raises ``ModelDownloadError`` instead of returning this."""

    model_name: str
    status: str  # "complete" | "restarted_then_complete"
    restarts: int = 0
    resumes: int = 0
    verified: bool = False


@dataclass
class InferenceResult:
    """``ModelInferenceRouter.generate`` return — always a structured result,
    never a raised provider exception or a bare stack trace.

    ``error_code`` (when ``ok`` is False) is one of ``no_model_active`` /
    ``ollama_not_running`` / ``model_not_downloaded`` / ``model_not_loaded`` /
    ``inference_failed``; ``message`` is the user-readable companion string.
    """

    ok: bool = True
    text: str = ""
    model_name: str = ""
    error_code: str | None = None
    message: str | None = None


class ModelDownloadError(Exception):
    """Raised by ``ModelManager`` for the "specific, actionable error" arm of
    the three-state guarantee (state (c)). ``str(exc)`` is user-facing."""
