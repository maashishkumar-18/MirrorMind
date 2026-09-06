"""Database encryption key: generation, storage, first-launch resolution
(Production Roadmap Phase 2 Step 2.1).

The key is 32 bytes from ``os.urandom`` held as a 64-character lowercase hex
string (the form ``db.connection.open_session_db`` feeds straight into
SQLCipher's raw-key ``PRAGMA key = "x'...'"`` — no KDF, no salt derivation).

Storage is ``keyring``. On Windows that resolves to
``keyring.backends.Windows.WinVaultKeyring`` — the Credential Manager,
DPAPI-protected and bound to the machine + Windows account. Tests install an
in-memory backend via ``keyring.set_keyring`` and never touch the real store.

This module is a leaf: it imports ``keyring`` and this package's own errors,
nothing from ``db/``.
"""

from __future__ import annotations

import os
from pathlib import Path

import keyring

from src.security.errors import PreviousDataUnrecoverableError

#: Credential Manager "service" / target name.
SERVICE = "PersonalAICompanion"
#: Credential Manager "account" / username. The ``_v1`` suffix reserves a
#: rotation path: a future v1 -> v2 migration would load the v1 key,
#: re-encrypt the database (``sqlcipher_export``), store the v2 key, then
#: delete the v1 entry. Not implemented in 2.1.
ACCOUNT = "db_encryption_key_v1"

_KEY_BYTES = 32


def generate_key() -> str:
    """A fresh 64-hex-character (32-byte) database key."""
    return os.urandom(_KEY_BYTES).hex()


def store_key(hex_key: str) -> None:
    """Persist ``hex_key`` in the credential store, replacing any existing entry."""
    keyring.set_password(SERVICE, ACCOUNT, hex_key)


def load_key() -> str | None:
    """The stored key, or ``None`` if the credential store has no entry."""
    return keyring.get_password(SERVICE, ACCOUNT)


def delete_key() -> None:
    """Remove the stored key. Used by tests and, later, key rotation."""
    try:
        keyring.delete_password(SERVICE, ACCOUNT)
    except keyring.errors.PasswordDeleteError:
        pass


def resolve_key(db_path: str | os.PathLike[str]) -> str:
    """Return the database key for this launch, deciding first-launch vs. reinstall.

    - Key in the store -> return it (the normal path).
    - No key, no database file on disk -> genuine first launch: generate a
      key, persist it, return it.
    - No key but a database file exists -> the key is gone (Windows
      reinstall). Raise :class:`PreviousDataUnrecoverableError`; the caller
      shows its message and starts fresh. Do **not** open the old file here.
    """
    existing = load_key()
    if existing:
        return existing

    if Path(db_path).exists():
        raise PreviousDataUnrecoverableError()

    key = generate_key()
    store_key(key)
    return key
