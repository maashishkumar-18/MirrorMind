"""Database-key resolution for the backend entrypoint (Phase 3 Step 3.1a).

Normally the key comes from the Windows Credential Manager via
``src.security.keyring_store.resolve_key`` (first-launch generate-and-store,
or the ``PreviousDataUnrecoverableError`` reinstall path).

``RAGPIPE_DB_KEY`` (64 hex chars) is a **dev / test seam** — when set it is
used verbatim and the Credential Manager is never touched. The packaged app
never sets it. This mirrors the existing ``RAGPIPE_DATA_DIR`` /
``RAGPIPE_APP_CONFIG_PATH`` overrides.
"""

from __future__ import annotations

import os
import re

from src.security.errors import DatabaseKeyError
from src.security.keyring_store import resolve_key

_HEX64 = re.compile(r"\A[0-9a-fA-F]{64}\Z")


def resolve_db_key(db_path: str) -> str:
    override = os.getenv("RAGPIPE_DB_KEY")
    if override:
        if not _HEX64.match(override):
            raise DatabaseKeyError("RAGPIPE_DB_KEY must be exactly 64 hex characters")
        return override.lower()
    return resolve_key(db_path)
