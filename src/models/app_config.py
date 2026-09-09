"""The mutable app-config file — currently just the active generative model
(Phase 1 Step 1.6).

This is machine-written runtime state, not hand-edited tunables, so it is
**JSON** (the YAML config files elsewhere in the repo are for algorithm
parameters). It is written **atomically** (temp file + ``os.replace``) —
crash-safety is a Phase 2 theme and cheap to honor now.

Path resolution mirrors the ``RAGPIPE_DB_PATH`` / ``RAGPIPE_*_CONFIG``
precedents:

- ``RAGPIPE_APP_CONFIG_PATH``  — full override (test seam), else
- ``<RAGPIPE_DATA_DIR or ./data>/app_config.json``.

First-launch authority (project_logic.md §8): the app-config ``active_model``
is authoritative for the *app*. ``$OLLAMA_DEFAULT_MODEL`` is only a
dev/test convenience default for the low-level ``simple_generate`` helper and
``resolve_default_model`` below — it is **not** consulted by
``ModelInferenceRouter`` and does **not** satisfy first-launch.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from src.features.base import now_iso  # generic ISO-8601 UTC timestamp helper

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).parent.parent.parent
# v2 (Phase 2 Step 2.2) added last_exported_at; v3 (Phase 3 Step 3.4) added the two
# General-settings fields. Older files load fine — missing fields default to None.
_CONFIG_VERSION = 3


def app_config_path(path: str | None = None) -> Path:
    """Resolve the app-config file path (see module docstring)."""
    if path:
        return Path(path)
    env_path = os.getenv("RAGPIPE_APP_CONFIG_PATH")
    if env_path:
        return Path(env_path)
    data_dir = os.getenv("RAGPIPE_DATA_DIR") or str(_REPO_ROOT / "data")
    return Path(data_dir) / "app_config.json"


@dataclass
class AppConfig:
    """The app-config document. Construct via :meth:`load`."""

    active_model: str | None = None
    #: ISO 8601 timestamp of the last successful data export (Phase 2 Step 2.2);
    #: ``None`` = never exported. Drives the Settings export nudge badge.
    last_exported_at: str | None = None
    #: Settings → General (Phase 3 Step 3.4). ``None`` = fall back to the
    #: existing env/YAML default. Resolved to an *effective* value by
    #: ``src/backend/settings.py``, never read raw by the worker/scheduler.
    idle_timeout_minutes: int | None = None
    #: local time-of-day "HH:MM" for the daily summary; ``None`` = env/YAML default.
    summary_time: str | None = None
    updated_at: str = ""
    version: int = _CONFIG_VERSION
    #: where this instance reads/writes; not serialized
    path: Path | None = None

    @classmethod
    def load(cls, path: str | None = None) -> AppConfig:
        """Read the app-config file. A missing, empty, or corrupt file yields
        defaults (``active_model=None`` — i.e. first launch); this never
        raises on a bad file."""
        resolved = app_config_path(path)
        data: dict = {}
        try:
            raw = resolved.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            if not isinstance(data, dict):
                raise ValueError("app-config root is not an object")
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning(
                "app-config at %s is unreadable (%s); treating as first launch", resolved, exc
            )
            data = {}

        active = data.get("active_model")
        last_exported = data.get("last_exported_at")
        idle = data.get("idle_timeout_minutes")
        summary_time = data.get("summary_time")
        return cls(
            active_model=str(active) if active else None,
            last_exported_at=str(last_exported) if last_exported else None,
            idle_timeout_minutes=int(idle) if isinstance(idle, int | float) and idle else None,
            summary_time=str(summary_time) if summary_time else None,
            updated_at=str(data.get("updated_at", "")),
            version=int(data.get("version", _CONFIG_VERSION)),
            path=resolved,
        )

    def save(self) -> None:
        """Write the file atomically (temp + ``os.replace``), creating parent
        directories as needed."""
        target = self.path or app_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = now_iso()
        self.version = _CONFIG_VERSION
        payload = {
            "version": self.version,
            "active_model": self.active_model,
            "last_exported_at": self.last_exported_at,
            "idle_timeout_minutes": self.idle_timeout_minutes,
            "summary_time": self.summary_time,
            "updated_at": self.updated_at,
        }
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
        self.path = target

    def set_active_model(self, name: str | None) -> None:
        """Set (or clear, with ``None``) the active model and persist."""
        self.active_model = name or None
        self.save()

    def set_last_exported_at(self, value: str | None) -> None:
        """Set (or clear, with ``None``) the last-export timestamp and persist."""
        self.last_exported_at = value or None
        self.save()

    def set_idle_timeout_minutes(self, value: int | None) -> None:
        """Set (or clear, with ``None`` → env/YAML default) the idle timeout."""
        self.idle_timeout_minutes = value if value else None
        self.save()

    def set_summary_time(self, value: str | None) -> None:
        """Set (or clear, with ``None`` → env/YAML default) the daily summary time."""
        self.summary_time = value or None
        self.save()


def resolve_default_model(config: AppConfig | None = None) -> str:
    """Return a model name for callers that just need *a* model and are **not**
    enforcing first-launch gating: app-config ``active_model``, else
    ``$OLLAMA_DEFAULT_MODEL``, else ``"llama3.1:8b"``.

    This helper is for callers that need a model name and are NOT enforcing
    first-launch gating. It is intentionally NOT used by ModelInferenceRouter.
    Do not add it to ModelInferenceRouter.generate() without explicit user
    approval — doing so silently breaks first-launch enforcement.
    """
    cfg = config or AppConfig.load()
    return cfg.active_model or os.getenv("OLLAMA_DEFAULT_MODEL") or "llama3.1:8b"


def model_setup_required(path: str | None = None) -> bool:
    """True when no model has been activated yet — the Phase 3 backend calls
    this to block inference-dependent IPC handlers on first launch."""
    return AppConfig.load(path).active_model is None
