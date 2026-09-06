"""Encryption-at-rest key management (Production Roadmap Phase 2 Step 2.1).

The session database is SQLCipher-encrypted. Its 32-byte encryption key is
generated once on first launch with ``os.urandom(32)`` and stored in the
Windows Credential Manager via ``keyring``. This package owns that key's
lifecycle:

- ``keyring_store``  — generate / store / load / first-launch resolution.
- ``errors``         — the typed failures a launch flow must handle
                       (key gone after a Windows reinstall; a wrong key or
                       otherwise unreadable cipher file).

Phase 2 Step 2.1 is *primitives only*. The on-launch composition
(resolve key -> run migrations -> open -> ``PRAGMA integrity_check`` ->
offer restore) belongs to the Phase 3 backend entry point, which does not
exist yet.
"""

from src.security.errors import (
    DatabaseKeyError,
    KeyNotFoundError,
    PreviousDataUnrecoverableError,
)
from src.security.keyring_store import (
    ACCOUNT,
    SERVICE,
    generate_key,
    load_key,
    resolve_key,
    store_key,
)

__all__ = [
    "ACCOUNT",
    "SERVICE",
    "DatabaseKeyError",
    "KeyNotFoundError",
    "PreviousDataUnrecoverableError",
    "generate_key",
    "load_key",
    "resolve_key",
    "store_key",
]
