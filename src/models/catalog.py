"""Loader for the bundled model catalog (Phase 1 Step 1.6).

``config/models/catalog.json`` is a curated list of recommended models with
name, size, description, and minimum RAM. It is shipped in the app and is
**never fetched from the network** (project_logic.md §8).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from src.models.types import ModelCatalogEntry

logger = logging.getLogger(__name__)

_DEFAULT_CATALOG = Path(__file__).parent.parent.parent / "config" / "models" / "catalog.json"


def catalog_path(path: str | None = None) -> Path:
    """``RAGPIPE_MODEL_CATALOG`` override, else the bundled file."""
    return Path(path or os.getenv("RAGPIPE_MODEL_CATALOG") or _DEFAULT_CATALOG)


def load_model_catalog(path: str | None = None) -> list[ModelCatalogEntry]:
    """Parse the catalog JSON into ``ModelCatalogEntry`` objects. A missing or
    unreadable file yields ``[]`` (with a warning) — the frontend then shows an
    empty catalog rather than crashing."""
    resolved = catalog_path(path)
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        logger.warning("model catalog at %s is unavailable (%s)", resolved, exc)
        return []

    entries: list[ModelCatalogEntry] = []
    for item in data.get("models", []):
        entries.append(
            ModelCatalogEntry(
                name=str(item["name"]),
                display_name=str(item.get("display_name", item["name"])),
                size_bytes=int(item.get("size_bytes", 0)),
                description=str(item.get("description", "")),
                min_ram_gb=float(item.get("min_ram_gb", 0)),
                recommended=bool(item.get("recommended", False)),
            )
        )
    return entries
