"""Typed failures for the encryption-key lifecycle (Phase 2 Step 2.1)."""

from __future__ import annotations


class KeyNotFoundError(Exception):
    """No database encryption key is present in the credential store.

    On its own this is ambiguous — it is a genuine first launch *unless* an
    encrypted database file already exists on disk, in which case
    :class:`PreviousDataUnrecoverableError` is raised instead.
    ``keyring_store.resolve_key`` makes that distinction; this bare error is
    only raised by the lower-level ``load`` helpers.
    """


class PreviousDataUnrecoverableError(Exception):
    """The credential store has no key but an encrypted database file exists.

    The DPAPI-protected key is machine + Windows-account bound. After a
    Windows reinstall it is gone for good and the old database cannot be
    decrypted. The launch flow shows :attr:`MESSAGE` verbatim and starts
    fresh — it must **not** try to open the old file.

    (A plaintext pre-2.1 development database with no stored key also lands
    here. The message is production-accurate — a shipped build never had a
    plaintext session DB — but a developer upgrading a local checkout should
    delete their ``data/`` directory and old ``db/session.db``.)
    """

    MESSAGE = (
        "Your previous data is protected by your Windows account and cannot be "
        "recovered after a Windows reinstall. Starting fresh."
    )

    def __init__(self, message: str | None = None):
        super().__init__(message or self.MESSAGE)


class DatabaseKeyError(Exception):
    """Opening an encrypted database failed at the key-verification probe.

    SQLCipher does not reject a wrong key at ``PRAGMA key`` time — the first
    real read (``SELECT count(*) FROM sqlite_master``) raises
    ``sqlcipher3.dbapi2.DatabaseError`` ("file is not a database" for a wrong
    key or a corrupt page-1 header, "database disk image is malformed" for a
    damaged interior page). ``db.connection.open_session_db`` wraps that into
    this error so callers get one thing to catch, not a driver-specific
    exception tree.
    """
