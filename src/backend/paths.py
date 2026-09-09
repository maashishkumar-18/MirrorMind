"""Filesystem locations the backend composes at startup (Phase 3 Step 3.1a).

One home for the ``RAGPIPE_DATA_DIR`` convention already used by
``src/models/app_config.py`` (``app_config.json``) and
``src/features/backup_manager.py`` (``default_backup_dir``). The session
database and its pre-migration snapshots live under the same directory.
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent


def data_dir() -> Path:
    """``$RAGPIPE_DATA_DIR`` if set, else ``<repo>/data``. Not created here —
    the writers (``AppConfig.save``, ``MigrationRunner``, ``BackupManager``)
    each ``mkdir(parents=True, exist_ok=True)`` their own subtree."""
    return Path(os.getenv("RAGPIPE_DATA_DIR") or str(_REPO_ROOT / "data"))


def session_db_path() -> str:
    """The single encrypted SQLite file (project_logic.md §5)."""
    return str(data_dir() / "session.db")


def snapshot_dir() -> Path:
    """Where ``MigrationRunner`` drops pre-migration ``.bak`` files."""
    return data_dir() / "snapshots"


def log_dir() -> Path:
    """Where the rotating backend log file lives (Phase 3 Step 3.4a). One
    definition shared by ``logging_setup`` (the ``RotatingFileHandler``) and the
    ``diagnostics.logs`` / ``diagnostics.report`` handlers."""
    return data_dir() / "logs"


def log_file() -> Path:
    """The active rotating log file; its rotations are ``backend.log.1`` … ``.3``."""
    return log_dir() / "backend.log"
